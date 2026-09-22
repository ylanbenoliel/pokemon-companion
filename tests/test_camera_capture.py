from __future__ import annotations

import cv2
import pytest

from pokemon_companion.vision.camera_capture import CameraCapture, get_capture_backend


class _FakeVideoCapture:
    def __init__(self, opened_backends: set[int]) -> None:
        self._opened_backends = opened_backends
        self.backend: int | None = None
        self.released = False
        self.opened = False

    def __call__(self, index: int, backend: int) -> _FakeVideoCapture:
        self.backend = backend
        self.opened = backend in self._opened_backends
        return self

    def isOpened(self) -> bool:
        return self.opened

    def release(self) -> None:
        self.released = True
        self.opened = False

    def read(self) -> tuple[bool, str | None]:
        return (True, "frame") if self.opened else (False, None)


def test_get_capture_backend_maps_known_systems():
    assert get_capture_backend("Windows") == cv2.CAP_DSHOW
    assert get_capture_backend("Darwin") == cv2.CAP_AVFOUNDATION
    assert get_capture_backend("Linux") == cv2.CAP_V4L2
    assert get_capture_backend("Plan9") == cv2.CAP_ANY


def test_open_succeeds_with_preferred_backend(monkeypatch):
    fake = _FakeVideoCapture(opened_backends={12345})
    monkeypatch.setattr(cv2, "VideoCapture", fake)

    capture = CameraCapture(device_index=0, backend=12345)
    capture.open()

    assert fake.backend == 12345
    assert capture.read_frame() == "frame"


def test_open_falls_back_to_cap_any(monkeypatch):
    attempts: list[int] = []

    class TrackingFakeCapture(_FakeVideoCapture):
        def __call__(self, index: int, backend: int) -> TrackingFakeCapture:
            attempts.append(backend)
            return super().__call__(index, backend)

    monkeypatch.setattr(cv2, "VideoCapture", TrackingFakeCapture(opened_backends={cv2.CAP_ANY}))

    capture = CameraCapture(device_index=0, backend=99999)
    capture.open()

    assert attempts == [99999, cv2.CAP_ANY]


def test_open_raises_when_no_backend_works(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", _FakeVideoCapture(opened_backends=set()))

    with pytest.raises(RuntimeError):
        CameraCapture(device_index=0, backend=1).open()


def test_read_frame_before_open_raises():
    with pytest.raises(RuntimeError):
        CameraCapture().read_frame()


def test_context_manager_opens_and_releases(monkeypatch):
    fake = _FakeVideoCapture(opened_backends={1})
    monkeypatch.setattr(cv2, "VideoCapture", fake)

    with CameraCapture(backend=1) as capture:
        assert capture.read_frame() == "frame"

    assert fake.released is True
