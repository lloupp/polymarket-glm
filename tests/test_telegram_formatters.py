"""Tests for Telegram formatters — cycle summary, signal batch, closed positions, market resolved.

Ported from polymarket-hermes/src/notifications/telegram.ts
"""
import pytest

from polymarket_glm.monitoring.telegram_formatters import (
    CycleSummaryData,
    SignalData,
    ClosedPositionData,
    MarketResolvedData,
    format_cycle_summary,
    format_signal_message,
    format_signals_batch,
    format_closed_positions,
    format_market_resolved,
    format_critical_error,
    format_pnl_str,
)


# ── format_pnl_str ──────────────────────────────────────

class TestFormatPnlStr:
    def test_positive(self):
        assert format_pnl_str(10.50) == "+$10.50"

    def test_zero(self):
        assert format_pnl_str(0.0) == "+$0.00"

    def test_negative(self):
        assert format_pnl_str(-5.25) == "-$5.25"

    def test_large_positive(self):
        assert format_pnl_str(1234.56) == "+$1,234.56"


# ── format_cycle_summary ────────────────────────────────

class TestFormatCycleSummary:
    def test_basic_cycle(self):
        data = CycleSummaryData(
            iteration=5,
            markets_scanned=20,
            signals_generated=3,
            fills=2,
            rejections=1,
            errors=0,
            portfolio_balance=1000.0,
            open_positions=4,
            unrealized_pnl=15.50,
        )
        result = format_cycle_summary(data)
        assert "📊" in result
        assert "Iteration #5" in result
        assert "Markets scanned" in result
        assert "20" in result
        assert "Fills" in result
        assert "$1,000.00" in result
        assert "+$15.50" in result

    def test_cycle_with_errors(self):
        data = CycleSummaryData(
            iteration=10,
            errors=3,
        )
        result = format_cycle_summary(data)
        assert "⚠️" in result
        assert "3" in result

    def test_zero_values(self):
        data = CycleSummaryData()
        result = format_cycle_summary(data)
        assert "📊" in result
        assert "$0.00" in result


# ── format_signal_message ───────────────────────────────

class TestFormatSignalMessage:
    def test_basic_signal(self):
        data = SignalData(
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k by end of 2026?",
            side="BUY YES",
            price=0.65,
            edge=0.12,
            position_size_usd=25.0,
            reason="Strong upward trend",
        )
        result = format_signal_message(data)
        assert "🔔" in result
        assert "BUY YES" in result
        assert "Bitcoin" in result
        assert "0.65" in result
        assert "0.12" in result
        assert "$25.00" in result
        assert "Strong upward trend" in result

    def test_signal_no_reason(self):
        data = SignalData(
            market_slug="test",
            market_question="Test question?",
            side="BUY NO",
            price=0.30,
            edge=0.05,
            position_size_usd=10.0,
        )
        result = format_signal_message(data)
        assert "BUY NO" in result
        assert "Reason" not in result

    def test_long_question_truncated(self):
        data = SignalData(
            market_slug="x",
            market_question="A" * 100,
            side="BUY YES",
            price=0.50,
            edge=0.01,
            position_size_usd=5.0,
        )
        result = format_signal_message(data)
        # market_question[:80] in formatter
        assert len([l for l in result.split("\n") if "A" * 70 in l][0]) < 120


# ── format_signals_batch ────────────────────────────────

class TestFormatSignalsBatch:
    def test_empty(self):
        assert format_signals_batch([]) == ""

    def test_single_uses_detailed_format(self):
        signals = [SignalData(
            market_slug="test",
            market_question="Single signal?",
            side="BUY YES",
            price=0.55,
            edge=0.10,
            position_size_usd=20.0,
        )]
        result = format_signals_batch(signals)
        assert "🔔" in result
        assert "Signal Detected" in result
        assert "Single signal" in result

    def test_batch_format(self):
        signals = [
            SignalData(
                market_slug="m1",
                market_question="Market one question?",
                side="BUY YES",
                price=0.60,
                edge=0.08,
                position_size_usd=15.0,
            ),
            SignalData(
                market_slug="m2",
                market_question="Market two question?",
                side="BUY NO",
                price=0.35,
                edge=0.05,
                position_size_usd=10.0,
            ),
        ]
        result = format_signals_batch(signals)
        assert "2 Signals Detected" in result
        assert "BUY YES" in result
        assert "BUY NO" in result
        assert "edge=" in result


