"""Tela inicial: escolher o seu deck, o deck da IA e a dificuldade antes da
partida (no espírito da seleção de decks do Pokémon TCG Pocket).

Os decks vêm dos arquivos de decklist em `examples/decks/` (meta atual,
Mundial) e nos seus próprios decks em `data/decks/`. A arte de cada deck usa
o Pokémon principal da lista, buscado **só no cache local** — a tela abre
rápido e sem internet; decks ainda não importados aparecem com a cor do tipo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.models import Card
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.theme import GOLD, energy_color, primary_type, ui_font

DECK_FOLDERS = (
    Path("examples/decks/top"),
    Path("examples/decks/worlds2026"),
    Path("examples/decks"),
    Path("data/decks"),
)
GROUP_LABELS = {
    "top": "Meta atual",
    "worlds2026": "Mundial 2026",
    "decks": "Exemplos",
    "data": "Meus decks",
}
DIFFICULTIES = (("easy", "FÁCIL"), ("medium", "MÉDIO"), ("hard", "DIFÍCIL"))
TILE_SIZE = QSize(168, 148)


@dataclass(frozen=True)
class DeckEntry:
    path: Path
    title: str
    subtitle: str
    group: str


def _pretty(stem: str) -> str:
    """ "03_ns_zoroark_ex" → "NS Zoroark ex"."""
    words = [w for w in stem.split("_") if not w.isdigit()]
    return " ".join("ex" if w == "ex" else w.capitalize() for w in words)


def read_entry(path: Path) -> DeckEntry:
    """Título e subtítulo vêm dos comentários do arquivo, quando existem
    (`# Dragapult — #1 do meta (11% dos pontos)`)."""
    title, subtitle = _pretty(path.stem), ""
    first = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            first = line.lstrip("# ").strip()
            break
    if first:
        head, _, tail = first.partition("—")
        title = head.strip() or title
        subtitle = tail.strip()
    group = GROUP_LABELS.get(path.parent.name, path.parent.name)
    return DeckEntry(path=path, title=title, subtitle=subtitle, group=group)


def discover_decks(folders: tuple[Path, ...] = DECK_FOLDERS) -> list[DeckEntry]:
    entries: list[DeckEntry] = []
    seen: set[Path] = set()
    for folder in folders:
        for path in sorted(folder.glob("*.txt")):
            if path.resolve() not in seen:
                seen.add(path.resolve())
                entries.append(read_entry(path))
    return entries


class _CacheOnly:
    """Fonte de cartas que nunca vai à rede (a tela não pode travar)."""

    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        return None


def deck_highlight(entry: DeckEntry, cache: CardCache) -> Card | None:
    """Pokémon que representa o deck: o do nome do arquétipo, senão o de
    maior HP. `None` se a lista ainda não estiver no cache local."""
    cards, _ = load_deck(entry.path, cache, _CacheOnly())
    pokemon = [card for card in cards if card.is_pokemon]
    if not pokemon:
        return None
    named = [card for card in pokemon if card.name.lower() in entry.title.lower()]
    return max(named or pokemon, key=lambda card: card.hp or 0)


class DeckTile(QFrame):
    """Cartão de um deck na grade (arte, nome e subtítulo)."""

    clicked = pyqtSignal(object)

    def __init__(self, entry: DeckEntry) -> None:
        super().__init__()
        self.entry = entry
        self._selected = False
        self._accent = QColor("#4a5878")
        self.setFixedSize(TILE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.art = QLabel()
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setFixedHeight(78)
        self.name = QLabel(entry.title)
        self.name.setFont(ui_font(10.5))
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.detail = QLabel(entry.subtitle)
        self.detail.setFont(ui_font(8))
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail.setStyleSheet("color: rgba(255,255,255,140);")
        for widget in (self.art, self.name, self.detail):
            layout.addWidget(widget)
        self._restyle()

    def set_art(self, pixmap: QPixmap | None, accent: QColor) -> None:
        self._accent = accent
        if pixmap is not None and not pixmap.isNull():
            self.art.setPixmap(
                pixmap.scaled(
                    150,
                    78,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self._restyle()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._restyle()

    @property
    def selected(self) -> bool:
        return self._selected

    def _restyle(self) -> None:
        accent = self._accent
        border = GOLD.name() if self._selected else "rgba(255,255,255,45)"
        width = 3 if self._selected else 1
        self.setStyleSheet(f"""
            DeckTile {{
                border: {width}px solid {border};
                border-radius: 14px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {accent.lighter(115).name()}, stop:1 {accent.darker(175).name()});
            }}
            QLabel {{ color: white; background: transparent; }}
            """)

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 (API do Qt)
        self.clicked.emit(self.entry)


class DeckColumn(QWidget):
    """Coluna rolável com o título e a grade de decks."""

    selected = pyqtSignal(object)

    def __init__(self, title: str, entries: list[DeckEntry], columns: int = 3) -> None:
        super().__init__()
        self.tiles: list[DeckTile] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(title)
        heading.setFont(ui_font(13, QFont.Weight.Black))
        heading.setStyleSheet(f"color: {GOLD.name()}; background: transparent;")
        layout.addWidget(heading)

        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setSpacing(12)
        row = column = 0
        group = ""
        for entry in entries:
            if entry.group != group:
                group = entry.group
                if column:
                    row, column = row + 1, 0
                label = QLabel(group.upper())
                label.setFont(ui_font(9))
                label.setStyleSheet("color: rgba(255,255,255,120);")
                grid.addWidget(label, row, 0, 1, columns)
                row += 1
            tile = DeckTile(entry)
            tile.clicked.connect(self._on_tile_clicked)
            self.tiles.append(tile)
            grid.addWidget(tile, row, column)
            column += 1
            if column == columns:
                row, column = row + 1, 0
        grid.setRowStretch(row + 1, 1)

        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll)

    def _on_tile_clicked(self, entry: DeckEntry) -> None:
        self.select(entry)
        self.selected.emit(entry)

    def select(self, entry: DeckEntry | None) -> None:
        for tile in self.tiles:
            tile.set_selected(entry is not None and tile.entry.path == entry.path)

    @property
    def selection(self) -> DeckEntry | None:
        return next((tile.entry for tile in self.tiles if tile.selected), None)


class DeckMenu(QWidget):
    """Tela de seleção: seu deck × deck da IA, dificuldade e "JOGAR"."""

    start_requested = pyqtSignal(object, object, str)

    def __init__(
        self,
        entries: list[DeckEntry] | None = None,
        art: ArtProvider | None = None,
        cache_factory: Callable[[], CardCache] = CardCache,
    ) -> None:
        super().__init__()
        self._entries = entries if entries is not None else discover_decks()
        self._art = art
        self._cache_factory = cache_factory
        self.difficulty = "medium"

        self.setStyleSheet("background: #0d1730;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        title = QLabel("ESCOLHA OS DECKS")
        title.setFont(ui_font(22, QFont.Weight.Black))
        title.setStyleSheet(f"color: {GOLD.name()}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        self.player_column = DeckColumn("SEU DECK", self._entries)
        self.opponent_column = DeckColumn("DECK DA IA", self._entries)
        columns.addWidget(self.player_column)
        columns.addWidget(self.opponent_column)
        outer.addLayout(columns, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        label = QLabel("DIFICULDADE")
        label.setFont(ui_font(10))
        label.setStyleSheet("color: rgba(255,255,255,170); background: transparent;")
        controls.addWidget(label)
        self.difficulty_buttons = QButtonGroup(self)
        for key, text in DIFFICULTIES:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(key == self.difficulty)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(self._difficulty_style())
            button.clicked.connect(lambda _=False, k=key: self._set_difficulty(k))
            self.difficulty_buttons.addButton(button)
            controls.addWidget(button)
        controls.addStretch(1)

        self.status = QLabel("")
        self.status.setFont(ui_font(9.5))
        self.status.setStyleSheet("color: rgba(255,255,255,150); background: transparent;")
        controls.addWidget(self.status)

        self.play_button = QPushButton("JOGAR")
        self.play_button.setFont(ui_font(13, QFont.Weight.Black))
        self.play_button.setMinimumSize(190, 46)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setStyleSheet("""
            QPushButton { color: #10203f; border-radius: 23px; padding: 6px 18px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffd34d, stop:1 #e09a00); }
            QPushButton:disabled { color: rgba(255,255,255,120); background: #39425a; }
            """)
        self.play_button.clicked.connect(self._start)
        controls.addWidget(self.play_button)
        outer.addLayout(controls)

        self.player_column.selected.connect(lambda _: self._refresh())
        self.opponent_column.selected.connect(lambda _: self._refresh())
        if self._entries:
            self.player_column.select(self._entries[0])
            self.opponent_column.select(self._entries[min(1, len(self._entries) - 1)])
        self._refresh()
        QTimer.singleShot(0, self.load_art)

    # ------------------------------------------------------------------
    def _difficulty_style(self) -> str:
        return """
            QPushButton { color: white; border: 1px solid rgba(255,255,255,60);
                border-radius: 16px; padding: 7px 16px; background: #1d2b4f; }
            QPushButton:checked { color: #10203f; background: #ffd34d;
                border: 1px solid #ffd34d; font-weight: bold; }
        """

    def _set_difficulty(self, key: str) -> None:
        self.difficulty = key

    def _refresh(self) -> None:
        ready = (
            self.player_column.selection is not None and self.opponent_column.selection is not None
        )
        self.play_button.setEnabled(ready)

    def load_art(self) -> None:
        """Preenche a arte dos decks já importados (uma carta por vez, para a
        tela não travar na primeira abertura)."""
        art = self._art or ArtProvider()
        tiles = [*self.player_column.tiles, *self.opponent_column.tiles]
        by_path: dict[Path, list[DeckTile]] = {}
        for tile in tiles:
            by_path.setdefault(tile.entry.path, []).append(tile)
        pending = list(by_path.items())

        def step() -> None:
            if not pending:
                self.status.setText("")
                return
            path, group = pending.pop(0)
            self.status.setText(f"Carregando decks… ({len(pending)} restantes)")
            try:
                with self._cache_factory() as cache:
                    card = deck_highlight(group[0].entry, cache)
            except Exception:  # noqa: BLE001 - arte é opcional
                card = None
            if card is not None:
                pixmap = art.card_art(card)
                accent = QColor(energy_color(primary_type(card.types)))
                for tile in group:
                    tile.set_art(pixmap, accent)
            QTimer.singleShot(0, step)

        QTimer.singleShot(0, step)

    def _start(self) -> None:
        player, opponent = self.player_column.selection, self.opponent_column.selection
        if player is None or opponent is None:
            return
        self.play_button.setEnabled(False)
        self.status.setText("Importando as cartas…")
        self.start_requested.emit(player.path, opponent.path, self.difficulty)


def menu_size_hint() -> QSize:
    return QSize(1180, 760)


__all__ = [
    "DeckColumn",
    "DeckEntry",
    "DeckMenu",
    "DeckTile",
    "deck_highlight",
    "discover_decks",
    "menu_size_hint",
    "read_entry",
]
