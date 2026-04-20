"""Dashboard scaffold — terminal-based real-time dashboard for polymarket-glm.

This is a scaffold that renders dashboard data as a formatted terminal output.
Can be extended to web (FastAPI + WebSocket) or TUI (rich/textual) later.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DashboardData(BaseModel):
    """Data payload for dashboard rendering."""
    balance_usd: float = 0.0
    total_exposure: float = 0.0
    daily_pnl: float = 0.0
    positions: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    risk_status: dict[str, Any] = Field(default_factory=dict)


class Dashboard:
    """Renders polymarket-glm dashboard to terminal output.

    Scaffold for future web/TUI extension. Currently renders
    a formatted text snapshot.
    """

    SEPARATOR = "═" * 50

    def render(self, data: DashboardData) -> str:
        """Render full dashboard from data."""
        lines: list[str] = []

        # Header
        lines.append(self.SEPARATOR)
        lines.append("  📊 polymarket-glm Dashboard")
        lines.append(self.SEPARATOR)

        # Account summary
        lines.append("")
        lines.append("  ┌─ Account ─────────────────────────┐")
        pnl_sign = "+" if data.daily_pnl >= 0 else ""
        lines.append(f"  │ Balance:    ${data.balance_usd:>12,.2f}     │")
        lines.append(f"  │ Exposure:  ${data.total_exposure:>12,.2f}     │")
        lines.append(f"  │ Daily P&L: {pnl_sign}${data.daily_pnl:>11,.2f}     │")
        lines.append("  └──────────────────────────────────┘")

        # Risk status
        lines.append("")
        kill = data.risk_status.get("kill_switch", False)
        daily_loss = data.risk_status.get("daily_loss", 0.0)
        if kill:
            lines.append("  🚨 KILL SWITCH ACTIVE — All trading halted")
        else:
            lines.append("  ✅ Risk: Normal")
        if daily_loss > 0:
            lines.append(f"  📉 Daily Loss: ${daily_loss:,.2f}")

        # Open positions
        lines.append("")
        lines.append(f"  📈 Open Positions ({len(data.positions)})")
        lines.append("  " + "-" * 46)
        if data.positions:
            for pos in data.positions:
                lines.append("  " + self.format_position(pos))
        else:
            lines.append("  (no open positions)")

        # Recent signals
        lines.append("")
        lines.append(f"  🔔 Recent Signals ({len(data.signals)})")
        lines.append("  " + "-" * 46)
        if data.signals:
            for sig in data.signals[-5:]:  # last 5
                lines.append("  " + self.format_signal(sig))
        else:
            lines.append("  (no recent signals)")

        lines.append("")
        lines.append(self.SEPARATOR)
        return "\n".join(lines)

    def format_position(self, pos: dict[str, Any]) -> str:
        """Format a single position line."""
        mid = pos.get("market_id", "?")[:12]
        outcome = pos.get("outcome", "?")
        size = pos.get("size", 0)
        avg_price = pos.get("avg_price", 0)
        return f"[{mid}] {outcome} ×{size:.0f} @ {avg_price:.2f}"

    def format_signal(self, sig: dict[str, Any]) -> str:
        """Format a single signal line."""
        mid = sig.get("market_id", "?")[:12]
        edge = sig.get("edge", 0)
        direction = sig.get("direction", "?")
        edge_pct = f"{edge:.1%}" if edge else "N/A"
        return f"[{mid}] {direction} edge={edge_pct}"
