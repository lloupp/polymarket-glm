"""Tests for SignalController + PositionExecutor (Controller→Executor separation)."""
import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polymarket_glm.execution.barriers import (
    CloseType,
    PositionBarrierResult,
    TripleBarrierConfig,
    TrailingStop,
)
from polymarket_glm.execution.position_executor import (
    ManagedPosition,
    PositionExecutor,
    PositionExecutorConfig,
    PositionMetrics,
)
from polymarket_glm.execution.signal_controller import (
    ControllerConfig,
    ControllerState,
    SignalController,
    PositionExecutorProtocol,
)
from polymarket_glm.models import Market, OrderBook, OrderBookLevel, Side
from polymarket_glm.strategy.signal_engine import Signal, SignalEngine, SignalType


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_market(
    market_id: str = "mkt_001",
    question: str = "Will X happen?",
    volume: float = 50_000.0,
    active: bool = True,
    closed: bool = False,
) -> Market:
    return Market(
        condition_id="cond_001",
        market_id=market_id,
        question=question,
        outcomes=["Yes", "No"],
        outcome_prices=[0.40, 0.60],
        tokens=["tok_yes", "tok_no"],
        active=active,
        closed=closed,
        volume=volume,
    )


def _make_book(
    market_id: str = "mkt_001",
    bid: float = 0.39,
    ask: float = 0.41,
) -> OrderBook:
    return OrderBook(
        market_id=market_id,
        bids=[OrderBookLevel(price=bid, size=100)],
        asks=[OrderBookLevel(price=ask, size=100)],
    )


def _make_signal(
    market_id: str = "mkt_001",
    edge: float = 0.15,
    size_usd: float = 100.0,
) -> Signal:
    return Signal(
        market_id=market_id,
        condition_id="cond_001",
        question="Will X happen?",
        signal_type=SignalType.BUY,
        outcome="Yes",
        edge=edge,
        estimated_prob=0.55,
        market_price=0.40,
        size_usd=size_usd,
        kelly_raw=0.25,
        kelly_sized=0.25,
        target_price=0.55,
    )


def _make_barrier(
    stop_loss_pct: float = 0.50,
    take_profit_pct: float = 0.50,
    time_limit_sec: int = 3600,
) -> TripleBarrierConfig:
    return TripleBarrierConfig(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        time_limit_sec=time_limit_sec,
    )


# ════════════════════════════════════════════════════════════════════
# SignalController Tests
# ════════════════════════════════════════════════════════════════════

class TestControllerConfig:
    def test_defaults(self):
        cfg = ControllerConfig()
        assert cfg.min_edge == 0.05
        assert cfg.kelly_fraction == 0.25
        assert cfg.max_open_positions == 10
        assert cfg.market_cooldown_sec == 300.0

    def test_custom(self):
        cfg = ControllerConfig(min_edge=0.10, max_open_positions=5)
        assert cfg.min_edge == 0.10
        assert cfg.max_open_positions == 5


class TestControllerState:
    def test_initial_state(self):
        state = ControllerState()
        assert state.signals_generated == 0
        assert state.signals_executed == 0
        assert state.errors == 0


