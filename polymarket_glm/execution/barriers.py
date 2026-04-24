"""Triple barrier config + CloseType — position lifecycle management.

Inspired by Hummingbot V2's PositionExecutor + TripleBarrierConfig pattern.
Adapted for prediction markets where:
- Prices are probabilities (0-1)
- "Stop loss" = probability moved against us by X%
- "Take profit" = probability moved in our favor by X%
- "Time limit" = close N seconds before market resolution
- "Trailing stop" = lock profit with trailing activation

This replaces the simple PositionManagerConfig with a richer barrier system.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CloseType(str, enum.Enum):
    """Reason a position was closed."""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TIME_LIMIT = "time_limit"
    TRAILING_STOP = "trailing_stop"
    RESOLVED = "resolved"           # Market resolved naturally
    EARLY_STOP = "early_stop"       # Manual/strategy stop
    EXPIRED = "expired"             # Order expired unfilled
    INSUFFICIENT_BALANCE = "insufficient_balance"
    FAILED = "failed"               # Execution failed
    COMPLETED = "completed"         # Normal completion


class TrailingStop(BaseModel):
    """Trailing stop configuration.

    activation_price_pct: % move in our favor before trailing starts
    trailing_delta_pct: % drawdown from peak after activation triggers close

    Example: buy YES at 0.30, activation=0.10 (10%), delta=0.03 (3%)
    - Price must reach 0.30 * 1.10 = 0.33 to activate
    - After activation, if price drops 3% from peak, close position
    """
    activation_price_pct: float = Field(ge=0, default=0.10)
    trailing_delta_pct: float = Field(ge=0, default=0.03)


class TripleBarrierConfig(BaseModel):
    """Triple barrier position management — adapted for prediction markets.

    Inspired by Hummingbot V2's TripleBarrierConfig. Each barrier independently
    can close a position when its condition is met.

    For prediction markets:
    - stop_loss_pct: close if unrealized loss exceeds X% (e.g. 0.50 = 50% loss)
    - take_profit_pct: close if unrealized profit exceeds X% (e.g. 0.50 = 50% gain)
    - time_limit_sec: close position this many seconds before market end_date
    - trailing_stop: optional trailing stop (activation + delta)
    """
    stop_loss_pct: Optional[float] = Field(
        default=0.50,
        ge=0,
        description="Max loss as fraction of entry cost (0.50 = 50%% loss triggers close)"
    )
    take_profit_pct: Optional[float] = Field(
        default=0.50,
        ge=0,
        description="Target profit as fraction of entry cost (0.50 = 50%% gain triggers close)"
    )
    time_limit_sec: Optional[int] = Field(
        default=3600,
        ge=0,
        description="Close position this many seconds BEFORE market resolution. 0 = no time limit."
    )
    trailing_stop: Optional[TrailingStop] = Field(
        default=None,
        description="Trailing stop: activation_price_pct + trailing_delta_pct"
    )

    @field_validator("stop_loss_pct", "take_profit_pct")
    @classmethod
    def _validate_pct(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("Percentage must be >= 0")
        return v

    def adjusted_for_volatility(self, volatility_factor: float) -> TripleBarrierConfig:
        """Create a new config with barriers widened by volatility factor.

        Higher volatility → wider stops (less likely to get stopped out by noise).
        Lower volatility → tighter stops (more confident in levels).
        """
        new_sl = self.stop_loss_pct * volatility_factor if self.stop_loss_pct is not None else None
        new_tp = self.take_profit_pct * volatility_factor if self.take_profit_pct is not None else None
        new_ts = None
        if self.trailing_stop is not None:
            new_ts = TrailingStop(
                activation_price_pct=self.trailing_stop.activation_price_pct * volatility_factor,
                trailing_delta_pct=self.trailing_stop.trailing_delta_pct * volatility_factor,
            )
        return TripleBarrierConfig(
            stop_loss_pct=new_sl,
            take_profit_pct=new_tp,
            time_limit_sec=self.time_limit_sec,
            trailing_stop=new_ts,
        )


class PositionBarrierResult(BaseModel):
    """Result of checking barriers against a position."""
    should_close: bool = False
    close_type: CloseType = CloseType.COMPLETED
    reason: str = ""
    current_return_pct: float = 0.0
    peak_return_pct: float = 0.0  # For trailing stop tracking
    trailing_activated: bool = False


def check_barriers(
    entry_price: float,
    current_price: float,
    side: str,  # "BUY" or "SELL"
    outcome: str,  # "YES" or "NO"
    config: TripleBarrierConfig,
    market_end_date: str | None = None,
    position_opened_at: datetime | None = None,
    peak_price: float | None = None,  # Best price since entry (for trailing)
    trailing_activated: bool = False,
) -> PositionBarrierResult:
    """Check all barriers for a position and determine if it should close.

    Args:
        entry_price: Price at which position was opened
        current_price: Current market price
        side: BUY or SELL
        outcome: YES or NO
        config: TripleBarrierConfig with barrier levels
        market_end_date: ISO date string of market resolution
        position_opened_at: When position was opened
        peak_price: Highest (for BUY YES) or lowest (for BUY NO) price seen
        trailing_activated: Whether trailing stop is already active

    Returns:
        PositionBarrierResult with should_close flag and close_type
    """
    if entry_price <= 0:
        return PositionBarrierResult(reason="invalid_entry_price")

    # Calculate return percentage based on side and outcome
    return_pct = _calculate_return_pct(entry_price, current_price, side, outcome)
    peak_return = _calculate_return_pct(entry_price, peak_price or current_price, side, outcome) if peak_price else return_pct

    # 1. Stop Loss check
    if config.stop_loss_pct is not None and return_pct <= -config.stop_loss_pct:
        return PositionBarrierResult(
            should_close=True,
            close_type=CloseType.STOP_LOSS,
            reason=f"Stop loss triggered: return {return_pct:.1%} <= -{config.stop_loss_pct:.1%}",
            current_return_pct=return_pct,
            peak_return_pct=peak_return,
            trailing_activated=trailing_activated,
        )

    # 2. Take Profit check
    if config.take_profit_pct is not None and return_pct >= config.take_profit_pct:
        return PositionBarrierResult(
            should_close=True,
            close_type=CloseType.TAKE_PROFIT,
            reason=f"Take profit triggered: return {return_pct:.1%} >= {config.take_profit_pct:.1%}",
            current_return_pct=return_pct,
            peak_return_pct=peak_return,
            trailing_activated=trailing_activated,
        )

    # 3. Time Limit check
    if config.time_limit_sec is not None and config.time_limit_sec > 0 and market_end_date and position_opened_at:
        try:
            from datetime import timezone
            end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00")).replace(tzinfo=None)
            now = datetime.utcnow()
            seconds_to_resolution = (end_dt - now).total_seconds()
            if seconds_to_resolution <= config.time_limit_sec:
                return PositionBarrierResult(
                    should_close=True,
                    close_type=CloseType.TIME_LIMIT,
                    reason=f"Time limit: {seconds_to_resolution:.0f}s to resolution <= {config.time_limit_sec}s",
                    current_return_pct=return_pct,
                    peak_return_pct=peak_return,
                    trailing_activated=trailing_activated,
                )
        except (ValueError, TypeError):
            pass  # Invalid date format, skip this check

    # 4. Trailing Stop check
    if config.trailing_stop is not None:
        # Check if trailing stop should activate
        if not trailing_activated:
            if peak_return >= config.trailing_stop.activation_price_pct:
                trailing_activated = True

        # If activated, check if drawdown from peak exceeds delta
        if trailing_activated:
            drawdown_from_peak = peak_return - return_pct
            if drawdown_from_peak >= config.trailing_stop.trailing_delta_pct:
                return PositionBarrierResult(
                    should_close=True,
                    close_type=CloseType.TRAILING_STOP,
                    reason=f"Trailing stop: drawdown {drawdown_from_peak:.1%} from peak >= {config.trailing_stop.trailing_delta_pct:.1%}",
                    current_return_pct=return_pct,
                    peak_return_pct=peak_return,
                    trailing_activated=True,
                )

    # No barrier triggered
    return PositionBarrierResult(
        should_close=False,
        close_type=CloseType.COMPLETED,
        reason="no_barrier_triggered",
        current_return_pct=return_pct,
        peak_return_pct=peak_return,
        trailing_activated=trailing_activated,
    )


def _calculate_return_pct(
    entry_price: float,
    current_price: float,
    side: str,
    outcome: str,
) -> float:
    """Calculate return percentage for a position.

    For BUY YES: profit when current_price > entry_price
    For BUY NO: profit when current_price < entry_price (NO price = 1 - YES price)
    For SELL YES: profit when current_price < entry_price
    For SELL NO: profit when current_price > entry_price
    """
    if entry_price <= 0:
        return 0.0

    side_upper = side.upper()
    outcome_upper = outcome.upper()

    if side_upper == "BUY":
        if outcome_upper == "YES":
            # Buy YES: profit when price goes up
            return (current_price - entry_price) / entry_price
        else:
            # Buy NO: NO price = 1 - YES_price
            # Entry NO price = 1 - entry_YES_price (if entry is YES price)
            # Profit when YES price goes down (NO price goes up)
            entry_no = 1.0 - entry_price
            current_no = 1.0 - current_price
            if entry_no <= 0:
                return 0.0
            return (current_no - entry_no) / entry_no
    else:  # SELL
        if outcome_upper == "YES":
            # Sell YES: profit when price goes down
            return (entry_price - current_price) / entry_price
        else:
            # Sell NO: profit when YES price goes up (NO price goes down)
            entry_no = 1.0 - entry_price
            current_no = 1.0 - current_price
            if entry_no <= 0:
                return 0.0
            return (entry_no - current_no) / entry_no
