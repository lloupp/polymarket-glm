#!/usr/bin/env python3
"""Run the Telegram bot with long-polling.

Usage:
    python scripts/run_bot.py

Reads config from .env (PGLM_TELEGRAM_ALERT_TOKEN, PGLM_TELEGRAM_ALERT_CHAT_ID).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from polymarket_glm.config import Settings
from polymarket_glm.ops.telegram_bot import TelegramBot, parse_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("bot-runner")


async def run_polling(bot: TelegramBot) -> None:
    """Long-poll Telegram getUpdates and dispatch commands."""
    api_base = f"https://api.telegram.org/bot{bot.token}"
    last_update_id = 0

    logger.info("🤖 Bot started — polling for updates...")

    # Send startup message
    await bot.send_message("🟢 *Polymarket GLM Bot online!*\nCommands: /status /risk /killswitch /positions")

    while True:
        try:
            url = f"{api_base}/getUpdates"
            params = {
                "offset": last_update_id + 1,
                "timeout": 30,  # long-poll timeout
                "allowed_updates": json.dumps(["message"]),
            }

            import httpx
            async with httpx.AsyncClient(timeout=35) as client:
                resp = await client.get(url, params=params)

            if resp.status_code != 200:
                logger.warning("getUpdates error: %d", resp.status_code)
                await asyncio.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                logger.warning("getUpdates not ok: %s", data.get("description", ""))
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                last_update_id = update["update_id"]
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id", "")
                username = message.get("from", {}).get("username", "unknown")

                if not text:
                    continue

                logger.info("Message from @%s (chat %s): %s", username, chat_id, text)

                # Only respond to the configured chat
                if str(chat_id) != str(bot.chat_id):
                    logger.info("Ignoring message from unauthorized chat %s", chat_id)
                    continue

                cmd = parse_command(text)
                result = await bot.handle_command(cmd)

                if result.text:
                    await bot.send_message(result.text)

        except asyncio.CancelledError:
            logger.info("Bot polling cancelled — shutting down.")
            break
        except Exception:
            logger.exception("Unexpected error in polling loop")
            await asyncio.sleep(10)


def main() -> None:
    settings = Settings()

    if not settings.telegram_alert_token:
        logger.error("PGLM_TELEGRAM_ALERT_TOKEN not set! Check .env")
        sys.exit(1)

    if not settings.telegram_alert_chat_id:
        logger.error("PGLM_TELEGRAM_ALERT_CHAT_ID not set! Check .env")
        sys.exit(1)

    logger.info("Config: mode=%s chat_id=%s", settings.execution_mode.value, settings.telegram_alert_chat_id)

    # Wire up basic providers for standalone mode
    def status_provider() -> dict:
        return {
            "mode": settings.execution_mode.value,
            "balance": settings.paper_balance_usd,
            "trades": 0,
        }

    def risk_provider() -> dict:
        return {
            "total_exposure": 0.0,
            "max_exposure": settings.risk.max_total_exposure_usd,
            "daily_pnl": 0.0,
            "daily_limit": settings.risk.daily_loss_limit_usd,
            "kill_switch_active": False,
        }

    def positions_provider() -> list:
        return []

    kill_switch_state = {"active": False}

    def killswitch_fn() -> bool:
        kill_switch_state["active"] = not kill_switch_state["active"]
        return kill_switch_state["active"]

    bot = TelegramBot(
        token=settings.telegram_alert_token,
        chat_id=settings.telegram_alert_chat_id,
        status_provider=status_provider,
        risk_provider=risk_provider,
        positions_provider=positions_provider,
        killswitch_fn=killswitch_fn,
    )

    try:
        asyncio.run(run_polling(bot))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
