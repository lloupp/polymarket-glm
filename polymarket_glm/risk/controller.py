"""Risk controller — exposure limits, daily loss cap, circuit-breaker, kill switch.

Inspired by sterlingcrispin/nothing-ever-happens — the best risk management
implementation in the Polymarket ecosystem. Ported and improved with:
- Per-market exposure tracking
- Daily loss limit with automatic reset
- Drawdown circuit-breaker with configurable arm period and observations
- Kill switch with cooldown (manual or auto-triggered)
- Atomic file persistence (write-then-rename)
- Pre-trade drawdown check using projected balance
"""
from __future__ import annotations

import enum
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from collections import defaultdict

from pydantic import BaseModel

from polymarket_glm.config import RiskConfig

logger = logging.getLogger(__name__)


class RiskVerdict(str, enum.Enum):
    ALLOW = "allow"
    DENY_EXPOSURE = "deny_exposure"
    DENY_DAILY_LIMIT = "deny_daily_limit"
    DENY_PER_TRADE = "deny_per_trade"
    DENY_MARKET_LIMIT = "deny_market_limit"
    KILL_SWITCH = "kill_switch"
    DENY_DRAWDOWN = "deny_drawdown"


class RiskController:
    """Pre-trade risk gate with circuit-breaker and kill switch."""

    DEFAULT_KILL_SWITCH_FILE = Path("data/kill_switch.json")

    def __init__(
        self,
        config: RiskConfig | None = None,
        kill_switch_file: Path | None = None,
        initial_balance: float = 1_000.0,
    ):
        self._config = config or RiskConfig()
        self._market_exposure: dict[str, float] = defaultdict(float)
        self._daily_loss: float = 0.0
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_at: float = 0.0  # monotonic timestamp
        self._peak_balance: float = initial_balance
        self._drawdown_observations: list[tuple[float, float]] = []  # (timestamp, balance)
        self._kill_switch_file: Path = kill_switch_file or self.DEFAULT_KILL_SWITCH_FILE
        self._restore_kill_switch()

    # ── Public API ──────────────────────────────────────────────

    @property
    def config(self) -> RiskConfig:
        return self._config

    @property
    def total_exposure(self) -> float:
        return sum(self._market_exposure.values())

    @property
    def daily_loss(self) -> float:
        return self._daily_loss

    def market_exposure(self, market_id: str) -> float:
        return self._market_exposure.get(market_id, 0.0)

    def check(
        self,
        market_id: str,
        outcome: str,
        trade_usd: float,
        current_balance: float | None = None,
    ) -> tuple[RiskVerdict, str]:
        """Pre-trade risk check. Returns (verdict, reason).

        Args:
            market_id: Market identifier.
            outcome: "Yes" or "No".
            trade_usd: USD value of the proposed trade.
            current_balance: Current cash balance. If provided, the drawdown
                check uses the *projected* post-trade balance
                (current_balance - trade_usd) to detect drawdown BEFORE
                the trade executes, not after.
        """
        # 1. Kill switch check (with cooldown)
        if self._kill_switch_active:
            elapsed = time.monotonic() - self._kill_switch_at
            if elapsed < self._config.kill_switch_cooldown_sec:
                return RiskVerdict.KILL_SWITCH, f"Kill switch active: {self._kill_switch_reason}"
            # Cooldown expired — deactivate
            self._kill_switch_active = False
            self._clear_kill_switch_file()
            logger.info("Kill switch cooldown expired — re-enabling trading")

        # 2. Pre-trade drawdown check using projected balance
        if current_balance is not None:
            projected_balance = current_balance - trade_usd
            if projected_balance < 0:
                return RiskVerdict.DENY_PER_TRADE, (
                    f"Trade ${trade_usd:.2f} would result in negative balance "
                    f"(${projected_balance:.2f})"
                )
            drawdown_pct = (self._peak_balance - projected_balance) / self._peak_balance if self._peak_balance > 0 else 0
            if drawdown_pct >= self._config.drawdown_circuit_breaker_pct:
                # Check if sustained (arm period + min observations)
                now = time.monotonic()
                self._drawdown_observations.append((now, projected_balance))
                cutoff = now - self._config.drawdown_arm_period_sec
                self._drawdown_observations = [
                    (ts, b) for ts, b in self._drawdown_observations if ts >= cutoff
                ]
                if len(self._drawdown_observations) >= self._config.drawdown_min_observations:
                    self.activate_kill_switch(
                        f"Pre-trade drawdown circuit-breaker: {drawdown_pct:.1%} "
                        f"from peak ${self._peak_balance:.2f} "
                        f"(projected balance ${projected_balance:.2f})"
                    )
                    return RiskVerdict.KILL_SWITCH, (
                        f"Trade would cause {drawdown_pct:.1%} drawdown "
                        f"from peak ${self._peak_balance:.2f}"
                    )
                else:
                    return RiskVerdict.DENY_DRAWDOWN, (
                        f"Trade would cause {drawdown_pct:.1%} drawdown "
                        f"(observation {len(self._drawdown_observations)}/"
                        f"{self._config.drawdown_min_observations})"
                    )

        # 3. Per-trade limit
        if trade_usd > self._config.max_per_trade_usd:
            return RiskVerdict.DENY_PER_TRADE, (
                f"Trade ${trade_usd:.2f} exceeds per-trade limit "
                f"${self._config.max_per_trade_usd:.2f}"
            )

        # 4. Total exposure limit
        projected_total = self.total_exposure + trade_usd
        if projected_total > self._config.max_total_exposure_usd:
            return RiskVerdict.DENY_EXPOSURE, (
                f"Total exposure ${projected_total:.2f} would exceed limit "
                f"${self._config.max_total_exposure_usd:.2f}"
            )

        # 5. Per-market exposure limit
        projected_market = self.market_exposure(market_id) + trade_usd
        if projected_market > self._config.max_per_market_exposure_usd:
            return RiskVerdict.DENY_MARKET_LIMIT, (
                f"Market {market_id} exposure ${projected_market:.2f} would exceed limit "
                f"${self._config.max_per_market_exposure_usd:.2f}"
            )

        # 6. Daily loss limit
        if self._daily_loss >= self._config.daily_loss_limit_usd:
            return RiskVerdict.DENY_DAILY_LIMIT, (
                f"Daily loss ${self._daily_loss:.2f} reached limit "
                f"${self._config.daily_loss_limit_usd:.2f}"
            )

        return RiskVerdict.ALLOW, "OK"

    def record_fill(self, market_id: str, outcome: str, usd: float) -> None:
        """Record a filled trade for exposure tracking."""
        self._market_exposure[market_id] += usd
        logger.debug("Exposure update: %s += $%.2f (total: $%.2f)",
                     market_id, usd, self.total_exposure)

    def record_loss(self, amount: float) -> None:
        """Record a realized loss for daily loss tracking."""
        self._daily_loss += amount
        logger.info("Daily loss recorded: $%.2f (total: $%.2f)", amount, self._daily_loss)

    def update_balance(self, balance: float) -> None:
        """Update current balance for drawdown detection.

        This should be called AFTER a fill settles to track realized
        drawdowns. The pre-trade drawdown check is done in check()
        with the current_balance parameter.
        """
        if balance > self._peak_balance:
            self._peak_balance = balance
        self._check_drawdown(balance)

    def _check_drawdown(self, balance: float) -> None:
        """Check if drawdown exceeds circuit-breaker threshold."""
        if self._peak_balance == 0:
            return
        drawdown_pct = (self._peak_balance - balance) / self._peak_balance
        if drawdown_pct < self._config.drawdown_circuit_breaker_pct:
            return

        now = time.monotonic()
        self._drawdown_observations.append((now, balance))

        # Prune observations outside arm period
        cutoff = now - self._config.drawdown_arm_period_sec
        self._drawdown_observations = [
            (ts, b) for ts, b in self._drawdown_observations if ts >= cutoff
        ]

        if len(self._drawdown_observations) >= self._config.drawdown_min_observations:
            self.activate_kill_switch(
                f"Drawdown circuit-breaker: {drawdown_pct:.1%} from peak ${self._peak_balance:.2f}"
            )

    def activate_kill_switch(self, reason: str) -> None:
        """Manually or automatically activate the kill switch."""
        self._kill_switch_active = True
        self._kill_switch_reason = reason
        self._kill_switch_at = time.monotonic()
        self._persist_kill_switch()
        logger.critical("🚨 KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Manually deactivate the kill switch (overrides cooldown)."""
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._clear_kill_switch_file()
        logger.info("Kill switch manually deactivated")

    def _persist_kill_switch(self) -> None:
        """Write kill switch state to disk atomically (write-then-rename)."""
        try:
            self._kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "active": True,
                "reason": self._kill_switch_reason,
                "activated_at": time.time(),
            })
            # Atomic write: write to temp file, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._kill_switch_file.parent),
                suffix=".tmp",
            )
            try:
                os.write(fd, payload.encode("utf-8"))
                os.close(fd)
                os.replace(tmp_path, str(self._kill_switch_file))
            except BaseException:
                # Clean up temp file on any error
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.warning("Failed to persist kill switch: %s", exc)

    def _clear_kill_switch_file(self) -> None:
        """Remove kill switch flag file."""
        try:
            if self._kill_switch_file.exists():
                self._kill_switch_file.unlink()
        except Exception as exc:
            logger.warning("Failed to clear kill switch file: %s", exc)

    def _restore_kill_switch(self) -> None:
        """Restore kill switch state from disk on startup.

        Handles corrupted/invalid files gracefully — logs warning and
        continues with kill switch inactive rather than crashing.
        """
        try:
            if not self._kill_switch_file.exists():
                return
            raw = self._kill_switch_file.read_text()
            if not raw.strip():
                logger.warning("Kill switch file is empty — ignoring")
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning("Kill switch file is not a JSON object — ignoring")
                return
            if data.get("active") is not True:
                return
            activated_at = data.get("activated_at", 0)
            if not isinstance(activated_at, (int, float)):
                activated_at = 0
            elapsed = time.time() - activated_at
            # If within cooldown, restore the kill switch
            if elapsed < self._config.kill_switch_cooldown_sec:
                self._kill_switch_active = True
                self._kill_switch_reason = str(data.get("reason", "Persisted from previous session"))
                self._kill_switch_at = time.monotonic() - elapsed
                logger.warning(
                    "🔄 Kill switch restored from previous session: %s (%.0fs remaining)",
                    self._kill_switch_reason,
                    self._config.kill_switch_cooldown_sec - elapsed,
                )
            else:
                # Cooldown expired while we were down — clear the file
                self._clear_kill_switch_file()
                logger.info("Kill switch cooldown expired while offline — re-enabling trading")
        except json.JSONDecodeError:
            logger.warning("Kill switch file contains invalid JSON — ignoring")
        except Exception as exc:
            logger.warning("Failed to restore kill switch: %s", exc)

    def reset_daily(self) -> None:
        """Reset daily loss counter (call at start of new trading day)."""
        self._daily_loss = 0.0
        logger.info("Daily loss counter reset")

    def status(self) -> dict:
        """Return a dict summarizing current risk state."""
        return {
            "kill_switch_active": self._kill_switch_active,
            "kill_switch_reason": self._kill_switch_reason,
            "total_exposure": self.total_exposure,
            "daily_loss": self._daily_loss,
            "peak_balance": self._peak_balance,
            "markets_tracked": len(self._market_exposure),
        }
