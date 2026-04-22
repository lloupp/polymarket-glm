"""Daily report — generate and send Telegram portfolio summary.

Produces a formatted daily report with:
- Portfolio P&L (realized + unrealized)
- Open positions with mark-to-market
- Recently settled markets
- Risk status (drawdown, kill switch)
- Trade statistics
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from polymarket_glm.execution.portfolio_tracker import PortfolioSummary
from polymarket_glm.execution.settlement_tracker import SettlementTracker

logger = logging.getLogger(__name__)


def format_daily_report(
    portfolio: PortfolioSummary,
    settlement: SettlementTracker,
    total_trades: int = 0,
    total_signals: int = 0,
    total_rejections: int = 0,
    daily_loss_limit: float = 0.0,
    kill_switch_active: bool = False,
) -> str:
    """Format a daily portfolio report for Telegram.

    Args:
        portfolio: Current portfolio summary with P&L.
        settlement: Settlement tracker with realized P&L history.
        total_trades: Total filled trades.
        total_signals: Total signals generated.
        total_rejections: Total risk rejections.
        daily_loss_limit: Configured daily loss limit.
        kill_switch_active: Whether kill switch is active.

    Returns:
        Formatted report string.
    """
    lines: list[str] = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Header
    lines.append(f"📊 **Daily Report** — {now}")
    lines.append("")

    # Portfolio overview
    lines.append("💼 **Portfolio**")
    lines.append(f"  Balance: ${portfolio.balance_usd:,.2f}")
    lines.append(f"  Unrealized P&L: ${portfolio.unrealized_pnl:,.2f} ({portfolio.unrealized_pnl_pct:.1f}%)")
    lines.append(f"  Realized P&L: ${settlement.total_realized_pnl:,.2f}")
    lines.append(f"  Total P&L: ${portfolio.unrealized_pnl + settlement.total_realized_pnl:,.2f}")
    lines.append(f"  Open Positions: {portfolio.num_open_positions}")
    lines.append("")

    # Open positions
    if portfolio.positions:
        lines.append("📈 **Open Positions**")
        for p in portfolio.positions[:10]:  # cap at 10
            emoji = "🟢" if p.is_profitable else "🔴"
            lines.append(
                f"  {emoji} {p.market_id[:16]}/{p.outcome} "
                f"×{p.size:.0f} @${p.avg_price:.2f} → ${p.current_price:.2f} "
                f"({p.unrealized_pnl:+.2f})"
            )
        if len(portfolio.positions) > 10:
            lines.append(f"  ... +{len(portfolio.positions) - 10} more")
        lines.append("")

    # Recent settlements (last 24h)
    recent = [
        s for s in settlement.settlement_history
        if False  # no timestamp on settlement yet, show all
    ]
    if settlement.settlement_history:
        lines.append("🏛️ **Settlements**")
        for s in settlement.settlement_history[-5:]:
            emoji = "✅" if s.is_profitable else "❌"
            lines.append(
                f"  {emoji} {s.market_id[:16]}/{s.outcome} "
                f"→ {s.winning_outcome} wins | P&L ${s.realized_pnl:+.2f}"
            )
        lines.append("")

    # Risk status
    lines.append("🛡️ **Risk**")
    lines.append(f"  Exposure: ${portfolio.total_cost_basis:,.2f}")
    if daily_loss_limit > 0:
        lines.append(f"  Daily Limit: ${daily_loss_limit:,.2f}")
    if kill_switch_active:
        lines.append("  ⚠️ **KILL SWITCH ACTIVE**")
    lines.append("")

    # Stats
    lines.append("🔢 **Stats**")
    lines.append(f"  Signals: {total_signals} | Fills: {total_trades} | Rejected: {total_rejections}")

    return "\n".join(lines)


def format_pnl_alert(
    portfolio: PortfolioSummary,
    threshold_pct: float = 5.0,
) -> str | None:
    """Generate a P&L alert if unrealized P&L exceeds threshold.

    Returns:
        Alert message string, or None if no alert needed.
    """
    if abs(portfolio.unrealized_pnl_pct) < threshold_pct:
        return None

    direction = "📈" if portfolio.unrealized_pnl > 0 else "📉"
    return (
        f"{direction} P&L Alert: ${portfolio.unrealized_pnl:+,.2f} "
        f"({portfolio.unrealized_pnl_pct:+.1f}%) "
        f"across {portfolio.num_open_positions} positions"
    )
