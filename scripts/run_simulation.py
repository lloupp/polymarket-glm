#!/usr/bin/env python3
"""Run polymarket-glm in paper trading simulation mode (24/7).

Wires together: MarketFetcher → SignalEngine → RiskController → PaperExecutor
with Telegram alerts + health monitoring.

Usage:
    python scripts/run_simulation.py
    python scripts/run_simulation.py --scan-interval 120 --max-iterations 50
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from polymarket_glm.config import Settings, ExecutionMode
from polymarket_glm.ingestion.market_fetcher import MarketFetcher, MarketFilter
from polymarket_glm.ingestion.price_feed import PriceFeed
from polymarket_glm.strategy.signal_engine import SignalEngine, SignalType
from polymarket_glm.strategy.llm_router import LLMRouter, LLMRouterConfig as RouterConfig, LLMProviderConfig
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.execution.paper_executor import PaperExecutor
from polymarket_glm.execution.exchange import OrderRequest
from polymarket_glm.monitoring.alerts import AlertManager, Alert, AlertLevel
from polymarket_glm.ops.telegram_bot import TelegramBot
from polymarket_glm.ops.health import HealthCheck, check_loop_health
from polymarket_glm.models import Side

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulation")


class SimulationEngine:
    """Full paper trading simulation loop with Telegram integration."""

    def __init__(self, settings: Settings, scan_interval: float = 60.0, max_iterations: int = 0):
        self._settings = settings
        self._scan_interval = scan_interval
        self._max_iterations = max_iterations

        # Core components
        self._fetcher = MarketFetcher()
        self._price_feed = PriceFeed()
        self._signal_engine = SignalEngine(
            min_edge=0.05,
            kelly_fraction=0.25,
            max_position_usd=settings.risk.max_per_trade_usd,
        )
        self._risk = RiskController(config=settings.risk)
        self._executor = PaperExecutor(initial_balance=settings.paper_balance_usd)

        # LLM Router (replaces Gaussian noise estimator)
        self._llm_router: LLMRouter | None = None
        self._use_llm = False
        llm_cfg = settings.llm_router
        if llm_cfg.enabled and llm_cfg.active_providers > 0:
            providers = []
            if llm_cfg.groq_api_key:
                providers.append(LLMProviderConfig(
                    name="groq", base_url=llm_cfg.groq_base_url,
                    model=llm_cfg.groq_model, rpm=llm_cfg.groq_rpm,
                    api_key=llm_cfg.groq_api_key, priority=1,
                ))
            if llm_cfg.gemini_api_key:
                providers.append(LLMProviderConfig(
                    name="gemini", base_url=llm_cfg.gemini_base_url,
                    model=llm_cfg.gemini_model, rpm=llm_cfg.gemini_rpm,
                    api_key=llm_cfg.gemini_api_key, priority=2,
                ))
            if llm_cfg.github_api_key:
                providers.append(LLMProviderConfig(
                    name="github", base_url=llm_cfg.github_base_url,
                    model=llm_cfg.github_model, rpm=llm_cfg.github_rpm,
                    api_key=llm_cfg.github_api_key, priority=3,
                ))
            if llm_cfg.cerebras_api_key:
                providers.append(LLMProviderConfig(
                    name="cerebras", base_url=llm_cfg.cerebras_base_url,
                    model=llm_cfg.cerebras_model, rpm=llm_cfg.cerebras_rpm,
                    api_key=llm_cfg.cerebras_api_key, priority=4,
                ))
            if llm_cfg.mistral_api_key:
                providers.append(LLMProviderConfig(
                    name="mistral", base_url=llm_cfg.mistral_base_url,
                    model=llm_cfg.mistral_model, rpm=llm_cfg.mistral_rpm,
                    api_key=llm_cfg.mistral_api_key, priority=5,
                ))

            self._llm_router = LLMRouter(RouterConfig(
                providers=providers,
                max_retries_per_provider=llm_cfg.max_retries_per_provider,
                timeout_sec=llm_cfg.timeout_sec,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
            ))
            self._use_llm = True
            logger.info(
                "🧠 LLM Router enabled: %d providers (%s)",
                len(providers),
                ", ".join(p.name for p in providers),
            )
        else:
            logger.info("📊 LLM Router disabled — using Gaussian noise estimator")

        # Market filter: active, non-sports, decent volume
        self._market_filter = MarketFilter(
            active_only=True,
            exclude_sports=True,
            min_volume_usd=1000,
            max_markets=50,
        )

        # Telegram bot for alerts
        self._bot: TelegramBot | None = None
        self._alert_mgr: AlertManager | None = None
        if settings.telegram_alert_token and settings.telegram_alert_chat_id:
            self._bot = TelegramBot(
                token=settings.telegram_alert_token,
                chat_id=settings.telegram_alert_chat_id,
                status_provider=self._status_provider,
                risk_provider=self._risk_provider,
                positions_provider=self._positions_provider,
                killswitch_fn=self._toggle_killswitch,
            )
            self._alert_mgr = AlertManager(
                telegram_token=settings.telegram_alert_token,
                telegram_chat_id=settings.telegram_alert_chat_id,
            )
            logger.info("Telegram alerts configured")

        # Health checker
        self._health = HealthCheck()

        # State
        self._iteration = 0
        self._total_signals = 0
        self._total_fills = 0
        self._total_rejections = 0
        self._running = False
        self._stop_event = asyncio.Event()
        self._last_loop_time = 0.0

    # ── Providers for Telegram bot ──────────────────────────

    def _status_provider(self) -> dict:
        acct = self._executor.account
        return {
            "mode": self._settings.execution_mode.value,
            "balance": acct.balance_usd,
            "trades": self._total_fills,
        }

    def _risk_provider(self) -> dict:
        acct = self._executor.account
        return {
            "total_exposure": acct.total_exposure_usd,
            "max_exposure": self._settings.risk.max_total_exposure_usd,
            "daily_pnl": -self._risk.daily_loss,
            "daily_limit": self._settings.risk.daily_loss_limit_usd,
            "kill_switch_active": self._risk._kill_switch_active,
        }

    def _positions_provider(self) -> list[dict]:
        acct = self._executor.account
        result = []
        for pos in acct.positions:
            market_id = pos.market_id
            # Try to find the question from our cache
            result.append({
                "market": market_id[:20] + "...",
                "side": "LONG",
                "size": pos.size * pos.avg_price,
                "avg_price": pos.avg_price,
            })
        return result

    def _toggle_killswitch(self) -> bool:
        if self._risk._kill_switch_active:
            self._risk.deactivate_kill_switch()
            return False
        else:
            self._risk.activate_kill_switch("Manual activation via Telegram /killswitch")
            return True

    # ── Alert helpers ───────────────────────────────────────

    async def _send_alert(self, title: str, message: str, level: str = "info"):
        if self._bot:
            await self._bot.send_alert(title=title, message=message, level=level)
        if self._alert_mgr:
            alert_level = AlertLevel(level)
            self._alert_mgr.emit(Alert(level=alert_level, title=title, message=message))

    # ── Main loop ───────────────────────────────────────────

    async def run(self) -> None:
        """Run the simulation loop."""
        self._running = True
        logger.info(
            "🚀 Simulation started (mode=%s, interval=%.0fs, balance=$%.2f)",
            self._settings.execution_mode.value,
            self._scan_interval,
            self._settings.paper_balance_usd,
        )

        # Startup alert
        await self._send_alert(
            "Simulation Started",
            f"Mode: paper | Balance: ${self._settings.paper_balance_usd:,.2f} | Interval: {self._scan_interval:.0f}s",
            "info",
        )

        # Start Telegram bot polling in parallel
        bot_task = None
        if self._bot:
            from polymarket_glm.ops.telegram_bot import parse_command
            bot_task = asyncio.create_task(self._run_bot_polling())

        try:
            while not self._stop_event.is_set():
                if self._max_iterations > 0 and self._iteration >= self._max_iterations:
                    logger.info("Max iterations (%d) reached", self._max_iterations)
                    break

                await self._run_iteration()
                self._iteration += 1

                # Health heartbeat
                try:
                    hc_status = check_loop_health(self._health)
                    if hc_status.value == "stuck":
                        await self._send_alert("Loop Stuck", "Trading loop appears stuck — no progress", "warning")
                except Exception:
                    pass

                # Wait for next iteration
                if not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self._scan_interval)
                        break  # stop_event was set
                    except asyncio.TimeoutError:
                        pass  # normal — next iteration

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as exc:
            logger.exception("Simulation crashed: %s", exc)
            await self._send_alert("Simulation Crashed", str(exc), "critical")
        finally:
            self._running = False
            if bot_task and not bot_task.done():
                bot_task.cancel()
            await self._shutdown()

    async def _run_bot_polling(self) -> None:
        """Run Telegram bot polling loop."""
        from polymarket_glm.ops.telegram_bot import parse_command
        import httpx
        import json as _json

        api_base = f"https://api.telegram.org/bot{self._bot.token}"
        last_update_id = 0

        logger.info("🤖 Bot polling started")

        while self._running:
            try:
                params = {
                    "offset": last_update_id + 1,
                    "timeout": 10,
                    "allowed_updates": _json.dumps(["message"]),
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{api_base}/getUpdates", params=params)

                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    last_update_id = update["update_id"]
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = str(message.get("chat", {}).get("id", ""))

                    if not text or chat_id != str(self._bot.chat_id):
                        continue

                    cmd = parse_command(text)
                    result = await self._bot.handle_command(cmd)
                    if result.text:
                        await self._bot.send_message(result.text)

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    async def _run_iteration(self) -> None:
        """One full scan → estimate → signal → risk → execute cycle."""
        self._last_loop_time = time.time()
        logger.info("── Iteration %d ──", self._iteration + 1)

        # 1. Scan markets
        try:
            markets = await self._fetcher.fetch_markets(self._market_filter)
            logger.info("Scanned %d markets", len(markets))
        except Exception as exc:
            logger.warning("Market scan failed: %s", exc)
            return

        if not markets:
            logger.info("No markets found — sleeping")
            return

        # 2. Process each market
        signals_this_round = 0
        fills_this_round = 0
        rejections_this_round = 0

        for market in markets[:20]:  # cap at 20 markets per iteration
            try:
                result = await self._process_market(market)
                if result == "signal":
                    signals_this_round += 1
                elif result == "filled":
                    signals_this_round += 1
                    fills_this_round += 1
                elif result == "rejected":
                    signals_this_round += 1
                    rejections_this_round += 1
            except Exception as exc:
                logger.debug("Error processing %s: %s", market.market_id, exc)

        self._total_signals += signals_this_round
        self._total_fills += fills_this_round
        self._total_rejections += rejections_this_round

        if signals_this_round > 0:
            logger.info(
                "Round summary: %d signals, %d fills, %d rejected",
                signals_this_round, fills_this_round, rejections_this_round,
            )

        # Update balance for drawdown check
        acct = self._executor.account
        self._risk.update_balance(acct.balance_usd)

    async def _process_market(self, market) -> str | None:
        """Process a single market: fetch book → estimate → signal → risk → execute."""
        # Fetch order book using the FULL CLOB token ID (not the short numeric market_id)
        token_id = market.tokens[0] if market.tokens else None
        if not token_id:
            return None

        book = await self._price_feed.fetch_book(token_id)
        if book is None or not book.bids or not book.asks:
            return None

        # ── Estimator ──
        # Use LLM Router if available, otherwise fall back to Gaussian noise
        if self._use_llm and self._llm_router:
            from polymarket_glm.strategy.estimator import MarketInfo
            mi = MarketInfo(
                question=market.question,
                volume=market.volume,
                spread=market.spread,
                current_price=market.outcome_prices[0] if market.outcome_prices else 0.5,
                category=market.category or "",
            )
            estimate = await self._llm_router.estimate(mi)
            estimated_prob = estimate.probability
            logger.debug(
                "🧠 LLM estimate: %.2f (confidence=%.2f, source=%s) — %s",
                estimated_prob, estimate.confidence, estimate.source,
                market.question[:50],
            )
        else:
            # Fallback: Gaussian noise estimator (for testing without LLM keys)
            import random
            base_prob = market.outcome_prices[0] if market.outcome_prices else 0.5
            noise = random.gauss(0, 0.05)
            estimated_prob = max(0.01, min(0.99, base_prob + noise))

        # Generate signal
        signal = self._signal_engine.generate_signal(
            market=market,
            book=book,
            estimated_prob=estimated_prob,
            balance_usd=self._executor.account.balance_usd,
        )

        if signal is None:
            return None  # no edge

        logger.info(
            "📈 Signal: %s %s edge=%.4f size=$%.2f",
            signal.signal_type.value,
            market.question[:50],
            signal.edge,
            signal.size_usd,
        )

        # Risk check
        verdict, reason = self._risk.check(
            market_id=signal.market_id,
            outcome=signal.outcome,
            trade_usd=signal.size_usd,
        )

        if verdict != RiskVerdict.ALLOW:
            logger.info("⛔ Risk rejected: %s (%s)", verdict.value, reason)
            return "rejected"

        # Execute (paper)
        side = Side.BUY if signal.signal_type == SignalType.BUY else Side.SELL
        order = OrderRequest(
            market_id=signal.market_id,
            side=side,
            outcome=signal.outcome,
            price=signal.market_price,
            size=signal.size_usd / signal.market_price if signal.market_price > 0 else 0,
        )

        fill = await self._executor.submit_order(order)

        if fill.filled:
            self._risk.record_fill(signal.market_id, signal.outcome, signal.size_usd)
            logger.info(
                "✅ Filled: %s %s $%.2f @ %.4f",
                side.value, signal.outcome, signal.size_usd, signal.market_price,
            )

            # Alert for fills
            if self._total_fills <= 5 or self._total_fills % 10 == 0:
                await self._send_alert(
                    "Trade Filled",
                    f"{side.value.upper()} {signal.outcome} ${signal.size_usd:.2f} @ {signal.market_price:.4f}\n{market.question[:80]}",
                    "info",
                )
            return "filled"
        else:
            logger.info("❌ Fill failed: %s", fill.reason)
            return "signal"

    async def _shutdown(self) -> None:
        """Graceful shutdown."""
        acct = self._executor.account
        logger.info(
            "🛑 Simulation stopped — iterations=%d signals=%d fills=%d rejections=%d balance=$%.2f",
            self._iteration, self._total_signals, self._total_fills,
            self._total_rejections, acct.balance_usd,
        )
        await self._send_alert(
            "Simulation Stopped",
            f"Iterations: {self._iteration} | Fills: {self._total_fills} | Balance: ${acct.balance_usd:,.2f}",
            "warning",
        )
        await self._fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run polymarket-glm paper trading simulation")
    parser.add_argument("--scan-interval", type=float, default=120.0, help="Seconds between market scans (default: 120)")
    parser.add_argument("--max-iterations", type=int, default=0, help="Stop after N iterations (0=infinite)")
    args = parser.parse_args()

    settings = Settings()

    if settings.execution_mode != ExecutionMode.PAPER:
        logger.warning("⚠️ Execution mode is '%s' — forcing PAPER for simulation safety", settings.execution_mode.value)
        settings.execution_mode = ExecutionMode.PAPER

    if not settings.telegram_alert_token:
        logger.warning("No TELEGRAM_BOT_TOKEN configured — alerts disabled")

    engine = SimulationEngine(
        settings=settings,
        scan_interval=args.scan_interval,
        max_iterations=args.max_iterations,
    )

    # Handle SIGINT/SIGTERM gracefully
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, engine._stop_event.set)

    try:
        loop.run_until_complete(engine.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
