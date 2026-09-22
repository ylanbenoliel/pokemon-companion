"""Detecção de ocupação de zona e rastreamento de estabilidade entre frames.

Roda a poucos FPS de análise (o jogo é por turnos, mudanças são discretas).
Só dispara reconhecimento quando uma zona muda de estado e essa mudança se
confirma por N frames seguidos — evita falsos positivos por tremor de mão
ou sombra passageira.
"""

from __future__ import annotations

import cv2
import numpy as np


def is_occupied(crop: np.ndarray, threshold: float = 30.0) -> bool:
    """Heurística simples: uma zona vazia do tapete tem baixa variância de
    intensidade (fundo uniforme); uma carta introduz bordas e cores
    variadas, elevando o desvio padrão dos pixels em tons de cinza."""
    if crop.size == 0:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    return bool(gray.std() > threshold)


class ZoneStabilityTracker:
    def __init__(self, stable_frames: int = 3) -> None:
        self._stable_frames = stable_frames
        self._history: dict[str, list[bool]] = {}
        self._last_stable: dict[str, bool] = {}

    def observe(self, zone_name: str, occupied: bool) -> bool | None:
        """Retorna o novo estado estável (True/False) só quando ele muda e
        se confirma por `stable_frames` leituras seguidas; `None` enquanto
        instável ou sem mudança em relação ao último estado estável."""
        history = self._history.setdefault(zone_name, [])
        history.append(occupied)
        if len(history) > self._stable_frames:
            history.pop(0)

        if len(history) < self._stable_frames or len(set(history)) != 1:
            return None

        stable_state = history[0]
        if self._last_stable.get(zone_name) == stable_state:
            return None

        self._last_stable[zone_name] = stable_state
        return stable_state

    def reset(self, zone_name: str | None = None) -> None:
        if zone_name is None:
            self._history.clear()
            self._last_stable.clear()
        else:
            self._history.pop(zone_name, None)
            self._last_stable.pop(zone_name, None)
