"""Zonas do playmat (ativo, banco, prêmios, descarte) como polígonos em
coordenadas normalizadas (0-1) sobre a imagem retificada pela calibração —
editável em JSON, já que layouts de playmat variam. A mão fica de fora de
propósito (não é uma zona monitorada pela câmera)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

Point = tuple[float, float]

ACTIVE = "active"
BENCH_PREFIX = "bench_"
PRIZES = "prizes"
DISCARD = "discard"


@dataclass(frozen=True)
class Zone:
    name: str
    polygon: tuple[Point, ...]  # >= 3 pontos, coordenadas normalizadas (0-1)


ZoneMap = dict[str, Zone]


def _rect(x0: float, y0: float, x1: float, y1: float) -> tuple[Point, ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def default_zone_map() -> ZoneMap:
    """Layout de exemplo (grade simples) — pensado para ser editado pelo
    usuário conforme o playmat físico real, não uma posição oficial."""
    zones: ZoneMap = {
        ACTIVE: Zone(ACTIVE, _rect(0.35, 0.35, 0.65, 0.55)),
        PRIZES: Zone(PRIZES, _rect(0.0, 0.0, 1.0, 0.15)),
        DISCARD: Zone(DISCARD, _rect(0.85, 0.6, 1.0, 0.8)),
    }
    bench_width = 1.0 / 5
    for i in range(5):
        x0 = i * bench_width
        zones[f"{BENCH_PREFIX}{i}"] = Zone(
            f"{BENCH_PREFIX}{i}", _rect(x0, 0.65, x0 + bench_width, 0.95)
        )
    return zones


def crop_zone(frame: np.ndarray, zone: Zone) -> np.ndarray:
    height, width = frame.shape[:2]
    points = np.array([[int(x * width), int(y * height)] for x, y in zone.polygon])
    x, y, w, h = cv2.boundingRect(points)
    x, y = max(x, 0), max(y, 0)
    return frame[y : y + h, x : x + w]


def point_in_zone(point: Point, zone: Zone) -> bool:
    contour = np.array(zone.polygon, dtype=np.float32)
    result = cv2.pointPolygonTest(contour, point, False)
    return result >= 0


def save_zone_map(zone_map: ZoneMap, path: Path) -> None:
    payload = {name: list(zone.polygon) for name, zone in zone_map.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_zone_map(path: Path) -> ZoneMap:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: Zone(name, tuple(tuple(p) for p in polygon)) for name, polygon in payload.items()}
