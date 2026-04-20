"""Monitoring — alerts, logging setup, and Telegram notifications."""
from __future__ import annotations

import enum
import logging
import uuid
from datetime import datetime
from typing import Callable

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AlertLevel(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Alert(BaseModel):
    level: AlertLevel = AlertLevel.INFO
    title: str = ""
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])


class AlertManager:
    """Central alert system with in-memory buffer and optional Telegram push.

    Usage:
        am = AlertManager(telegram_token="...", telegram_chat_id="...")
        am.emit(Alert(level=AlertLevel.CRITICAL, title="Kill Switch", message="..."))
    """

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        buffer_size: int = 100,
    ):
        self._token = telegram_token
        self._chat_id = telegram_chat_id
        self._buffer: list[Alert] = []
        self._buffer_size = buffer_size
        self._callbacks: list[Callable[[Alert], None]] = []

    def on_alert(self, callback: Callable[[Alert], None]) -> None:
        """Register a callback for every emitted alert."""
        self._callbacks.append(callback)

    def emit(self, alert: Alert) -> None:
        """Emit an alert: buffer it, run callbacks, optionally push to Telegram."""
        self._buffer.append(alert)
        if len(self._buffer) > self._buffer_size:
            self._buffer = self._buffer[-self._buffer_size:]

        # Log
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
        }[alert.level]
        log_fn("ALERT [%s] %s: %s", alert.level.value, alert.title, alert.message)

        # Callbacks
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as exc:
                logger.debug("Alert callback error: %s", exc)

        # Telegram
        if self._token and self._chat_id and alert.level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
            self._send_telegram(alert)

    def collect(self, level: AlertLevel | None = None) -> list[Alert]:
        """Collect and clear buffered alerts, optionally filtered by level."""
        if level:
            result = [a for a in self._buffer if a.level == level]
            self._buffer = [a for a in self._buffer if a.level != level]
        else:
            result = list(self._buffer)
            self._buffer = []
        return result

    def _send_telegram(self, alert: Alert) -> None:
        """Push alert to Telegram via Bot API."""
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert.level.value, "")
        text = f"{emoji} *{alert.title}*\n{alert.message}"
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            import asyncio
            async def _send():
                async with httpx.AsyncClient() as client:
                    await client.post(url, json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    })
            # Best-effort: try to send in existing loop, else skip
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send())
            except RuntimeError:
                # No running loop — send sync
                with httpx.Client() as client:
                    client.post(url, json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    })
        except Exception as exc:
            logger.debug("Telegram send failed: %s", exc)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