class TestSignalController:
    def test_init_defaults(self):
        ctrl = SignalController()
        assert ctrl.config.min_edge == 0.05
        assert ctrl.executor is None
        assert ctrl.state.signals_generated == 0

    def test_init_with_config(self):
        cfg = ControllerConfig(min_edge=0.15, max_open_positions=3)
        ctrl = SignalController(config=cfg)
        assert ctrl.config.min_edge == 0.15
        assert ctrl.config.max_open_positions == 3

    # ── Market Filtering ────────────────────────────────────────

    def test_filter_market_passes(self):
        ctrl = SignalController()
        market = _make_market(volume=50_000)
        book = _make_book()
        assert ctrl.filter_market(market, book) is True

    def test_filter_market_low_volume(self):
        ctrl = SignalController()
        market = _make_market(volume=100)  # Below default 10_000
        assert ctrl.filter_market(market) is False

    def test_filter_market_inactive(self):
        ctrl = SignalController()
        market = _make_market(active=False)
        assert ctrl.filter_market(market) is False

    def test_filter_market_closed(self):
        ctrl = SignalController()
        market = _make_market(closed=True)
        assert ctrl.filter_market(market) is False

    def test_filter_market_high_spread(self):
        ctrl = SignalController()
        market = _make_market()
        # Spread > 500 bps (5%)
        book = OrderBook(
            market_id="mkt_001",
            bids=[OrderBookLevel(price=0.35, size=100)],
            asks=[OrderBookLevel(price=0.65, size=100)],
        )
        assert ctrl.filter_market(market, book) is False

    def test_filter_custom_volume_threshold(self):
        cfg = ControllerConfig(min_volume_usd=100_000)
        ctrl = SignalController(config=cfg)
        market = _make_market(volume=50_000)
        assert ctrl.filter_market(market) is False

    # ── Signal Processing ───────────────────────────────────────

    def test_process_market_generates_signal(self):
        ctrl = SignalController()
        market = _make_market()
        book = _make_book()
        # High estimated prob → strong edge
        signal = ctrl.process_market(market, book, estimated_prob=0.80, balance_usd=10_000)
        # Edge = 0.80 - 0.40 = 0.40 → clamped to 0.30, still > min_edge
        assert signal is not None
        assert ctrl.state.signals_generated == 1

    def test_process_market_no_edge(self):
        ctrl = SignalController()
        market = _make_market()
        book = _make_book()
        # Estimated prob close to market price → no edge
        signal = ctrl.process_market(market, book, estimated_prob=0.41, balance_usd=10_000)
        assert signal is None

    def test_process_market_filtered(self):
        ctrl = SignalController()
        market = _make_market(volume=100)  # Below threshold
        book = _make_book()
        signal = ctrl.process_market(market, book, estimated_prob=0.80)
        assert signal is None

    # ── Cooldown ────────────────────────────────────────────────

    def test_cooldown_blocks_signal(self):
        cfg = ControllerConfig(market_cooldown_sec=9999.0)
        ctrl = SignalController(config=cfg)
        market = _make_market()
        book = _make_book()

        # First signal passes
        signal1 = ctrl.process_market(market, book, estimated_prob=0.80)
        assert signal1 is not None

        # Second signal blocked by cooldown
        signal2 = ctrl.process_market(market, book, estimated_prob=0.80)
        assert signal2 is None
        assert ctrl.state.signals_skipped_cooldown == 1

    # ── Max Positions ───────────────────────────────────────────

    def test_max_positions_blocks_signal(self):
        cfg = ControllerConfig(max_open_positions=1)
        ctrl = SignalController(config=cfg)

        # Mock executor with 1 open position
        mock_executor = MagicMock(spec=PositionExecutorProtocol)
        mock_executor.open_position_ids = ["mkt_001::Yes::abc"]
        ctrl.executor = mock_executor

        market2 = _make_market(market_id="mkt_002")
        book2 = _make_book(market_id="mkt_002")
        signal2 = ctrl.process_market(market2, book2, estimated_prob=0.80)
        assert signal2 is None
        assert ctrl.state.signals_skipped_max_pos == 1

    # ── Execute Signal ──────────────────────────────────────────

    def test_execute_signal_no_executor(self):
        ctrl = SignalController()
        signal = _make_signal()
        result = ctrl.execute_signal(signal)
        assert result is None

    def test_execute_signal_with_executor(self):
        ctrl = SignalController()
        mock_executor = MagicMock(spec=PositionExecutorProtocol)
        mock_executor.open_position.return_value = "pos_001"
        ctrl.executor = mock_executor

        signal = _make_signal()
        result = ctrl.execute_signal(signal)
        assert result == "pos_001"
        assert ctrl.state.signals_executed == 1
        mock_executor.open_position.assert_called_once()

    # ── Batch Processing ────────────────────────────────────────

    def test_process_markets_batch(self):
        ctrl = SignalController()
        mock_executor = MagicMock(spec=PositionExecutorProtocol)
        mock_executor.open_position_ids = []
        mock_executor.open_position.return_value = "pos_001"
        ctrl.executor = mock_executor

        markets = [
            (_make_market(market_id="m1"), _make_book(market_id="m1"), 0.80),
        ]
        results = ctrl.process_markets(markets, balance_usd=10_000)
        assert ctrl.state.scan_count == 1
        assert ctrl.state.markets_scanned == 1

    # ── Barrier Checking ────────────────────────────────────────

    def test_check_barriers_no_executor(self):
        ctrl = SignalController()
        result = ctrl.check_all_barriers({"m1": 0.30})
        assert result == []

    def test_check_barriers_with_executor(self):
        ctrl = SignalController()
        mock_executor = MagicMock(spec=PositionExecutorProtocol)
        mock_executor.check_barriers.return_value = ["pos_001"]
        ctrl.executor = mock_executor

        closed = ctrl.check_all_barriers({"m1": 0.30})
        assert closed == ["pos_001"]

    # ── Stats ───────────────────────────────────────────────────

    def test_stats(self):
        ctrl = SignalController()
        stats = ctrl.stats()
        assert "signals_generated" in stats
        assert "scan_count" in stats
        assert "errors" in stats


