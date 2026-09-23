"""Tela inicial: montar o confronto antes da partida.

A tela é o próprio duelo: o seu deck e o da IA ficam frente a frente, em
tamanho de carta, e a tira embaixo é a caixa de decks de onde você tira o
próximo. Clicar num dos dois lados diz qual deles a tira vai trocar.

Os decks vêm do pacote (`decks/`: meta atual, Mundial e exemplos) e da pasta
de decks do usuário (os importados).
A arte é a do Pokémon principal da lista, buscada **só no cache local** — a
tela abre rápido e sem internet; deck ainda não importado mostra só a cor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
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
from pokemon_companion.paths import BUNDLED_DECKS, USER_DECKS
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.deck_import import DeckImportDialog
from pokemon_companion.ui.theme import (
    BONE,
    FELT_DEEP,
    FELT_LIT,
    INK,
    VOLT,
    display_font,
    energy_color,
    primary_type,
    ui_font,
)

DECK_FOLDERS = (
    BUNDLED_DECKS / "top",
    BUNDLED_DECKS / "worlds2026",
    BUNDLED_DECKS,
    USER_DECKS,
)
GROUP_LABELS = {"top": "Meta atual", "worlds2026": "Mundial 2026"}
DIFFICULTIES = (("easy", "Fácil"), ("medium", "Média"), ("hard", "Difícil"))
HERO_SIZE = QSize(300, 306)
THUMB_SIZE = QSize(132, 124)
PLAYER, OPPONENT = "player", "opponent"


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
    return DeckEntry(path=path, title=title, subtitle=subtitle, group=_group(path.parent))


def _group(folder: Path) -> str:
    if folder == USER_DECKS:
        return "Meus decks"
    if folder == BUNDLED_DECKS:
        return "Exemplos"
    return GROUP_LABELS.get(folder.name, folder.name)


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


class _DeckCard(QFrame):
    """Base das cartas de deck: arte em cima, nome embaixo, cor do tipo."""

    clicked = pyqtSignal(object)

    def __init__(self, entry: DeckEntry | None, size: QSize, art_height: int) -> None:
        super().__init__()
        self.entry = entry
        self._accent = QColor("#2f4f4a")
        self._highlight = False
        self._art_height = art_height
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        self.art = QLabel()
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setFixedHeight(art_height)
        self.name = QLabel(entry.title if entry else "")
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.art)
        layout.addWidget(self.name)

    def set_art(self, pixmap: QPixmap | None, accent: QColor) -> None:
        self._accent = accent
        if pixmap is not None and not pixmap.isNull():
            self.art.setPixmap(
                pixmap.scaled(
                    self.width() - 30,
                    self._art_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self._restyle()

    def set_highlight(self, highlight: bool) -> None:
        self._highlight = highlight
        self._restyle()

    @property
    def highlighted(self) -> bool:
        return self._highlight

    def _restyle(self) -> None:
        raise NotImplementedError

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 (API do Qt)
        self.clicked.emit(self.entry)


class DeckHero(_DeckCard):
    """Um dos dois lados do confronto, do tamanho de uma carta na mesa."""

    def __init__(self, side: str, caption: str) -> None:
        super().__init__(None, HERO_SIZE, 156)
        self.side = side
        self.caption = caption
        self.name.setFont(display_font(15))
        self.detail = QLabel("")
        self.detail.setFont(ui_font(9.5))
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        self.tag = QLabel(caption)
        self.tag.setFont(ui_font(9.5, QFont.Weight.DemiBold))
        self.tag.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.tag)
        self._restyle()

    def set_entry(self, entry: DeckEntry) -> None:
        self.entry = entry
        self.name.setText(entry.title)
        self.detail.setText(entry.subtitle or entry.group)
        self._restyle()

    def _restyle(self) -> None:
        accent = self._accent
        border = VOLT.name() if self._highlight else "rgba(247,239,225,55)"
        self.setStyleSheet(f"""
            DeckHero {{
                border: {3 if self._highlight else 1}px solid {border};
                border-radius: 18px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {BONE.name()}, stop:0.83 {BONE.name()},
                    stop:0.831 {accent.lighter(130).name()}, stop:1 {accent.darker(120).name()});
            }}
            QLabel {{ background: transparent; }}
            """)
        self.name.setStyleSheet(f"color: {INK.name()};")
        self.detail.setStyleSheet("color: rgba(32,36,43,165);")
        on_light = self._accent.lightnessF() > 0.62
        self.tag.setStyleSheet(
            f"color: {'rgba(32,36,43,190)' if on_light else 'rgba(247,239,225,225)'};"
        )


class DeckThumb(_DeckCard):
    """Miniatura na tira de decks."""

    def __init__(self, entry: DeckEntry) -> None:
        super().__init__(entry, THUMB_SIZE, 70)
        self.name.setFont(ui_font(9.5, QFont.Weight.DemiBold))
        self._restyle()

    def _restyle(self) -> None:
        accent = self._accent
        border = VOLT.name() if self._highlight else "rgba(247,239,225,35)"
        self.setStyleSheet(f"""
            DeckThumb {{
                border: {2 if self._highlight else 1}px solid {border};
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {accent.lighter(118).name()}, stop:1 {accent.darker(150).name()});
            }}
            QLabel {{ color: {BONE.name()}; background: transparent; }}
            """)


class VersusMark(QWidget):
    """A marca do confronto, tingida pelos tipos dos dois decks escolhidos."""

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(118, 118)
        self.label = QLabel("VS")
        self.label.setFont(display_font(32))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note = QLabel("treino")
        self.note.setFont(ui_font(9.5))
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setStyleSheet("color: rgba(247,239,225,120); background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addStretch(1)
        layout.addWidget(self.label)
        layout.addWidget(self.note)
        layout.addStretch(1)
        self.set_colors(QColor(BONE), QColor(BONE))

    def set_colors(self, left: QColor, right: QColor) -> None:
        self.label.setStyleSheet(f"background: transparent; color: {left.lighter(115).name()};")
        self.setStyleSheet(
            "VersusMark { border-radius: 59px; background: qlineargradient("
            f"x1:0, y1:1, x2:1, y2:0, stop:0 rgba({left.red()},{left.green()},{left.blue()},60),"
            f" stop:1 rgba({right.red()},{right.green()},{right.blue()},60)); }}"
        )


class DeckStrip(QWidget):
    """Tira horizontal com todos os decks disponíveis, filtrada por grupo."""

    chosen = pyqtSignal(object)

    def __init__(self, entries: list[DeckEntry]) -> None:
        super().__init__()
        self.entries = entries
        self.tiles: list[DeckThumb] = []
        self._filter = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.filters = QButtonGroup(self)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        groups = ["Todos", *dict.fromkeys(entry.group for entry in entries)]
        for index, group in enumerate(groups):
            button = QPushButton(group)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFont(ui_font(9.5, QFont.Weight.DemiBold))
            button.setStyleSheet("""
                QPushButton { color: rgba(247,239,225,190); border: none; padding: 5px 13px;
                    border-radius: 13px; background: rgba(247,239,225,18); }
                QPushButton:checked { color: #20242b; background: #f7efe1; }
                """)
            button.clicked.connect(lambda _=False, g=group: self.filter_group(g))
            self.filters.addButton(button)
            filter_row.addWidget(button)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(2, 2, 2, 10)
        row.setSpacing(10)
        for entry in entries:
            thumb = DeckThumb(entry)
            thumb.clicked.connect(self.chosen.emit)
            self.tiles.append(thumb)
            row.addWidget(thumb)
        row.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(THUMB_SIZE.height() + 28)
        scroll.setStyleSheet("background: transparent;")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar = scroll.horizontalScrollBar()
        assert bar is not None
        bar.setStyleSheet("""
            QScrollBar:horizontal { height: 6px; background: transparent; margin: 0; }
            QScrollBar::handle:horizontal { background: rgba(247,239,225,70); border-radius: 3px;
                min-width: 60px; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; }
            """)
        layout.addWidget(scroll)

    def filter_group(self, group: str) -> None:
        self._filter = "" if group == "Todos" else group
        for tile in self.tiles:
            assert tile.entry is not None
            tile.setVisible(not self._filter or tile.entry.group == self._filter)

    def mark(self, chosen: list[DeckEntry]) -> None:
        paths = {entry.path for entry in chosen}
        for tile in self.tiles:
            assert tile.entry is not None
            tile.set_highlight(tile.entry.path in paths)


class DeckMenu(QWidget):
    """Confronto: seu deck × deck da IA, dificuldade e "Jogar"."""

    start_requested = pyqtSignal(object, object, str)

    def __init__(
        self,
        entries: list[DeckEntry] | None = None,
        art: ArtProvider | None = None,
        cache_factory: Callable[[], CardCache] = CardCache,
        import_dialog: Callable[..., DeckImportDialog] = DeckImportDialog,
    ) -> None:
        super().__init__()
        self._entries = entries if entries is not None else discover_decks()
        self._art = art
        self._cache_factory = cache_factory
        self._import_dialog = import_dialog
        self._cards: dict[Path, Card | None] = {}
        self.difficulty = "medium"
        self.armed = PLAYER

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "DeckMenu { background: qlineargradient(x1:0.5, y1:0, x2:0.5, y2:1,"
            f" stop:0 {FELT_LIT.name()}, stop:1 {FELT_DEEP.name()}); }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 22, 30, 22)
        outer.setSpacing(16)
        outer.addLayout(self._build_header())
        outer.addLayout(self._build_matchup(), 1)

        self.strip = DeckStrip(self._entries)
        self.strip.chosen.connect(self._on_deck_chosen)
        outer.addWidget(self.strip)
        outer.addLayout(self._build_footer())

        if self._entries:
            self.choose(PLAYER, self._entries[0])
            self.choose(OPPONENT, self._entries[min(1, len(self._entries) - 1)])
        self.arm(PLAYER)
        QTimer.singleShot(0, self.load_art)

    # ------------------------------------------------------------------
    # construção
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Treino")
        title.setFont(display_font(20))
        title.setStyleSheet(f"color: {BONE.name()}; background: transparent;")
        row.addWidget(title)
        hint = QLabel("escolha os dois decks e jogue")
        hint.setFont(ui_font(10))
        hint.setStyleSheet("color: rgba(247,239,225,130); background: transparent;")
        row.addWidget(hint)
        row.addStretch(1)

        label = QLabel("IA")
        label.setFont(ui_font(10, QFont.Weight.DemiBold))
        label.setStyleSheet("color: rgba(247,239,225,150); background: transparent;")
        row.addWidget(label)
        self.difficulty_buttons = QButtonGroup(self)
        for key, text in DIFFICULTIES:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(key == self.difficulty)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFont(ui_font(10, QFont.Weight.DemiBold))
            button.setStyleSheet("""
                QPushButton { color: rgba(247,239,225,200); border: 1px solid rgba(247,239,225,45);
                    border-radius: 14px; padding: 6px 15px; background: transparent; }
                QPushButton:checked { color: #20242b; background: #ffe03d;
                    border: 1px solid #ffe03d; }
                """)
            button.clicked.connect(lambda _=False, k=key: self._set_difficulty(k))
            self.difficulty_buttons.addButton(button)
            row.addWidget(button)
        return row

    def _build_matchup(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addStretch(1)
        self.heroes = {
            PLAYER: DeckHero(PLAYER, "seu deck"),
            OPPONENT: DeckHero(OPPONENT, "deck da IA"),
        }
        self.versus = VersusMark()
        row.addWidget(self.heroes[PLAYER])
        row.addWidget(self.versus)
        row.addWidget(self.heroes[OPPONENT])
        row.addStretch(1)
        for side, hero in self.heroes.items():
            hero.clicked.connect(lambda _=None, s=side: self.arm(s))
        return row

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.import_button = QPushButton("Importar deck")
        self.import_button.setFont(ui_font(10, QFont.Weight.DemiBold))
        self.import_button.setMinimumHeight(38)
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.setStyleSheet("""
            QPushButton { color: #f7efe1; background: rgba(247,239,225,16);
                border: 1px solid rgba(247,239,225,45); border-radius: 8px; padding: 8px 16px; }
            QPushButton:hover { background: rgba(247,239,225,30); }
            """)
        self.import_button.clicked.connect(self._import_deck)
        row.addWidget(self.import_button)
        self.status = QLabel("")
        self.status.setFont(ui_font(9.5))
        self.status.setStyleSheet("color: rgba(247,239,225,150); background: transparent;")
        row.addWidget(self.status)
        row.addStretch(1)
        self.play_button = QPushButton("Jogar")
        self.play_button.setFont(display_font(13))
        self.play_button.setMinimumSize(200, 48)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setStyleSheet("""
            QPushButton { color: #20242b; border: none; border-radius: 10px; padding: 8px 20px;
                background: #ffe03d; }
            QPushButton:hover { background: #fff06f; }
            QPushButton:disabled { color: rgba(247,239,225,110); background: rgba(247,239,225,22); }
            """)
        self.play_button.clicked.connect(self._start)
        row.addWidget(self.play_button)
        return row

    # ------------------------------------------------------------------
    # escolha
    def arm(self, side: str) -> None:
        """Marca qual lado a próxima escolha na tira vai trocar."""
        self.armed = side
        for key, hero in self.heroes.items():
            hero.set_highlight(key == side)

    def choose(self, side: str, entry: DeckEntry) -> None:
        hero = self.heroes[side]
        hero.set_entry(entry)
        card = self._cards.get(entry.path)
        if card is not None:
            hero.set_art(self._pixmap(card), QColor(energy_color(primary_type(card.types))))
        self.strip.mark([h.entry for h in self.heroes.values() if h.entry is not None])
        self.versus.set_colors(self._accent(PLAYER), self._accent(OPPONENT))
        self.play_button.setEnabled(all(h.entry is not None for h in self.heroes.values()))

    def selection(self, side: str) -> DeckEntry | None:
        return self.heroes[side].entry

    @property
    def tiles(self) -> list[DeckThumb]:
        return self.strip.tiles

    def _accent(self, side: str) -> QColor:
        entry = self.heroes[side].entry
        card = self._cards.get(entry.path) if entry else None
        return QColor(energy_color(primary_type(card.types))) if card else QColor(BONE)

    def _on_deck_chosen(self, entry: DeckEntry) -> None:
        self.choose(self.armed, entry)
        self.arm(OPPONENT if self.armed == PLAYER else PLAYER)

    def _set_difficulty(self, key: str) -> None:
        self.difficulty = key

    def _pixmap(self, card: Card) -> QPixmap | None:
        art = self._art or ArtProvider()
        return art.card_art(card)

    def load_art(self) -> None:
        """Preenche a arte dos decks já importados, um por vez, para a tela
        não travar na primeira abertura."""
        pending = list({t.entry.path: t.entry for t in self.tiles if t.entry}.values())

        def step() -> None:
            if not pending:
                self.status.setText("")
                return
            entry = pending.pop(0)
            self.status.setText(f"Lendo as listas… faltam {len(pending)}")
            self._load_entry_art(entry)
            QTimer.singleShot(0, step)

        QTimer.singleShot(0, step)

    def _load_entry_art(self, entry: DeckEntry) -> None:
        try:
            with self._cache_factory() as cache:
                card = deck_highlight(entry, cache)
        except Exception:  # noqa: BLE001 - arte é opcional
            card = None
        self._cards[entry.path] = card
        if card is not None:
            pixmap = self._pixmap(card)
            accent = QColor(energy_color(primary_type(card.types)))
            for tile in self.tiles:
                if tile.entry is not None and tile.entry.path == entry.path:
                    tile.set_art(pixmap, accent)
            for hero in self.heroes.values():
                if hero.entry is not None and hero.entry.path == entry.path:
                    hero.set_art(pixmap, accent)
            self.versus.set_colors(self._accent(PLAYER), self._accent(OPPONENT))

    # ------------------------------------------------------------------
    # importação
    def _import_deck(self) -> None:
        dialog = self._import_dialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.imported_path is None:
            return
        entry = read_entry(dialog.imported_path)
        self._entries = [e for e in self._entries if e.path != entry.path] + [entry]
        self._rebuild_strip()
        self.choose(self.armed, entry)
        self.arm(OPPONENT if self.armed == PLAYER else PLAYER)
        self.status.setText(f"Importado: {entry.title}")
        self._load_entry_art(entry)

    def _rebuild_strip(self) -> None:
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        index = layout.indexOf(self.strip)
        old_strip = self.strip
        new_strip = DeckStrip(self._entries)
        new_strip.chosen.connect(self._on_deck_chosen)
        layout.removeWidget(old_strip)
        old_strip.deleteLater()
        layout.insertWidget(index, new_strip)
        self.strip = new_strip
        for path, card in self._cards.items():
            if card is None:
                continue
            pixmap = self._pixmap(card)
            accent = QColor(energy_color(primary_type(card.types)))
            for tile in self.tiles:
                if tile.entry is not None and tile.entry.path == path:
                    tile.set_art(pixmap, accent)
        self.strip.mark([h.entry for h in self.heroes.values() if h.entry is not None])

    def _start(self) -> None:
        player, opponent = self.selection(PLAYER), self.selection(OPPONENT)
        if player is None or opponent is None:
            return
        self.play_button.setEnabled(False)
        self.status.setText("Importando as cartas…")
        self.start_requested.emit(player.path, opponent.path, self.difficulty)


def menu_size_hint() -> QSize:
    return QSize(1200, 800)
