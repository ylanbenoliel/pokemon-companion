"""Estatísticas e replays: o que você já jogou.

As estatísticas vêm de `stats.load_matches` (só partidas que você jogou,
não as de espectador); os replays, de `engine.replay.list_replays`.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.engine.replay import list_replays, replays_dir
from pokemon_companion.stats import Summary, Tally, load_matches, summarize
from pokemon_companion.ui.settings import DIFFICULTIES
from pokemon_companion.ui.theme import display_font, ui_font
from pokemon_companion.ui.widgets import hint_label, make_button, style_dialog, title_label

DIFFICULTY_NAMES = dict(DIFFICULTIES)


def _pct(tally: Tally) -> str:
    return f"{tally.rate:.0%}" if tally.games else "—"


def _streak_text(streak: int) -> str:
    if streak > 0:
        return f"{streak} vitória{'s' if streak > 1 else ''}"
    if streak < 0:
        return f"{-streak} derrota{'s' if streak < -1 else ''}"
    return "—"


class _Figure(QFrame):
    """Um número em destaque com a legenda embaixo."""

    def __init__(self, value: str, caption: str) -> None:
        super().__init__()
        self.setStyleSheet(
            "_Figure { background: rgba(247,239,225,12); border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.value = QLabel(value)
        self.value.setFont(display_font(20))
        self.value.setStyleSheet("color: #ffe03d;")
        layout.addWidget(self.value)
        label = QLabel(caption)
        label.setFont(ui_font(9.5))
        label.setStyleSheet("color: rgba(247,239,225,150);")
        layout.addWidget(label)


def _table(rows: dict[str, Tally], first: str, names: dict[str, str] | None = None) -> QTableWidget:
    table = QTableWidget(len(rows), 5)
    table.setHorizontalHeaderLabels([first, "Partidas", "Vitórias", "Derrotas", "Aproveitamento"])
    table.verticalHeader().setVisible(False)  # type: ignore[union-attr]
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setShowGrid(False)
    table.setFont(ui_font(10))
    header = table.horizontalHeader()
    assert header is not None
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in range(1, 5):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    ordered = sorted(rows.items(), key=lambda kv: (-kv[1].games, kv[0]))
    for row, (name, tally) in enumerate(ordered):
        cells = [
            (names or {}).get(name, name) or "—",
            str(tally.games),
            str(tally.wins),
            str(tally.losses),
            _pct(tally),
        ]
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if column:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(row, column, item)
    return table


class StatsDialog(QDialog):
    def __init__(self, summary: Summary | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary or summarize(load_matches())
        self.setWindowTitle("Estatísticas")
        self.resize(720, 560)
        style_dialog(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title_label("Estatísticas"))

        overall = self.summary.overall
        if not overall.games:
            layout.addWidget(
                hint_label(
                    "Nenhuma partida ainda. Jogue uma no menu inicial — vitórias, derrotas e "
                    "desistências aparecem aqui, por deck e por adversário.",
                    11,
                )
            )
            layout.addStretch(1)
        else:
            figures = QGridLayout()
            figures.setSpacing(10)
            items = (
                (str(overall.games), "partidas"),
                (_pct(overall), f"de vitórias ({overall.wins}–{overall.losses})"),
                (_streak_text(self.summary.streak), "sequência atual"),
                (str(self.summary.best_streak), "melhor sequência de vitórias"),
                (f"{self.summary.average_turns:.0f}", "turnos por partida"),
            )
            for column, (value, caption) in enumerate(items):
                figures.addWidget(_Figure(value, caption), 0, column)
            layout.addLayout(figures)

            tabs = QTabWidget()
            tabs.setFont(ui_font(10.5))
            tabs.addTab(_table(self.summary.by_deck, "Seu deck"), "Por deck")
            tabs.addTab(_table(self.summary.by_opponent, "Deck da IA"), "Por adversário")
            tabs.addTab(
                _table(self.summary.by_difficulty, "Dificuldade", DIFFICULTY_NAMES),
                "Por dificuldade",
            )
            layout.addWidget(tabs, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = make_button("Fechar", primary=True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


def describe_replay(info: dict[str, Any]) -> str:
    """ "23/09 14:05 · Mega Charizard X ex × Mega Gardevoir ex · Vitória em 14 turnos"."""
    when = ""
    with contextlib.suppress(ValueError):
        when = datetime.fromisoformat(str(info.get("date", ""))).strftime("%d/%m %H:%M")
    player = info.get("player_deck") or info.get("player_name") or "?"
    opponent = info.get("opponent_deck") or info.get("opponent_name") or "?"
    winner = info.get("winner")
    turns = info.get("turns")
    if winner == "player":
        result = "Vitória"
    elif winner == "opponent":
        result = "Desistência" if info.get("conceded") else "Derrota"
    else:
        result = "Sem resultado"
    detail = f"{result} em {turns} turnos" if turns else result
    return " · ".join(part for part in (when, f"{player} × {opponent}", detail) if part)


class ReplaysDialog(QDialog):
    """Lista de replays; "Assistir" emite o arquivo escolhido."""

    watch_requested = pyqtSignal(object)

    def __init__(self, folder: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder = folder or replays_dir()
        self.setWindowTitle("Replays")
        self.resize(680, 520)
        style_dialog(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(title_label("Replays"))
        layout.addWidget(
            hint_label(
                "Cada partida é salva ao terminar (as 50 mais recentes). Os arquivos são "
                "pequenos e dá para mandar para alguém assistir."
            )
        )
        self.list = QListWidget()
        self.list.setFont(ui_font(10.5))
        self.list.itemDoubleClicked.connect(lambda _: self._watch_selected())
        for path, info in list_replays(self._folder):
            item = QListWidgetItem(describe_replay(info))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            empty = QListWidgetItem("Nenhum replay ainda — termine uma partida para ver aqui.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        open_file = make_button("Abrir arquivo…")
        open_file.clicked.connect(self._open_file)
        row.addWidget(open_file)
        show_folder = make_button("Mostrar pasta")
        show_folder.clicked.connect(self._show_folder)
        row.addWidget(show_folder)
        row.addStretch(1)
        close = make_button("Fechar")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.watch = make_button("Assistir", primary=True)
        self.watch.setFont(ui_font(11, QFont.Weight.DemiBold))
        self.watch.setEnabled(
            self.list.currentItem() is not None and bool(list_replays(self._folder))
        )
        self.watch.clicked.connect(self._watch_selected)
        row.addWidget(self.watch)
        layout.addLayout(row)

    def _watch_selected(self) -> None:
        item = self.list.currentItem()
        raw = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if raw:
            self.watch_requested.emit(Path(raw))
            self.accept()

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir replay", str(Path.home()), "Replays (*.json)"
        )
        if path:
            self.watch_requested.emit(Path(path))
            self.accept()

    def _show_folder(self) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))
