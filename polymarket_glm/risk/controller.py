"""Risk controller — exposure limits, daily loss cap, circuit-breaker, kill switch.

Inspired by sterlingcrispin/nothing-ever-happens — the best risk management
implementation in the Polymarket ecosystem. Ported and improved with:
- Per-market exposure tracking
- Daily loss limit with automatic reset
- Drawdown circuit-breaker with configurable arm period and observations
- Kill switch with cooldown (manual or auto-triggered)
"""
from __future__ import annotations

import enum
import logging
import time
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


class RiskController:
    """Pre-trade risk gate with circuit-breaker and kill switch."""

    def __init__(self, config: RiskConfig | None = None):
        self._config = config or RiskConfig()
        self._market_exposure: dict[str, float] = defaultdict(float)
        self._daily_loss: float = 0.0
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_at: float = 0.0  # monotonic timestamp
        self._peak_balance: float = 10_000.0
        self._drawdown_observations: list[tuple[float, float]] = []  # (timestamp, balance)

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
    ) -> tuple[RiskVerdict, str]:
        """Pre-trade risk check. Returns (verdict, reason)."""
        # 1. Kill switch check (with cooldown)
        if self._kill_switch_active:
            elapsed = time.monotonic() - self._kill_switch_at
            if elapsed < self._config.kill_switch_cooldown_sec:
                return RiskVerdict.KILL_SWITCH, f"Kill switch active: {self._kill_switch_reason}"
            # Cooldown expired — deactivate
            self._kill_switch_active = False
            logger.info("Kill switch cooldown expired — re-enabling trading")

        # 2. Per-trade limit
        if trade_usd > self._config.max_per_trade_usd:
            return RiskVerdict.DENY_PER_TRADE, (
                f"Trade ${trade_usd:.2f} exceeds per-trade limit "
                f"${self._config.max_per_trade_usd:.2f}"
            )

        # 3. Total exposure limit
        projected_total = self.total_exposure + trade_usd
        if projected_total > self._config.max_total_exposure_usd:
            return RiskVerdict.DENY_EXPOSURE, (
                f"Total exposure ${projected_total:.2f} would exceed limit "
                f"${self._config.max_total_exposure_usd:.2f}"
            )

        # 4. Per-market exposure limit
        projected_market = self.market_exposure(market_id) + trade_usd
        if projected_market > self._config.max_per_market_exposure_usd:
            return RiskVerdict.DENY_MARKET_LIMIT, (
                f"Market {market_id} exposure ${projected_market:.2f} would exceed limit "
                f"${self._config.max_per_market_exposure_usd:.2f}"
            )

        # 5. Daily loss limit
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
        """Update current balance for drawdown detection."""
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
        logger.critical("🚨 KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Manually deactivate the kill switch (overrides cooldown)."""
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        logger.info("Kill switch manually deactivated")

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
