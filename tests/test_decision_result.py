"""Tests for DecisionType enum and DecisionResult model."""
import pytest
from datetime import datetime

from polymarket_glm.models import DecisionType, DecisionResult


class TestDecisionType:
    """DecisionType enum covers all paper-trading decision states."""

    def test_all_values_exist(self):
        """All 5 decision types are defined."""
        expected = {"BUY_YES", "BUY_NO", "HOLD", "REJECT", "CLOSE_POSITION"}
        actual = {dt.value for dt in DecisionType}
        assert actual == expected

    def test_buy_yes_value(self):
        assert DecisionType.BUY_YES.value == "BUY_YES"

    def test_buy_no_value(self):
        assert DecisionType.BUY_NO.value == "BUY_NO"

    def test_hold_value(self):
        assert DecisionType.HOLD.value == "HOLD"

    def test_reject_value(self):
        assert DecisionType.REJECT.value == "REJECT"

    def test_close_position_value(self):
        assert DecisionType.CLOSE_POSITION.value == "CLOSE_POSITION"

    def test_is_str_enum(self):
        """DecisionType values are strings (str enum)."""
        assert isinstance(DecisionType.BUY_YES, str)
        assert isinstance(DecisionType.HOLD, str)


class TestDecisionResult:
    """DecisionResult model captures full decision context."""

    def test_minimal_construction(self):
        """DecisionResult requires only decision field."""
        r = DecisionResult(decision=DecisionType.HOLD)
        assert r.decision == DecisionType.HOLD
        assert r.market_id == ""
        assert r.edge == 0.0
        assert r.reason == ""
        assert r.llm_state == "normal"

    def test_all_decision_types_work(self):
        """Every DecisionType can be used in DecisionResult."""
        for dt in DecisionType:
            r = DecisionResult(decision=dt)
            assert r.decision == dt

    def test_full_construction(self):
        """All fields populate correctly."""
        r = DecisionResult(
            decision=DecisionType.BUY_YES,
            market_id="0xabc123",
            question="Will X happen?",
            outcome="Yes",
            edge=0.15,
            estimated_prob=0.65,
            market_price=0.50,
            confidence=0.8,
            ev=0.15,
            size_usd=25.0,
            reason="All risk gates passed",
            risk_verdict="allow",
            risk_reason="",
            llm_source="groq",
            llm_state="normal",
            context_available=True,
            portfolio_cash=950.0,
            portfolio_positions_value=50.0,
            portfolio_total=1000.0,
            total_exposure=50.0,
        )
        assert r.decision == DecisionType.BUY_YES
        assert r.market_id == "0xabc123"
        assert r.question == "Will X happen?"
        assert r.outcome == "Yes"
        assert r.edge == 0.15
        assert r.estimated_prob == 0.65
        assert r.market_price == 0.50
        assert r.confidence == 0.8
        assert r.ev == 0.15
        assert r.size_usd == 25.0
        assert r.reason == "All risk gates passed"
        assert r.risk_verdict == "allow"
        assert r.llm_source == "groq"
        assert r.llm_state == "normal"
        assert r.context_available is True
        assert r.portfolio_cash == 950.0
        assert r.portfolio_positions_value == 50.0
        assert r.portfolio_total == 1000.0
        assert r.total_exposure == 50.0

    def test_hold_decision(self):
        """HOLD with edge below threshold."""
        r = DecisionResult(
            decision=DecisionType.HOLD,
            market_id="0xdef456",
            question="Will Y happen?",
            edge=0.02,
            reason="edge=0.0200 < min_edge=0.0500",
            llm_source="groq",
            llm_state="normal",
        )
        assert r.decision == DecisionType.HOLD
        assert r.edge < 0.05

    def test_reject_decision_with_risk_verdict(self):
        """REJECT carries risk_verdict and risk_reason."""
        r = DecisionResult(
            decision=DecisionType.REJECT,
            market_id="0xghi789",
            reason="deny_spread: Spread 150bps > max 100bps",
            risk_verdict="deny_spread",
            risk_reason="Spread 150bps > max 100bps",
        )
        assert r.decision == DecisionType.REJECT
        assert r.risk_verdict == "deny_spread"
        assert "150bps" in r.risk_reason

    def test_degraded_llm_state(self):
        """Degraded LLM state tracked correctly."""
        r = DecisionResult(
            decision=DecisionType.HOLD,
            reason="LLM fallback exhausted",
            llm_source="fallback",
            llm_state="degraded",
        )
        assert r.llm_state == "degraded"

    def test_heuristic_only_state(self):
        """Heuristic-only mode tracked."""
        r = DecisionResult(
            decision=DecisionType.HOLD,
            reason="No LLM available",
            llm_source="heuristic",
            llm_state="heuristic_only",
        )
        assert r.llm_state == "heuristic_only"
