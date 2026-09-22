from __future__ import annotations

import dataclasses
import io

import numpy as np
from PIL import Image

from pokemon_companion.vision.recognition_index import (
    build_recognition_index_for_deck,
    load_or_download_card_image,
)


def _png_bytes(color: tuple[int, int, int] = (10, 20, 30), size: tuple[int, int] = (8, 8)) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


def _fake_image_for(card_id: str) -> Image.Image:
    seed = abs(hash(card_id)) % 1000
    array = np.random.default_rng(seed).integers(0, 255, size=(16, 16, 3), dtype=np.uint8)
    return Image.fromarray(array, mode="RGB")


def test_load_or_download_uses_local_cache_when_present(tmp_path, charmander):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / f"{charmander.id}.png").write_bytes(_png_bytes())

    image = load_or_download_card_image(charmander, images_dir)

    assert image is not None
    assert image.size == (8, 8)


def test_load_or_download_fetches_and_caches_when_missing(tmp_path, charmander, monkeypatch):
    images_dir = tmp_path / "images"
    card_with_url = dataclasses.replace(charmander, image_url="https://example.com/card.png")

    monkeypatch.setattr(
        "pokemon_companion.vision.recognition_index.requests.get",
        lambda url, timeout: _FakeResponse(_png_bytes()),
    )

    image = load_or_download_card_image(card_with_url, images_dir)

    assert image is not None
    assert (images_dir / f"{card_with_url.id}.png").exists()


def test_load_or_download_returns_none_without_url_or_cache(tmp_path, charmander):
    images_dir = tmp_path / "images"

    assert load_or_download_card_image(charmander, images_dir) is None


def test_build_recognition_index_deduplicates_repeated_cards(
    tmp_path, charmander, squirtle, monkeypatch
):
    monkeypatch.setattr(
        "pokemon_companion.vision.recognition_index.load_or_download_card_image",
        lambda card, images_dir: _fake_image_for(card.id),
    )

    deck = [charmander, charmander, charmander, squirtle]
    recognizer = build_recognition_index_for_deck(deck, tmp_path / "images")

    assert len(recognizer) == 2


def test_build_recognition_index_skips_cards_without_image(
    tmp_path, charmander, squirtle, monkeypatch
):
    def fake_loader(card: object, images_dir: object) -> Image.Image | None:
        return _fake_image_for(charmander.id) if card is charmander else None

    monkeypatch.setattr(
        "pokemon_companion.vision.recognition_index.load_or_download_card_image", fake_loader
    )

    recognizer = build_recognition_index_for_deck([charmander, squirtle], tmp_path / "images")

    assert len(recognizer) == 1
