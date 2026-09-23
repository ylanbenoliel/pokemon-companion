"""Importar um deck do competitivo sem sair do app.

Três caminhos, na mesma janela: procurar o arquétipo pelo nome no Limitless
(o app baixa a melhor lista publicada), colar o link de uma lista, ou colar o
texto da decklist (formato Limitless/PTCGO). O deck vai para a pasta de decks
do usuário (`paths.USER_DECKS`) e
aparece na tira da tela inicial.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.cards_db import limitless
from pokemon_companion.cards_db.limitless import Archetype
from pokemon_companion.paths import USER_DECKS
from pokemon_companion.ui.theme import FELT_DEEP, FELT_LIT, display_font, ui_font

SearchFn = Callable[[str], list[Archetype]]
DownloadFn = Callable[[Archetype], tuple[str, str, int]]
UrlFn = Callable[[str], tuple[str, str, int]]

_FIELD_STYLE = """
QLineEdit, QPlainTextEdit { background: rgba(247,239,225,14); color: #f7efe1;
    border: 1px solid rgba(247,239,225,45); border-radius: 8px; padding: 7px 10px; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #ffe03d; }
QListWidget { background: rgba(247,239,225,10); color: #f7efe1; border-radius: 8px;
    border: 1px solid rgba(247,239,225,30); padding: 4px; }
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:selected { background: #ffe03d; color: #20242b; }
"""


class DeckImportDialog(QDialog):
    """Janela de importação. As funções de rede são injetáveis (testes)."""

    def __init__(
        self,
        folder: Path = USER_DECKS,
        search: SearchFn | None = None,
        download: DownloadFn | None = None,
        from_url: UrlFn | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder = folder
        self._search = search or limitless.search_archetypes
        self._download = download or limitless.archetype_decklist
        self._from_url = from_url or limitless.deck_text_from_url
        self._results: list[Archetype] = []
        self.imported_path: Path | None = None

        self.setWindowTitle("Importar deck")
        self.setMinimumSize(560, 560)
        self.setStyleSheet(
            f"QDialog {{ background: qlineargradient(x1:0.5, y1:0, x2:0.5, y2:1,"
            f" stop:0 {FELT_LIT.name()}, stop:1 {FELT_DEEP.name()}); }}"
            "QLabel { color: #f7efe1; background: transparent; }" + _FIELD_STYLE
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("Importar deck")
        title.setFont(display_font(17))
        layout.addWidget(title)
        layout.addWidget(self._hint("Procure o arquétipo pelo nome, ou cole um link do Limitless."))

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setFont(ui_font(11))
        self.query.setPlaceholderText("dragapult, crustle, gardevoir… ou um link")
        self.query.returnPressed.connect(self.run_search)
        search_row.addWidget(self.query, 1)
        self.search_button = self._button("Procurar", primary=False)
        self.search_button.clicked.connect(self.run_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results = QListWidget()
        self.results.setFont(ui_font(10.5))
        self.results.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.results, 1)

        layout.addWidget(self._hint("Ou cole a decklist em texto (formato Limitless/PTCGO):"))
        self.paste = QPlainTextEdit()
        self.paste.setFont(ui_font(10))
        self.paste.setPlaceholderText("Pokémon (19)\n4 Dreepy TWM 128\n…")
        self.paste.setFixedHeight(120)
        layout.addWidget(self.paste)

        name_row = QHBoxLayout()
        name_row.addWidget(self._hint("Nome do deck"))
        self.name = QLineEdit()
        self.name.setFont(ui_font(11))
        self.name.setPlaceholderText("Meu Dragapult")
        name_row.addWidget(self.name, 1)
        layout.addLayout(name_row)

        self.status = QLabel("")
        self.status.setFont(ui_font(9.5))
        self.status.setStyleSheet("color: rgba(247,239,225,160); background: transparent;")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = self._button("Cancelar", primary=False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.import_button = self._button("Importar", primary=True)
        self.import_button.clicked.connect(self.accept)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(9.5))
        label.setStyleSheet("color: rgba(247,239,225,150); background: transparent;")
        return label

    def _button(self, text: str, primary: bool) -> QPushButton:
        button = QPushButton(text)
        button.setFont(ui_font(11, QFont.Weight.DemiBold))
        button.setMinimumHeight(38)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setStyleSheet(
                "QPushButton { color: #20242b; background: #ffe03d; border: none;"
                " border-radius: 8px; padding: 8px 20px; }"
                "QPushButton:hover { background: #fff06f; }"
            )
        else:
            button.setStyleSheet(
                "QPushButton { color: #f7efe1; background: rgba(247,239,225,16);"
                " border: 1px solid rgba(247,239,225,45); border-radius: 8px; padding: 8px 16px; }"
                "QPushButton:hover { background: rgba(247,239,225,30); }"
            )
        return button

    # ------------------------------------------------------------------
    def run_search(self) -> None:
        query = self.query.text().strip()
        if query.startswith("http"):
            self.status.setText("Link pronto: clique em Importar.")
            return
        self.results.clear()
        self.status.setText("Procurando no Limitless…")
        try:
            self._results = self._search(query)
        except Exception as exc:  # noqa: BLE001 - erro de rede vira mensagem
            self._results = []
            self.status.setText(f"Não deu para buscar: {exc}")
            return
        for archetype in self._results:
            QListWidgetItem(archetype.label, self.results)
        if self._results:
            self.results.setCurrentRow(0)
            self.status.setText(f"{len(self._results)} arquétipo(s). Escolha e clique em Importar.")
        else:
            self.status.setText("Nenhum arquétipo com esse nome no formato atual.")

    def accept(self) -> None:  # noqa: D102 - QDialog
        try:
            path = self._import()
        except Exception as exc:  # noqa: BLE001 - erro vira mensagem na janela
            self.status.setText(f"Falhou: {exc}")
            return
        if path is None:
            return
        self.imported_path = path
        super().accept()

    def _import(self) -> Path | None:
        pasted = self.paste.toPlainText().strip()
        typed = self.query.text().strip()
        name = self.name.text().strip()
        if pasted:
            if not name:
                self.status.setText("Dê um nome ao deck colado.")
                return None
            return limitless.save_deck(self._folder, name, "colado por você", pasted)
        if typed.startswith("http"):
            self.status.setText("Baixando a lista…")
            title, text, total = self._from_url(typed)
            return limitless.save_deck(self._folder, name or title, title, text, typed)
        row = self.results.currentRow()
        if not (0 <= row < len(self._results)):
            self.status.setText("Procure um arquétipo ou cole uma decklist.")
            return None
        archetype = self._results[row]
        self.status.setText(f"Baixando {archetype.name}…")
        title, text, total = self._download(archetype)
        source = f"{limitless.BASE}/decks/{archetype.deck_id}"
        return limitless.save_deck(self._folder, name or archetype.name, title, text, source)
