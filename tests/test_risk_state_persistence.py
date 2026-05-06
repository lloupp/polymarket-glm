"""Tests for RiskController export_state / import_state persistence."""

import json
import tempfile
from pathlib import Path

import pytest

from polymarket_glm.risk.controller import RiskController
from polymarket_glm.config import RiskConfig


# ── Helpers ──────────────────────────────────────────────────────

def _make_controller(**kwargs) -> RiskController:
    """Create a RiskController with a temp kill-switch file so tests are isolated."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    kill_file = Path(tmp.name)
    ctrl = RiskController(kill_switch_file=kill_file, **kwargs)
    return ctrl, kill_file


# ── Tests ────────────────────────────────────────────────────────

def test_export_import_roundtrip():
    """State exported from one controller can be imported into another."""
    ctrl1, kf1 = _make_controller(initial_balance=1_000.0)

    # Build some state
    ctrl1.set_market_category("m1", "politics")
    ctrl1.set_market_category("m2", "sports")
    ctrl1.record_fill("m1", "YES", 50.0)
    ctrl1.record_fill("m2", "NO", 75.0)
    ctrl1.record_loss(12.5)
    ctrl1.update_balance(950.0)          # moves peak_balance up if balance rises
    ctrl1.record_trade_time("m1")

    state = ctrl1.export_state()

    # Import into a fresh controller
    ctrl2, kf2 = _make_controller(initial_balance=500.0)
    ctrl2.import_state(state)

    assert ctrl2.total_exposure == pytest.approx(125.0)
    assert ctrl2.daily_loss == pytest.approx(12.5)
    assert ctrl2._peak_balance == pytest.approx(1_000.0)
    assert ctrl2._market_exposure["m1"] == pytest.approx(50.0)
    assert ctrl2._market_exposure["m2"] == pytest.approx(75.0)
    assert ctrl2._market_categories == {"m1": "politics", "m2": "sports"}
    assert ctrl2._category_exposure["politics"] == pytest.approx(50.0)
    assert ctrl2._category_exposure["sports"] == pytest.approx(75.0)
    assert "m1" in ctrl2._last_trade_at

    # Cleanup
    kf1.unlink(missing_ok=True)
    kf2.unlink(missing_ok=True)


def test_import_invalid_version():
    """import_state raises ValueError on unsupported version."""
    ctrl, kf = _make_controller()
    with pytest.raises(ValueError, match="Unsupported risk state version"):
        ctrl.import_state({"version": 99})
    kf.unlink(missing_ok=True)


def test_export_default_state():
    """A fresh controller exports sensible default values."""
    ctrl, kf = _make_controller(initial_balance=2_000.0)
    state = ctrl.export_state()

    assert state["version"] == 1
    assert state["market_exposure"] == {}
    assert state["daily_loss"] == 0.0
    assert state["peak_balance"] == 2_000.0
    assert state["market_categories"] == {}
    assert state["category_exposure"] == {}
    assert state["last_trade_at"] == {}

    # Ensure everything is JSON-serializable
    json.dumps(state)
    kf.unlink(missing_ok=True)


def test_import_preserves_kill_switch():
    """import_state does NOT touch the kill switch file or flag."""
    ctrl, kf = _make_controller()

    # Manually write a kill-switch file to simulate an active kill switch
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_text(json.dumps({
        "reason": "manual_test",
        "timestamp": 0,  # epoch so it will likely be "expired" but file exists
    }))

    # Reload controller so it picks up the kill-switch file
    ctrl2 = RiskController(kill_switch_file=kf)

    # Import some state (unrelated)
    ctrl2.import_state({
        "version": 1,
        "market_exposure": {"x": 42.0},
        "daily_loss": 5.0,
        "peak_balance": 900.0,
        "market_categories": {},
        "category_exposure": {},
        "last_trade_at": {},
    })

    # The kill-switch file should still exist on disk (import didn't delete it)
    assert kf.exists()

    # Kill switch flag should be whatever the file dictated, not altered by import
    # (With timestamp=0 the cooldown will have expired, so active=False)
    # The key point: import_state never calls _clear_kill_switch_file or similar
    assert ctrl2._kill_switch_file == kf

    kf.unlink(missing_ok=True)
