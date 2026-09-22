"""Monta o índice de reconhecimento (`CardRecognizer`) a partir das cartas
de um deck, baixando (e cacheando localmente) a imagem de cada carta única.

Simplificação do MVP: usa a imagem inteira da carta (não um recorte só da
arte) — mais simples de implementar e ainda funciona bem com pHash, ao
custo de exigir um enquadramento de câmera relativamente consistente com a
carta ocupando a maior parte do crop da zona.
"""

from __future__ import annotations

from pathlib import Path

import requests
from PIL import Image

from pokemon_companion.cards_db.models import Card
from pokemon_companion.vision.card_recognizer import CardRecognizer


def _local_image_path(card: Card, images_dir: Path) -> Path:
    return images_dir / f"{card.id}.png"


def load_or_download_card_image(card: Card, images_dir: Path) -> Image.Image | None:
    images_dir.mkdir(parents=True, exist_ok=True)
    local_path = _local_image_path(card, images_dir)

    if local_path.exists():
        return Image.open(local_path).convert("RGBA").convert("RGB")

    if not card.image_url:
        return None

    response = requests.get(card.image_url, timeout=10)
    response.raise_for_status()
    local_path.write_bytes(response.content)
    return Image.open(local_path).convert("RGBA").convert("RGB")


def build_recognition_index_for_deck(
    cards: list[Card],
    images_dir: Path,
    recognizer: CardRecognizer | None = None,
) -> CardRecognizer:
    recognizer = recognizer or CardRecognizer()
    unique_cards = {card.id: card for card in cards}.values()

    pairs = []
    for card in unique_cards:
        image = load_or_download_card_image(card, images_dir)
        if image is not None:
            pairs.append((card, image))

    recognizer.build_index(pairs)
    return recognizer
