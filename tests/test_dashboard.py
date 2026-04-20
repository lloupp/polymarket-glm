"""Tests for dashboard scaffold."""
import pytest
from polymarket_glm.interface.dashboard import Dashboard, DashboardData


def test_dashboard_data_defaults():
    d = DashboardData()
    assert d.balance_usd == 0.0
    assert d.positions == []
    assert d.signals == []
    assert d.risk_status == {}


def test_dashboard_data_with_values():
    d = DashboardData(
        balance_usd=5000.0,
        positions=[{"market": "test", "size": 100}],
        signals=[{"edge": 0.05}],
        risk_status={"kill_switch": False},
    )
    assert d.balance_usd == 5000.0
    assert len(d.positions) == 1
    assert len(d.signals) == 1


def test_dashboard_render_empty():
    db = Dashboard()
    output = db.render(DashboardData())
    assert "polymarket-glm" in output.lower() or "dashboard" in output.lower()


def test_dashboard_render_with_data():
    data = DashboardData(
        balance_usd=10_000.0,
        total_exposure=500.0,
        daily_pnl=75.0,
        positions=[
            {"market_id": "abc", "outcome": "Yes", "size": 100, "avg_price": 0.65},
        ],
        signals=[
            {"market_id": "xyz", "edge": 0.08, "direction": "BUY"},
        ],
        risk_status={"kill_switch": False, "daily_loss": 0.0},
    )
    db = Dashboard()
    output = db.render(data)
    assert "10,000" in output or "10000" in output
    assert "500" in output
    assert "75" in output


def test_dashboard_render_kill_switch_active():
    data = DashboardData(
        risk_status={"kill_switch": True, "daily_loss": 200.0},
    )
    db = Dashboard()
    output = db.render(data)
    assert "KILL" in output.upper() or "🚨" in output


def test_dashboard_format_position():
    db = Dashboard()
    pos = {"market_id": "m1", "outcome": "Yes", "size": 250, "avg_price": 0.72}
    line = db.format_position(pos)
    assert "m1" in line
    assert "Yes" in line
    assert "250" in line


def test_dashboard_format_signal():
    db = Dashboard()
    sig = {"market_id": "m2", "edge": 0.12, "direction": "BUY"}
    line = db.format_signal(sig)
    assert "m2" in line
    assert "0.12" in line or "12.0%" in line or "12%" in line
