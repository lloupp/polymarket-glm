"""Weather-specific probability estimator — Gaussian CDF + multi-model forecasts.

Deterministic scorer for weather markets on Polymarket. No LLMs.

Sources absorbed:
- weather_scanner.py: Gaussian CDF, multi-model forecast, city/date parsing
- hermes weather-score.ts: rain/heat scoring, forecast day resolution

Architecture:
- Temperature markets → Gaussian CDF (ECMWF/GFS/UKMO)
- Rain markets → precipitation_probability_max from Open-Meteo
- Heat markets → temperature_max_c / 50
- Unknown city or failed forecast → confidence=0, probability=0.5
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from polymarket_glm.strategy.estimator import EstimateResult, MarketInfo

logger = logging.getLogger(__name__)

# ── City coordinate mapping (for Open-Meteo) ────────────────────────────────

CITY_COORDS: dict[str, tuple[float, float]] = {
    "london": (51.51, -0.13),
    "shanghai": (31.23, 121.47),
    "hong kong": (22.32, 114.17),
    "seoul": (37.57, 127.00),
    "tokyo": (35.68, 139.69),
    "new york": (40.71, -74.01),
    "nyc": (40.71, -74.01),
    "paris": (48.86, 2.35),
    "beijing": (39.90, 116.40),
    "miami": (25.76, -80.19),
    "los angeles": (34.05, -118.24),
    "la": (34.05, -118.24),
    "san francisco": (37.77, -122.42),
    "sf": (37.77, -122.42),
    "chicago": (41.88, -87.63),
    "singapore": (1.35, 103.82),
    "taipei": (25.03, 121.57),
    "bangkok": (13.76, 100.50),
    "sydney": (-33.87, 151.21),
    "berlin": (52.52, 13.41),
    "madrid": (40.42, -3.70),
    "rome": (41.90, 12.50),
    "amsterdam": (52.37, 4.90),
    "dubai": (25.20, 55.27),
    "washington": (38.91, -77.04),
    "dc": (38.91, -77.04),
    "boston": (42.36, -71.06),
    "phoenix": (33.45, -112.07),
    "denver": (39.74, -104.99),
    "dallas": (32.78, -96.80),
    "atlanta": (33.75, -84.39),
    "seattle": (47.61, -122.33),
    "minneapolis": (44.98, -93.27),
    "detroit": (42.33, -83.05),
    "houston": (29.76, -95.37),
    "delhi": (28.61, 77.21),
    "cairo": (30.04, 31.24),
    "istanbul": (41.01, 28.98),
    "toronto": (43.65, -79.38),
    "melbourne": (-37.81, 144.96),
    "dublin": (53.35, -6.26),
    "lisbon": (38.72, -9.14),
    "milan": (45.46, 9.19),
    "qingdao": (36.07, 120.38),
    "wuhan": (30.59, 114.31),
    "shenzhen": (22.54, 114.06),
    "jeddah": (21.49, 39.19),
    "mumbai": (19.08, 72.88),
    "sao paulo": (-23.55, -46.63),
    "bucharest": (44.43, 26.10),
    "warsaw": (52.23, 21.01),
    "vienna": (48.21, 16.37),
    "budapest": (47.50, 19.04),
    "prague": (50.08, 14.44),
    "hamburg": (53.55, 10.01),
    "stockholm": (59.33, 18.07),
    "oslo": (59.91, 10.75),
    "copenhagen": (55.68, 12.57),
    "zurich": (47.37, 8.54),
    "helsinki": (60.17, 24.94),
    "auckland": (-36.85, 174.76),
    "moscow": (55.76, 37.62),
}

# Known station discrepancy cities (airport/coastal microclimate)
STATION_WARNING_CITIES: dict[str, str] = {
    "london": "EGLC (Thames Estuary) — estação até 4°C mais fria que o grid",
    "tokyo": "RJTT — efeito urbano vs aeroporto",
    "san francisco": "KSFO — névoa marítima pode derrubar 5°C",
    "seoul": "RKSS — ilha de calor vs aeroporto",
    "seattle": "KSEA — Puget Sound cooling",
    "new york": "KJFK/KLGA — coastal cooling possible",
    "nyc": "KJFK/KLGA — coastal cooling possible",
}

# Minimum σ for Gaussian CDF (combines model spread + station error)
FORECAST_UNCERTAINTY_C: float = 1.5

USER_AGENT = "polymarket-glm/2.0"


# ── Math helpers ──────────────────────────────────────────────────────────────

def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Standard normal CDF — P(X <= x) using error function."""
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def celsius_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def fahrenheit_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: int = 15) -> dict:
    """Blocking GET JSON with User-Agent header. Call via asyncio.to_thread."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── City / Date extraction ───────────────────────────────────────────────────

def extract_city_from_title(title: str) -> Optional[str]:
    """Extract city name from market title like 'Highest temperature in London on May 6'.

    Handles patterns:
    - "...in London on May 6"
    - "...rain in Tokyo on May 7"
    - "...temperature in New York?"
    - "...in São Paulo"
    """
    title_lower = title.lower()
    # Pattern 1: "in <city> on <date>"
    m = re.search(r"in\s+([a-záàâãéèêíïóôõöúüñç\s]+?)(?:\s+on\s)", title_lower)
    if m:
        city = m.group(1).strip()
        if city:
            return city
    # Pattern 2: "in <city>?" or end of string
    m = re.search(r"in\s+([a-záàâãéèêíïóôõöúüñç\s]+?)(?:\s*\?|\s*$)", title_lower)
    if m:
        city = m.group(1).strip()
        if city:
            return city
    # Pattern 3: fallback — "in <city>" with any trailing
    m = re.search(r"in\s+([a-záàâãéèêíïóôõöúüñç]+(?:\s+[a-záàâãéèêíïóôõöúüñç]+)?)", title_lower)
    if m:
        city = m.group(1).strip()
        if city:
            return city
    return None


def extract_date_from_title(title: str) -> Optional[str]:
    """Extract target date from title like 'on May 6 2026' → '2026-05-06'."""
    m = re.search(r"on\s+(\w+\s+\d{1,2},?\s+\d{4})", title)
    if m:
        try:
            dt = datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def extract_temp_bucket(question: str) -> Optional[int]:
    """Extract temperature bucket from market question like '25°C or higher'."""
    m = re.search(r"(\d+)\s*°?\s*[CcFf]", question)
    if m:
        return int(m.group(1))
    return None


def detect_temp_unit(question: str) -> str:
    """Detect if market uses Celsius or Fahrenheit."""
    if re.search(r"\d+\s*°?\s*F", question):
        return "F"
    return "C"


# ── Forecast models ──────────────────────────────────────────────────────────

class ForecastResult(BaseModel):
    """Result from multi-model forecast fetch."""
    model_highs: dict[str, float] = Field(default_factory=dict)
    best_high_c: Optional[float] = None
    sigma: float = FORECAST_UNCERTAINTY_C
    precipitation_prob_max: Optional[float] = None  # 0-100
    temperature_max_c: Optional[float] = None


def _fetch_multi_model_forecast_sync(
    city_name: str, target_date: Optional[str] = None
) -> ForecastResult:
    """Blocking: fetch hourly forecast from multiple models via Open-Meteo.

    Returns ForecastResult with model highs, best estimate, and sigma.
    """
    coords = CITY_COORDS.get(city_name)
    if not coords:
        logger.debug("City '%s' not in coordinate map", city_name)
        return ForecastResult()

    lat, lon = coords
    models = ["ecmwf_ifs025", "gfs_seamless", "ukmo_global_seamless"]
    model_highs: dict[str, float] = {}

    date_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for model in models:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=temperature_2m"
            f"&models={model}"
            f"&timezone=auto"
            f"&forecast_days=2"
        )
        try:
            data = _http_get_json(url)
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            today_temps = [
                t for ts, t in zip(times, temps)
                if ts.startswith(date_str) and t is not None
            ]
            if today_temps:
                model_highs[model] = max(today_temps)
        except Exception as e:
            logger.debug("Forecast fetch failed for model %s: %s", model, e)
            continue

    # Also get default (best-match) model with precipitation data
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,precipitation_probability"
        f"&daily=temperature_2m_max,precipitation_probability_max"
        f"&timezone=auto"
        f"&forecast_days=2"
    )
    precip_prob_max: Optional[float] = None
    temp_max_c: Optional[float] = None

    try:
        data = _http_get_json(url)
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precip_probs = hourly.get("precipitation_probability", [])

        today_temps = [
            t for ts, t in zip(times, temps)
            if ts.startswith(date_str) and t is not None
        ]
        if today_temps:
            model_highs["best_match"] = max(today_temps)
            temp_max_c = max(today_temps)

        # Extract precipitation probability for today
        today_precip = [
            p for ts, p in zip(times, precip_probs)
            if ts.startswith(date_str) and p is not None
        ]
        if today_precip:
            precip_prob_max = max(today_precip)

        # Also try daily data
        daily = data.get("daily", {})
        daily_times = daily.get("time", [])
        for i, dt_str in enumerate(daily_times):
            if dt_str == date_str:
                daily_max_temps = daily.get("temperature_2m_max", [])
                daily_precip = daily.get("precipitation_probability_max", [])
                if i < len(daily_max_temps) and daily_max_temps[i] is not None:
                    temp_max_c = daily_max_temps[i]
                if i < len(daily_precip) and daily_precip[i] is not None:
                    precip_prob_max = daily_precip[i]
                break
    except Exception as e:
        logger.debug("Best-match forecast fetch failed: %s", e)

    if not model_highs:
        return ForecastResult(
            precipitation_prob_max=precip_prob_max,
            temperature_max_c=temp_max_c,
        )

    # Use median of models as best estimate (robust to outliers)
    values = sorted(model_highs.values())
    if len(values) % 2 == 1:
        best_high = values[len(values) // 2]
    else:
        best_high = (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2

    # Compute σ from model spread (min 0.5°C)
    if len(values) >= 2:
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        model_sigma = max(math.sqrt(variance), 0.5)
    else:
        model_sigma = 0.5

    # Total σ = model spread + station uncertainty
    station_extra = 2.0 if city_name in STATION_WARNING_CITIES else 1.0
    total_sigma = math.sqrt(model_sigma**2 + station_extra**2)

    return ForecastResult(
        model_highs=model_highs,
        best_high_c=best_high,
        sigma=total_sigma,
        precipitation_prob_max=precip_prob_max,
        temperature_max_c=temp_max_c,
    )


# ── WeatherScorer ────────────────────────────────────────────────────────────

class WeatherScorer:
    """Deterministic weather probability estimator — no LLMs.

    Routes to the correct scoring method:
    - Temperature markets → Gaussian CDF (ECMWF/GFS/UKMO)
    - Rain/precipitation markets → precipitation_probability_max / 100
    - Heat markets → temperature_max_c / 50
    - Unknown → confidence=0, probability=0.5

    Implements the ProbabilityEstimator protocol (async def estimate).
    """

    # Cache forecasts for the same city within a single run
    _forecast_cache: dict[str, ForecastResult]

    def __init__(self) -> None:
        self._forecast_cache: dict[str, ForecastResult] = {}

    async def estimate(self, market: MarketInfo) -> EstimateResult:
        """Produce a deterministic probability estimate for a weather market."""
        question = market.question
        q_lower = question.lower()

        # Detect market type
        market_type = self._detect_market_type(q_lower)

        if market_type == "unknown":
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning="not a recognized weather market type",
            )

        # Extract city
        city = extract_city_from_title(question)
        if not city:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning="could not extract city from question",
            )

        # Extract target date
        target_date = extract_date_from_title(question)

        # Fetch forecast (with cache)
        cache_key = f"{city}:{target_date or 'today'}"
        if cache_key not in self._forecast_cache:
            forecast = await asyncio.to_thread(
                _fetch_multi_model_forecast_sync, city, target_date
            )
            self._forecast_cache[cache_key] = forecast
            logger.info(
                "WeatherScorer: forecast for %s → best_high=%.1f°C σ=%.2f",
                city, forecast.best_high_c or 0, forecast.sigma,
            )

        forecast = self._forecast_cache[cache_key]

        # Dispatch to correct scoring method
        if market_type == "temperature":
            return self._score_temperature(question, q_lower, forecast, city)
        elif market_type == "rain":
            return self._score_rain(forecast, city)
        elif market_type == "heat":
            return self._score_heat(forecast, city)
        else:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning=f"unhandled weather market type: {market_type}",
            )

    def _detect_market_type(self, q_lower: str) -> str:
        """Detect weather market type from question text."""
        # Temperature patterns (most specific first)
        temp_patterns = [
            r"\d+\s*°?\s*[CcFf]\s+or\s+(higher|above)",
            r"\d+\s*°?\s*[CcFf]\s+or\s+(lower|below)",
            r"highest\s+temperature",
            r"temperature.*be\s+\d+",
            r"\d+°c",
        ]
        if any(re.search(p, q_lower) for p in temp_patterns):
            return "temperature"

        # Rain/precipitation patterns
        rain_patterns = ["rain", "precipitation", "snow", "snowfall"]
        if any(kw in q_lower for kw in rain_patterns):
            return "rain"

        # Heat patterns
        if "heat" in q_lower or "heat index" in q_lower:
            return "heat"

        return "unknown"

    def _score_temperature(
        self,
        question: str,
        q_lower: str,
        forecast: ForecastResult,
        city: str,
    ) -> EstimateResult:
        """Score temperature market using Gaussian CDF."""
        if forecast.best_high_c is None:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning=f"no forecast data for {city}",
            )

        bucket = extract_temp_bucket(question)
        if bucket is None:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning="could not extract temperature bucket from question",
            )

        unit = detect_temp_unit(question)
        if unit == "F":
            bucket_c = fahrenheit_to_c(bucket)
        else:
            bucket_c = float(bucket)

        forecast_c = forecast.best_high_c
        sigma = forecast.sigma

        # Determine market type and compute probability via Gaussian CDF
        if any(kw in q_lower for kw in ["or higher", "or above", "more than", "greater than"]):
            # P(temp >= bucket) = 1 - CDF(bucket; forecast, sigma)
            prob_yes = 1.0 - normal_cdf(bucket_c, mu=forecast_c, sigma=sigma)
            market_type_label = "gt"
        elif any(kw in q_lower for kw in ["or lower", "or below", "less than", "below"]):
            # P(temp <= bucket) = CDF(bucket; forecast, sigma)
            prob_yes = normal_cdf(bucket_c, mu=forecast_c, sigma=sigma)
            market_type_label = "lt"
        else:
            # Exact bucket: P(bucket-0.5 <= temp < bucket+0.5)
            prob_yes = (
                normal_cdf(bucket_c + 0.5, mu=forecast_c, sigma=sigma)
                - normal_cdf(bucket_c - 0.5, mu=forecast_c, sigma=sigma)
            )
            market_type_label = "exact"

        # Clamp probability
        prob_yes = max(0.01, min(0.99, prob_yes))

        # Confidence based on model agreement and forecast quality
        n_models = len(forecast.model_highs)
        if n_models >= 3:
            confidence = 0.85
        elif n_models >= 2:
            confidence = 0.70
        elif n_models >= 1:
            confidence = 0.50
        else:
            confidence = 0.20

        # Reduce confidence for station-warning cities
        if city in STATION_WARNING_CITIES:
            confidence *= 0.85

        model_str = " ".join(
            f"{k.split('_')[0].upper()}:{v:.0f}°"
            for k, v in forecast.model_highs.items()
            if k != "best_match"
        )

        return EstimateResult(
            probability=round(prob_yes, 4),
            confidence=round(confidence, 4),
            source="weather_scorer",
            reasoning=(
                f"temperature {market_type_label} "
                f"city={city} forecast={forecast_c:.1f}°C "
                f"bucket={bucket}{unit} σ={sigma:.2f} "
                f"models=[{model_str}]"
            ),
        )

    def _score_rain(
        self,
        forecast: ForecastResult,
        city: str,
    ) -> EstimateResult:
        """Score rain/precipitation market using Open-Meteo precipitation probability."""
        if forecast.precipitation_prob_max is None:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning=f"no precipitation data for {city}",
            )

        prob = forecast.precipitation_prob_max / 100.0
        prob = max(0.01, min(0.99, prob))

        # Confidence based on data source
        confidence = 0.75

        return EstimateResult(
            probability=round(prob, 4),
            confidence=round(confidence, 4),
            source="weather_scorer",
            reasoning=(
                f"rain city={city} "
                f"precip_prob_max={forecast.precipitation_prob_max}%"
            ),
        )

    def _score_heat(
        self,
        forecast: ForecastResult,
        city: str,
    ) -> EstimateResult:
        """Score heat market using temperature_max_c / 50."""
        if forecast.temperature_max_c is None:
            return EstimateResult(
                probability=0.5,
                confidence=0.0,
                source="weather_scorer",
                reasoning=f"no temperature data for {city}",
            )

        prob = forecast.temperature_max_c / 50.0
        prob = max(0.01, min(0.99, prob))
        confidence = 0.65

        return EstimateResult(
            probability=round(prob, 4),
            confidence=round(confidence, 4),
            source="weather_scorer",
            reasoning=(
                f"heat city={city} "
                f"temp_max_c={forecast.temperature_max_c}°C"
            ),
        )

    def clear_cache(self) -> None:
        """Clear the forecast cache (call between cycles)."""
        self._forecast_cache.clear()
