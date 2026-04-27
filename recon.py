#!/usr/bin/env python3
"""Reconciliation script for polymarket-glm paper trading."""
import sqlite3
import json

conn = sqlite3.connect('polymarket_glm.db')
cursor = conn.cursor()

# All trades with proper formatting
cursor.execute('SELECT trade_id, market_id, side, outcome, price, size, fee, total_cost, created_at FROM trades ORDER BY created_at')
trades = cursor.fetchall()
print(f"Total: {len(trades)} trades\n")

running_balance = 10000.0
total_fees = 0.0
total_bought = 0.0

for t in trades:
    trade_id, market_id, side, outcome, price, size, fee, total_cost, created_at = t
    notional = price * size
    actual_fee = price * size * 0.01  # 1% = 100 bps
    actual_total_cost = notional + actual_fee

    if side == "buy":
        running_balance -= actual_total_cost
        total_fees += actual_fee
        total_bought += notional

    print(f"{created_at} | {side.upper():4s} {outcome:3s} p={price:.4f} sz={size:.1f} notional=${notional:.2f} fee=${actual_fee:.2f} total=${actual_total_cost:.2f} | bal=${running_balance:.2f} | mkt={market_id[:16]}")

print(f"\n=== SUMMARY ===")
print(f"Total bought (notional): ${total_bought:.2f}")
print(f"Total fees: ${total_fees:.2f}")
print(f"Total spent (notional+fees): ${total_bought + total_fees:.2f}")
print(f"Remaining balance: ${running_balance:.2f}")
print(f"Initial balance: $10,000.00")
print(f"Balance reduction: ${10000 - running_balance:.2f}")

# Group by market for position analysis
cursor.execute("SELECT market_id, outcome, SUM(price * size) as notional, SUM(size) as total_size, COUNT(*) as cnt FROM trades WHERE side='buy' GROUP BY market_id, outcome ORDER BY market_id, outcome")
positions = cursor.fetchall()
print(f"\n=== POSITION AGGREGATION (from trades DB) ===")
for p in positions:
    market_id, outcome, notional, total_size, cnt = p
    avg_price = notional / total_size if total_size > 0 else 0
    print(f"  mkt={market_id[:16]} outcome={outcome} size={total_size:.1f} notional=${notional:.2f} avg_price={avg_price:.4f} trades={cnt}")

# Signals
cursor.execute("SELECT market_id, signal_type, edge, estimated_prob, market_price, size_usd, kelly_raw, kelly_sized FROM signals ORDER BY created_at")
signals = cursor.fetchall()
print(f"\n=== SIGNALS WITH SIZE_USD ===")
total_size_usd = 0
for s in signals:
    market_id, signal_type, edge, est_prob, mkt_price, size_usd, kelly_raw, kelly_sized = s
    print(f"  {signal_type:4s} mkt={market_id[:16]} edge={edge:.4f} est={est_prob:.4f} mkt={mkt_price:.4f} size_usd={size_usd} kelly_raw={kelly_raw} kelly_sized={kelly_sized}")
    if size_usd:
        total_size_usd += size_usd

print(f"\n  Total size_usd from signals: ${total_size_usd:.2f}")

conn.close()
