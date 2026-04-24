"""Tests for triple barrier config + CloseType — position lifecycle management."""
import pytest
from datetime import datetime, timedelta, timezone

from polymarket_glm.execution.barriers import (
    check_barriers,
    CloseType,
    PositionBarrierResult,
    TrailingStop,
    TripleBarrierConfig,
)


# ── TripleBarrierConfig ──────────────────────────────────────────────

class TestTripleBarrierConfig:
    def test_defaults(self):
        cfg = TripleBarrierConfig()
        assert cfg.stop_loss_pct == 0.50
        assert cfg.take_profit_pct == 0.50
        assert cfg.time_limit_sec == 3600
        assert cfg.trailing_stop is None

    def test_custom_values(self):
        cfg = TripleBarrierConfig(
            stop_loss_pct=0.30,
            take_profit_pct=0.60,
            time_limit_sec=1800,
            trailing_stop=TrailingStop(activation_price_pct=0.10, trailing_delta_pct=0.03),
        )
        assert cfg.stop_loss_pct == 0.30
        assert cfg.take_profit_pct == 0.60
        assert cfg.time_limit_sec == 1800
        assert cfg.trailing_stop.activation_price_pct == 0.10

    def test_adjusted_for_volatility(self):
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=0.50)
        adjusted = cfg.adjusted_for_volatility(1.5)
        assert adjusted.stop_loss_pct == 0.75
        assert adjusted.take_profit_pct == 0.75
        assert adjusted.time_limit_sec == 3600  # time limit unchanged

    def test_adjusted_for_volatility_with_trailing(self):
        cfg = TripleBarrierConfig(
            trailing_stop=TrailingStop(activation_price_pct=0.10, trailing_delta_pct=0.03)
        )
        adjusted = cfg.adjusted_for_volatility(2.0)
        assert adjusted.trailing_stop.activation_price_pct == 0.20
        assert adjusted.trailing_stop.trailing_delta_pct == 0.06

    def test_none_barriers_stay_none(self):
        cfg = TripleBarrierConfig(stop_loss_pct=None, take_profit_pct=None)
        adjusted = cfg.adjusted_for_volatility(1.5)
        assert adjusted.stop_loss_pct is None
        assert adjusted.take_profit_pct is None

    def test_disable_barrier_with_zero(self):
        cfg = TripleBarrierConfig(stop_loss_pct=0, take_profit_pct=0, time_limit_sec=0)
        assert cfg.stop_loss_pct == 0
        assert cfg.take_profit_pct == 0

    def test_negative_pct_rejected(self):
        with pytest.raises(Exception):
            TripleBarrierConfig(stop_loss_pct=-0.1)


# ── CloseType ─────────────────────────────────────────────────────────

class TestCloseType:
    def test_all_close_types(self):
        expected = [
            "stop_loss", "take_profit", "time_limit", "trailing_stop",
            "resolved", "early_stop", "expired", "insufficient_balance",
            "failed", "completed",
        ]
        actual = [ct.value for ct in CloseType]
        assert set(actual) == set(expected)

    def test_close_type_is_string_enum(self):
        assert CloseType.STOP_LOSS == "stop_loss"
        assert CloseType.TAKE_PROFIT == "take_profit"


# ── check_barriers ────────────────────────────────────────────────────

