"""Widget de uma carta de Pokémon em jogo (ativo ou banco): nome, barra de
HP colorida por porcentagem, pips de energia anexada, badge de status e
(no card ativo) a lista de ataques com custo de energia e dano — visual
inspirado em cards do Pokémon TCG Pocket em vez de texto corrido."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from pokemon_companion.cards_db.models import Attack
from pokemon_companion.engine.game_state import PokemonInPlay
from pokemon_companion.engine.rules import energy_satisfies_cost
from pokemon_companion.ui.theme import STATUS_LABELS, energy_color, hp_bar_color

_NAME_COLOR_FILLED = "#263238"
_NAME_COLOR_EMPTY = "rgba(255, 255, 255, 0.55)"
_ATTACK_READY_COLOR = "#263238"
_ATTACK_NOT_READY_COLOR = "rgba(38, 50, 56, 0.45)"


class EnergyPip(QLabel):
    def __init__(self, energy_type: str, size: int = 14) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.setStyleSheet(
            f"background-color: {energy_color(energy_type)}; "
            f"border-radius: {size // 2}px; border: 1px solid rgba(0, 0, 0, 0.25);"
        )
        self.setToolTip(energy_type)


class AttackRow(QWidget):
    """Uma linha: pips do custo de energia + nome do ataque + dano.

    Fica esmaecida quando a energia atualmente anexada não é suficiente
    para usar o ataque — resposta direta a "não dá pra saber quantas
    energias meu pokémon precisa pra atacar"."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._cost_row = QHBoxLayout()
        self._cost_row.setSpacing(2)
        cost_container = QWidget()
        cost_container.setLayout(self._cost_row)
        cost_container.setMinimumWidth(46)
        layout.addWidget(cost_container)

        self.name_label = QLabel()
        layout.addWidget(self.name_label, 1)

        self.damage_label = QLabel()
        layout.addWidget(self.damage_label)

    def _clear_cost_pips(self) -> None:
        while self._cost_row.count():
            item = self._cost_row.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def update_attack(self, attack: Attack, ready: bool) -> None:
        self._clear_cost_pips()
        for energy_type in attack.cost:
            self._cost_row.addWidget(EnergyPip(energy_type, size=10))
        self._cost_row.addStretch(1)

        color = _ATTACK_READY_COLOR if ready else _ATTACK_NOT_READY_COLOR
        weight = 700 if ready else 500
        style = f"color: {color}; font-weight: {weight}; font-size: 11px;"
        self.name_label.setStyleSheet(style)
        self.damage_label.setStyleSheet(style)

        self.name_label.setText(attack.name)
        self.damage_label.setText(attack.damage or "0")
        tooltip = attack.text or f"Custo: {', '.join(attack.cost) or 'nenhum'}"
        self.setToolTip(tooltip)


class PokemonCardWidget(QFrame):
    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("pokemonCard")
        self._compact = compact
        self.setMinimumWidth(92 if compact else 160)
        self.setMinimumHeight(72 if compact else 100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        self.name_label = QLabel("(vazio)")
        self.name_label.setObjectName("cardName")
        if compact:
            # Nomes longos ("Charmander", "Rhydon"...) não cabem numa carta
            # estreita do banco — em vez de quebrar linha (o que espremia a
            # altura reservada até o texto sumir), mantemos 1 linha e
            # truncamos com "..." via _display_name, garantindo que o nome
            # sempre apareça, mesmo que cortado.
            self.name_label.setWordWrap(False)
            font = self.name_label.font()
            font.setPointSize(max(font.pointSize() - 2, 7))
            self.name_label.setFont(font)
        else:
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

        # A lista de ataques só aparece no card ativo (não no banco, que é
        # pequeno demais e não pode atacar de qualquer forma) — mostra
        # custo de energia (pips) e dano de cada ataque, esmaecendo os que
        # ainda não têm energia suficiente anexada.
        self._attacks_container: QVBoxLayout | None = None
        if not compact:
            self._attacks_container = QVBoxLayout()
            self._attacks_container.setSpacing(2)
            layout.addLayout(self._attacks_container)

        self.set_empty()

    def _display_name(self, name: str) -> str:
        # Truncamento por caracteres (não por pixel) para o nome nunca
        # desaparecer, e ser independente de fonte/DPI da tela real.
        if self._compact and len(name) > 11:
            return name[:10] + "…"
        return name

    def _clear_energy_pips(self) -> None:
        while self._energy_row.count():
            item = self._energy_row.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _clear_attack_rows(self) -> None:
        if self._attacks_container is None:
            return
        while self._attacks_container.count():
            item = self._attacks_container.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_empty(self) -> None:
        self.setProperty("empty", True)
        self.setProperty("energyType", None)
        self.name_label.setText("(vazio)")
        self.name_label.setToolTip("")
        self.name_label.setStyleSheet(f"color: {_NAME_COLOR_EMPTY}; font-weight: 700;")
        self.hp_bar.setRange(0, 1)
        self.hp_bar.setValue(0)
        self.hp_bar.setFormat("")
        self.hp_bar.setStyleSheet("")
        self._clear_energy_pips()
        self._clear_attack_rows()
        self.status_label.hide()
        self._repolish()

    def update_pokemon(self, mon: PokemonInPlay) -> None:
        self.setProperty("empty", False)
        self.name_label.setText(self._display_name(mon.card.name))
        self.name_label.setToolTip(mon.card.name)
        # Cor definida diretamente aqui (não via seletor QSS descendente
        # `[empty="true"] QLabel#cardName`): repolir o QFrame pai não força
        # reavaliação de estilo do QLabel filho de forma confiável — o
        # texto ficava "preso" na cor clara do estado vazio mesmo depois de
        # o card ser preenchido. Bug real, encontrado inspecionando um
        # screenshot com o banco cheio.
        self.name_label.setStyleSheet(f"color: {_NAME_COLOR_FILLED}; font-weight: 700;")

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

        if self._attacks_container is not None:
            self._clear_attack_rows()
            for attack in mon.card.attacks:
                row = AttackRow()
                ready = energy_satisfies_cost(mon.attached_energies, attack.cost)
                row.update_attack(attack, ready)
                self._attacks_container.addWidget(row)

        self._repolish()

    def _repolish(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
