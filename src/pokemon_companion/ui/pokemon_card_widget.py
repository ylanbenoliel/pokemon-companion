"""Widget de uma carta de Pokémon em jogo (ativo ou banco): arte da carta,
nome, barra de HP colorida por porcentagem, pips de energia anexada (com
emoji por tipo), badge de status e (no card ativo) a lista de ataques com
custo de energia e dano — clicável para atacar/recuar, estilo "toque para
agir" (Hearthstone/TCG Pocket) em vez de só uma lista de texto."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from pokemon_companion.cards_db.models import Attack
from pokemon_companion.engine.game_state import PokemonInPlay
from pokemon_companion.engine.rules import energy_satisfies_cost
from pokemon_companion.engine.status_conditions import can_attack
from pokemon_companion.ui.card_art import card_art_provider
from pokemon_companion.ui.theme import STATUS_LABELS, energy_color, energy_emoji, hp_bar_color

_NAME_COLOR_FILLED = "#263238"
_NAME_COLOR_EMPTY = "rgba(255, 255, 255, 0.55)"
_ATTACK_READY_COLOR = "#263238"
_ATTACK_NOT_READY_COLOR = "rgba(38, 50, 56, 0.45)"

_ART_SIZE_FULL = 108
_ART_SIZE_COMPACT = 52


class EnergyPip(QLabel):
    def __init__(self, energy_type: str, size: int = 18) -> None:
        super().__init__(energy_emoji(energy_type))
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background-color: {energy_color(energy_type)}; "
            f"border-radius: {size // 2}px; border: 1px solid rgba(0, 0, 0, 0.25); "
            f"font-size: {max(int(size * 0.6), 8)}px;"
        )
        self.setToolTip(energy_type)


class AttackRow(QWidget):
    """Uma linha: pips do custo de energia + nome do ataque + dano.

    Fica esmaecida quando a energia anexada (ou o status do Pokémon, ex:
    dormindo/paralisado) não permite usar o ataque agora — resposta direta
    a "não dá pra saber quantas energias meu pokémon precisa pra atacar".

    "Pronto" (estilo em negrito) e "clicável" (reage a clique) são coisas
    separadas: o card do oponente também mostra quando um ataque dele
    está pronto (informação útil sobre a ameaça), mas nunca é clicável;
    o card do jogador só fica clicável no turno dele."""

    clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._clickable = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        self._cost_row = QHBoxLayout()
        self._cost_row.setSpacing(2)
        cost_container = QWidget()
        cost_container.setLayout(self._cost_row)
        cost_container.setMinimumWidth(50)
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
                widget.hide()
                widget.deleteLater()

    def update_attack(self, attack: Attack, ready: bool, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )

        self._clear_cost_pips()
        for energy_type in attack.cost:
            self._cost_row.addWidget(EnergyPip(energy_type, size=14))
        self._cost_row.addStretch(1)

        color = _ATTACK_READY_COLOR if ready else _ATTACK_NOT_READY_COLOR
        weight = 700 if ready else 500
        style = f"color: {color}; font-weight: {weight}; font-size: 11px;"
        self.name_label.setStyleSheet(style)
        self.damage_label.setStyleSheet(style)

        self.name_label.setText(attack.name)
        self.damage_label.setText(attack.damage or "0")
        tooltip = attack.text or f"Custo: {', '.join(attack.cost) or 'nenhum'}"
        if clickable:
            tooltip += " — clique para atacar"
        self.setToolTip(tooltip)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


class PokemonCardWidget(QFrame):
    #: emitido com o índice do ataque quando uma AttackRow pronta é clicada
    attack_clicked = pyqtSignal(int)
    #: emitido quando o card inteiro é clicado (usado para recuar para o banco)
    clicked = pyqtSignal()

    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("pokemonCard")
        self._compact = compact
        self._clickable = False
        self.setMinimumWidth(96 if compact else 170)
        self.setMinimumHeight(110 if compact else 190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        self._art_size = _ART_SIZE_COMPACT if compact else _ART_SIZE_FULL
        self.art_label = QLabel()
        self.art_label.setFixedSize(self._art_size, self._art_size)
        self.art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.art_label, alignment=Qt.AlignmentFlag.AlignHCenter)

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
        # pequeno demais e não pode atacar de qualquer forma).
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
                widget.hide()
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
                widget.hide()
                widget.deleteLater()

    def set_clickable(self, clickable: bool) -> None:
        """Controla se o card inteiro reage a clique (usado só para recuar
        um Pokémon do banco para a posição ativa) — realça com borda
        amarela quando é um alvo válido no momento."""
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        self.setProperty("targetable", clickable)
        self._repolish()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_empty(self) -> None:
        self.setProperty("empty", True)
        self.setProperty("energyType", None)
        self.art_label.clear()
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
        self.set_clickable(False)
        self._repolish()

    def update_pokemon(self, mon: PokemonInPlay, interactive: bool = False) -> None:
        self.setProperty("empty", False)
        self.art_label.setPixmap(card_art_provider.get_pixmap(mon.card, self._art_size))
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
            pokemon_can_attack = can_attack(mon)
            for i, attack in enumerate(mon.card.attacks):
                row = AttackRow()
                ready = pokemon_can_attack and energy_satisfies_cost(
                    mon.attached_energies, attack.cost
                )
                row.update_attack(attack, ready=ready, clickable=ready and interactive)
                row.clicked.connect(lambda index=i: self.attack_clicked.emit(index))
                self._attacks_container.addWidget(row)

        self._repolish()

    def _repolish(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
