"""Tests for signal engine."""
import math
import pytest
from polymarket_glm.strategy.signal_engine import SignalEngine, Signal, SignalType
from polymarket_glm.models import Market, OrderBook, OrderBookLevel


def _make_market(prices=(0.60, 0.40), vol=50000) -> Market:
    return Market(
        condition_id="0xabc", market_id="m1",
        question="Will X happen?", outcomes=["Yes", "No"],
        outcome_prices=list(prices), tokens=["t1", "t2"],
        volume=vol,
    )


def _make_book(bid=0.55, ask=0.60, bid_size=5000, ask_size=5000) -> OrderBook:
    """Order book with enough size to pass min_liquidity_usd=500.

    Default sizes produce:
      asks liquidity = ask * ask_size = 0.60 * 5000 = $3,000
      bids liquidity = bid * bid_size = 0.55 * 5000 = $2,750
    """
    return OrderBook(
        market_id="m1",
        bids=[OrderBookLevel(price=bid, size=bid_size)],
        asks=[OrderBookLevel(price=ask, size=ask_size)],
    )


def test_signal_type_values():
    assert SignalType.BUY.value == "buy"
    assert SignalType.SELL.value == "sell"
    assert SignalType.NO_SIGNAL.value == "no_signal"


def test_edge_positive():
    engine = SignalEngine(fair_estimate_bias=0.0)
    # market price = 0.60, our estimate = 0.70
    edge = engine.calculate_edge(market_price=0.60, estimated_prob=0.70)
    assert edge > 0
    assert edge == pytest.approx(0.10)


def test_edge_negative():
    engine = SignalEngine()
    edge = engine.calculate_edge(market_price=0.70, estimated_prob=0.55)
    assert edge < 0


def test_edge_zero():
    engine = SignalEngine()
    edge = engine.calculate_edge(market_price=0.50, estimated_prob=0.50)
    assert edge == pytest.approx(0.0)


def test_kelly_fraction_basic():
    engine = SignalEngine(kelly_fraction=0.25)
    # p=0.70, price=0.60 → b = 0.60/0.40 = 1.5
    k = engine.kelly_fraction(prob=0.70, price=0.60)
    assert k > 0
    assert k <= 0.25  # quarter-kelly cap


def test_kelly_no_edge():
    engine = SignalEngine(kelly_fraction=0.25)
    k = engine.kelly_fraction(prob=0.50, price=0.50)
    assert k == 0.0


def test_kelly_negative_edge():
    engine = SignalEngine(kelly_fraction=0.25)
    k = engine.kelly_fraction(prob=0.40, price=0.60)
    assert k == 0.0


def test_generate_signal_buy():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,  # edge = 0.15
    )
    assert sig is not None
    assert sig.signal_type == SignalType.BUY
    assert sig.edge > 0
    assert sig.size_usd > 0


def test_generate_signal_no_edge():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.62,  # edge = 0.02 < min_edge
    )
    assert sig is None


def test_generate_signal_sell():
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    # Estimate much lower than market → sell signal
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.35,  # edge = -0.25 → sell
    )
    assert sig is not None
    assert sig.signal_type == SignalType.SELL


# ── Cash-aware Sizing Tests ─────────────────────────────────

def test_cash_aware_sizing_caps_to_available_cash():
    """Signal size should be capped to cash_available when set."""
    engine = SignalEngine(min_edge=0.05, max_position_usd=500.0)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    # With balance=$10K and cash_available=$50, size must be ≤ $50
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,  # strong edge
        balance_usd=10_000.0,
        cash_available=50.0,
    )
    assert sig is not None
    assert sig.size_usd <= 50.0


def test_cash_aware_sizing_no_cap_when_cash_sufficient():
    """When cash_available is None or sufficient, size follows Kelly+max."""
    engine = SignalEngine(min_edge=0.05, max_position_usd=500.0)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    # No cash_available → no cap beyond Kelly+max
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
        balance_usd=10_000.0,
        cash_available=None,
    )
    assert sig is not None
    # Kelly sizing on $10K * 0.25 fraction → capped at max_position_usd
    assert sig.size_usd <= 500.0


