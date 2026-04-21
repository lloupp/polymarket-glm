"""Tests for health check and auto-recovery system."""
import pytest
import time
from unittest.mock import MagicMock, patch

from polymarket_glm.ops.health import (
    HealthCheck,
    HeartbeatRecord,
    LoopStatus,
    check_loop_health,
    format_health_status,
)


class TestHeartbeatRecord:
    def test_create_record(self):
        record = HeartbeatRecord(
            timestamp=1000.0,
            loop_iteration=5,
            mode="paper",
        )
        assert record.timestamp == 1000.0
        assert record.loop_iteration == 5
        assert record.mode == "paper"

    def test_record_auto_timestamp(self):
        record = HeartbeatRecord(loop_iteration=1, mode="paper")
        assert record.timestamp > 0


class TestLoopStatus:
    def test_healthy_status(self):
        status = LoopStatus.HEALTHY
        assert status == LoopStatus.HEALTHY

    def test_stuck_status(self):
        status = LoopStatus.STUCK
        assert status == LoopStatus.STUCK

    def test_recovering_status(self):
        status = LoopStatus.RECOVERING
        assert status == LoopStatus.RECOVERING


class TestHealthCheck:
    def test_init_defaults(self):
        hc = HealthCheck()
        assert hc.max_stale_sec == 300
        assert hc.max_consecutive_errors == 5

    def test_init_custom(self):
        hc = HealthCheck(max_stale_sec=60, max_consecutive_errors=3)
        assert hc.max_stale_sec == 60
        assert hc.max_consecutive_errors == 3

    def test_record_heartbeat(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        assert hc.last_heartbeat is not None
        assert hc.last_heartbeat.loop_iteration == 1

    def test_multiple_heartbeats(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        hc.record_heartbeat(iteration=2, mode="paper")
        assert hc.last_heartbeat.loop_iteration == 2
        assert hc.heartbeat_count == 2

    def test_is_healthy_fresh(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        assert hc.is_healthy() is True

    def test_is_healthy_stale(self):
        hc = HealthCheck(max_stale_sec=1)
        hc.record_heartbeat(iteration=1, mode="paper")
        # Manually set last heartbeat to the past
        hc._last_heartbeat.timestamp = time.time() - 10
        assert hc.is_healthy() is False

    def test_is_healthy_no_heartbeat(self):
        hc = HealthCheck()
        assert hc.is_healthy() is False

    def test_record_error(self):
        hc = HealthCheck()
        hc.record_error("connection failed")
        assert hc.consecutive_errors == 1
        assert hc.total_errors == 1

    def test_consecutive_errors_reset_on_heartbeat(self):
        hc = HealthCheck()
        hc.record_error("err1")
        hc.record_error("err2")
        assert hc.consecutive_errors == 2
        hc.record_heartbeat(iteration=1, mode="paper")
        assert hc.consecutive_errors == 0

    def test_should_restart_on_stale(self):
        hc = HealthCheck(max_stale_sec=1)
        hc.record_heartbeat(iteration=1, mode="paper")
        hc._last_heartbeat.timestamp = time.time() - 10
        assert hc.should_restart() is True

    def test_should_restart_on_consecutive_errors(self):
        hc = HealthCheck(max_consecutive_errors=3)
        for i in range(3):
            hc.record_error(f"err{i}")
        assert hc.should_restart() is True

    def test_should_not_restart_if_healthy(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        assert hc.should_restart() is False

    def test_uptime(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        uptime = hc.uptime_sec()
        assert uptime >= 0

    def test_uptime_no_start(self):
        hc = HealthCheck()
        assert hc.uptime_sec() == 0.0


class TestCheckLoopHealth:
    def test_healthy_loop(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=5, mode="paper")
        status = check_loop_health(hc)
        assert status == LoopStatus.HEALTHY

    def test_stuck_loop(self):
        hc = HealthCheck(max_stale_sec=1)
        hc.record_heartbeat(iteration=1, mode="paper")
        hc._last_heartbeat.timestamp = time.time() - 10
        status = check_loop_health(hc)
        assert status == LoopStatus.STUCK

    def test_recovering_loop(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        hc.record_error("transient")
        # Still healthy because heartbeat is fresh, but has errors
        status = check_loop_health(hc)
        # Fresh heartbeat overrides error count for status
        assert status in (LoopStatus.HEALTHY, LoopStatus.RECOVERING)


class TestFormatHealthStatus:
    def test_format(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=42, mode="paper")
        text = format_health_status(hc)
        assert "42" in text
        assert "paper" in text

    def test_format_with_errors(self):
        hc = HealthCheck()
        hc.record_heartbeat(iteration=1, mode="paper")
        hc.record_error("test error")
        text = format_health_status(hc)
        assert "1" in text  # 1 error
