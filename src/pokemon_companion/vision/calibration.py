"""Calibração do playmat: 4 cliques do usuário (cantos do tapete) viram uma
homografia que retifica qualquer frame para uma vista "top-down" de
proporções fixas, sobre a qual as zonas (`zone_mapper.py`) são definidas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

Point = tuple[float, float]


@dataclass(frozen=True)
class Calibration:
    corners: tuple[Point, Point, Point, Point]  # top-left, top-right, bottom-right, bottom-left
    output_size: tuple[int, int]  # (largura, altura) em pixels da vista retificada

    @property
    def homography(self) -> np.ndarray:
        return compute_homography(self.corners, self.output_size)

    def to_dict(self) -> dict:
        return {"corners": list(self.corners), "output_size": list(self.output_size)}

    @staticmethod
    def from_dict(data: dict) -> Calibration:
        corners = tuple(tuple(p) for p in data["corners"])
        if len(corners) != 4:
            raise ValueError("Calibração precisa de exatamente 4 cantos.")
        return Calibration(corners=corners, output_size=tuple(data["output_size"]))  # type: ignore[arg-type]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> Calibration:
        return Calibration.from_dict(json.loads(path.read_text(encoding="utf-8")))


def compute_homography(
    corners: tuple[Point, Point, Point, Point], output_size: tuple[int, int]
) -> np.ndarray:
    if len(corners) != 4:
        raise ValueError("São necessários exatamente 4 cantos para calcular a homografia.")
    width, height = output_size
    src = np.array(corners, dtype=np.float32)
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix: np.ndarray = cv2.getPerspectiveTransform(src, dst)
    return matrix


def rectify(frame: np.ndarray, calibration: Calibration) -> np.ndarray:
    rectified: np.ndarray = cv2.warpPerspective(
        frame, calibration.homography, calibration.output_size
    )
    return rectified
