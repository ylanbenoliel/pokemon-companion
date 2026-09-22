from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from pokemon_companion.ui.camera_debug_view import CameraDebugView, frame_to_pixmap
from pokemon_companion.vision.zone_mapper import default_zone_map


def _synthetic_frame(size: tuple[int, int] = (240, 320)) -> np.ndarray:
    height, width = size
    return np.random.default_rng(0).integers(0, 255, size=(height, width, 3), dtype=np.uint8)


def _click_at(view: CameraDebugView, x: float, y: float) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(event)


def test_frame_to_pixmap_preserves_dimensions(qapp):
    # QPixmap exige uma QApplication já instanciada — sem o fixture `qapp`
    # (pytest-qt), a construção do QPixmap aborta o processo em vez de
    # levantar uma exceção Python capturável.
    frame = _synthetic_frame((100, 150))

    pixmap = frame_to_pixmap(frame)

    assert pixmap.width() == 150
    assert pixmap.height() == 100


def test_show_frame_does_not_raise(qtbot):
    view = CameraDebugView()
    qtbot.addWidget(view)

    view.show_frame(_synthetic_frame())

    assert view.pixmap() is not None


def test_clicking_four_corners_emits_signal_and_completes_calibration(qtbot):
    view = CameraDebugView()
    qtbot.addWidget(view)
    view.show_frame(_synthetic_frame())

    received: list[list[tuple[int, int]]] = []
    view.corners_selected.connect(received.append)

    _click_at(view, 10, 10)
    assert not view.calibration_complete
    _click_at(view, 300, 10)
    _click_at(view, 300, 220)
    _click_at(view, 10, 220)

    assert view.calibration_complete
    assert view.corners == [(10, 10), (300, 10), (300, 220), (10, 220)]
    assert received == [[(10, 10), (300, 10), (300, 220), (10, 220)]]


def test_clicks_after_calibration_are_ignored(qtbot):
    view = CameraDebugView()
    qtbot.addWidget(view)
    view.show_frame(_synthetic_frame())

    for x, y in [(1, 1), (2, 2), (3, 3), (4, 4)]:
        _click_at(view, x, y)
    assert view.calibration_complete

    _click_at(view, 99, 99)

    assert view.corners == [(1, 1), (2, 2), (3, 3), (4, 4)]


def test_reset_corners_allows_recalibration(qtbot):
    view = CameraDebugView()
    qtbot.addWidget(view)
    view.show_frame(_synthetic_frame())
    for x, y in [(1, 1), (2, 2), (3, 3), (4, 4)]:
        _click_at(view, x, y)

    view.reset_corners()

    assert not view.calibration_complete
    assert view.corners == []


def test_show_frame_with_zone_overlay_after_calibration_does_not_raise(qtbot):
    view = CameraDebugView(zone_map=default_zone_map())
    qtbot.addWidget(view)
    for x, y in [(0, 0), (319, 0), (319, 239), (0, 239)]:
        view.show_frame(_synthetic_frame())
        _click_at(view, x, y)

    view.show_frame(_synthetic_frame())

    assert view.calibration_complete
