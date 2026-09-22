"""Widget de uma carta de Pokémon em jogo (ativo ou banco): nome, barra de
HP colorida por porcentagem, pips de energia anexada e badge de status —
visual inspirado em cards do Pokémon TCG Pocket em vez de texto corrido."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from pokemon_companion.engine.game_state import PokemonInPlay
from pokemon_companion.ui.theme import STATUS_LABELS, energy_color, hp_bar_color


class EnergyPip(QLabel):
    def __init__(self, energy_type: str) -> None:
        super().__init__()
        self.setFixedSize(14, 14)
        self.setStyleSheet(
            f"background-color: {energy_color(energy_type)}; "
            "border-radius: 7px; border: 1px solid rgba(0, 0, 0, 0.25);"
        )
        self.setToolTip(energy_type)


class PokemonCardWidget(QFrame):
    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("pokemonCard")
        self.setMinimumWidth(78 if compact else 150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        self.name_label = QLabel("(vazio)")
        self.name_label.setObjectName("cardName")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        self.hp_bar = QProgressBar()
        self.hp_bar.setObjectName("hpBar")
        self.hp_bar.setTextVisible(True)
        self.hp_bar.setFixedHeight(14)
        layout.addWidget(self.hp_bar)

        self._energy_row = QHBoxLayout()
        self._energy_row.setSpacing(3)
        layout.addLayout(self._energy_row)

        self.status_label = QLabel()
        self.status_label.setObjectName("statusBadge")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.set_empty()

    def _clear_energy_pips(self) -> None:
        while self._energy_row.count():
            item = self._energy_row.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_empty(self) -> None:
        self.setProperty("empty", True)
        self.setProperty("energyType", None)
        self.name_label.setText("(vazio)")
        self.hp_bar.setRange(0, 1)
        self.hp_bar.setValue(0)
        self.hp_bar.setFormat("")
        self.hp_bar.setStyleSheet("")
        self._clear_energy_pips()
        self.status_label.hide()
        self._repolish()

    def update_pokemon(self, mon: PokemonInPlay) -> None:
        self.setProperty("empty", False)
        self.name_label.setText(mon.card.name)

        max_hp = mon.card.hp or 1
        current_hp = mon.current_hp
        self.hp_bar.setRange(0, max_hp)
        self.hp_bar.setValue(current_hp)
        self.hp_bar.setFormat(f"{current_hp}/{max_hp}")
        color = hp_bar_color(current_hp, max_hp)
        self.hp_bar.setStyleSheet(f"QProgressBar#hpBar::chunk {{ background-color: {color}; }}")

        primary_type = mon.card.types[0] if mon.card.types else "Colorless"
        self.setProperty("energyType", primary_type)

        self._clear_energy_pips()
        for energy_type in mon.attached_energies:
            self._energy_row.addWidget(EnergyPip(energy_type))
        self._energy_row.addStretch(1)

        if mon.status.name != "NONE":
            self.status_label.setText(STATUS_LABELS.get(mon.status.name, mon.status.name))
            self.status_label.show()
        else:
            self.status_label.hide()

        self._repolish()

    def _repolish(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
