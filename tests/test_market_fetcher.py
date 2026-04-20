"""Tests for market fetcher."""
import pytest
from polymarket_glm.ingestion.market_fetcher import MarketFetcher, MarketFilter


def test_market_filter_defaults():
    f = MarketFilter()
    assert f.min_volume_usd == 0
    assert f.active_only is True
    assert f.max_markets == 100


def test_market_filter_custom():
    f = MarketFilter(min_volume_usd=5000, max_markets=20, exclude_sports=True)
    assert f.min_volume_usd == 5000
    assert f.exclude_sports is True


def test_sport_exclusion():
    f = MarketFilter(exclude_sports=True)
    fetcher = MarketFetcher()
    from polymarket_glm.models import Market
    sport_m = Market(
        condition_id="0x1", market_id="1", question="Will the NBA finals go to game 7?",
        outcomes=["Yes", "No"], outcome_prices=[0.5, 0.5], tokens=["t1", "t2"],
    )
    assert fetcher._passes_filter(sport_m, f) is False


def test_keyword_filter():
    f = MarketFilter(keywords_include=["bitcoin"], keywords_exclude=["weather"])
    fetcher = MarketFetcher()
    from polymarket_glm.models import Market
    btc_m = Market(
        condition_id="0x1", market_id="1", question="Will Bitcoin reach $100k?",
        outcomes=["Yes", "No"], outcome_prices=[0.5, 0.5], tokens=["t1", "t2"],
    )
    weather_m = Market(
        condition_id="0x2", market_id="2", question="Will it rain in NYC?",
        outcomes=["Yes", "No"], outcome_prices=[0.5, 0.5], tokens=["t1", "t2"],
    )
    assert fetcher._passes_filter(btc_m, f) is True
    assert fetcher._passes_filter(weather_m, f) is False


def test_parse_market_valid():
    fetcher = MarketFetcher()
    raw = {
        "conditionId": "0xabc",
        "id": "123",
        "question": "Will X happen?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.60","0.40"]',
        "clobTokenIds": '["tok1","tok2"]',
        "active": True,
        "closed": False,
        "volume": "50000",
        "negRisk": False,
        "slug": "will-x-happen",
    }
    m = fetcher._parse_market(raw)
    assert m is not None
    assert m.market_id == "123"
    assert m.outcomes == ["Yes", "No"]


def test_parse_market_invalid_json():
    fetcher = MarketFetcher()
    raw = {"conditionId": "0x", "id": "1", "question": "Q", "outcomes": "bad"}
    m = fetcher._parse_market(raw)
    assert m is None
