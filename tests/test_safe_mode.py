"""Tests for safe mode feature flags — SIGNALS_ENABLED, ORDERS_ENABLED, TRADING_ENABLED.

These flags control what the system is allowed to do:
- SIGNALS_ENABLED: Can the LLM generate trading signals? (default: True)
- ORDERS_ENABLED: Can orders be submitted? (default: True)
- TRADING_ENABLED: Master switch — if False, signals AND orders are both disabled

Env var mapping:
- PGLM_SIGNALS_ENABLED
- PGLM_ORDERS_ENABLED
- PGLM_TRADING_ENABLED
"""
import os
import pytest
from polymarket_glm.config import Settings, ExecutionMode


class TestSafeModeDefaults:
    """Default settings should have all flags enabled (paper trading)."""

    def test_signals_enabled_by_default(self):
        s = Settings()
        assert s.signals_enabled is True

    def test_orders_enabled_by_default(self):
        s = Settings()
        assert s.orders_enabled is True

    def test_trading_enabled_by_default(self):
        s = Settings()
        assert s.trading_enabled is True


class TestSafeModeTradingMasterSwitch:
    """TRADING_ENABLED=False should override both signals and orders."""

    def test_trading_off_disables_signals(self):
        s = Settings(trading_enabled=False)
        assert s.effective_signals_enabled is False

    def test_trading_off_disables_orders(self):
        s = Settings(trading_enabled=False)
        assert s.effective_orders_enabled is False

    def test_trading_on_respects_individual_flags(self):
        s = Settings(trading_enabled=True, signals_enabled=False, orders_enabled=True)
        assert s.effective_signals_enabled is False
        assert s.effective_orders_enabled is True

    def test_trading_on_all_enabled(self):
        s = Settings(trading_enabled=True, signals_enabled=True, orders_enabled=True)
        assert s.effective_signals_enabled is True
        assert s.effective_orders_enabled is True


class TestSafeModeEnvVars:
    """Feature flags should be overridable via environment variables."""

    def test_pglm_trading_enabled_false(self, monkeypatch):
        monkeypatch.setenv("PGLM_TRADING_ENABLED", "false")
        s = Settings()
        assert s.trading_enabled is False

    def test_pglm_signals_enabled_false(self, monkeypatch):
        monkeypatch.setenv("PGLM_SIGNALS_ENABLED", "false")
        s = Settings()
        assert s.signals_enabled is False

    def test_pglm_orders_enabled_false(self, monkeypatch):
        monkeypatch.setenv("PGLM_ORDERS_ENABLED", "false")
        s = Settings()
        assert s.orders_enabled is False

    def test_pglm_string_truthy(self, monkeypatch):
        """String '1' and 'true' (case-insensitive) should be truthy."""
        for val in ("1", "true", "True", "TRUE", "yes"):
            monkeypatch.setenv("PGLM_TRADING_ENABLED", val)
            s = Settings()
            assert s.trading_enabled is True, f"'{val}' should be truthy"

    def test_pglm_string_falsy(self, monkeypatch):
        """String '0' and 'false' (case-insensitive) should be falsy."""
        for val in ("0", "false", "False", "FALSE", "no"):
            monkeypatch.setenv("PGLM_TRADING_ENABLED", val)
            s = Settings()
            assert s.trading_enabled is False, f"'{val}' should be falsy"


class TestSafeModeSummary:
    """status() should include safe mode flags for observability."""

    def test_status_includes_safe_mode(self):
        s = Settings(trading_enabled=False)
        assert s.safe_mode_summary()["trading_enabled"] is False
        assert s.safe_mode_summary()["effective_signals_enabled"] is False
        assert s.safe_mode_summary()["effective_orders_enabled"] is False
