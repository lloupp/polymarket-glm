"""Live executor — real CLOB integration via py-clob-client.

Implements ExchangeClient protocol with:
- Real order submission via Polymarket CLOB API
- API key validation at init time
- Dry-run mode for testing without actual trades
"""
from __future__ import annotations

import logging
import uuid

from polymarket_glm.config import ClobConfig
from polymarket_glm.execution.exchange import (
    FillResult,
    OrderRequest,
    CancelResult,
)
from polymarket_glm.models import Side, Account

logger = logging.getLogger(__name__)


class LiveExecutor:
    """Live trading executor — sends real orders to Polymarket CLOB.

    Requires valid API keys. Uses py-clob-client under the hood.
    """

    def __init__(
        self,
        clob_config: ClobConfig | None = None,
        dry_run: bool = False,
    ):
        self._clob_config = clob_config or ClobConfig()
        self._dry_run = dry_run
        self._client = None

        # Validate keys (unless dry_run)
        if not dry_run:
            self._validate_keys()

    def _validate_keys(self) -> None:
        """Ensure all required API keys are present."""
        missing = []
        if not self._clob_config.api_key:
            missing.append("api_key")
        if not self._clob_config.api_secret:
            missing.append("api_secret")
        if not self._clob_config.api_passphrase:
            missing.append("api_passphrase")
        if not self._clob_config.private_key:
            missing.append("private_key")
        if missing:
            raise ValueError(
                f"API keys required for live trading: missing {', '.join(missing)}. "
                f"Set PGLM_CLOB_API_KEY, PGLM_CLOB_API_SECRET, "
                f"PGLM_CLOB_API_PASSPHRASE, PGLM_PRIVATE_KEY env vars."
            )

    async def _ensure_client(self):
        """Lazy-init the CLOB client."""
        if self._client is None:
            try:
                from py_clob_client.client import ClobClient
                self._client = ClobClient(
                    self._clob_config.clob_url,
                    key=self._clob_config.private_key,
                    chain_id=self._clob_config.chain_id,
                    api_key=self._clob_config.api_key,
                    api_secret=self._clob_config.api_secret,
                    api_passphrase=self._clob_config.api_passphrase,
                )
                logger.info("CLOB client initialized (chain_id=%d)", self._clob_config.chain_id)
            except ImportError:
                raise ImportError(
                    "py-clob-client is required for live trading. "
                    "Install with: pip install py-clob-client"
                )
        return self._client

    # ── ExchangeClient protocol ─────────────────────────────────

    async def submit_order(self, request: OrderRequest) -> FillResult:
        """Submit a real order to the CLOB (or dry-run)."""
        order_id = str(uuid.uuid4())[:8]

        if self._dry_run:
            logger.info("DRY RUN: would submit %s %s@%.2f x%.0f",
                       request.side.value, request.outcome,
                       request.price, request.size)
            return FillResult(
                order_id=order_id,
                market_id=request.market_id,
                side=request.side,
                outcome=request.outcome,
                price=request.price,
                size=request.size,
                filled=False,
                reason="Dry run — order not submitted",
            )

        client = await self._ensure_client()
        try:
            # Build the order for py-clob-client
            # NOTE: The actual order format depends on py-clob-client version
            # This is the general approach — may need adjustment
            order_args = {
                "token_id": request.market_id,  # CLOB uses token_id
                "price": request.price,
                "size": request.size,
                "side": request.side.value,
                "order_type": request.order_type,
            }
            resp = client.create_and_post_order(**order_args)
            order_id_resp = resp.get("orderID", order_id)

            logger.info("Live order submitted: %s → %s", order_id, order_id_resp)
            return FillResult(
                order_id=order_id_resp,
                market_id=request.market_id,
                side=request.side,
                outcome=request.outcome,
                price=request.price,
                size=request.size,
                filled=False,  # CLOB orders are async — fills come later
                reason="Order submitted to CLOB",
            )
        except Exception as exc:
            logger.error("Live order failed: %s", exc)
            return FillResult(
                order_id=order_id,
                market_id=request.market_id,
                side=request.side,
                outcome=request.outcome,
                price=request.price,
                size=0,
                filled=False,
                reason=f"CLOB error: {exc}",
            )

    async def cancel_order(self, order_id: str) -> CancelResult:
        """Cancel a live order on the CLOB."""
        if self._dry_run:
            return CancelResult(order_id=order_id, success=False, reason="Dry run")

        client = await self._ensure_client()
        try:
            resp = client.cancel(order_id=order_id)
            return CancelResult(order_id=order_id, success=True)
        except Exception as exc:
            logger.error("Cancel failed for %s: %s", order_id, exc)
            return CancelResult(order_id=order_id, success=False, reason=str(exc))

    async def get_account(self) -> Account:
        """Get account state from the exchange."""
        # NOTE: This would need integration with Polymarket's balance API
        # For now, return a placeholder
        return Account(
            balance_usd=0.0,
            total_exposure_usd=0.0,
        )

    async def get_open_orders(self, market_id: str | None = None) -> list[OrderRequest]:
        """Get open orders from the CLOB."""
        if self._dry_run:
            return []
        client = await self._ensure_client()
        try:
            orders = client.get_orders()
            result = []
            for o in orders:
                result.append(OrderRequest(
                    market_id=o.get("asset_id", ""),
                    side=Side.BUY if o.get("side") == "BUY" else Side.SELL,
                    outcome="Yes",  # Would need token→outcome mapping
                    price=float(o.get("price", 0)),
                    size=float(o.get("original_size", 0)),
                ))
            return result
        except Exception as exc:
            logger.error("Failed to fetch orders: %s", exc)
            return []
