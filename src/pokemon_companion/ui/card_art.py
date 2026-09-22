"""Carrega e cacheia (em memória + disco) a arte de cada carta como
QPixmap, reaproveitando o download/cache de `vision.recognition_index`
(que já existe para o reconhecimento por câmera). Cartas sem `image_url`
(ex: os mocks de `demo_data.py` sem URL real) ou com falha de rede caem
num placeholder colorido pelo tipo, para a UI nunca quebrar por falta de
internet."""

from __future__ import annotations

import io
from pathlib import Path

from platformdirs import user_data_dir
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap

from pokemon_companion.cards_db.models import Card
from pokemon_companion.ui.theme import energy_color
from pokemon_companion.vision.recognition_index import load_or_download_card_image

DEFAULT_IMAGES_DIR = Path(user_data_dir("pokemon-companion", "pokemon-companion")) / "card_images"


def _placeholder_pixmap(card: Card, size: int) -> QPixmap:
    primary_type = card.types[0] if card.types else "Colorless"
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(energy_color(primary_type)))

    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPointSize(max(size // 3, 10))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "?")
    painter.end()

    return pixmap


class CardArtProvider:
    """Instancie uma vez e reuse — o cache em memória é por instância."""

    def __init__(self, images_dir: Path | None = None) -> None:
        self._images_dir = images_dir or DEFAULT_IMAGES_DIR
        self._cache: dict[str, QPixmap] = {}

    def get_pixmap(self, card: Card, size: int) -> QPixmap:
        cache_key = f"{card.id}:{size}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        pixmap = self._load_pixmap(card, size)
        if pixmap is None:
            pixmap = _placeholder_pixmap(card, size)

        self._cache[cache_key] = pixmap
        return pixmap

    def _load_pixmap(self, card: Card, size: int) -> QPixmap | None:
        try:
            image = load_or_download_card_image(card, self._images_dir)
        except Exception:  # noqa: BLE001 — falha de rede/IO vira placeholder, nunca crash da UI
            return None

        if image is None:
            return None

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        loaded = QPixmap()
        if not loaded.loadFromData(buffer.getvalue(), "PNG"):
            return None

        return loaded.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )


card_art_provider = CardArtProvider()