# ── format_closed_positions ─────────────────────────────

class TestFormatClosedPositions:
    def test_empty(self):
        assert format_closed_positions([]) == ""

    def test_single_position_profit(self):
        pos = ClosedPositionData(
            market_id="567687",
            outcome="NO",
            entry_price=0.49,
            exit_price=0.51,
            realized_pnl=1.88,
            close_reason="take_profit",
        )
        result = format_closed_positions([pos])
        assert "💼" in result
        assert "567687" in result
        assert "📈" in result
        assert "+$1.88" in result

    def test_single_position_loss(self):
        pos = ClosedPositionData(
            market_id="123456",
            outcome="YES",
            entry_price=0.70,
            exit_price=0.26,
            realized_pnl=-31.65,
            close_reason="stop_loss",
        )
        result = format_closed_positions([pos])
        assert "📉" in result
        assert "-$31.65" in result

    def test_multiple_positions(self):
        positions = [
            ClosedPositionData(
                market_id="m1",
                outcome="NO",
                entry_price=0.49,
                exit_price=0.51,
                realized_pnl=1.88,
                close_reason="take_profit",
            ),
            ClosedPositionData(
                market_id="m2",
                outcome="YES",
                entry_price=0.70,
                exit_price=0.26,
                realized_pnl=-31.65,
                close_reason="stop_loss",
            ),
        ]
        result = format_closed_positions(positions)
        assert "2 Paper Positions Closed" in result
        assert "Total PnL" in result
        # 1.88 + (-31.65) = -29.77
        assert "-$29.77" in result


# ── format_market_resolved ──────────────────────────────

class TestFormatMarketResolved:
    def test_empty(self):
        assert format_market_resolved([]) == ""

    def test_single_won(self):
        r = MarketResolvedData(
            market_question="Will Bitcoin hit $100k?",
            outcome="YES",
            winning_outcome="YES",
            entry_price=0.65,
            exit_price=1.00,
            shares=50.0,
            realized_pnl=17.50,
        )
        result = format_market_resolved([r])
        assert "🏁" in result
        assert "🏆" in result
        assert "YES" in result
        assert "+$17.50" in result

    def test_single_lost(self):
        r = MarketResolvedData(
            market_question="Will ETH hit $10k?",
            outcome="NO",
            winning_outcome="YES",
            entry_price=0.40,
            exit_price=0.00,
            shares=30.0,
            realized_pnl=-12.00,
        )
        result = format_market_resolved([r])
        assert "❌" in result
        assert "-$12.00" in result

    def test_multiple_resolved(self):
        resolved = [
            MarketResolvedData(
                market_question="Market A?",
                outcome="YES",
                winning_outcome="YES",
                entry_price=0.60,
                exit_price=1.00,
                shares=20.0,
                realized_pnl=8.00,
            ),
            MarketResolvedData(
                market_question="Market B?",
                outcome="NO",
                winning_outcome="YES",
                entry_price=0.35,
                exit_price=0.00,
                shares=25.0,
                realized_pnl=-8.75,
            ),
        ]
        result = format_market_resolved(resolved)
        assert "2 Markets Resolved" in result
        assert "✅" in result
        assert "❌" in result
        assert "Total PnL" in result
        # 8.00 + (-8.75) = -0.75
        assert "-$0.75" in result


# ── format_critical_error ───────────────────────────────

class TestFormatCriticalError:
    def test_basic_error(self):
        result = format_critical_error("Connection timeout")
        assert "🔴" in result
        assert "Critical Error" in result
        assert "Connection timeout" in result

    def test_long_error_truncated(self):
        result = format_critical_error("A" * 500)
        # Should be truncated to 400 chars + prefix
        assert len(result) < 500