class TestCheckBarriers:
    def test_no_barrier_triggered(self):
        """Position within all barriers → should not close."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=0.50)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.35,
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert not result.should_close
        assert result.reason == "no_barrier_triggered"

    def test_stop_loss_triggered(self):
        """Position drops 60% → stop loss at 50% triggers."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=1.0)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.12,  # 60% loss
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.STOP_LOSS
        assert result.current_return_pct < 0

    def test_take_profit_triggered(self):
        """Position gains 60% → take profit at 50% triggers."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.80, take_profit_pct=0.50)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.48,  # (0.48-0.30)/0.30 = 60% gain
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.TAKE_PROFIT
        assert result.current_return_pct > 0

    def test_stop_loss_disabled(self):
        """stop_loss_pct=None → no stop loss check."""
        cfg = TripleBarrierConfig(stop_loss_pct=None, take_profit_pct=1.0)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.01,  # ~97% loss
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert not result.should_close

    def test_take_profit_disabled(self):
        """take_profit_pct=None → no take profit check."""
        cfg = TripleBarrierConfig(stop_loss_pct=1.0, take_profit_pct=None)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.90,  # 200% gain
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert not result.should_close

    def test_buy_no_position_profit(self):
        """BUY NO: profit when YES price drops (NO price rises)."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=0.50)
        # Buy NO when YES=0.70 → NO price=0.30
        # Current YES=0.50 → NO price=0.50 → gain = (0.50-0.30)/0.30 = 67%
        result = check_barriers(
            entry_price=0.70,  # YES price at entry
            current_price=0.50,  # YES price now (NO went up)
            side="BUY",
            outcome="NO",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.TAKE_PROFIT

    def test_buy_no_position_loss(self):
        """BUY NO: loss when YES price rises (NO price drops)."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=1.0)
        # Buy NO when YES=0.30 → NO price=0.70
        # Current YES=0.65 → NO price=0.35 → loss = (0.35-0.70)/0.70 = -50%
        result = check_barriers(
            entry_price=0.30,
            current_price=0.65,
            side="BUY",
            outcome="NO",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.STOP_LOSS

    def test_sell_yes_profit(self):
        """SELL YES: profit when price goes down."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=0.50)
        result = check_barriers(
            entry_price=0.50,
            current_price=0.25,  # 50% drop = 50% profit for seller
            side="SELL",
            outcome="YES",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.TAKE_PROFIT

    def test_time_limit_triggered(self):
        """Position within time_limit of resolution → close."""
        cfg = TripleBarrierConfig(time_limit_sec=3600)
        end_date = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
        result = check_barriers(
            entry_price=0.50,
            current_price=0.50,
            side="BUY",
            outcome="YES",
            config=cfg,
            market_end_date=end_date,
            position_opened_at=datetime.utcnow() - timedelta(hours=2),
        )
        assert result.should_close
        assert result.close_type == CloseType.TIME_LIMIT

    def test_time_limit_not_triggered(self):
        """Position far from resolution → no time limit."""
        cfg = TripleBarrierConfig(time_limit_sec=3600)
        end_date = (datetime.utcnow() + timedelta(days=7)).isoformat()
        result = check_barriers(
            entry_price=0.50,
            current_price=0.50,
            side="BUY",
            outcome="YES",
            config=cfg,
            market_end_date=end_date,
            position_opened_at=datetime.utcnow(),
        )
        assert not result.should_close

    def test_time_limit_disabled(self):
        """time_limit_sec=0 → no time limit check."""
        cfg = TripleBarrierConfig(time_limit_sec=0)
        end_date = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
        result = check_barriers(
            entry_price=0.50,
            current_price=0.50,
            side="BUY",
            outcome="YES",
            config=cfg,
            market_end_date=end_date,
            position_opened_at=datetime.utcnow(),
        )
        assert not result.should_close

    def test_trailing_stop_activation_and_trigger(self):
        """Trailing stop: activates after 10% gain, triggers after 3% drawdown from peak."""
        cfg = TripleBarrierConfig(
            stop_loss_pct=0.80,
            take_profit_pct=1.0,
            trailing_stop=TrailingStop(activation_price_pct=0.10, trailing_delta_pct=0.03),
        )
        # Entry at 0.30, peak was 0.36 (20% gain → activated), now 0.35 (drawdown 0.06 from peak)
        # peak_return = 20%, current_return = 16.7%, drawdown = 3.3% → triggers
        result = check_barriers(
            entry_price=0.30,
            current_price=0.35,  # 16.7% gain
            side="BUY",
            outcome="YES",
            config=cfg,
            peak_price=0.36,  # 20% gain peak
            trailing_activated=True,
        )
        assert result.should_close
        assert result.close_type == CloseType.TRAILING_STOP

    def test_trailing_stop_not_yet_activated(self):
        """Trailing stop not activated (gain < activation threshold)."""
        cfg = TripleBarrierConfig(
            stop_loss_pct=0.80,
            take_profit_pct=1.0,
            trailing_stop=TrailingStop(activation_price_pct=0.10, trailing_delta_pct=0.03),
        )
        result = check_barriers(
            entry_price=0.30,
            current_price=0.31,  # ~3.3% gain, below 10% activation
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert not result.should_close
        assert not result.trailing_activated

    def test_trailing_stop_activates_at_threshold(self):
        """Trailing stop activates when return reaches activation_price_pct."""
        cfg = TripleBarrierConfig(
            stop_loss_pct=0.80,
            take_profit_pct=1.0,
            trailing_stop=TrailingStop(activation_price_pct=0.10, trailing_delta_pct=0.03),
        )
        # 10% gain exactly → activates but doesn't trigger yet
        result = check_barriers(
            entry_price=0.30,
            current_price=0.33,  # 10% gain
            side="BUY",
            outcome="YES",
            config=cfg,
            peak_price=0.33,
        )
        assert not result.should_close
        assert result.trailing_activated  # Now activated

    def test_stop_loss_takes_priority(self):
        """Stop loss triggers before take profit if both conditions met."""
        cfg = TripleBarrierConfig(stop_loss_pct=0.50, take_profit_pct=0.50)
        result = check_barriers(
            entry_price=0.30,
            current_price=0.12,  # 60% loss
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert result.should_close
        assert result.close_type == CloseType.STOP_LOSS

    def test_invalid_entry_price(self):
        """Entry price of 0 → no barriers checked."""
        cfg = TripleBarrierConfig()
        result = check_barriers(
            entry_price=0.0,
            current_price=0.50,
            side="BUY",
            outcome="YES",
            config=cfg,
        )
        assert not result.should_close

    def test_result_model_fields(self):
        """PositionBarrierResult has all expected fields."""
        result = PositionBarrierResult(
            should_close=True,
            close_type=CloseType.STOP_LOSS,
            reason="test",
            current_return_pct=-0.55,
            peak_return_pct=0.10,
            trailing_activated=False,
        )
        assert result.should_close
        assert result.current_return_pct == -0.55
        assert result.peak_return_pct == 0.10
