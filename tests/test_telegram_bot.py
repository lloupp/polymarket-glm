"""Tests for Telegram bot command handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from polymarket_glm.ops.telegram_bot import (
    TelegramBot,
    TelegramCommand,
    parse_command,
    format_status,
    format_risk,
    format_positions,
    CommandResult,
)


# ── parse_command ──

class TestParseCommand:
    def test_status_command(self):
        cmd = parse_command("/status")
        assert cmd == TelegramCommand.STATUS

    def test_risk_command(self):
        cmd = parse_command("/risk")
        assert cmd == TelegramCommand.RISK

    def test_killswitch_command(self):
        cmd = parse_command("/killswitch")
        assert cmd == TelegramCommand.KILLSWITCH

    def test_positions_command(self):
        cmd = parse_command("/positions")
        assert cmd == TelegramCommand.POSITIONS

    def test_unknown_command(self):
        cmd = parse_command("/foo")
        assert cmd == TelegramCommand.UNKNOWN

    def test_non_command_text(self):
        cmd = parse_command("hello world")
        assert cmd == TelegramCommand.UNKNOWN

    def test_command_with_args(self):
        cmd = parse_command("/status extra args")
        assert cmd == TelegramCommand.STATUS

    def test_command_case_insensitive(self):
        cmd = parse_command("/STATUS")
        assert cmd == TelegramCommand.STATUS

    def test_killswitch_alias_ks(self):
        cmd = parse_command("/ks")
        assert cmd == TelegramCommand.KILLSWITCH


# ── CommandResult ──

class TestCommandResult:
    def test_ok_result(self):
        result = CommandResult(ok=True, text="All good")
        assert result.ok is True
        assert result.text == "All good"

    def test_error_result(self):
        result = CommandResult(ok=False, text="Something failed")
        assert result.ok is False
        assert "failed" in result.text


# ── format_status ──

class TestFormatStatus:
    def test_basic_status(self):
        text = format_status(mode="paper", balance=10000.0, trades=5)
        assert "paper" in text
        assert "10,000.00" in text
        assert "5" in text

    def test_live_status(self):
        text = format_status(mode="live", balance=500.0, trades=42)
        assert "live" in text

    def test_zero_trades(self):
        text = format_status(mode="paper", balance=10000.0, trades=0)
        assert "0" in text


# ── format_risk ──

class TestFormatRisk:
    def test_basic_risk(self):
        text = format_risk(
            total_exposure=200.0,
            max_exposure=1500.0,
            daily_pnl=-15.0,
            daily_limit=200.0,
            kill_switch_active=False,
        )
        assert "200" in text
        assert "1,500" in text
        assert "-15" in text
        assert "OFF" in text

    def test_kill_switch_active(self):
        text = format_risk(
            total_exposure=0.0,
            max_exposure=1500.0,
            daily_pnl=0.0,
            daily_limit=200.0,
            kill_switch_active=True,
        )
        assert "ON" in text


# ── format_positions ──

class TestFormatPositions:
    def test_empty_positions(self):
        text = format_positions(positions=[])
        assert "no active" in text.lower() or "0" in text

    def test_with_positions(self):
        positions = [
            {"market": "Will X happen?", "side": "YES", "size": 10.0, "avg_price": 0.55},
        ]
        text = format_positions(positions=positions)
        assert "Will X happen?" in text
        assert "YES" in text


# ── TelegramBot ──

class TestTelegramBot:
    def test_init(self):
        bot = TelegramBot(token="test-token", chat_id="123")
        assert bot.token == "test-token"
        assert bot.chat_id == "123"

    def test_init_without_chat_id(self):
        bot = TelegramBot(token="test-token")
        assert bot.chat_id == ""

    @pytest.mark.asyncio
    async def test_send_message(self):
        bot = TelegramBot(token="test-token", chat_id="123")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

            await bot.send_message("Hello!")
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "sendMessage" in call_args[0][0]
            assert call_args[1]["json"]["chat_id"] == "123"

    @pytest.mark.asyncio
    async def test_handle_status_command(self):
        bot = TelegramBot(token="test-token", chat_id="123")
        # Provide mock status provider
        bot.status_provider = lambda: {
            "mode": "paper", "balance": 10000.0, "trades": 5
        }
        result = await bot.handle_command(TelegramCommand.STATUS)
        assert result.ok
        assert "paper" in result.text

    @pytest.mark.asyncio
    async def test_handle_killswitch_command(self):
        bot = TelegramBot(token="test-token", chat_id="123")
        bot.killswitch_fn = MagicMock(return_value=True)
        result = await bot.handle_command(TelegramCommand.KILLSWITCH)
        assert result.ok
        assert "activated" in result.text.lower() or "kill" in result.text.lower()

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self):
        bot = TelegramBot(token="test-token", chat_id="123")
        result = await bot.handle_command(TelegramCommand.UNKNOWN)
        assert not result.ok or "unknown" in result.text.lower() or "available" in result.text.lower()