# ════════════════════════════════════════════════════════════════════
# PositionMetrics Tests
# ════════════════════════════════════════════════════════════════════

class TestPositionMetrics:
    def test_return_pct_buy_yes_profit(self):
        m = PositionMetrics(entry_price=0.40, current_price=0.50, peak_price=0.50, side="BUY", outcome="Yes")
        assert m.return_pct == pytest.approx(0.25, abs=0.01)  # 25% gain

    def test_return_pct_buy_yes_loss(self):
        m = PositionMetrics(entry_price=0.40, current_price=0.20, peak_price=0.40, side="BUY", outcome="Yes")
        assert m.return_pct == pytest.approx(-0.50, abs=0.01)  # 50% loss

    def test_return_pct_zero_entry(self):
        m = PositionMetrics(entry_price=0, current_price=0.50, peak_price=0.50)
        assert m.return_pct == 0.0

    def test_update_price_increases_peak(self):
        m = PositionMetrics(entry_price=0.40, current_price=0.40, peak_price=0.40, side="BUY", outcome="Yes")
        m.update_price(0.60)
        assert m.peak_price == 0.60
        assert m.current_price == 0.60


# ════════════════════════════════════════════════════════════════════
# PositionExecutor Tests
# ════════════════════════════════════════════════════════════════════

class TestPositionExecutorConfig:
    def test_defaults(self):
        cfg = PositionExecutorConfig()
        assert cfg.max_open_positions == 10
        assert cfg.default_stop_loss_pct == 0.50


class TestManagedPosition:
    def test_is_open_true(self):
        mp = ManagedPosition(
            position_id="pos_001",
            market_id="m1",
            outcome="Yes",
            signal=_make_signal(),
            barrier_config=_make_barrier(),
            metrics=PositionMetrics(
                entry_price=0.40,
                peak_price=0.40,
                current_price=0.40,
            ),
        )
        assert mp.is_open is True
        assert mp.closed_at is None

    def test_is_open_false(self):
        mp = ManagedPosition(
            position_id="pos_001",
            market_id="m1",
            outcome="Yes",
            signal=_make_signal(),
            barrier_config=_make_barrier(),
            metrics=PositionMetrics(
                entry_price=0.40,
                peak_price=0.40,
                current_price=0.40,
            ),
            closed_at=datetime.utcnow(),
        )
        assert mp.is_open is False

    def test_hold_duration(self):
        now = datetime.utcnow()
        mp = ManagedPosition(
            position_id="pos_001",
            market_id="m1",
            outcome="Yes",
            signal=_make_signal(),
            barrier_config=_make_barrier(),
            metrics=PositionMetrics(
                entry_price=0.40,
                peak_price=0.40,
                current_price=0.40,
                entry_time=now - timedelta(hours=2),
            ),
        )
        assert mp.hold_duration >= timedelta(hours=2)


