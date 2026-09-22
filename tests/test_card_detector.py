from __future__ import annotations

import numpy as np

from pokemon_companion.vision.card_detector import ZoneStabilityTracker, is_occupied


def test_is_occupied_false_for_uniform_background():
    empty = np.full((50, 50), 128, dtype=np.uint8)
    assert not is_occupied(empty)


def test_is_occupied_true_for_noisy_crop():
    noisy = np.random.default_rng(0).integers(0, 255, size=(50, 50), dtype=np.uint8)
    assert is_occupied(noisy)


def test_is_occupied_handles_empty_crop():
    assert not is_occupied(np.zeros((0, 0), dtype=np.uint8))


def test_tracker_requires_consecutive_stable_frames():
    tracker = ZoneStabilityTracker(stable_frames=3)

    assert tracker.observe("active", True) is None
    assert tracker.observe("active", True) is None
    assert tracker.observe("active", True) is True


def test_tracker_ignores_flicker_before_stabilizing():
    tracker = ZoneStabilityTracker(stable_frames=3)

    assert tracker.observe("active", True) is None
    assert tracker.observe("active", False) is None
    assert tracker.observe("active", True) is None
    assert tracker.observe("active", True) is None
    assert tracker.observe("active", True) is True


def test_tracker_does_not_repeat_same_stable_state():
    tracker = ZoneStabilityTracker(stable_frames=2)

    assert tracker.observe("a", True) is None
    assert tracker.observe("a", True) is True
    assert tracker.observe("a", True) is None  # já estava estável em True

    assert tracker.observe("a", False) is None  # histórico misto [True, False]
    assert tracker.observe("a", False) is False  # agora estabilizou em False


def test_reset_clears_history_for_zone():
    tracker = ZoneStabilityTracker(stable_frames=2)
    tracker.observe("a", True)
    tracker.observe("a", True)

    tracker.reset("a")

    assert tracker.observe("a", True) is None
