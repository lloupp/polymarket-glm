"""Health check and auto-recovery system.

Provides heartbeat tracking, loop-stuck detection, error counting,
and restart decision logic for the trading loop.
"""

from __future__ import annotations

import enum
import logging
import time
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LoopStatus(str, enum.Enum):
    """Health status of the trading loop."""
    HEALTHY = "healthy"
    STUCK = "stuck"
    RECOVERING = "recovering"


class HeartbeatRecord(BaseModel):
    """A single heartbeat record from the trading loop."""
    timestamp: float = Field(default_factory=time.time)
    loop_iteration: int = 0
    mode: str = "paper"


class HealthCheck:
    """Tracks heartbeat, errors, and determines if the loop needs restart.

    Usage:
        hc = HealthCheck(max_stale_sec=300, max_consecutive_errors=5)
        # In the trading loop:
        hc.record_heartbeat(iteration=n, mode="paper")
        # On error:
        hc.record_error("connection failed")
        # Periodically check:
        if hc.should_restart():
            logger.critical("Loop unhealthy, triggering restart")
    """

    def __init__(
        self,
        max_stale_sec: int = 300,
        max_consecutive_errors: int = 5,
    ):
        self.max_stale_sec = max_stale_sec
        self.max_consecutive_errors = max_consecutive_errors
        self._last_heartbeat: Optional[HeartbeatRecord] = None
        self._start_time: Optional[float] = None
        self._heartbeat_count: int = 0
        self.consecutive_errors: int = 0
        self.total_errors: int = 0
        self._last_error_msg: Optional[str] = None

    @property
    def last_heartbeat(self) -> Optional[HeartbeatRecord]:
        return self._last_heartbeat

    @property
    def heartbeat_count(self) -> int:
        return self._heartbeat_count

    def record_heartbeat(self, iteration: int, mode: str = "paper") -> None:
        """Record a heartbeat from the trading loop."""
        now = time.time()
        if self._start_time is None:
            self._start_time = now

        self._last_heartbeat = HeartbeatRecord(
            timestamp=now,
            loop_iteration=iteration,
            mode=mode,
        )
        self._heartbeat_count += 1
        self.consecutive_errors = 0  # Reset on successful heartbeat

    def record_error(self, message: str = "") -> None:
        """Record an error. Consecutive errors accumulate until a heartbeat resets them."""
        self.consecutive_errors += 1
        self.total_errors += 1
        self._last_error_msg = message
        logger.warning(
            "Health error #%d (consecutive: %d): %s",
            self.total_errors, self.consecutive_errors, message,
        )

    def is_healthy(self) -> bool:
        """Check if the loop is healthy based on heartbeat freshness."""
        if self._last_heartbeat is None:
            return False

        elapsed = time.time() - self._last_heartbeat.timestamp
        return elapsed < self.max_stale_sec

    def should_restart(self) -> bool:
        """Determine if the loop should be restarted.

        Returns True if:
        - Heartbeat is stale (no heartbeat within max_stale_sec)
        - Consecutive errors exceed threshold
        """
        if not self.is_healthy():
            return True

        if self.consecutive_errors >= self.max_consecutive_errors:
            return True

        return False

    def uptime_sec(self) -> float:
        """Return uptime in seconds since first heartbeat."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time


def check_loop_health(hc: HealthCheck) -> LoopStatus:
    """Determine the loop status from a HealthCheck instance.

    Returns:
        HEALTHY — heartbeat fresh, errors within tolerance
        STUCK — heartbeat stale (no recent heartbeat)
        RECOVERING — heartbeat fresh but consecutive errors > 0
    """
    if not hc.is_healthy():
        return LoopStatus.STUCK

    if hc.consecutive_errors > 0:
        return LoopStatus.RECOVERING

    return LoopStatus.HEALTHY


def format_health_status(hc: HealthCheck) -> str:
    """Format health check status as a human-readable string."""
    status = check_loop_health(hc)
    uptime = hc.uptime_sec()
    hours = int(uptime // 3600)
    mins = int((uptime % 3600) // 60)

    iteration = hc.last_heartbeat.loop_iteration if hc.last_heartbeat else 0
    mode = hc.last_heartbeat.mode if hc.last_heartbeat else "unknown"

    status_emoji = {
        LoopStatus.HEALTHY: "✅",
        LoopStatus.STUCK: "🚨",
        LoopStatus.RECOVERING: "⚠️",
    }

    return (
        f"{status_emoji.get(status, '❓')} *Loop Health*\n"
        f"Status: {status.value}\n"
        f"Mode: {mode}\n"
        f"Uptime: {hours}h {mins}m\n"
        f"Iterations: {iteration}\n"
        f"Errors: {hc.consecutive_errors} consecutive / {hc.total_errors} total"
    )
