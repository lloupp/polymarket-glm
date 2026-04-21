"""Tests for calibration tracker — Brier score, reliability diagram, calibration."""
import pytest
from polymarket_glm.strategy.calibration import (
    CalibrationTracker,
    CalibrationEntry,
    BrierDecomposition,
)


def test_brier_score_perfect():
    """Perfect predictions → Brier = 0."""
    tracker = CalibrationTracker()
    tracker.add(prediction=0.9, outcome=True)   # high prob, event happened
    tracker.add(prediction=0.1, outcome=False)  # low prob, event didn't happen

    assert tracker.brier_score() == pytest.approx(0.0, abs=0.05)


def test_brier_score_worst():
    """Worst predictions → Brier ≈ 1."""
    tracker = CalibrationTracker()
    tracker.add(prediction=0.9, outcome=False)  # predicted high, didn't happen
    tracker.add(prediction=0.1, outcome=True)   # predicted low, happened

    assert tracker.brier_score() > 0.5


def test_brier_score_random():
    """Random 0.5 predictions → Brier = 0.25."""
    tracker = CalibrationTracker()
    for _ in range(100):
        tracker.add(prediction=0.5, outcome=True)

    # Brier = (0.5 - 1)^2 = 0.25
    assert tracker.brier_score() == pytest.approx(0.25, abs=0.01)


def test_brier_decomposition():
    """Brier score decomposes into reliability, resolution, uncertainty."""
    tracker = CalibrationTracker()
    # Add varied predictions
    tracker.add(prediction=0.8, outcome=True)
    tracker.add(prediction=0.8, outcome=True)
    tracker.add(prediction=0.8, outcome=False)
    tracker.add(prediction=0.2, outcome=False)
    tracker.add(prediction=0.2, outcome=False)
    tracker.add(prediction=0.2, outcome=True)

    decomp = tracker.brier_decomposition()
    assert isinstance(decomp, BrierDecomposition)
    assert 0 <= decomp.reliability <= 1
    assert 0 <= decomp.resolution <= 1
    assert 0 <= decomp.uncertainty <= 1
    # Brier = reliability - resolution + uncertainty
    assert abs(decomp.total - (decomp.reliability - decomp.resolution + decomp.uncertainty)) < 0.01


def test_calibration_bins():
    """Should group predictions into bins and compute observed frequency."""
    tracker = CalibrationTracker(n_bins=5)
    # 10 predictions at 0.9, all True
    for _ in range(10):
        tracker.add(prediction=0.9, outcome=True)
    # 10 predictions at 0.1, all False
    for _ in range(10):
        tracker.add(prediction=0.1, outcome=False)

    bins = tracker.calibration_bins()
    assert len(bins) > 0

    # The 0.8-1.0 bin should have observed ~1.0
    high_bin = [b for b in bins if b["predicted_mid"] > 0.7]
    assert len(high_bin) >= 1
    assert high_bin[0]["observed_frequency"] > 0.8

    # The 0.0-0.2 bin should have observed ~0.0
    low_bin = [b for b in bins if b["predicted_mid"] < 0.3]
    assert len(low_bin) >= 1
    assert low_bin[0]["observed_frequency"] < 0.2


def test_calibration_empty():
    """Empty tracker should return safe defaults."""
    tracker = CalibrationTracker()
    assert tracker.brier_score() == 0.0
    assert tracker.count == 0
    assert tracker.calibration_bins() == []


def test_calibration_entry_creation():
    """CalibrationEntry should store prediction, outcome, timestamp."""
    entry = CalibrationEntry(prediction=0.75, outcome=True)
    assert entry.prediction == 0.75
    assert entry.outcome is True
    assert entry.timestamp is not None


def test_tracker_count():
    """Tracker should count entries correctly."""
    tracker = CalibrationTracker()
    assert tracker.count == 0
    tracker.add(0.5, True)
    tracker.add(0.6, False)
    assert tracker.count == 2


def test_per_estimator_tracking():
    """Should support per-estimator Brier scores."""
    tracker = CalibrationTracker()
    tracker.add(prediction=0.9, outcome=True, estimator="heuristic")
    tracker.add(prediction=0.7, outcome=True, estimator="heuristic")
    tracker.add(prediction=0.6, outcome=False, estimator="llm")
    tracker.add(prediction=0.8, outcome=True, estimator="llm")

    heuristic_brier = tracker.brier_score(estimator="heuristic")
    llm_brier = tracker.brier_score(estimator="llm")

    # Heuristic is better calibrated here (0.9 and 0.7, both True)
    # LLM has one miss (0.6 False)
    assert isinstance(heuristic_brier, float)
    assert isinstance(llm_brier, float)


def test_serialization():
    """Should serialize/deserialize entries for persistence."""
    tracker = CalibrationTracker()
    tracker.add(0.7, True, estimator="test")
    tracker.add(0.3, False, estimator="test")

    data = tracker.to_dict()
    tracker2 = CalibrationTracker.from_dict(data)
    assert tracker2.count == 2
    assert tracker2.brier_score() == pytest.approx(tracker.brier_score())
