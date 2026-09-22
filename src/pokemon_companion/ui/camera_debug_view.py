"""Widget de calibração/debug de câmera: mostra o frame ao vivo, deixa o
usuário clicar os 4 cantos do playmat (gerando uma `Calibration`) e, depois
de calibrado, desenha o overlay das zonas (`zone_mapper`) sobre a imagem
retificada — útil tanto para calibrar quanto para depurar detecção.

Nota: não há câmera física neste ambiente de desenvolvimento, então a
integração ao vivo (abrir uma `CameraCapture` real e rodar isto dentro de
`ui/app.py`) não foi validada com hardware real — só com frames sintéticos
nos testes. Ver PLANO.md."""

from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel

from pokemon_companion.vision.zone_mapper import ZoneMap

CORNERS_NEEDED = 4


def frame_to_pixmap(frame: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    height, width, channels = rgb.shape
    image = QImage(rgb.tobytes(), width, height, channels * width, QImage.Format.Format_RGB888)
    # .copy() força o Qt a alocar seu próprio buffer: sem isso, o QImage
    # aponta para a memória do array numpy, que pode ser liberada/realocada
    # assim que `rgb` sai de escopo — causa crash intermitente (memória
    # inválida), não uma exceção Python capturável.
    return QPixmap.fromImage(image.copy())


class CameraDebugView(QLabel):
    """Exibe um frame; antes de calibrado, clique nos 4 cantos do playmat na
    ordem topo-esquerda, topo-direita, baixo-direita, baixo-esquerda."""

    corners_selected = pyqtSignal(list)

    def __init__(self, zone_map: ZoneMap | None = None) -> None:
        super().__init__()
        self._zone_map = zone_map
        self._corners: list[tuple[int, int]] = []
        self._last_frame: np.ndarray | None = None
        self.setMinimumSize(320, 240)

    @property
    def corners(self) -> list[tuple[int, int]]:
        return list(self._corners)

    @property
    def calibration_complete(self) -> bool:
        return len(self._corners) >= CORNERS_NEEDED

    def reset_corners(self) -> None:
        self._corners = []

    def show_frame(self, frame: np.ndarray) -> None:
        self._last_frame = frame
        pixmap = frame_to_pixmap(frame)
        if not self.calibration_complete:
            pixmap = self._draw_corners(pixmap)
        elif self._zone_map is not None:
            pixmap = self._draw_zones(pixmap)
        self.setPixmap(pixmap)

    def _draw_corners(self, pixmap: QPixmap) -> QPixmap:
        if not self._corners:
            return pixmap
        painter = QPainter(pixmap)
        pen = QPen(Qt.GlobalColor.red)
        pen.setWidth(4)
        painter.setPen(pen)
        for x, y in self._corners:
            painter.drawEllipse(x - 4, y - 4, 8, 8)
        painter.end()
        return pixmap

    def _draw_zones(self, pixmap: QPixmap) -> QPixmap:
        assert self._zone_map is not None
        width, height = pixmap.width(), pixmap.height()
        painter = QPainter(pixmap)
        pen = QPen(Qt.GlobalColor.green)
        pen.setWidth(2)
        painter.setPen(pen)
        for zone in self._zone_map.values():
            points = [(int(x * width), int(y * height)) for x, y in zone.polygon]
            for i in range(len(points)):
                x0, y0 = points[i]
                x1, y1 = points[(i + 1) % len(points)]
                painter.drawLine(x0, y0, x1, y1)
        painter.end()
        return pixmap

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self.calibration_complete:
            return
        position = event.position()
        self._corners.append((int(position.x()), int(position.y())))
        if self._last_frame is not None:
            self.show_frame(self._last_frame)
        if self.calibration_complete:
            self.corners_selected.emit(self._corners)
