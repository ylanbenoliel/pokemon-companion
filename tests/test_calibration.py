from __future__ import annotations

import numpy as np
import pytest

from pokemon_companion.vision.calibration import Calibration, compute_homography, rectify


def test_compute_homography_returns_3x3_matrix():
    corners = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))

    matrix = compute_homography(corners, (200, 200))

    assert matrix.shape == (3, 3)


def test_compute_homography_requires_exactly_four_corners():
    with pytest.raises(ValueError):
        compute_homography(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)), (10, 10))  # type: ignore[arg-type]


def test_rectify_produces_expected_output_size():
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    calibration = Calibration(
        corners=((0.0, 0.0), (49.0, 0.0), (49.0, 49.0), (0.0, 49.0)),
        output_size=(80, 60),
    )

    rectified = rectify(frame, calibration)

    assert rectified.shape[:2] == (60, 80)  # (altura, largura), como warpPerspective retorna


def test_calibration_serialization_roundtrip(tmp_path):
    calibration = Calibration(
        corners=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0)),
        output_size=(100, 200),
    )
    path = tmp_path / "calibration.json"

    calibration.save(path)
    loaded = Calibration.load(path)

    assert loaded.corners == calibration.corners
    assert loaded.output_size == calibration.output_size
