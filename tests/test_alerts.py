"""Tests for monitoring / alert system."""
import pytest
from polymarket_glm.monitoring.alerts import AlertManager, Alert, AlertLevel


def test_alert_level():
    assert AlertLevel.INFO.value == "info"
    assert AlertLevel.WARNING.value == "warning"
    assert AlertLevel.CRITICAL.value == "critical"


def test_alert_creation():
    a = Alert(level=AlertLevel.WARNING, title="Risk", message="Daily limit near")
    assert a.level == AlertLevel.WARNING
    assert "Risk" in a.title


def test_alert_manager_collect():
    am = AlertManager()
    am.emit(Alert(level=AlertLevel.INFO, title="Test", message="hello"))
    alerts = am.collect()
    assert len(alerts) == 1
    assert alerts[0].message == "hello"


def test_alert_manager_collect_clears():
    am = AlertManager()
    am.emit(Alert(level=AlertLevel.INFO, title="T", message="m1"))
    am.collect()
    alerts = am.collect()
    assert len(alerts) == 0


def test_alert_manager_filter_level():
    am = AlertManager()
    am.emit(Alert(level=AlertLevel.INFO, title="T1", message="info"))
    am.emit(Alert(level=AlertLevel.CRITICAL, title="T2", message="critical"))
    am.emit(Alert(level=AlertLevel.WARNING, title="T3", message="warn"))
    critical = am.collect(level=AlertLevel.CRITICAL)
    assert len(critical) == 1
    assert critical[0].level == AlertLevel.CRITICAL


def test_alert_manager_callback():
    received = []
    am = AlertManager()
    am.on_alert(lambda a: received.append(a))
    am.emit(Alert(level=AlertLevel.WARNING, title="T", message="cb"))
    assert len(received) == 1


def test_alert_manager_no_telegram_without_config():
    """AlertManager should not fail if Telegram config is empty."""
    am = AlertManager(telegram_token="", telegram_chat_id="")
    # Should not raise
    am.emit(Alert(level=AlertLevel.CRITICAL, title="Test", message="no tg"))
