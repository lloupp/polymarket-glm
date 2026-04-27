"""Engine — orchestrates all components: data ingestion, signals, risk, execution, storage, monitoring."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from polymarket_glm.config import Settings, ExecutionMode
from polymarket_glm.execution.exchange import OrderRequest, FillResult
from polymarket_glm.execution.paper_executor import PaperExecutor
from polymarket_glm.execution.live_executor import LiveExecutor
from polymarket_glm.ingestion.market_fetcher import MarketFetcher, MarketFilter
from polymarket_glm.ingestion.price_feed import PriceFeed
from polymarket_glm.monitoring.alerts import AlertManager, Alert, AlertLevel
from polymarket_glm.risk.controller import RiskController, RiskVerdict
from polymarket_glm.strategy.signal_engine import Signal, SignalType
from polymarket_glm.storage.database import Database
from polymarket_glm.models import Side

logger = logging.getLogger(__name__)


class Engine:
    """Main orchestrator — wires together all subsystems.

    Flow:
    1. MarketFetcher discovers markets
    2. PriceFeed tracks prices
    3. SignalEngine generates signals (from external estimator)
    4. RiskController gates each trade
    5. Executor (paper or live) fills orders
    6. Database records everything
    7. AlertManager sends notifications
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or Settings()
        self._setup_logging()
        self._init_components()

    def _setup_logging(self) -> None:
        from polymarket_glm.monitoring.alerts import setup_logging
        setup_logging(self._settings.log_level)

    def _init_components(self) -> None:
        # Risk
        self._risk = RiskController(
 self._settings.risk,
 initial_balance=self._settings.paper_balance_usd,
 )

        # Alerts
        self._alerts = AlertManager(
            telegram_token=self._settings.telegram_alert_token,
            telegram_chat_id=self._settings.telegram_alert_chat_id,
        )

        # Kill switch → alert
        self._risk.on_alert = lambda a: self._alerts.emit(a)  # type: ignore

        # Execution
        if self._settings.execution_mode == ExecutionMode.PAPER:
            self._executor = PaperExecutor(
                initial_balance=self._settings.paper_balance_usd,
            )
            self._is_paper = True
        else:
            if not self._settings.live_ready:
                raise ValueError(
                    "API keys required for live mode. "
                    "Set PGLM_CLOB_API_KEY, PGLM_CLOB_API_SECRET, "
                    "PGLM_CLOB_API_PASSPHRASE, PGLM_PRIVATE_KEY"
                )
            self._executor = LiveExecutor(clob_config=self._settings.clob)
            self._is_paper = False

        # Ingestion
        self._fetcher = MarketFetcher()
        self._price_feed = PriceFeed()

        # Storage (lazy init)
        self._db: Database | None = None

    @property
    def is_paper(self) -> bool:
        return self._is_paper

    @property
    def is_live(self) -> bool:
        return not self._is_paper

    @property
    def risk_controller(self) -> RiskController:
        return self._risk

    @property
    def alert_manager(self) -> AlertManager:
        return self._alerts

    def _ensure_db(self) -> Database:
        if self._db is None:
            self._db = Database("polymarket_glm.db")
            self._db.initialize()
        return self._db

    # ── Risk gate ───────────────────────────────────────────────

    def check_risk(self, market_id: str, outcome: str, trade_usd: float) -> tuple[RiskVerdict, str]:
        """Pre-trade risk check."""
        verdict, reason = self._risk.check(market_id, outcome, trade_usd)
        if verdict == RiskVerdict.KILL_SWITCH:
            self._alerts.emit(Alert(
                level=AlertLevel.CRITICAL,
                title="Kill Switch Active",
                message=reason,
            ))
        return verdict, reason

    # ── Signal processing ───────────────────────────────────────

    def process_signal_sync(self, signal: Signal, price: float | None = None) -> FillResult:
        """Process a signal synchronously: risk check → execute → record.

        This is the main entry point for the trading loop.
        """
        trade_usd = signal.size_usd
        market_id = signal.market_id
        outcome = signal.outcome

        # 1. Risk gate
        verdict, reason = self.check_risk(market_id, outcome, trade_usd)
        if verdict != RiskVerdict.ALLOW:
            self._alerts.emit(Alert(
                level=AlertLevel.WARNING,
                title="Trade Blocked",
                message=f"{verdict.value}: {reason}",
            ))
            return FillResult(
                order_id="",
                market_id=market_id,
                side=Side.BUY if signal.signal_type == SignalType.BUY else Side.SELL,
                outcome=outcome,
                price=signal.market_price,
                size=0,
                filled=False,
                reason=f"Risk: {verdict.value}",
            )

        # 2. Build order
        fill_price = price or signal.market_price
        order = OrderRequest(
            market_id=market_id,
            side=Side.BUY if signal.signal_type == SignalType.BUY else Side.SELL,
            outcome=outcome,
            price=fill_price,
            size=trade_usd / fill_price if fill_price > 0 else 0,
        )

        # 3. Execute
        if isinstance(self._executor, PaperExecutor):
            result = self._executor.submit_order_sync(order)
        else:
            # For live, we'd need async — but sync path returns placeholder
            result = FillResult(
                order_id=str(uuid.uuid4())[:8],
                market_id=market_id,
                side=order.side,
                outcome=outcome,
                price=fill_price,
                size=0,
                filled=False,
                reason="Live execution requires async context",
            )

        # 4. Record
        if result.filled:
            self._risk.record_fill(market_id, outcome, result.total_cost)
            self._alerts.emit(Alert(
                level=AlertLevel.INFO,
                title="Trade Filled",
                message=f"{order.side.value} {outcome}@{fill_price:.2f} ×{order.size:.0f} ${trade_usd:.2f}",
            ))
            try:
                db = self._ensure_db()
                db.save_trade(
                    trade_id=result.order_id,
                    market_id=market_id,
                    side=order.side.value,
                    outcome=outcome,
                    price=result.price,
                    size=result.size,
                    fee=result.fee,
                )
                db.save_signal(
                    market_id=market_id,
                    signal_type=signal.signal_type.value,
                    edge=signal.edge,
                    estimated_prob=signal.estimated_prob,
                    market_price=signal.market_price,
                    size_usd=signal.size_usd,
                    kelly_raw=signal.kelly_raw,
                    kelly_sized=signal.kelly_sized,
                )
            except Exception as exc:
                logger.warning("DB record failed: %s", exc)

        return result

    # ── Status ──────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return engine status summary."""
        account = self._executor.account if isinstance(self._executor, PaperExecutor) else None
        return {
            "mode": "paper" if self._is_paper else "live",
            "balance_usd": account.balance_usd if account else 0,
            "kill_switch_active": self._risk._kill_switch_active,
            "total_exposure": self._risk.total_exposure,
            "daily_loss": self._risk.daily_loss,
            "risk_status": self._risk.status(),
        }

    async def scan_markets(self, market_filter: MarketFilter | None = None) -> list:
        """Discover markets via MarketFetcher."""
        markets = await self._fetcher.fetch_markets(market_filter)
        for m in markets:
            try:
                db = self._ensure_db()
                db.save_market(
                    condition_id=m.condition_id,
                    market_id=m.market_id,
                    question=m.question,
                    outcomes=str(m.outcomes),
                    outcome_prices=str(m.outcome_prices),
                    tokens=str(m.tokens),
                    volume=m.volume,
                )
            except Exception:
                pass
        return markets
