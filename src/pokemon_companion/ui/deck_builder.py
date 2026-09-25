"""Construtor de deck: buscar no Standard, montar, validar ao vivo e salvar.

Três colunas: a busca no catálogo (nome, ataque ou texto; filtros por
categoria e tipo), a carta selecionada por inteiro (com aviso quando algum
efeito dela ainda não está implementado no jogo) e o deck, com a contagem e
os problemas das regras de construção atualizados a cada carta.

O deck sai no formato do Limitless ("4 Charmander PFL 11"), na pasta de
decks do usuário, e as cartas vão para o cache local — ele abre na hora,
sem internet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.catalog import CatalogEntry, load_catalog
from pokemon_companion.cards_db.deck_rules import DECK_SIZE, MAX_COPIES, validate_deck
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.cards_db.standard import load_legal, signature
from pokemon_companion.engine.effects.cardinfo import is_basic_energy, stage_of, trainer_kind
from pokemon_companion.engine.effects.coverage import unimplemented
from pokemon_companion.engine.effects.descriptions import describe_card
from pokemon_companion.paths import BUNDLED_DECKS, USER_DECKS
from pokemon_companion.ui.theme import ENERGY_NAMES_PT, ui_font
from pokemon_companion.ui.widgets import (
    hint_label,
    make_button,
    section_label,
    style_dialog,
    title_label,
)

SECTIONS = (
    ("Pokémon", Supertype.POKEMON),
    ("Trainer", Supertype.TRAINER),
    ("Energy", Supertype.ENERGY),
)
SECTION_PT = {"Pokémon": "Pokémon", "Trainer": "Treinador", "Energy": "Energia"}
KINDS = (
    ("Todas as cartas", None),
    ("Pokémon", Supertype.POKEMON),
    ("Treinador", Supertype.TRAINER),
    ("Energia", Supertype.ENERGY),
)
MAX_RESULTS = 250
TRAINER_PT = {"Item": "Item", "Supporter": "Apoiador", "Stadium": "Estádio", "Tool": "Ferramenta"}


def _fold(text: str) -> str:
    """Minúsculas sem acento, para "pokemon" achar "Pokémon"."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def search_text(card: Card) -> str:
    parts = [card.name, *card.subtypes, *card.types, *card.rules]
    for attack in card.attacks:
        parts += [attack.name, attack.text]
    for ability in card.abilities:
        parts += [ability.name, ability.text]
    return _fold(" ".join(parts))


@dataclass
class DeckRow:
    entry: CatalogEntry
    count: int

    @property
    def card(self) -> Card:
        return self.entry.card


def deck_cards(rows: list[DeckRow]) -> list[Card]:
    """As 60 cartas como o jogo as vê (energia básica = a carta básica do motor)."""
    cards: list[Card] = []
    for row in rows:
        card = row.card
        if is_basic_energy(card) and card.types and card.types[0] in BASIC_ENERGIES:
            card = BASIC_ENERGIES[card.types[0]]
        elif is_basic_energy(card):
            card = BASIC_ENERGIES.get(card.name.replace(" Energy", ""), card)
        cards.extend([card] * row.count)
    return cards


def deck_text(name: str, rows: list[DeckRow]) -> str:
    """Decklist no formato do Limitless, com o nome no comentário."""
    lines = [f"# {name} — montado no construtor de deck"]
    for section, supertype in SECTIONS:
        chosen = [row for row in rows if row.card.supertype == supertype and row.count]
        if not chosen:
            continue
        lines.append(f"{section} ({sum(row.count for row in chosen)})")
        for row in chosen:
            if is_basic_energy(row.card):
                lines.append(f"{row.count} {row.card.name}")
            else:
                lines.append(f"{row.count} {row.card.name} {row.entry.line_code}")
    return "\n".join(lines) + "\n"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(name)).strip("_") or "meu_deck"


class _CacheOnly:
    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        return None


