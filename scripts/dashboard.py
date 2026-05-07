#!/usr/bin/env python3
"""Lightweight HTTP dashboard for polymarket-glm simulation status.

Serves a single HTML page with auto-refresh showing:
- Wallet balance + P&L
- Open positions
- Recent signals/trades
- Risk state (kill switch, drawdown)
- Scorer dispatcher stats

Usage:
    python scripts/dashboard.py [--port 8080] [--db polymarket_glm.db]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


DB_PATH = str(PROJECT_ROOT / "polymarket_glm.db")
PORT = 8080


def _sf(value: object, fmt: str = ".2f") -> str:
    """Safe float formatting — handles None, str, and numeric values."""
    if value is None:
        return f"{0:{fmt}}"
    try:
        return f"{float(value):{fmt}}"
    except (ValueError, TypeError):
        return f"{0:{fmt}}"


def _query_db(db_path: str) -> dict:
    """Extract dashboard data from SQLite."""
    result: dict = {
        "signals": [],
        "trades": [],
        "audit": [],
        "counts": {"signals": 0, "trades": 0, "audit": 0},
    }
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Recent signals (last 50)
        try:
            cur.execute(
                "SELECT * FROM signals ORDER BY rowid DESC LIMIT 50"
            )
            result["signals"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM signals")
            result["counts"]["signals"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        # Recent trades (last 50)
        try:
            cur.execute(
                "SELECT * FROM trades ORDER BY rowid DESC LIMIT 50"
            )
            result["trades"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM trades")
            result["counts"]["trades"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        # Recent audit log (last 50)
        try:
            cur.execute(
                "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 50"
            )
            result["audit"] = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM audit_log")
            result["counts"]["audit"] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception as exc:
        result["error"] = str(exc)

    return result


def _load_state() -> dict:
    """Load paper wallet state if available."""
    state_path = PROJECT_ROOT / "data" / "wallet_state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {}


def _load_kill_switch() -> dict:
    """Load kill switch state if available."""
    ks_path = PROJECT_ROOT / "data" / "kill_switch.json"
    if ks_path.exists():
        try:
            return json.loads(ks_path.read_text())
        except Exception:
            pass
    return {"active": False}


def _build_html(data: dict, state: dict, kill_switch: dict) -> str:
    """Render dashboard HTML."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    signals = data.get("signals", [])
    trades = data.get("trades", [])
    audit = data.get("audit", [])
    counts = data.get("counts", {})
    error = data.get("error")
    balance = state.get("balance_usd", 0)
    positions = state.get("positions", [])
    total_fees = state.get("total_fees_paid", 0)

    # Compute portfolio value
    pos_value = sum(
        float(p.get("entry_price", 0) or 0) * float(p.get("size", 0) or 0)
        for p in positions
    )
    bal_val = float(balance) if balance not in (None, "N/A") else 0
    total_value = bal_val + pos_value

    # Kill switch
    ks_active = kill_switch.get("active", False)
    ks_reason = kill_switch.get("reason", "")
    ks_time = kill_switch.get("activated_at", "")

    # Signal stats
    buy_signals = [s for s in signals if "BUY" in str(s.get("signal_type", "") or "").upper()]
    sell_signals = [s for s in signals if "SELL" in str(s.get("signal_type", "") or "").upper()]

    # Trade P&L
    realized_pnl = 0.0
    for t in trades:
        v = t.get("realized_pnl")
        if v is not None:
            try:
                realized_pnl += float(v)
            except (ValueError, TypeError):
                pass

    # Audit verdicts
    allow_count = sum(1 for a in audit if a.get("verdict") == "ALLOW")
    deny_count = sum(1 for a in audit if a.get("verdict", "").startswith("DENY"))

    # Position rows
    pos_rows = ""
    for p in positions:
        pos_rows += f"""
        <tr>
            <td>{p.get('market_id', '')[:12]}</td>
            <td>{p.get('outcome', '') or ''}</td>
            <td>{_sf(p.get('size'), '.2f')}</td>
            <td>{_sf(p.get('entry_price'), '.4f')}</td>
            <td>{p.get('side', '') or ''}</td>
        </tr>"""

    # Recent signals rows (last 10)
    sig_rows = ""
    for s in signals[:10]:
        sig_rows += f"""
        <tr>
            <td>{str(s.get('created_at', ''))[:19]}</td>
            <td>{s.get('signal_type', '') or ''}</td>
            <td>{str(s.get('market_id', ''))[:12]}</td>
            <td>{_sf(s.get('edge'), '.4f')}</td>
            <td>{_sf(s.get('confidence'), '.2f')}</td>
        </tr>"""

    # Recent trades rows (last 10)
    trade_rows = ""
    for t in trades[:10]:
        trade_rows += f"""
        <tr>
            <td>{str(t.get('created_at', ''))[:19]}</td>
            <td>{t.get('side', '') or ''}</td>
            <td>{str(t.get('market_id', ''))[:12]}</td>
            <td>{_sf(t.get('price'), '.4f')}</td>
            <td>{_sf(t.get('size'), '.2f')}</td>
            <td>{_sf(t.get('realized_pnl'), '.2f')}</td>
        </tr>"""

    # Audit rows (last 15)
    audit_rows = ""
    for a in audit[:15]:
        verdict = a.get("decision", "") or a.get("risk_verdict", "") or ""
        v_color = "#4ade80" if verdict == "ALLOW" else "#f87171"
        audit_rows += f"""
        <tr>
            <td>{str(a.get('created_at', '') or '')[:19]}</td>
            <td style="color:{v_color}">{verdict}</td>
            <td>{str(a.get('question', '') or '')[:40]}</td>
            <td>{a.get('llm_source', '') or ''}</td>
            <td>{_sf(a.get('edge'), '.4f')}</td>
        </tr>"""

    ks_badge = (
        '<span style="background:#f87171;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold">KILL SWITCH ACTIVE</span>'
        if ks_active
        else '<span style="background:#4ade80;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold">OK</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>polymarket-glm Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'SF Mono',Monaco,Consolas,monospace; background:#0d1117; color:#c9d1d9; padding:20px; }}
h1 {{ color:#58a6ff; margin-bottom:10px; font-size:1.4em; }}
h2 {{ color:#8b949e; margin:20px 0 10px; font-size:1.1em; border-bottom:1px solid #21262d; padding-bottom:5px; }}
.card {{ background:#161b22; border:1px solid #21262d; border-radius:8px; padding:16px; margin-bottom:16px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
.stat {{ text-align:center; }}
.stat .value {{ font-size:1.8em; font-weight:bold; color:#58a6ff; }}
.stat .label {{ font-size:0.8em; color:#8b949e; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; font-size:0.85em; }}
th {{ text-align:left; color:#8b949e; padding:6px 8px; border-bottom:1px solid #21262d; }}
td {{ padding:6px 8px; border-bottom:1px solid #161b22; }}
.green {{ color:#4ade80; }} .red {{ color:#f87171; }} .yellow {{ color:#fbbf24; }}
.refresh {{ color:#8b949e; font-size:0.75em; float:right; }}
</style>
</head>
<body>
<h1>polymarket-glm Dashboard {ks_badge}</h1>
<span class="refresh">Last update: {now} | Auto-refresh: 30s</span>

<div class="cards">
        <div class="card stat"><div class="value">${_sf(total_value, ',.2f')}</div><div class="label">Portfolio Value</div></div>
  <div class="card stat"><div class="value">${balance}</div><div class="label">Cash</div></div>
        <div class="card stat"><div class="value {'green' if realized_pnl >= 0 else 'red'}">${_sf(realized_pnl, ',.2f')}</div><div class="label">Realized P&L</div></div>
  <div class="card stat"><div class="value">{len(positions)}</div><div class="label">Open Positions</div></div>
  <div class="card stat"><div class="value">{counts.get('signals',0)}</div><div class="label">Total Signals</div></div>
  <div class="card stat"><div class="value">{counts.get('trades',0)}</div><div class="label">Total Trades</div></div>
</div>

<h2>Risk State</h2>
<div class="card">
  <p>Kill Switch: <b>{'ACTIVE - ' + ks_reason if ks_active else 'INACTIVE'}</b></p>
  <p>Activated at: {ks_time or 'N/A'}</p>
        <p>Total Fees Paid: ${_sf(total_fees, ',.2f')}</p>
</div>

<h2>Open Positions</h2>
<div class="card">
<table>
<tr><th>Market</th><th>Outcome</th><th>Size</th><th>Entry Price</th><th>Side</th></tr>
{pos_rows if pos_rows else '<tr><td colspan="5" style="text-align:center;color:#8b949e">No open positions</td></tr>'}
</table>
</div>

<h2>Recent Signals (last 10)</h2>
<div class="card">
<table>
<tr><th>Time</th><th>Type</th><th>Market</th><th>Edge</th><th>Confidence</th></tr>
{sig_rows if sig_rows else '<tr><td colspan="5" style="text-align:center;color:#8b949e">No signals yet</td></tr>'}
</table>
</div>

<h2>Recent Trades (last 10)</h2>
<div class="card">
<table>
<tr><th>Time</th><th>Side</th><th>Market</th><th>Price</th><th>Size</th><th>P&L</th></tr>
{trade_rows if trade_rows else '<tr><td colspan="6" style="text-align:center;color:#8b949e">No trades yet</td></tr>'}
</table>
</div>

<h2>Audit Log (last 15)</h2>
<div class="card">
<table>
<tr><th>Time</th><th>Verdict</th><th>Market</th><th>Source</th><th>Edge</th></tr>
{audit_rows if audit_rows else '<tr><td colspan="5" style="text-align:center;color:#8b949e">No audit entries</td></tr>'}
</table>
</div>

<p style="color:#484f58;font-size:0.7em;margin-top:20px">polymarket-glm deterministic scorer | No LLMs used | Powered by Open-Meteo + Heuristics</p>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve dashboard HTML."""

    def do_GET(self) -> None:
        try:
            if self.path in ("/", "/dashboard", "/index.html"):
                data = _query_db(DB_PATH)
                state = _load_state()
                ks = _load_kill_switch()
                html = _build_html(data, state, ks)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            elif self.path == "/api/status":
                data = _query_db(DB_PATH)
                state = _load_state()
                ks = _load_kill_switch()
                payload = {"db": data, "wallet": state, "kill_switch": ks}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload, default=str).encode())
            else:
                self.send_response(404)
                self.end_headers()
        except Exception as exc:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Dashboard error: {exc}".encode())

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress request logging


def main() -> None:
    parser = argparse.ArgumentParser(description="polymarket-glm dashboard")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--db", type=str, default=DB_PATH)
    parser.add_argument("--bind", type=str, default="0.0.0.0", help="Bind address (use Tailscale IP for private access)")
    args = parser.parse_args()

    # Set module-level DB_PATH so handler can read it
    globals()["DB_PATH"] = args.db

    server = HTTPServer((args.bind, args.port), DashboardHandler)
    print(f"polymarket-glm dashboard → http://{args.bind}:{args.port}")
    print(f"  DB: {args.db}")
    print(f"  API: http://{args.bind}:{args.port}/api/status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
