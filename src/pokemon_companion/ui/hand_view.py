"""Mão do jogador como uma fileira de cartas em miniatura com a arte real
(estilo "cartas na mão" de Hearthstone/TCG Pocket), em vez de uma linha de
texto com os nomes. Clicar numa carta tenta jogá-la — se houver mais de um
alvo possível (ex: anexar energia em qual Pokémon), a ação fica
pré-selecionada na lista de ações para o jogador escolher o alvo."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from pokemon_companion.cards_db.models import Card
from pokemon_companion.ui.card_art import card_art_provider

_HAND_ART_SIZE = 64


class HandCardWidget(QFrame):
    clicked = pyqtSignal()

    def __init__(self, card: Card) -> None:
        super().__init__()
        self.setObjectName("handCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(card.name)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        art_label = QLabel()
        art_label.setFixedSize(_HAND_ART_SIZE, _HAND_ART_SIZE)
        art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_label.setPixmap(card_art_provider.get_pixmap(card, _HAND_ART_SIZE))
        layout.addWidget(art_label)

        display_name = card.name if len(card.name) <= 12 else card.name[:11] + "…"
        name_label = QLabel(display_name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(False)
        name_label.setStyleSheet("color: #263238; font-weight: 700; font-size: 10px;")
        layout.addWidget(name_label)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None:
            self.clicked.emit()
        super().mousePressEvent(event)


class HandView(QScrollArea):
    card_clicked = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("handView")
        self.setWidgetResizable(True)
        self.setFixedHeight(_HAND_ART_SIZE + 50)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self._layout = QHBoxLayout(container)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)
        self.setWidget(container)

    def update_hand(self, hand: list[Card]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

        for i, card in enumerate(hand):
            card_widget = HandCardWidget(card)
            card_widget.clicked.connect(lambda index=i: self.card_clicked.emit(index))
            self._layout.addWidget(card_widget)
        self._layout.addStretch(1)
