"""CLI interface for polymarket-glm."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from polymarket_glm.config import Settings, ExecutionMode
from polymarket_glm.engine import Engine
from polymarket_glm.ingestion.market_fetcher import MarketFilter


def cmd_status(engine: Engine, args: argparse.Namespace) -> None:
    """Show engine status."""
    status = engine.status()
    print("═══ polymarket-glm status ═══")
    print(f"  Mode:           {status['mode']}")
    print(f"  Balance:        ${status['balance_usd']:,.2f}")
    print(f"  Total Exposure: ${status['total_exposure']:,.2f}")
    print(f"  Daily Loss:     ${status['daily_loss']:,.2f}")
    print(f"  Kill Switch:    {'🚨 ACTIVE' if status['kill_switch_active'] else '✅ Clear'}")


def cmd_scan(engine: Engine, args: argparse.Namespace) -> None:
    """Scan and list markets."""
    filt = MarketFilter(
        min_volume_usd=args.min_volume,
        max_markets=args.limit,
        exclude_sports=args.no_sports,
    )
    markets = asyncio.run(engine.scan_markets(filt))
    if not markets:
        print("No markets found.")
        return
    print(f"═══ {len(markets)} markets found ═══")
    for m in markets:
        price_str = ", ".join(f"{o}={p:.2f}" for o, p in zip(m.outcomes, m.outcome_prices))
        print(f"  [{m.market_id}] {m.question}")
        print(f"    Prices: {price_str}  Vol: ${m.volume:,.0f}")


def cmd_trade(engine: Engine, args: argparse.Namespace) -> None:
    """Execute a manual trade (paper mode)."""
    from polymarket_glm.strategy.signal_engine import Signal, SignalType
    from polymarket_glm.models import Side

    sig = Signal(
        market_id=args.market,
        condition_id="manual",
        question="Manual trade",
        signal_type=SignalType.BUY if args.side == "buy" else SignalType.SELL,
        outcome=args.outcome,
        edge=0.0,  # manual — no edge calc
        estimated_prob=args.price,
        market_price=args.price,
        size_usd=args.size,
        target_price=args.price,
    )
    result = engine.process_signal_sync(sig, price=args.price)
    if result.filled:
        print(f"✅ Filled: {result.side.value} {result.outcome}@{result.price:.2f} ×{result.size:.0f}")
        print(f"   Fee: ${result.fee:.4f}  Total: ${result.total_cost:.2f}")
    else:
        print(f"❌ Not filled: {result.reason}")


def cmd_risk(engine: Engine, args: argparse.Namespace) -> None:
    """Show risk status."""
    rc = engine.risk_controller
    print("═══ Risk Status ═══")
    print(f"  Total Exposure:  ${rc.total_exposure:,.2f}")
    print(f"  Daily Loss:      ${rc.daily_loss:,.2f}")
    print(f"  Kill Switch:     {'🚨 ACTIVE' + rc._kill_switch_reason if rc._kill_switch_active else '✅ Clear'}")
    status = rc.status()
    print(f"  Markets Tracked: {status['markets_tracked']}")
    print(f"  Peak Balance:    ${status['peak_balance']:,.2f}")


def cmd_killswitch(engine: Engine, args: argparse.Namespace) -> None:
    """Activate or deactivate kill switch."""
    rc = engine.risk_controller
    if args.deactivate:
        rc.deactivate_kill_switch()
        print("✅ Kill switch deactivated")
    else:
        rc.activate_kill_switch(args.reason or "Manual activation")
        print("🚨 Kill switch ACTIVATED")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pglm",
        description="polymarket-glm — Trading framework for Polymarket",
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--balance", type=float, default=10_000.0, help="Paper balance")
    parser.add_argument("--log-level", default="INFO")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # status
    p_status = sub.add_parser("status", help="Show engine status")

    # scan
    p_scan = sub.add_parser("scan", help="Scan markets")
    p_scan.add_argument("--min-volume", type=float, default=0, help="Min volume USD")
    p_scan.add_argument("--limit", type=int, default=20, help="Max markets")
    p_scan.add_argument("--no-sports", action="store_true", help="Exclude sports")

    # trade
    p_trade = sub.add_parser("trade", help="Execute manual trade")
    p_trade.add_argument("--market", required=True, help="Market ID")
    p_trade.add_argument("--side", required=True, choices=["buy", "sell"])
    p_trade.add_argument("--outcome", default="Yes")
    p_trade.add_argument("--price", type=float, required=True, help="Price 0-1")
    p_trade.add_argument("--size", type=float, required=True, help="Size in USD")

    # risk
    p_risk = sub.add_parser("risk", help="Show risk status")

    # killswitch
    p_ks = sub.add_parser("killswitch", help="Kill switch control")
    p_ks.add_argument("--deactivate", action="store_true")
    p_ks.add_argument("--reason", default="")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    settings = Settings(
        execution_mode=ExecutionMode(args.mode),
        paper_balance_usd=args.balance,
        log_level=args.log_level,
    )
    engine = Engine(settings)

    commands = {
        "status": cmd_status,
        "scan": cmd_scan,
        "trade": cmd_trade,
        "risk": cmd_risk,
        "killswitch": cmd_killswitch,
    }
    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(engine, args)


if __name__ == "__main__":
    main()
