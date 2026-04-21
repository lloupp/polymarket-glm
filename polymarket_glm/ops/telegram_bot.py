"""Telegram bot for polymarket-glm — command handling and notifications.

Provides an interactive Telegram bot that responds to commands like
/status, /risk, /killswitch, /positions, and sends push alerts
for critical events.
"""

from __future__ import annotations

import enum
import logging
from typing import Callable, Dict, List, Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TelegramCommand(str, enum.Enum):
    """Supported Telegram bot commands."""
    STATUS = "status"
    RISK = "risk"
    KILLSWITCH = "killswitch"
    POSITIONS = "positions"
    UNKNOWN = "unknown"


class CommandResult(BaseModel):
    """Result of handling a Telegram command."""
    ok: bool = True
    text: str = ""


def parse_command(text: str) -> TelegramCommand:
    """Parse a Telegram message text into a command.

    Handles /command, /COMMAND, /command with args, and aliases.
    """
    if not text or not text.startswith("/"):
        return TelegramCommand.UNKNOWN

    # Extract the command word (first token after /)
    word = text.split()[0].lower().lstrip("/")

    aliases = {
        "status": TelegramCommand.STATUS,
        "risk": TelegramCommand.RISK,
        "killswitch": TelegramCommand.KILLSWITCH,
        "ks": TelegramCommand.KILLSWITCH,
        "positions": TelegramCommand.POSITIONS,
        "pos": TelegramCommand.POSITIONS,
    }

    return aliases.get(word, TelegramCommand.UNKNOWN)


def format_status(
    mode: str = "paper",
    balance: float = 0.0,
    trades: int = 0,
) -> str:
    """Format a /status response."""
    return (
        f"📊 *Polymarket GLM Status*\n"
        f"Mode: `{mode}`\n"
        f"Balance: ${balance:,.2f}\n"
        f"Trades today: {trades}"
    )


def format_risk(
    total_exposure: float = 0.0,
    max_exposure: float = 0.0,
    daily_pnl: float = 0.0,
    daily_limit: float = 0.0,
    kill_switch_active: bool = False,
) -> str:
    """Format a /risk response."""
    ks_status = "🚨 ON" if kill_switch_active else "✅ OFF"
    return (
        f"🛡️ *Risk Dashboard*\n"
        f"Exposure: ${total_exposure:,.2f} / ${max_exposure:,.2f}\n"
        f"Daily P&L: ${daily_pnl:,.2f} / ${daily_limit:,.2f}\n"
        f"Kill switch: {ks_status}"
    )


def format_positions(positions: List[Dict[str, str | float]]) -> str:
    """Format a /positions response."""
    if not positions:
        return "📋 *Positions*\nNo active positions."

    lines = ["📋 *Positions*"]
    for i, pos in enumerate(positions, 1):
        market = pos.get("market", "?")
        side = pos.get("side", "?")
        size = pos.get("size", 0)
        avg = pos.get("avg_price", 0)
        lines.append(f"{i}. {market} — {side} ${size:,.2f} @ {avg:.2f}")

    return "\n".join(lines)


class TelegramBot:
    """Interactive Telegram bot for polymarket-glm.

    Responds to commands and sends push notifications for critical events.
    Can be wired to the Engine, RiskController, and AlertManager.
    """

    def __init__(
        self,
        token: str,
        chat_id: str = "",
        status_provider: Optional[Callable[[], Dict]] = None,
        risk_provider: Optional[Callable[[], Dict]] = None,
        positions_provider: Optional[Callable[[], List[Dict]]] = None,
        killswitch_fn: Optional[Callable[[], bool]] = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.status_provider = status_provider
        self.risk_provider = risk_provider
        self.positions_provider = positions_provider
        self.killswitch_fn = killswitch_fn
        self._api_base = f"https://api.telegram.org/bot{token}"

    async def send_message(self, text: str, chat_id: Optional[str] = None) -> bool:
        """Send a message via Telegram Bot API.

        Returns True if the message was sent successfully.
        """
        target_chat = chat_id or self.chat_id
        if not target_chat:
            logger.warning("No chat_id configured, cannot send message")
            return False

        url = f"{self._api_base}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning("Telegram API error: %d %s", resp.status_code, resp.text[:200])
                return False
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    async def handle_command(self, command: TelegramCommand) -> CommandResult:
        """Handle a parsed Telegram command and return the result."""
        if command == TelegramCommand.STATUS:
            return self._handle_status()
        elif command == TelegramCommand.RISK:
            return self._handle_risk()
        elif command == TelegramCommand.KILLSWITCH:
            return await self._handle_killswitch()
        elif command == TelegramCommand.POSITIONS:
            return self._handle_positions()
        else:
            return CommandResult(
                ok=False,
                text="❓ Unknown command. Available: /status /risk /killswitch /positions",
            )

    def _handle_status(self) -> CommandResult:
        """Handle /status command."""
        if not self.status_provider:
            return CommandResult(ok=False, text="⚠️ Status provider not configured")

        data = self.status_provider()
        text = format_status(
            mode=data.get("mode", "unknown"),
            balance=data.get("balance", 0.0),
            trades=data.get("trades", 0),
        )
        return CommandResult(ok=True, text=text)

    def _handle_risk(self) -> CommandResult:
        """Handle /risk command."""
        if not self.risk_provider:
            return CommandResult(ok=False, text="⚠️ Risk provider not configured")

        data = self.risk_provider()
        text = format_risk(
            total_exposure=data.get("total_exposure", 0.0),
            max_exposure=data.get("max_exposure", 0.0),
            daily_pnl=data.get("daily_pnl", 0.0),
            daily_limit=data.get("daily_limit", 0.0),
            kill_switch_active=data.get("kill_switch_active", False),
        )
        return CommandResult(ok=True, text=text)

    async def _handle_killswitch(self) -> CommandResult:
        """Handle /killswitch command."""
        if not self.killswitch_fn:
            return CommandResult(ok=False, text="⚠️ Kill switch function not configured")

        activated = self.killswitch_fn()
        if activated:
            text = "🚨 *Kill Switch ACTIVATED*\nAll trading halted. Use /status to confirm."
        else:
            text = "✅ Kill switch deactivated. Trading resumed."
        return CommandResult(ok=True, text=text)

    def _handle_positions(self) -> CommandResult:
        """Handle /positions command."""
        if not self.positions_provider:
            return CommandResult(ok=False, text="⚠️ Positions provider not configured")

        positions = self.positions_provider()
        text = format_positions(positions)
        return CommandResult(ok=True, text=text)

    async def send_alert(self, title: str, message: str, level: str = "info") -> bool:
        """Send a push alert notification.

    Emoji prefix based on level: info=ℹ️, warning=⚠️, critical=🚨
        """
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "")
        text = f"{emoji} *{title}*\n{message}"
        return await self.send_message(text)
