"""Web dashboard server for polymarket-glm — serves HTML + JSON over HTTP.

Designed to run on the Tailscale network for secure remote access.
Provides auto-refreshing dashboard with trading status, positions,
risk, and health information.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DashboardSnapshot(BaseModel):
    """Current state snapshot for the dashboard."""
    mode: str = "unknown"
    balance: float = 0.0
    total_exposure: float = 0.0
    daily_pnl: float = 0.0
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    kill_switch: bool = False
    loop_status: str = "unknown"
    uptime_sec: float = 0.0
    errors_total: int = 0


def generate_html(snapshot: DashboardSnapshot, refresh_sec: int = 10) -> str:
    """Generate a full HTML page for the dashboard.

    Includes auto-refresh via <meta> tag and embedded CSS.
    """
    uptime_h = int(snapshot.uptime_sec // 3600)
    uptime_m = int((snapshot.uptime_sec % 3600) // 60)

    # PnL styling
    pnl_color = "#4caf50" if snapshot.daily_pnl >= 0 else "#f44336"
    pnl_sign = "+" if snapshot.daily_pnl >= 0 else ""

    # Kill switch banner
    kill_banner = ""
    if snapshot.kill_switch:
        kill_banner = """
        <div class="alert-critical">
          🚨 KILL SWITCH ACTIVE — All trading halted
        </div>"""

    # Positions table
    pos_rows = ""
    if snapshot.positions:
        for pos in snapshot.positions:
            market = pos.get("market", pos.get("market_id", "?"))
            side = pos.get("side", pos.get("outcome", "?"))
            size = pos.get("size", 0)
            avg = pos.get("avg_price", 0)
            pos_rows += f"""
            <tr>
              <td>{market}</td>
              <td>{side}</td>
              <td>${size:,.2f}</td>
              <td>{avg:.2f}</td>
            </tr>"""
    else:
        pos_rows = '<tr><td colspan="4" style="text-align:center;color:#888;">No open positions</td></tr>'

    # Signals
    sig_rows = ""
    recent_sigs = snapshot.signals[-5:] if snapshot.signals else []
    if recent_sigs:
        for sig in recent_sigs:
            mid = sig.get("market_id", "?")[:16]
            direction = sig.get("direction", "?")
            edge = sig.get("edge", 0)
            sig_rows += f"""
            <tr>
              <td>{mid}</td>
              <td>{direction}</td>
              <td>{edge:.1%}</td>
            </tr>"""
    else:
        sig_rows = '<tr><td colspan="3" style="text-align:center;color:#888;">No recent signals</td></tr>'

    status_emoji = {"healthy": "✅", "stuck": "🚨", "recovering": "⚠️"}.get(
        snapshot.loop_status, "❓"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{refresh_sec}">
  <title>polymarket-glm Dashboard</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
      background: #1a1a2e;
      color: #e0e0e0;
      margin: 0;
      padding: 20px;
    }}
    h1 {{ color: #4fc3f7; margin-bottom: 5px; }}
    h2 {{ color: #81c784; border-bottom: 1px solid #333; padding-bottom: 5px; }}
    .card {{
      background: #16213e;
      border-radius: 8px;
      padding: 16px;
      margin: 10px 0;
    }}
    .metric {{ font-size: 1.5em; font-weight: bold; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #333; }}
    th {{ color: #aaa; font-size: 0.85em; text-transform: uppercase; }}
    .alert-critical {{
      background: #b71c1c;
      color: #fff;
      padding: 12px;
      border-radius: 6px;
      text-align: center;
      font-weight: bold;
      font-size: 1.1em;
      margin: 10px 0;
    }}
    .footer {{ color: #555; font-size: 0.8em; margin-top: 20px; }}
    .refresh {{ float: right; color: #666; font-size: 0.85em; }}
  </style>
</head>
<body>
  <h1>📊 polymarket-glm</h1>
  <span class="refresh">Auto-refresh: {refresh_sec}s</span>
  {kill_banner}

  <div class="grid">
    <div class="card">
      <div style="color:#aaa;">Mode</div>
      <div class="metric">{snapshot.mode.upper()}</div>
    </div>
    <div class="card">
      <div style="color:#aaa;">Balance</div>
      <div class="metric">${snapshot.balance:,.2f}</div>
    </div>
    <div class="card">
      <div style="color:#aaa;">Daily P&L</div>
      <div class="metric" style="color:{pnl_color}">{pnl_sign}${snapshot.daily_pnl:,.2f}</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div style="color:#aaa;">Exposure</div>
      <div>${snapshot.total_exposure:,.2f}</div>
    </div>
    <div class="card">
      <div style="color:#aaa;">Loop Status</div>
      <div>{status_emoji} {snapshot.loop_status}</div>
    </div>
    <div class="card">
      <div style="color:#aaa;">Uptime</div>
      <div>{uptime_h}h {uptime_m}m</div>
    </div>
  </div>

  <h2>📈 Open Positions ({len(snapshot.positions)})</h2>
  <table>
    <tr><th>Market</th><th>Side</th><th>Size</th><th>Avg Price</th></tr>
    {pos_rows}
  </table>

  <h2>🔔 Recent Signals</h2>
  <table>
    <tr><th>Market</th><th>Direction</th><th>Edge</th></tr>
    {sig_rows}
  </table>

  <div class="footer">
    Errors: {snapshot.errors_total} · polymarket-glm
  </div>
</body>
</html>"""


def format_snapshot_json(snapshot: DashboardSnapshot) -> str:
    """Format a snapshot as JSON string for API consumers."""
    return snapshot.model_dump_json()


class DashboardServer:
    """HTTP dashboard server for polymarket-glm.

    Serves HTML dashboard and JSON API endpoints.
    Designed to bind to Tailscale IP for secure access.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        refresh_sec: int = 10,
    ):
        self.host = host
        self.port = port
        self.refresh_sec = refresh_sec
        self._snapshot = DashboardSnapshot()
        self._provider: Optional[Callable[[], DashboardSnapshot]] = None

    def set_snapshot(self, snapshot: DashboardSnapshot) -> None:
        """Manually set the current dashboard snapshot."""
        self._snapshot = snapshot

    def get_snapshot(self) -> DashboardSnapshot:
        """Get current snapshot, refreshing from provider if set."""
        if self._provider:
            try:
                self._snapshot = self._provider()
            except Exception as exc:
                logger.error("Dashboard provider error: %s", exc)
        return self._snapshot

    def register_provider(self, provider: Callable[[], DashboardSnapshot]) -> None:
        """Register a callable that returns the latest DashboardSnapshot.

        Called on each request to get fresh data.
        """
        self._provider = provider

    async def serve(self) -> None:
        """Start the HTTP server (using aiohttp or similar).

        For now, this is a scaffold — full async serving requires
        aiohttp or Starlette integration with the trading loop.
        """
        logger.info("Dashboard server starting on %s:%d", self.host, self.port)
        # Actual serving would use aiohttp.web or starlette
        # This is the integration point for the trading loop

    def get_html(self) -> str:
        """Get the current dashboard as HTML."""
        snap = self.get_snapshot()
        return generate_html(snap, refresh_sec=self.refresh_sec)

    def get_json(self) -> str:
        """Get the current dashboard as JSON."""
        snap = self.get_snapshot()
        return format_snapshot_json(snap)