class TestPositionExecutor:
    def test_init_defaults(self):
        exec = PositionExecutor()
        assert exec.n_open == 0
        assert exec.n_closed == 0
        assert exec.total_pnl == 0.0

    # ── Open Position ───────────────────────────────────────────

    def test_open_position_basic(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)
        assert pid != ""
        assert exec.n_open == 1

    def test_open_position_with_barrier_config(self):
        exec = PositionExecutor()
        signal = _make_signal()
        barrier = TripleBarrierConfig(
            stop_loss_pct=0.30,
            take_profit_pct=0.80,
            time_limit_sec=86400,
        )
        pid = exec.open_position(signal, barrier_config=barrier)
        assert pid != ""
        mp = exec.get_position(pid)
        assert mp.barrier_config.stop_loss_pct == 0.30
        assert mp.barrier_config.take_profit_pct == 0.80

    def test_open_position_max_reached(self):
        cfg = PositionExecutorConfig(max_open_positions=1)
        exec = PositionExecutor(config=cfg)

        signal1 = _make_signal(market_id="m1")
        signal2 = _make_signal(market_id="m2")

        pid1 = exec.open_position(signal1)
        assert pid1 != ""

        pid2 = exec.open_position(signal2)
        assert pid2 == ""  # Blocked
        assert exec.n_open == 1

    def test_open_multiple_positions(self):
        exec = PositionExecutor()
        for i in range(5):
            signal = _make_signal(market_id=f"m{i}")
            pid = exec.open_position(signal)
            assert pid != ""
        assert exec.n_open == 5

    def test_position_id_format(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="mkt_test")  # outcome defaults to "Yes"
        pid = exec.open_position(signal)
        assert "mkt_test" in pid
        assert "Yes" in pid
        parts = pid.split("::")
        assert len(parts) == 3

    # ── Close Position ──────────────────────────────────────────

    def test_close_position_manual(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        result = exec.close_position(pid, reason="manual")
        assert result is True
        assert exec.n_open == 0
        assert exec.n_closed == 1

    def test_close_position_stop_loss(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        # Simulate price drop
        mp = exec.get_position(pid)
        mp.metrics.current_price = 0.20  # Entry at 0.40

        result = exec.close_position(pid, reason="stop_loss")
        assert result is True
        mp_closed = exec.get_position(pid)
        assert mp_closed.close_type == CloseType.STOP_LOSS

    def test_close_position_take_profit(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        mp = exec.get_position(pid)
        mp.metrics.current_price = 0.70  # Entry at 0.40 → big profit

        result = exec.close_position(pid, reason="take_profit")
        assert result is True
        mp_closed = exec.get_position(pid)
        assert mp_closed.close_type == CloseType.TAKE_PROFIT

    def test_close_position_time_limit(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        result = exec.close_position(pid, reason="time_limit")
        assert result is True
        mp_closed = exec.get_position(pid)
        assert mp_closed.close_type == CloseType.TIME_LIMIT

    def test_close_position_trailing_stop(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        result = exec.close_position(pid, reason="trailing_stop")
        assert result is True
        mp_closed = exec.get_position(pid)
        assert mp_closed.close_type == CloseType.TRAILING_STOP

    def test_close_nonexistent_position(self):
        exec = PositionExecutor()
        result = exec.close_position("nonexistent", reason="manual")
        assert result is False

    def test_close_already_closed_position(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        exec.close_position(pid, reason="manual")
        result = exec.close_position(pid, reason="manual")
        assert result is False

    # ── Barrier Checking ────────────────────────────────────────

    def test_check_barriers_stop_loss(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="m1")

        barrier = _make_barrier(stop_loss_pct=0.50, take_profit_pct=0.80)
        pid = exec.open_position(signal, barrier_config=barrier)

        # Price dropped 60% from entry (0.40 → 0.16)
        closed = exec.check_barriers({"m1": 0.16})
        assert pid in closed
        assert exec.n_closed == 1

    def test_check_barriers_take_profit(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="m1")

        barrier = _make_barrier(stop_loss_pct=0.50, take_profit_pct=0.50)
        pid = exec.open_position(signal, barrier_config=barrier)

        # Price rose 50%+ from entry (0.40 → 0.65)
        closed = exec.check_barriers({"m1": 0.65})
        assert pid in closed

    def test_check_barriers_no_trigger(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="m1")

        barrier = _make_barrier(stop_loss_pct=0.50, take_profit_pct=0.50)
        pid = exec.open_position(signal, barrier_config=barrier)

        # Price barely moved (0.40 → 0.42)
        closed = exec.check_barriers({"m1": 0.42})
        assert len(closed) == 0
        assert exec.n_open == 1

    def test_check_barriers_unknown_market(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="m1")
        exec.open_position(signal)

        # Price for a different market — should not trigger
        closed = exec.check_barriers({"m2": 0.10})
        assert len(closed) == 0

    def test_check_barriers_multiple_positions(self):
        exec = PositionExecutor()

        barrier = _make_barrier(stop_loss_pct=0.50, take_profit_pct=0.50)
        signal1 = _make_signal(market_id="m1")
        signal2 = _make_signal(market_id="m2")
        pid1 = exec.open_position(signal1, barrier_config=barrier)
        pid2 = exec.open_position(signal2, barrier_config=barrier)

        # Only m1 hits stop loss
        closed = exec.check_barriers({"m1": 0.10, "m2": 0.42})
        assert pid1 in closed
        assert pid2 not in closed
        assert exec.n_open == 1

    def test_check_barriers_trailing_stop(self):
        exec = PositionExecutor()
        signal = _make_signal(market_id="m1")

        barrier = TripleBarrierConfig(
            stop_loss_pct=0.50,
            take_profit_pct=0.80,
            time_limit_sec=3600,
            trailing_stop=TrailingStop(
                activation_price_pct=0.15,
                trailing_delta_pct=0.08,
            ),
        )
        pid = exec.open_position(signal, barrier_config=barrier)

        # First: price rises to activate trailing (0.40 → 0.50 = 25% gain)
        exec.check_barriers({"m1": 0.50})
        assert exec.n_open == 1  # Trailing activated but not triggered

        # Peak updated
        mp = exec.get_position(pid)
        assert mp.metrics.peak_price == 0.50
        assert mp.metrics.trailing_activated is True

        # Then: price drops enough from peak (0.50 → 0.45 ≈ 10% drop from 25% peak gain)
        closed = exec.check_barriers({"m1": 0.45})
        assert pid in closed

    # ── P&L Tracking ────────────────────────────────────────────

    def test_pnl_profit(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        mp = exec.get_position(pid)
        mp.metrics.current_price = 0.70  # Entry at 0.40

        exec.close_position(pid, reason="take_profit")
        assert exec.total_pnl > 0

    def test_pnl_loss(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)

        mp = exec.get_position(pid)
        mp.metrics.current_price = 0.20  # Entry at 0.40

        exec.close_position(pid, reason="stop_loss")
        assert exec.total_pnl < 0

    # ── Stats ───────────────────────────────────────────────────

    def test_stats(self):
        exec = PositionExecutor()
        signal = _make_signal()
        exec.open_position(signal)

        stats = exec.stats()
        assert stats["n_open"] == 1
        assert stats["n_closed"] == 0
        assert "close_type_counts" in stats

    def test_stats_after_close(self):
        exec = PositionExecutor()
        signal = _make_signal()
        pid = exec.open_position(signal)
        exec.close_position(pid, reason="stop_loss")

        stats = exec.stats()
        assert stats["n_open"] == 0
        assert stats["n_closed"] == 1
        assert stats["close_type_counts"].get("stop_loss") == 1


# ════════════════════════════════════════════════════════════════════
# Integration: Controller + Executor
# ════════════════════════════════════════════════════════════════════

class TestControllerExecutorIntegration:
    def test_full_flow(self):
        """Test: scan → signal → open position → barrier trigger → close."""
        # Create executor
        executor = PositionExecutor()

        # Create controller with executor
        cfg = ControllerConfig(
            min_edge=0.05,
            market_cooldown_sec=0,  # No cooldown for test
        )
        ctrl = SignalController(config=cfg, executor=executor)

        # Process a market with high estimated probability
        market = _make_market()
        book = _make_book()
        signal = ctrl.process_market(market, book, estimated_prob=0.80, balance_usd=10_000)
        assert signal is not None

        # Execute signal
        pid = ctrl.execute_signal(signal)
        assert pid is not None
        assert executor.n_open == 1

        # Simulate price crash → stop loss
        closed = ctrl.check_all_barriers({"mkt_001": 0.10})
        assert len(closed) == 1
        assert executor.n_open == 0

    def test_batch_scan_and_execute(self):
        """Test batch processing with multiple markets."""
        executor = PositionExecutor()
        ctrl = SignalController(
            config=ControllerConfig(market_cooldown_sec=0),
            executor=executor,
        )

        markets = [
            (_make_market(market_id=f"m{i}"), _make_book(market_id=f"m{i}"), 0.80)
            for i in range(3)
        ]
        results = ctrl.process_markets(markets, balance_usd=10_000)
        assert ctrl.state.markets_scanned == 3

    def test_controller_uses_default_barriers(self):
        """Controller should create default barrier config when none provided."""
        executor = PositionExecutor()
        cfg = ControllerConfig(default_stop_loss_pct=0.40, default_take_profit_pct=0.60)
        ctrl = SignalController(config=cfg, executor=executor)

        signal = _make_signal()
        pid = ctrl.execute_signal(signal)
        mp = executor.get_position(pid)
        assert mp.barrier_config.stop_loss_pct == 0.40
        assert mp.barrier_config.take_profit_pct == 0.60
