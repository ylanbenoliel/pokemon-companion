"""Board de um lado da partida: ativo + banco como `PokemonCardWidget`,
prêmios como pips coloridos, mão como chip de texto — substitui o antigo
label monoespaçado por um layout mais próximo do Pokémon TCG Pocket."""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pokemon_companion.engine.game_state import MAX_BENCH_SIZE, PRIZE_COUNT, PlayerState
from pokemon_companion.ui.pokemon_card_widget import PokemonCardWidget


class PrizeTracker(QWidget):
    def __init__(self, total: int = PRIZE_COUNT) -> None:
        super().__init__()
        self._total = total
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._pips = [QLabel() for _ in range(total)]
        for pip in self._pips:
            pip.setFixedSize(12, 12)
            layout.addWidget(pip)
        self.update_count(total)

    def update_count(self, remaining: int) -> None:
        for i, pip in enumerate(self._pips):
            color = "#ffca28" if i < remaining else "rgba(255, 255, 255, 0.25)"
            pip.setStyleSheet(f"background-color: {color}; border-radius: 6px;")


class BoardView(QWidget):
    def __init__(self, title: str, show_hand: bool = False) -> None:
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        prizes_caption = QLabel("Prêmios")
        prizes_caption.setObjectName("prizesLabel")
        header.addWidget(prizes_caption)
        self._prizes = PrizeTracker()
        header.addWidget(self._prizes)
        outer.addLayout(header)

        board_row = QHBoxLayout()
        board_row.setSpacing(8)
        self._active_card = PokemonCardWidget()
        board_row.addWidget(self._active_card)

        bench_row = QHBoxLayout()
        bench_row.setSpacing(4)
        self._bench_cards = [PokemonCardWidget(compact=True) for _ in range(MAX_BENCH_SIZE)]
        for card_widget in self._bench_cards:
            bench_row.addWidget(card_widget)
        board_row.addLayout(bench_row, 2)
        outer.addLayout(board_row)

        if show_hand:
            self._hand_label: QLabel | None = QLabel()
            self._hand_label.setObjectName("prizesLabel")
            self._hand_label.setWordWrap(True)
            outer.addWidget(self._hand_label)
        else:
            self._hand_label = None

    def update_state(self, player: PlayerState) -> None:
        self._prizes.update_count(len(player.prizes))

        if player.active is not None:
            self._active_card.update_pokemon(player.active)
        else:
            self._active_card.set_empty()

        for i, card_widget in enumerate(self._bench_cards):
            if i < len(player.bench):
                card_widget.update_pokemon(player.bench[i])
            else:
                card_widget.set_empty()

        if self._hand_label is not None:
            names = ", ".join(card.name for card in player.hand) or "(vazia)"
            self._hand_label.setText(f"Mão: {names}")
