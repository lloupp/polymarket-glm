"""Tests for Tailscale web dashboard server."""
import pytest
from unittest.mock import MagicMock, patch

from polymarket_glm.ops.web_dashboard import (
    DashboardServer,
    DashboardSnapshot,
    generate_html,
    format_snapshot_json,
)


class TestDashboardSnapshot:
    def test_create_snapshot(self):
        snap = DashboardSnapshot(
            mode="paper",
            balance=10000.0,
            total_exposure=200.0,
            daily_pnl=-15.0,
            positions=[],
            signals=[],
            kill_switch=False,
            loop_status="healthy",
            uptime_sec=3600.0,
            errors_total=2,
        )
        assert snap.mode == "paper"
        assert snap.balance == 10000.0
        assert snap.loop_status == "healthy"

    def test_snapshot_defaults(self):
        snap = DashboardSnapshot()
        assert snap.mode == "unknown"
        assert snap.balance == 0.0
        assert snap.positions == []
        assert snap.kill_switch is False


class TestGenerateHtml:
    def test_basic_html(self):
        snap = DashboardSnapshot(
            mode="paper",
            balance=10000.0,
            total_exposure=200.0,
            daily_pnl=50.0,
            positions=[],
            signals=[],
            kill_switch=False,
            loop_status="healthy",
            uptime_sec=3600.0,
            errors_total=0,
        )
        html = generate_html(snap)
        assert "<!DOCTYPE html>" in html
        assert "PAPER" in html
        assert "10,000" in html
        assert "auto-refresh" in html.lower() or "refresh" in html.lower()

    def test_kill_switch_warning(self):
        snap = DashboardSnapshot(kill_switch=True)
        html = generate_html(snap)
        assert "KILL SWITCH" in html or "kill" in html.lower()

    def test_html_with_positions(self):
        snap = DashboardSnapshot(
            positions=[{"market": "Test?", "side": "YES", "size": 10.0, "avg_price": 0.55}],
        )
        html = generate_html(snap)
        assert "Test?" in html

    def test_html_refresh_meta(self):
        snap = DashboardSnapshot()
        html = generate_html(snap, refresh_sec=30)
        assert "30" in html


class TestFormatSnapshotJson:
    def test_json_output(self):
        snap = DashboardSnapshot(
            mode="paper",
            balance=5000.0,
        )
        json_str = format_snapshot_json(snap)
        assert '"mode"' in json_str
        assert '"paper"' in json_str
        assert '"5000' in json_str or '"balance"' in json_str


class TestDashboardServer:
    def test_init(self):
        server = DashboardServer(host="100.64.0.1", port=8080)
        assert server.host == "100.64.0.1"
        assert server.port == 8080

    def test_init_defaults(self):
        server = DashboardServer()
        assert server.host == "127.0.0.1"
        assert server.port == 8080

    def test_set_snapshot(self):
        server = DashboardServer()
        snap = DashboardSnapshot(mode="paper", balance=1000.0)
        server.set_snapshot(snap)
        assert server._snapshot.mode == "paper"

    def test_get_snapshot_default(self):
        server = DashboardServer()
        snap = server.get_snapshot()
        assert snap.mode == "unknown"

    def test_register_provider(self):
        server = DashboardServer()
        provider = lambda: DashboardSnapshot(mode="live", balance=500.0)
        server.register_provider(provider)
        assert server._provider is not None
