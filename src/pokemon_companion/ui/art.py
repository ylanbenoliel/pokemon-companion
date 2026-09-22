"""Arte dos Pokémon e desenhos vetoriais do tabuleiro.

Arte do Pokémon (só o bicho, sem a moldura da carta), em ordem de
preferência:
1. Arte oficial em PNG transparente do repositório de sprites da PokeAPI,
   indexada pelo número da Pokédex (`Card.national_pokedex_numbers`, que a
   API do TCG já fornece).
2. Recorte da janela de ilustração da imagem da carta (`Card.image_url`),
   para cartas sem número de Pokédex.
3. `None` — quem desenha usa um placeholder.

Downloads ficam em cache em disco (diretório de dados do usuário) e em
memória; qualquer falha de rede vira `None`, nunca uma exceção na UI.

Orbes de energia e verso de carta são desenhados com QPainter (vetor), em
vez de emoji/imagens: ficam nítidos em qualquer escala e iguais em todo SO.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from pathlib import Path

import requests
from platformdirs import user_data_dir
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

from pokemon_companion.cards_db.models import Card
from pokemon_companion.ui.theme import energy_color
from pokemon_companion.vision.recognition_index import load_or_download_card_image

POKEAPI_ARTWORK_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/"
    "sprites/pokemon/other/official-artwork/{number}.png"
)
DEFAULT_ART_DIR = Path(user_data_dir("pokemon-companion", "pokemon-companion")) / "pokemon_art"

# Janela da ilustração dentro do scan de uma carta (frações de largura/altura).
CARD_ILLUSTRATION_WINDOW = (0.085, 0.105, 0.915, 0.47)

Fetcher = Callable[[str], bytes | None]


def _http_fetch(url: str) -> bytes | None:
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.content


class ArtProvider:
    def __init__(self, art_dir: Path | None = None, fetch: Fetcher | None = None) -> None:
        self._art_dir = art_dir or DEFAULT_ART_DIR
        self._fetch = fetch or _http_fetch
        self._cache: dict[str, QPixmap | None] = {}

    def pokemon_art(self, card: Card) -> QPixmap | None:
        if card.id in self._cache:
            return self._cache[card.id]
        pixmap = self._official_artwork(card) or self._cropped_card_art(card)
        self._cache[card.id] = pixmap
        return pixmap

    def _official_artwork(self, card: Card) -> QPixmap | None:
        if not card.national_pokedex_numbers:
            return None
        number = card.national_pokedex_numbers[0]
        path = self._art_dir / f"{number}.png"
        if not path.exists():
            data = self._fetch(POKEAPI_ARTWORK_URL.format(number=number))
            if not data:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        pixmap = QPixmap(str(path))
        return None if pixmap.isNull() else pixmap

    def _cropped_card_art(self, card: Card) -> QPixmap | None:
        try:
            image = load_or_download_card_image(card, self._art_dir / "cards")
        except (requests.RequestException, OSError):
            return None
        if image is None:
            return None
        left, top, right, bottom = CARD_ILLUSTRATION_WINDOW
        width, height = image.size
        cropped = image.crop(
            (int(left * width), int(top * height), int(right * width), int(bottom * height))
        )
        buffer = io.BytesIO()
        cropped.convert("RGB").save(buffer, format="PNG")
        pixmap = QPixmap()
        return pixmap if pixmap.loadFromData(buffer.getvalue(), "PNG") else None


def _glyph_path(energy_type: str) -> QPainterPath:
    """Símbolo do tipo num quadrado unitário [0,1]x[0,1]."""
    path = QPainterPath()
    if energy_type == "Fire":
        path.moveTo(0.5, 0.04)
        path.cubicTo(0.78, 0.32, 0.92, 0.56, 0.8, 0.78)
        path.cubicTo(0.7, 0.97, 0.3, 0.97, 0.2, 0.78)
        path.cubicTo(0.1, 0.56, 0.28, 0.44, 0.37, 0.28)
        path.cubicTo(0.42, 0.48, 0.5, 0.54, 0.56, 0.48)
        path.cubicTo(0.62, 0.34, 0.52, 0.2, 0.5, 0.04)
    elif energy_type == "Water":
        path.moveTo(0.5, 0.04)
        path.cubicTo(0.66, 0.3, 0.86, 0.5, 0.86, 0.66)
        path.cubicTo(0.86, 0.86, 0.69, 0.96, 0.5, 0.96)
        path.cubicTo(0.31, 0.96, 0.14, 0.86, 0.14, 0.66)
        path.cubicTo(0.14, 0.5, 0.34, 0.3, 0.5, 0.04)
    elif energy_type == "Grass":
        path.moveTo(0.14, 0.88)
        path.cubicTo(0.08, 0.4, 0.44, 0.1, 0.9, 0.1)
        path.cubicTo(0.9, 0.56, 0.6, 0.9, 0.14, 0.88)
    elif energy_type == "Lightning":
        path.addPolygon(
            QPolygonF(
                [
                    QPointF(0.6, 0.03),
                    QPointF(0.18, 0.56),
                    QPointF(0.46, 0.56),
                    QPointF(0.37, 0.97),
                    QPointF(0.82, 0.42),
                    QPointF(0.54, 0.42),
                    QPointF(0.64, 0.03),
                ]
            )
        )
    elif energy_type == "Psychic":
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.moveTo(0.04, 0.5)
        path.quadTo(0.5, 0.02, 0.96, 0.5)
        path.quadTo(0.5, 0.98, 0.04, 0.5)
        path.addEllipse(QRectF(0.37, 0.37, 0.26, 0.26))
    elif energy_type == "Darkness":
        outer = QPainterPath()
        outer.addEllipse(QRectF(0.1, 0.1, 0.8, 0.8))
        bite = QPainterPath()
        bite.addEllipse(QRectF(0.32, 0.02, 0.72, 0.72))
        path = outer.subtracted(bite)
    elif energy_type == "Metal":
        path.setFillRule(Qt.FillRule.OddEvenFill)
        hexagon = [QPointF(0.5, 0.05), QPointF(0.9, 0.28), QPointF(0.9, 0.72)]
        hexagon += [QPointF(0.5, 0.95), QPointF(0.1, 0.72), QPointF(0.1, 0.28)]
        path.addPolygon(QPolygonF(hexagon))
        path.closeSubpath()
        path.addEllipse(QRectF(0.34, 0.34, 0.32, 0.32))
    elif energy_type == "Fighting":
        points = []
        for i in range(16):
            radius = 0.46 if i % 2 == 0 else 0.26
            angle = i * math.pi / 8
            points.append(QPointF(0.5 + radius * math.cos(angle), 0.5 + radius * math.sin(angle)))
        path.addPolygon(QPolygonF(points))
    elif energy_type == "Fairy":
        sparkle = [QPointF(0.5, 0.02), QPointF(0.62, 0.38), QPointF(0.98, 0.5)]
        sparkle += [QPointF(0.62, 0.62), QPointF(0.5, 0.98), QPointF(0.38, 0.62)]
        sparkle += [QPointF(0.02, 0.5), QPointF(0.38, 0.38)]
        path.addPolygon(QPolygonF(sparkle))
    else:  # Colorless / Dragon / desconhecido: estrela de 5 pontas
        points = []
        for i in range(10):
            radius = 0.48 if i % 2 == 0 else 0.2
            angle = -math.pi / 2 + i * math.pi / 5
            points.append(QPointF(0.5 + radius * math.cos(angle), 0.53 + radius * math.sin(angle)))
        path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


def paint_energy_orb(painter: QPainter, rect: QRectF, energy_type: str) -> None:
    """Orbe brilhante na cor do tipo com o símbolo branco no centro."""
    base = energy_color(energy_type)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QRadialGradient(
        rect.center() - QPointF(rect.width() * 0.18, rect.height() * 0.2), rect.width() * 0.75
    )
    gradient.setColorAt(0.0, base.lighter(165))
    gradient.setColorAt(0.55, base)
    gradient.setColorAt(1.0, base.darker(160))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor(255, 255, 255, 230), max(rect.width() * 0.07, 1.0)))
    painter.drawEllipse(rect)

    inset = rect.width() * 0.24
    glyph_rect = rect.adjusted(inset, inset, -inset, -inset)
    painter.translate(glyph_rect.topLeft())
    painter.scale(glyph_rect.width(), glyph_rect.height())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 240))
    painter.drawPath(_glyph_path(energy_type))
    painter.restore()


def paint_card_back(painter: QPainter, rect: QRectF) -> None:
    """Verso de carta genérico: azul profundo com um emblema de bola."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = rect.width() * 0.09

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#2b5bd7"))
    gradient.setColorAt(1.0, QColor("#12297a"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor("#f2d16b"), max(rect.width() * 0.04, 1.0)))
    painter.drawRoundedRect(rect, radius, radius)

    size = min(rect.width(), rect.height()) * 0.5
    emblem = QRectF(0, 0, size, size)
    emblem.moveCenter(rect.center())
    painter.setPen(QPen(QColor("#1b1b1b"), size * 0.07))
    painter.setBrush(QColor("#f7f7f7"))
    painter.drawEllipse(emblem)
    top_half = QPainterPath()
    top_half.moveTo(emblem.left(), emblem.center().y())
    top_half.arcTo(emblem, 180, -180)
    top_half.closeSubpath()
    painter.setBrush(QColor("#e3350d"))
    painter.drawPath(top_half)
    painter.drawLine(
        QPointF(emblem.left(), emblem.center().y()), QPointF(emblem.right(), emblem.center().y())
    )
    button = QRectF(0, 0, size * 0.3, size * 0.3)
    button.moveCenter(emblem.center())
    painter.setBrush(QColor("#f7f7f7"))
    painter.drawEllipse(button)
    painter.restore()


def paint_placeholder_art(painter: QPainter, rect: QRectF, energy_type: str) -> None:
    """Silhueta genérica (orbe do tipo, translúcido) para quando não há arte."""
    size = min(rect.width(), rect.height()) * 0.6
    orb = QRectF(0, 0, size, size)
    orb.moveCenter(rect.center())
    painter.save()
    painter.setOpacity(0.55)
    paint_energy_orb(painter, orb, energy_type)
    painter.restore()
