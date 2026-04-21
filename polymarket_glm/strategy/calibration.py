"""Calibration tracker — Brier score, reliability diagram, per-estimator tracking.

Measures how well probability estimates match actual outcomes over time.
A well-calibrated estimator predicts 0.7 for events that happen ~70% of the time.

Key metrics:
- Brier score: mean squared error of predictions vs outcomes (lower = better)
- Brier decomposition: reliability - resolution + uncertainty
- Calibration bins: group predictions into buckets, compare predicted vs observed
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class CalibrationEntry(BaseModel):
    """A single prediction-outcome pair for calibration tracking."""
    prediction: float
    outcome: bool
    estimator: str = "unknown"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrierDecomposition(BaseModel):
    """Brier score decomposition: reliability - resolution + uncertainty."""
    reliability: float  # How close predicted probs are to true freq (lower = better)
    resolution: float   # How much true freq varies across bins (higher = better)
    uncertainty: float  # Variance of the outcome (inherent)
    total: float        # The raw Brier score


class CalibrationTracker:
    """Tracks prediction-outcome pairs and computes calibration metrics.

    Supports per-estimator tracking — pass estimator="heuristic" or "llm"
    to segment results.

    Usage:
        tracker = CalibrationTracker(n_bins=10)
        tracker.add(prediction=0.7, outcome=True, estimator="heuristic")
        tracker.add(prediction=0.3, outcome=False, estimator="heuristic")
        print(f"Brier: {tracker.brier_score():.4f}")
        print(f"Bins: {tracker.calibration_bins()}")
    """

    def __init__(self, n_bins: int = 10):
        self._entries: list[CalibrationEntry] = []
        self._n_bins = n_bins

    @property
    def count(self) -> int:
        return len(self._entries)

    def add(
        self,
        prediction: float,
        outcome: bool,
        estimator: str = "unknown",
    ) -> None:
        """Record a prediction-outcome pair."""
        self._entries.append(
            CalibrationEntry(
                prediction=prediction,
                outcome=outcome,
                estimator=estimator,
            )
        )

    def brier_score(self, estimator: str | None = None) -> float:
        """Compute Brier score = mean((prediction - outcome)^2).

        Args:
            estimator: If provided, only compute for this estimator's entries.

        Returns:
            Brier score (0 = perfect, 0.25 = random, 1 = worst).
        """
        entries = self._filter(estimator)
        if not entries:
            return 0.0

        return sum(
            (e.prediction - float(e.outcome)) ** 2 for e in entries
        ) / len(entries)

    def brier_decomposition(self, estimator: str | None = None) -> BrierDecomposition:
        """Decompose Brier score into reliability, resolution, uncertainty.

        Brier = reliability - resolution + uncertainty

        - Reliability: how close predicted probabilities are to observed
          frequencies in each bin (lower = better)
        - Resolution: how much observed frequencies vary across bins
          (higher = better, means the estimator distinguishes cases)
        - Uncertainty: variance of the outcome (inherent to the data)
        """
        entries = self._filter(estimator)
        if not entries:
            return BrierDecomposition(
                reliability=0.0, resolution=0.0, uncertainty=0.0, total=0.0
            )

        n = len(entries)
        total_outcome = sum(float(e.outcome) for e in entries)
        overall_freq = total_outcome / n

        # Uncertainty = p*(1-p) for the overall frequency
        uncertainty = overall_freq * (1 - overall_freq)

        # Bin the entries
        bins = self._compute_bins(entries)

        # Reliability: weighted sum of (predicted - observed)^2 per bin
        reliability = 0.0
        for b in bins:
            reliability += b["count"] * (b["predicted_avg"] - b["observed_frequency"]) ** 2
        reliability /= n

        # Resolution: weighted sum of (observed - overall_freq)^2 per bin
        resolution = 0.0
        for b in bins:
            resolution += b["count"] * (b["observed_frequency"] - overall_freq) ** 2
        resolution /= n

        total = self.brier_score(estimator)

        return BrierDecomposition(
            reliability=reliability,
            resolution=resolution,
            uncertainty=uncertainty,
            total=total,
        )

    def calibration_bins(
        self, estimator: str | None = None
    ) -> list[dict[str, Any]]:
        """Group predictions into bins and compute observed frequency per bin.

        Returns:
            List of dicts with keys:
            - bin_low, bin_high: bin boundaries
            - predicted_mid: midpoint of bin
            - predicted_avg: average prediction in bin
            - observed_frequency: fraction of True outcomes
            - count: number of entries
        """
        entries = self._filter(estimator)
        if not entries:
            return []

        bins = self._compute_bins(entries)
        return bins

    def to_dict(self) -> dict[str, Any]:
        """Serialize tracker state for persistence."""
        return {
            "n_bins": self._n_bins,
            "entries": [e.model_dump(mode="json") for e in self._entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationTracker:
        """Deserialize tracker from saved state."""
        tracker = cls(n_bins=data.get("n_bins", 10))
        for entry_data in data.get("entries", []):
            tracker._entries.append(CalibrationEntry(**entry_data))
        return tracker

    # ── Private ──────────────────────────────────────────────

    def _filter(
        self, estimator: str | None
    ) -> list[CalibrationEntry]:
        """Filter entries by estimator (or return all)."""
        if estimator is None:
            return self._entries
        return [e for e in self._entries if e.estimator == estimator]

    def _compute_bins(
        self, entries: list[CalibrationEntry]
    ) -> list[dict[str, Any]]:
        """Bin entries and compute stats per bin."""
        bin_width = 1.0 / self._n_bins
        bins: dict[int, list[CalibrationEntry]] = {}

        for e in entries:
            # Clamp prediction to [0, 1)
            p = max(0.0, min(e.prediction, 0.9999))
            idx = int(p / bin_width)
            idx = min(idx, self._n_bins - 1)
            bins.setdefault(idx, []).append(e)

        result = []
        for idx in sorted(bins.keys()):
            bin_entries = bins[idx]
            count = len(bin_entries)
            observed = sum(float(e.outcome) for e in bin_entries) / count
            predicted_avg = sum(e.prediction for e in bin_entries) / count

            result.append({
                "bin_low": round(idx * bin_width, 4),
                "bin_high": round((idx + 1) * bin_width, 4),
                "predicted_mid": round((idx + 0.5) * bin_width, 4),
                "predicted_avg": round(predicted_avg, 4),
                "observed_frequency": round(observed, 4),
                "count": count,
            })

        return result
