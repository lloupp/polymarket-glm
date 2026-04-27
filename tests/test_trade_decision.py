"""Tests for trade decision improvements — EV calculation, BUY NO mapping, confidence tracking.

Improvements:
1. Signal has EV (expected value) field
2. SELL signals are properly mapped to BUY NO with correct EV
3. Signal includes confidence from LLM estimate
4. min_edge configurable via Settings
"""
from __future__ import annotations

import pytest

from polymarket_glm.models import Market, OrderBook, OrderBookLevel
from polymarket_glm.strategy.signal_engine import SignalEngine, Signal, SignalType


def _make_market(market_id="m1", question="Will X happen?"):
    return Market(
        market_id=market_id,
        condition_id=f"cond-{market_id}",
        question=question,
        outcomes=["Yes", "No"],
        outcome_prices=[0.60, 0.40],
        tokens=[f"{market_id}_yes", f"{market_id}_no"],
        end_date_iso="2026-12-31",
        active=True,
    )


def _make_book(bid=0.58, ask=0.62):
    return OrderBook(
        market_id="m1",
        bids=[OrderBookLevel(price=bid, size=100.0)],
        asks=[OrderBookLevel(price=ask, size=100.0)],
    )


class TestSignalEV:
    """Signal should include expected value (EV) calculation."""

    def test_signal_has_ev_field(self):
        """Signal model should have an ev field."""
        s = Signal(
            market_id="m1", condition_id="c1", question="Q",
            signal_type=SignalType.BUY, outcome="Yes",
            edge=0.10, estimated_prob=0.70, market_price=0.60,
            size_usd=100.0, ev=10.0,
        )
        assert hasattr(s, "ev")
        assert s.ev == 10.0

    def test_ev_calculation_buy_yes(self):
        """EV for BUY YES = (estimated_prob - market_price) * size_usd."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book()

        signal = engine.generate_signal(m, b, estimated_prob=0.70, balance_usd=10_000)
        assert signal is not None
        # EV = edge * size_usd = (0.70 - 0.60) * size
        expected_ev = signal.edge * signal.size_usd
        assert signal.ev == pytest.approx(expected_ev, abs=0.01)

    def test_ev_calculation_sell_yes(self):
        """EV for SELL YES (= BUY NO) = (1-estimated_prob - (1-market_price)) * size."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book(bid=0.58, ask=0.62)

        # estimated_prob=0.25, market_price~0.60 → edge=-0.35 (clamped to -0.30)
        # no_prob=0.70, no_price=0.40 → Kelly positive
        signal = engine.generate_signal(m, b, estimated_prob=0.25, balance_usd=10_000)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL
        # EV = |edge| * size_usd
        expected_ev = abs(signal.edge) * signal.size_usd
        assert signal.ev == pytest.approx(expected_ev, abs=0.01)


class TestSELLToBUYNO:
    """SELL signals should properly map to BUY NO with correct EV."""

    def test_sell_signal_outcome_is_no(self):
        """SELL YES should produce outcome='No' (buying NO)."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book()

        signal = engine.generate_signal(m, b, estimated_prob=0.25, balance_usd=10_000)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL
        assert signal.outcome == "No"

    def test_buy_signal_outcome_is_yes(self):
        """BUY signal should have outcome='Yes'."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book()

        signal = engine.generate_signal(m, b, estimated_prob=0.70, balance_usd=10_000)
        assert signal is not None
        assert signal.signal_type == SignalType.BUY
        assert signal.outcome == "Yes"


class TestConfidenceTracking:
    """Signal should include LLM confidence level."""

    def test_signal_has_confidence_field(self):
        """Signal model should have a confidence field."""
        s = Signal(
            market_id="m1", condition_id="c1", question="Q",
            signal_type=SignalType.BUY, outcome="Yes",
            edge=0.10, estimated_prob=0.70, market_price=0.60,
            size_usd=100.0, ev=10.0, confidence="high",
        )
        assert s.confidence == "high"

    def test_generate_signal_accepts_confidence(self):
        """generate_signal should accept confidence parameter."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book()

        signal = engine.generate_signal(
            m, b, estimated_prob=0.70, balance_usd=10_000,
            confidence="medium",
        )
        assert signal is not None
        assert signal.confidence == "medium"

    def test_default_confidence_is_unknown(self):
        """Default confidence should be 'unknown'."""
        engine = SignalEngine(min_edge=0.03, kelly_fraction=1.0, max_position_usd=1_000)
        m = _make_market()
        b = _make_book()

        signal = engine.generate_signal(m, b, estimated_prob=0.70, balance_usd=10_000)
        assert signal is not None
        assert signal.confidence == "unknown"


class TestMinEdgeConfigurable:
    """min_edge should be configurable via Settings."""

    def test_config_has_min_edge(self):
        """Settings should have min_edge field."""
        from polymarket_glm.config import Settings
        s = Settings()
        assert hasattr(s, "min_edge")
        assert s.min_edge == 0.05

    def test_config_min_edge_env_var(self):
        """min_edge should be overridable via PGLM_MIN_EDGE env var."""
        import os
        from polymarket_glm.config import Settings
        old = os.environ.get("PGLM_MIN_EDGE")
        try:
            os.environ["PGLM_MIN_EDGE"] = "0.10"
            s = Settings()
            assert s.min_edge == 0.10
        finally:
            if old is None:
                os.environ.pop("PGLM_MIN_EDGE", None)
            else:
                os.environ["PGLM_MIN_EDGE"] = old