def test_cash_aware_sizing_zero_cash_no_signal():
    """With cash_available=0, no signal should be generated."""
    engine = SignalEngine(min_edge=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
        balance_usd=10_000.0,
        cash_available=0.0,
    )
    assert sig is None


# ── Min Price Filter Tests ─────────────────────────────────

def test_min_price_filter_skips_cheap_yes():
    """BUY_YES on market with YES price < min_price should be skipped."""
    engine = SignalEngine(min_edge=0.05, min_price=0.05)
    # Market prices YES=0.03, NO=0.97 → YES too cheap
    m = _make_market(prices=(0.03, 0.97))
    book = _make_book(bid=0.02, ask=0.03)
    # Even with huge edge (estimate 0.80 vs price 0.03), should be skipped
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.80,
    )
    assert sig is None


def test_min_price_filter_skips_cheap_no():
    """BUY_NO (SELL YES) on market with NO price < min_price should be skipped."""
    engine = SignalEngine(min_edge=0.05, min_price=0.05)
    # Market prices YES=0.97, NO=0.03 → NO too cheap
    m = _make_market(prices=(0.97, 0.03))
    book = _make_book(bid=0.96, ask=0.97)
    # Estimate 0.20 → huge negative edge → SELL YES / BUY NO
    # But NO price = 1 - 0.97 = 0.03 < min_price=0.05
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.20,
    )
    assert sig is None


def test_min_price_allows_reasonable_yes():
    """BUY_YES on market with YES price >= min_price should pass."""
    engine = SignalEngine(min_edge=0.05, min_price=0.05)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
    )
    assert sig is not None
    assert sig.signal_type == SignalType.BUY


def test_min_price_zero_skips_nothing():
    """min_price=0 disables the price filter entirely."""
    engine = SignalEngine(min_edge=0.05, min_price=0.0, min_liquidity_usd=0.0)
    # Use moderate prices so Kelly computes properly even after edge clamping
    m = _make_market(prices=(0.50, 0.50))
    book = _make_book(bid=0.49, ask=0.50)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.70,  # edge = 0.205
    )
    assert sig is not None


# ── Min Liquidity Filter Tests ─────────────────────────────

def test_min_liquidity_filter_skips_thin_book():
    """Signal should be skipped when order book liquidity is below threshold."""
    engine = SignalEngine(min_edge=0.05, min_liquidity_usd=500.0)
    m = _make_market(prices=(0.60, 0.40))
    # Thin book: asks liquidity = 0.60 * 10 = $6, bids = 0.55 * 10 = $5.5
    book = _make_book(bid=0.55, ask=0.60, bid_size=10, ask_size=10)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
    )
    assert sig is None


def test_min_liquidity_filter_allows_deep_book():
    """Signal should pass when order book liquidity exceeds threshold."""
    engine = SignalEngine(min_edge=0.05, min_liquidity_usd=500.0)
    m = _make_market(prices=(0.60, 0.40))
    # Deep book: asks = 0.60 * 5000 = $3000, bids = 0.55 * 5000 = $2750
    book = _make_book(bid=0.55, ask=0.60, bid_size=5000, ask_size=5000)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
    )
    assert sig is not None


def test_min_liquidity_zero_skips_nothing():
    """min_liquidity_usd=0 disables the liquidity filter entirely."""
    engine = SignalEngine(min_edge=0.05, min_liquidity_usd=0.0)
    m = _make_market(prices=(0.60, 0.40))
    book = _make_book(bid=0.55, ask=0.60, bid_size=1, ask_size=1)
    sig = engine.generate_signal(
        market=m, book=book,
        estimated_prob=0.75,
    )
    assert sig is not None
