"""Telegram formatters — cycle summary, signal batch, closed positions, market resolved.

Ported from polymarket-hermes/src/notifications/telegram.ts (TypeScript)
Adapted for Python with dataclasses and polymarket-glm conventions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Data Classes ───────────────────────────────────────────


@dataclass
class CycleSummaryData:
    """Data for a completed scan cycle summary."""
    iteration: int = 0
    markets_scanned: int = 0
    signals_generated: int = 0
    fills: int = 0
    rejections: int = 0
    errors: int = 0
    portfolio_balance: float = 0.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0


@dataclass
class SignalData:
    """Data for a single trading signal."""
    market_slug: str
    market_question: str
    side: str
    price: float
    edge: float
    position_size_usd: float
    reason: str = ""


@dataclass
class ClosedPositionData:
    """Data for a closed paper position."""
    market_id: str
    outcome: str
    entry_price: float
    exit_price: float
    realized_pnl: float
    close_reason: str = ""


@dataclass
class PositionMonitorData:
    """Data for monitoring an open position — entry, edge, PnL, TP/SL."""
    market_id: str
    market_question: str = ""
    outcome: str = ""
    entry_price: float = 0.0
    current_price: float = 0.0
    edge: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    size: float = 0.0
    target_price: float | None = None
    stop_loss_price: float | None = None
    iteration: int = 0
    end_date_iso: str = ""


@dataclass
class MarketResolvedData:
    """Data for a resolved market with position outcome."""
    market_question: str
    outcome: str
    winning_outcome: str
    entry_price: float
    exit_price: float
    shares: float
    realized_pnl: float


# ── Formatter Functions ────────────────────────────────────


def format_pnl_str(pnl: float) -> str:
    """Format P&L with sign and dollar sign."""
    if pnl >= 0:
        return f"+${pnl:,.2f}"
    return f"-${abs(pnl):,.2f}"


def format_cycle_summary(data: CycleSummaryData) -> str:
    """Format a cycle completion summary for Telegram.

    Shows iteration, markets, signals, fills, rejects, errors, portfolio state.
    """
    lines: list[str] = []
    lines.append("📊 <b>Cycle Complete</b>")
    lines.append(f"🔄 Iteration #{data.iteration}")
    lines.append("")
    lines.append(f"Markets scanned: <b>{data.markets_scanned}</b>")
    lines.append(f"Signals: {data.signals_generated} | Fills: <b>{data.fills}</b> | Rejected: {data.rejections}")

    if data.errors > 0:
        lines.append(f"⚠️ Errors: <b>{data.errors}</b>")

    lines.append("")
    lines.append(f"💰 Balance: <b>${data.portfolio_balance:,.2f}</b>")
    lines.append(f"📂 Open: {data.open_positions} | Unrealized PnL: {format_pnl_str(data.unrealized_pnl)}")

    return "\n".join(lines)


def format_signal_message(data: SignalData) -> str:
    """Format a single trading signal for Telegram."""
    lines: list[str] = []
    lines.append("🔔 <b>Signal Detected</b>")
    lines.append(f"<b>{data.side}</b> — {data.market_question[:80]}")
    lines.append("")
    lines.append(f"Price: {data.price:.4f}")
    lines.append(f"Edge: {data.edge:.4f}")
    lines.append(f"Sizing: ${data.position_size_usd:.2f}")
    if data.reason:
        lines.append(f"Reason: {data.reason}")

    return "\n".join(lines)


def format_signals_batch(signals: list[SignalData]) -> str:
    """Format a batch of signals for Telegram.

    Single signal: detailed format.
    Multiple: compact one-line per signal.
    Empty: returns empty string.
    """
    if not signals:
        return ""

    if len(signals) == 1:
        return format_signal_message(signals[0])

    lines: list[str] = []
    lines.append(f"🔔 <b>{len(signals)} Signals Detected</b>")
    lines.append("")

    for signal in signals:
        lines.append(
            f"• <b>{signal.side}</b> {signal.market_question[:50]} "
            f"— edge={signal.edge:.3f} size=${signal.position_size_usd:.0f}"
        )

    return "\n".join(lines)


def format_closed_positions(positions: list[ClosedPositionData]) -> str:
    """Format closed paper positions for Telegram.

    Single: detailed with entry/exit/reason/PnL.
    Multiple: compact one-line per position with total PnL.
    Empty: returns empty string.
    """
    if not positions:
        return ""

    if len(positions) == 1:
        p = positions[0]
        pnl_emoji = "📈" if p.realized_pnl >= 0 else "📉"
        return "\n".join([
            "💼 <b>Paper Position Closed</b>",
            f"Market: {p.market_id}",
            "",
            f"Side: <b>{p.outcome}</b>",
            f"Entry: {p.entry_price:.4f} → Exit: {p.exit_price:.4f}",
            f"Reason: {p.close_reason}",
            f"{pnl_emoji} PnL: <b>{format_pnl_str(p.realized_pnl)}</b>",
        ])

    lines: list[str] = []
    lines.append(f"💼 <b>{len(positions)} Paper Positions Closed</b>")
    lines.append("")

    total_pnl = 0.0
    for p in positions:
        total_pnl += p.realized_pnl
        lines.append(
            f"• {p.outcome} {p.market_id[:20]} — {p.close_reason} | {format_pnl_str(p.realized_pnl)}"
        )

    lines.append("")
    lines.append(f"Total PnL: <b>{format_pnl_str(total_pnl)}</b>")

    return "\n".join(lines)


def format_market_resolved(resolved: list[MarketResolvedData]) -> str:
    """Format resolved market notifications for Telegram.

    Single: detailed with winning outcome and PnL.
    Multiple: compact with winner emoji per line and total PnL.
    Empty: returns empty string.
    """
    if not resolved:
        return ""

    if len(resolved) == 1:
        r = resolved[0]
        won = r.outcome == r.winning_outcome
        emoji = "🏆" if won else "❌"
        return "\n".join([
            "🏁 <b>Market Resolved</b>",
            f"{r.market_question[:80]}",
            "",
            f"Your side: <b>{r.outcome}</b> | Winner: <b>{r.winning_outcome}</b> {emoji}",
            f"Entry: {r.entry_price:.4f} → Exit: {r.exit_price:.4f}",
            f"Shares: {r.shares:.2f}",
            f"PnL: <b>{format_pnl_str(r.realized_pnl)}</b>",
        ])

    lines: list[str] = []
    lines.append(f"🏁 <b>{len(resolved)} Markets Resolved</b>")
    lines.append("")

    total_pnl = 0.0
    for r in resolved:
        total_pnl += r.realized_pnl
        won = r.outcome == r.winning_outcome
        emoji = "✅" if won else "❌"
        lines.append(
            f"• {emoji} {r.outcome} {r.market_question[:40]} → {r.winning_outcome} won | {format_pnl_str(r.realized_pnl)}"
        )

    lines.append("")
    lines.append(f"Total PnL: <b>{format_pnl_str(total_pnl)}</b>")

    return "\n".join(lines)


def format_critical_error(error: str) -> str:
    """Format a critical error message for Telegram."""
    return f"🔴 <b>Critical Error</b>\n{error[:400]}"


def format_position_monitor(positions: list[PositionMonitorData]) -> str:
    """Format open position monitoring alerts for Telegram.

    Shows entry price, current price, edge, unrealized PnL, and TP/SL status.
    Single: detailed with full info.
    Multiple: compact one-line per position.
    Empty: returns empty string.
    """
    if not positions:
        return ""

    if len(positions) == 1:
        p = positions[0]
        pnl_emoji = "📈" if p.unrealized_pnl >= 0 else "📉"
        lines: list[str] = []
        lines.append("🔭 <b>Position Monitor</b>")
        if p.market_question:
            lines.append(f"{p.market_question[:80]}")
        else:
            lines.append(f"Market: <code>{p.market_id[:16]}</code>")
        lines.append("")
        lines.append(f"Side: <b>{p.outcome}</b> | Size: {p.size:.0f} shares")
        lines.append(f"Entry: {p.entry_price:.4f} → Now: <b>{p.current_price:.4f}</b>")
        lines.append(f"Edge: {p.edge:+.4f}")
        lines.append(f"{pnl_emoji} Unrealized PnL: <b>{format_pnl_str(p.unrealized_pnl)}</b> ({p.unrealized_pnl_pct:+.1f}%)")

        # TP/SL status
        tp_str = f"{p.target_price:.4f}" if p.target_price is not None else "—"
        sl_str = f"{p.stop_loss_price:.4f}" if p.stop_loss_price is not None else "—"
        lines.append(f"🎯 TP: {tp_str} | 🛑 SL: {sl_str}")

        if p.end_date_iso:
            lines.append(f"⏰ Expiry: {p.end_date_iso[:10]}")

        lines.append(f"🔄 Iteration #{p.iteration}")
        return "\n".join(lines)

    # Multiple positions — compact format
    lines: list[str] = []
    total_pnl = 0.0
    for p in positions:
        total_pnl += p.unrealized_pnl
        pnl_sign = "+" if p.unrealized_pnl >= 0 else ""
        tp_label = f"TP={p.target_price:.2f}" if p.target_price is not None else ""
        sl_label = f"SL={p.stop_loss_price:.2f}" if p.stop_loss_price is not None else ""
        targets = f" [{tp_label}|{sl_label}]" if (tp_label or sl_label) else ""
        question = p.market_question[:35] if p.market_question else p.market_id[:16]
        lines.append(
            f"• <b>{p.outcome}</b> {question} — "
            f"e={p.entry_price:.3f}→{p.current_price:.3f} "
            f"{pnl_sign}{p.unrealized_pnl:.2f}{targets}"
        )

    lines.append("")
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    lines.append(f"{pnl_emoji} Total Unrealized: <b>{format_pnl_str(total_pnl)}</b>")
    return "\n".join(lines)