class DeckBuilderDialog(QDialog):
    def __init__(
        self,
        catalog: list[CatalogEntry] | None = None,
        folder: Path = USER_DECKS,
        cache_factory: Callable[[], CardCache] = CardCache,
        edit: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog if catalog is not None else load_catalog()
        self._folder = folder
        self._cache_factory = cache_factory
        self._legal = load_legal()
        self._index = [(entry, search_text(entry.card)) for entry in self.catalog]
        self._by_signature = {signature(e.card): e for e in self.catalog}
        self.rows: list[DeckRow] = []
        self.saved_path: Path | None = None

        self.setWindowTitle("Montar deck")
        self.resize(1180, 740)
        style_dialog(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(title_label("Montar deck"))
        header.addSpacing(16)
        self.name = QLineEdit()
        self.name.setFont(ui_font(12))
        self.name.setPlaceholderText("Nome do deck")
        self.name.textChanged.connect(lambda _: self._refresh_deck())
        header.addWidget(self.name, 1)
        self.open_combo = QComboBox()
        self.open_combo.setFont(ui_font(10))
        self.open_combo.addItem("Começar de outro deck…", None)
        for path in _existing_decks():
            self.open_combo.addItem(path.stem.replace("_", " "), str(path))
        self.open_combo.activated.connect(self._open_selected)
        header.addWidget(self.open_combo)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(self._build_search(), 5)
        body.addLayout(self._build_detail(), 4)
        body.addLayout(self._build_deck(), 4)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status = hint_label("")
        footer.addWidget(self.status, 1)
        cancel = make_button("Cancelar")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save_button = make_button("Salvar deck", primary=True)
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        outer.addLayout(footer)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._run_search)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(lambda: self._change(-1))

        if edit is not None:
            self.load_deck_file(edit)
        self._run_search()
        self._refresh_deck()

    # -- construção -------------------------------------------------------------
    def _build_search(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(section_label("Cartas do Standard"))
        self.query = QLineEdit()
        self.query.setFont(ui_font(11))
        self.query.setPlaceholderText("Buscar por nome, ataque ou texto…")
        self.query.textChanged.connect(lambda _: self._search_timer.start())
        self.query.returnPressed.connect(lambda: self._add_selected(1))
        column.addWidget(self.query)
        filters = QHBoxLayout()
        self.kind = QComboBox()
        for label, value in KINDS:
            self.kind.addItem(label, value)
        self.kind.currentIndexChanged.connect(lambda _: self._run_search())
        filters.addWidget(self.kind, 1)
        self.type_filter = QComboBox()
        self.type_filter.addItem("Todos os tipos", None)
        for energy, label in ENERGY_NAMES_PT.items():
            self.type_filter.addItem(label, energy)
        self.type_filter.currentIndexChanged.connect(lambda _: self._run_search())
        filters.addWidget(self.type_filter, 1)
        column.addLayout(filters)
        self.results = QListWidget()
        self.results.setFont(ui_font(10.5))
        self.results.currentItemChanged.connect(lambda item, _: self._show(item))
        self.results.itemDoubleClicked.connect(lambda _: self._add_selected(1))
        column.addWidget(self.results, 1)
        self.result_count = hint_label("")
        column.addWidget(self.result_count)
        buttons = QHBoxLayout()
        add_one = make_button("Adicionar 1")
        add_one.clicked.connect(lambda: self._add_selected(1))
        buttons.addWidget(add_one)
        add_four = make_button("Adicionar 4")
        add_four.clicked.connect(lambda: self._add_selected(4))
        buttons.addWidget(add_four)
        column.addLayout(buttons)
        return column

    def _build_detail(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(section_label("Carta"))
        self.detail = QTextBrowser()
        self.detail.setFont(ui_font(10.5))
        self.detail.setStyleSheet(
            "QTextBrowser { background: rgba(247,239,225,8); border-radius: 10px;"
            " border: 1px solid rgba(247,239,225,25); padding: 10px; color: #f7efe1; }"
        )
        column.addWidget(self.detail, 1)
        return column

    def _build_deck(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(section_label("Seu deck"))
        self.counter = QLabel("")
        self.counter.setFont(ui_font(11))
        column.addWidget(self.counter)
        self.deck_list = QListWidget()
        self.deck_list.setFont(ui_font(10.5))
        self.deck_list.currentItemChanged.connect(lambda item, _: self._show(item))
        column.addWidget(self.deck_list, 1)
        buttons = QHBoxLayout()
        minus = make_button("−1")
        minus.clicked.connect(lambda: self._change(-1))
        buttons.addWidget(minus)
        plus = make_button("+1")
        plus.clicked.connect(lambda: self._change(1))
        buttons.addWidget(plus)
        clear = make_button("Esvaziar")
        clear.clicked.connect(self._clear)
        buttons.addWidget(clear)
        column.addLayout(buttons)
        self.problems = QLabel("")
        self.problems.setFont(ui_font(10))
        self.problems.setWordWrap(True)
        column.addWidget(self.problems)
        return column

    # -- busca --------------------------------------------------------------------
    def matches(self, query: str, kind: Supertype | None, energy: str | None) -> list[CatalogEntry]:
        words = _fold(query).split()
        found = []
        for entry, text in self._index:
            card = entry.card
            if kind is not None and card.supertype != kind:
                continue
            if energy is not None and energy not in card.types:
                continue
            if all(word in text for word in words):
                found.append(entry)
        # nome começando pela busca primeiro
        first = _fold(query.strip())
        found.sort(key=lambda e: (not _fold(e.card.name).startswith(first), e.card.name))
        return found

    def _run_search(self) -> None:
        found = self.matches(
            self.query.text(), self.kind.currentData(), self.type_filter.currentData()
        )
        self.results.clear()
        for entry in found[:MAX_RESULTS]:
            item = QListWidgetItem(_result_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, signature(entry.card))
            self.results.addItem(item)
        shown = min(len(found), MAX_RESULTS)
        self.result_count.setText(
            f"{len(found)} cartas" if shown == len(found) else f"{shown} de {len(found)} cartas"
        )
        if self.results.count():
            self.results.setCurrentRow(0)

    def _entry_of(self, item: QListWidgetItem | None) -> CatalogEntry | None:
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        return self._by_signature.get(key) if key else None

    def _show(self, item: QListWidgetItem | None) -> None:
        entry = self._entry_of(item)
        if entry is not None:
            self.detail.setHtml(card_html(entry))

    # -- deck -----------------------------------------------------------------------
    def add(self, entry: CatalogEntry, count: int = 1) -> int:
        """Adiciona até `count` cópias respeitando 60 cartas e 4 por nome;
        devolve quantas entraram."""
        added = 0
        problem = ""
        for _ in range(count):
            problem = self._blocked(entry)
            if problem:
                break
            row = next((r for r in self.rows if r.entry is entry), None)
            if row is None:
                self.rows.append(DeckRow(entry, 1))
            else:
                row.count += 1
            added += 1
        done = f"+{added} {entry.card.name}" if added else ""
        self.status.setText(" — ".join(part for part in (done, problem) if part))
        self._refresh_deck(select=entry)
        return added

    def _blocked(self, entry: CatalogEntry) -> str:
        total = sum(row.count for row in self.rows)
        if total >= DECK_SIZE:
            return f"O deck já tem {DECK_SIZE} cartas."
        card = entry.card
        if is_basic_energy(card):
            return ""
        same_name = sum(row.count for row in self.rows if row.card.name == card.name)
        if same_name >= MAX_COPIES:
            return f"No máximo {MAX_COPIES} cópias de {card.name}."
        if "ACE SPEC" in card.subtypes and any("ACE SPEC" in r.card.subtypes for r in self.rows):
            return "Só uma carta ACE SPEC por deck."
        return ""

    def _add_selected(self, count: int) -> None:
        entry = self._entry_of(self.results.currentItem())
        if entry is not None:
            self.add(entry, count)

    def _change(self, delta: int) -> None:
        entry = self._entry_of(self.deck_list.currentItem())
        if entry is None:
            return
        if delta > 0:
            self.add(entry, delta)
            return
        row = next(r for r in self.rows if r.entry is entry)
        row.count -= 1
        if row.count <= 0:
            self.rows.remove(row)
        self._refresh_deck(select=entry if row.count > 0 else None)

    def _clear(self) -> None:
        self.rows.clear()
        self._refresh_deck()

    def problems_now(self) -> list[str]:
        return validate_deck(deck_cards(self.rows), self._legal)

    def _refresh_deck(self, select: CatalogEntry | None = None) -> None:
        self.deck_list.clear()
        order = {supertype: i for i, (_, supertype) in enumerate(SECTIONS)}
        self.rows.sort(key=lambda r: (order[r.card.supertype], _row_rank(r), r.card.name))
        selected_row = -1
        for index, row in enumerate(self.rows):
            item = QListWidgetItem(f"{row.count}×  {row.card.name}")
            item.setData(Qt.ItemDataRole.UserRole, signature(row.card))
            self.deck_list.addItem(item)
            if row.entry is select:
                selected_row = index
        if selected_row >= 0:
            self.deck_list.setCurrentRow(selected_row)
        counts = {
            SECTION_PT[name]: sum(r.count for r in self.rows if r.card.supertype == supertype)
            for name, supertype in SECTIONS
        }
        total = sum(counts.values())
        self.counter.setText(
            f"<b>{total}/{DECK_SIZE}</b> &nbsp;·&nbsp; "
            + " · ".join(f"{name} {count}" for name, count in counts.items())
        )
        problems = self.problems_now()
        if problems:
            self.problems.setStyleSheet("color: #ffb199; background: transparent;")
            self.problems.setText("\n".join(f"• {p}" for p in problems))
        else:
            self.problems.setStyleSheet("color: #6bffa8; background: transparent;")
            self.problems.setText("Deck pronto para jogar.")
        self.save_button.setEnabled(bool(self.rows) and bool(self.name.text().strip()))

    # -- arquivos ---------------------------------------------------------------------
    def load_deck_file(self, path: Path) -> None:
        """Começa a partir de uma lista existente (as cartas precisam estar no
        cache local — decks já jogados ou importados)."""
        with self._cache_factory() as cache:
            cards, errors = load_deck(path, cache, _CacheOnly())
        by_name_energy = {e.card.name: e for e in self.catalog if is_basic_energy(e.card)}
        self.rows.clear()
        missing: set[str] = set()
        for card in cards:
            entry = self._by_signature.get(signature(card)) or by_name_energy.get(card.name)
            if entry is None:
                missing.add(card.name)
                continue
            row = next((r for r in self.rows if r.entry is entry), None)
            if row is None:
                self.rows.append(DeckRow(entry, 1))
            else:
                row.count += 1
        if not self.name.text().strip():
            self.name.setText(path.stem.replace("_", " ").title())
        note = f"Aberto: {path.name}."
        if missing or errors:
            note += " Ficaram de fora (fora do Standard ou ainda não baixadas): " + ", ".join(
                sorted(missing) or ["cartas não encontradas no cache"]
            )
        self.status.setText(note)
        self._refresh_deck()

    def _open_selected(self, index: int) -> None:
        raw = self.open_combo.itemData(index)
        if raw:
            self.load_deck_file(Path(raw))
        self.open_combo.setCurrentIndex(0)

    def save(self) -> Path | None:
        name = self.name.text().strip()
        if not name or not self.rows:
            return None
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._folder / f"{slugify(name)}.txt"
        path.write_text(deck_text(name, self.rows), encoding="utf-8")
        with self._cache_factory() as cache:
            for row in self.rows:
                if not is_basic_energy(row.card):
                    cache.save(row.card, row.entry.set_code, row.entry.number)
        self.saved_path = path
        self.accept()
        return path


def _row_rank(row: DeckRow) -> int:
    """Dentro de Pokémon: Básicos, Estágio 1, Estágio 2; Treinadores por tipo."""
    card = row.card
    if card.supertype == Supertype.POKEMON:
        return {"Basic": 0, "Stage 1": 1, "Stage 2": 2}.get(stage_of(card), 3)
    if card.supertype == Supertype.TRAINER:
        order = ("Supporter", "Item", "Tool", "Stadium")
        kind = trainer_kind(card)
        return order.index(kind) if kind in order else len(order)
    return 0


def _result_label(entry: CatalogEntry) -> str:
    card = entry.card
    if card.supertype == Supertype.POKEMON:
        kind = ENERGY_NAMES_PT.get(card.types[0], "") if card.types else ""
        extra = f"{kind} · {card.hp} HP" if card.hp else kind
        attacks = ", ".join(a.name for a in card.attacks[:2])
        return f"{card.name}  —  {extra}{' · ' + attacks if attacks else ''}"
    if card.supertype == Supertype.TRAINER:
        return f"{card.name}  —  {TRAINER_PT.get(trainer_kind(card), 'Treinador')}"
    return f"{card.name}  —  Energia{' básica' if is_basic_energy(card) else ' especial'}"


STAGE_PT = {"Basic": "Básico", "Stage 1": "Estágio 1", "Stage 2": "Estágio 2"}


def _cost_text(cost: list[str]) -> str:
    """["Fire", "Fire", "Colorless"] → "2× Fogo + 1× Incolor"."""
    if not cost:
        return "sem custo"
    counts: dict[str, int] = {}
    for energy in cost:
        counts[energy] = counts.get(energy, 0) + 1
    return " + ".join(f"{n}× {ENERGY_NAMES_PT.get(e, e)}" for e, n in counts.items())


def card_html(entry: CatalogEntry) -> str:
    card = entry.card
    lines = [f"<h3 style='color:#ffe03d;margin:0'>{card.name}</h3>"]
    meta: list[str] = []
    if card.supertype == Supertype.POKEMON:
        meta.append(STAGE_PT.get(stage_of(card), stage_of(card)))
        if card.evolves_from:
            meta.append(f"evolui de {card.evolves_from}")
        if card.types:
            meta.append(ENERGY_NAMES_PT.get(card.types[0], card.types[0]))
        if card.hp:
            meta.append(f"{card.hp} HP")
    elif card.supertype == Supertype.TRAINER:
        meta.append(TRAINER_PT.get(trainer_kind(card), "Treinador"))
    meta.append(entry.line_code)
    lines.append(f"<p style='color:#b9c9c2'>{' · '.join(meta)}</p>")
    for ability in card.abilities:
        lines.append(
            f"<p><b style='color:#ff9f7a'>Habilidade: {ability.name}</b><br>{ability.text}</p>"
        )
    for attack in card.attacks:
        cost = _cost_text(attack.cost)
        damage = f" <b>{attack.damage}</b>" if attack.damage else ""
        text = f"<br>{attack.text}" if attack.text else ""
        lines.append(
            f"<p><b>{attack.name}</b>{damage} <span style='color:#b9c9c2'>[{cost}]</span>{text}</p>"
        )
    if card.supertype != Supertype.POKEMON:
        described = describe_card(card)
        if described:
            lines.append(f"<p>{described}</p>")
    if card.weaknesses or card.retreat_cost:
        weak = ", ".join(
            f"{ENERGY_NAMES_PT.get(w.energy_type, w.energy_type)} {w.value}"
            for w in card.weaknesses
        )
        lines.append(
            f"<p style='color:#b9c9c2'>Fraqueza: {weak or '—'} · Recuo: {len(card.retreat_cost)}</p>"
        )
    missing = unimplemented(card)
    if missing:
        lines.append(
            "<p style='color:#ffb199'>Ainda sem efeito no jogo: "
            + ", ".join(missing)
            + " (causa só o dano impresso / não faz nada).</p>"
        )
    return "".join(lines)


def _existing_decks() -> list[Path]:
    folders = [USER_DECKS, BUNDLED_DECKS / "originais", BUNDLED_DECKS / "top", BUNDLED_DECKS]
    seen: set[Path] = set()
    found = []
    for folder in folders:
        for path in sorted(folder.glob("*.txt")) if folder.exists() else []:
            if path.resolve() not in seen:
                seen.add(path.resolve())
                found.append(path)
    return found
