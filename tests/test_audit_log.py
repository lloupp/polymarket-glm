"""Tests for auditable signal logs — decision tracking, risk context, portfolio snapshot.

Features:
1. Signal log records: approved/rejected, reason, price, prob, edge, confidence
2. Audit log: risk_verdict, risk_reason, portfolio_snapshot, context_available
3. New DB table: audit_log for full decision audit trail
"""
from __future__ import annotations

import os
import tempfile

import pytest

from polymarket_glm.storage.database import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "test.db")
        d = Database(path)
        d.initialize()
        yield d
        d.close()


class TestAuditLog:
    """audit_log table tracks every signal decision with full context."""

    def test_audit_log_table_exists(self, db):
        """audit_log table should exist after initialization."""
        conn = db._ensure_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchall()
        assert len(rows) == 1

    def test_save_audit_entry(self, db):
        """Should be able to save an audit log entry."""
        db.save_audit(
            market_id="m1",
            question="Will X happen?",
            decision="rejected",
            reason="edge_below_threshold",
            signal_type="buy",
            edge=0.02,
            estimated_prob=0.57,
            market_price=0.55,
            confidence="low",
            ev=0.50,
            risk_verdict="allow",
            risk_reason="OK",
            portfolio_cash=800.0,
            portfolio_positions_value=200.0,
            portfolio_total=1000.0,
            context_available=False,
        )
        entries = db.get_audit(limit=10)
        assert len(entries) == 1
        e = entries[0]
        assert e["market_id"] == "m1"
        assert e["decision"] == "rejected"
        assert e["reason"] == "edge_below_threshold"
        assert e["confidence"] == "low"
        assert e["risk_verdict"] == "allow"
        assert e["portfolio_total"] == 1000.0

    def test_save_approved_audit(self, db):
        """Approved signal should also be logged."""
        db.save_audit(
            market_id="m2",
            question="Will Y happen?",
            decision="approved",
            reason="edge_above_threshold",
            signal_type="buy",
            edge=0.15,
            estimated_prob=0.75,
            market_price=0.60,
            confidence="high",
            ev=15.0,
            risk_verdict="allow",
            risk_reason="OK",
            portfolio_cash=900.0,
            portfolio_positions_value=100.0,
            portfolio_total=1000.0,
            context_available=True,
        )
        entries = db.get_audit(limit=10)
        assert len(entries) == 1
        assert entries[0]["decision"] == "approved"

    def test_audit_filter_by_market(self, db):
        """Should be able to filter audit log by market_id."""
        for i in range(5):
            db.save_audit(
                market_id=f"m{i}",
                question=f"Q{i}?",
                decision="approved" if i % 2 == 0 else "rejected",
                reason="test",
                signal_type="buy",
                edge=0.1,
                estimated_prob=0.70,
                market_price=0.60,
                confidence="medium",
                ev=5.0,
                risk_verdict="allow",
                risk_reason="OK",
                portfolio_cash=1000.0,
                portfolio_positions_value=0.0,
                portfolio_total=1000.0,
                context_available=True,
            )
        m0_entries = db.get_audit(market_id="m0", limit=10)
        assert len(m0_entries) == 1
        assert m0_entries[0]["market_id"] == "m0"

    def test_audit_includes_timestamp(self, db):
        """Each audit entry should have a created_at timestamp."""
        db.save_audit(
            market_id="m1",
            question="Q?",
            decision="approved",
            reason="test",
            signal_type="buy",
            edge=0.1,
            estimated_prob=0.70,
            market_price=0.60,
            confidence="high",
            ev=5.0,
            risk_verdict="allow",
            risk_reason="OK",
            portfolio_cash=1000.0,
            portfolio_positions_value=0.0,
            portfolio_total=1000.0,
            context_available=True,
        )
        entries = db.get_audit(limit=1)
        assert "created_at" in entries[0]
        assert entries[0]["created_at"] is not None


class TestEnrichedSignalLog:
    """signals table should include new fields: outcome, confidence, ev."""

    def test_save_signal_with_new_fields(self, db):
        """save_signal should accept outcome, confidence, ev."""
        db.save_signal(
            market_id="m1",
            signal_type="buy",
            edge=0.10,
            estimated_prob=0.70,
            market_price=0.60,
            size_usd=100.0,
            kelly_raw=0.15,
            kelly_sized=0.15,
            outcome="Yes",
            confidence="high",
            ev=10.0,
        )
        signals = db.get_signals(limit=10)
        assert len(signals) == 1
        s = signals[0]
        assert s["outcome"] == "Yes"
        assert s["confidence"] == "high"
        assert s["ev"] == 10.0

    def test_save_signal_backward_compatible(self, db):
        """save_signal should still work without new fields (backward compat)."""
        db.save_signal(
            market_id="m1",
            signal_type="buy",
            edge=0.10,
            estimated_prob=0.70,
            market_price=0.60,
            size_usd=100.0,
        )
        signals = db.get_signals(limit=10)
        assert len(signals) == 1
        # New fields should have defaults
        assert signals[0].get("outcome") is None or signals[0].get("outcome") == ""
