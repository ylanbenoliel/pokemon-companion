"""Captura de vídeo multiplataforma.

Ponto de atenção real de portabilidade (ver PLANO.md): `cv2.VideoCapture`
precisa de um backend diferente por SO para abrir a câmera de forma
confiável — com fallback para o backend automático (`cv2.CAP_ANY`) se o
específico falhar.
"""

from __future__ import annotations

import platform

import cv2
import numpy as np

_BACKEND_BY_SYSTEM = {
    "Windows": cv2.CAP_DSHOW,
    "Darwin": cv2.CAP_AVFOUNDATION,
    "Linux": cv2.CAP_V4L2,
}


def get_capture_backend(system: str | None = None) -> int:
    system = system or platform.system()
    return _BACKEND_BY_SYSTEM.get(system, cv2.CAP_ANY)


class CameraCapture:
    def __init__(self, device_index: int = 0, backend: int | None = None) -> None:
        self._device_index = device_index
        self._backend = backend if backend is not None else get_capture_backend()
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        cap = cv2.VideoCapture(self._device_index, self._backend)
        if not cap.isOpened() and self._backend != cv2.CAP_ANY:
            cap.release()
            cap = cv2.VideoCapture(self._device_index, cv2.CAP_ANY)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir a câmera (índice {self._device_index}).")
        self._cap = cap

    def read_frame(self) -> np.ndarray | None:
        if self._cap is None:
            raise RuntimeError("Chame open() antes de read_frame().")
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> CameraCapture:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
