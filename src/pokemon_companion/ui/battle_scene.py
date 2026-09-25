"""Cena do tabuleiro: layout do tapete oficial do Pokémon TCG e a
sincronização *animada* entre o `GameState` e os itens gráficos.

Layout (espelhado para o oponente, como jogadores frente a frente):
prêmios à esquerda, deck e descarte à direita, Pokémon ativo no centro e
banco à frente do jogador; a mão em leque na borda inferior; o botão
"Fim do turno" integrado ao tabuleiro, à direita da linha central.

`sync()` compara o estado atual com o que está na tela e devolve uma
animação para cada diferença (Pokémon entrando, energia anexada, HP mudando,
nocaute, evolução, cartas compradas, prêmios pegos). Como a comparação é por
diferença, as jogadas da IA e do jogador ganham as mesmas animações sem
código específico por ação.
"""

from __future__ import annotations

import difflib
import math
import random
import re
from typing import TypeVar, cast

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects import passives
from pokemon_companion.engine.game_state import GameState, PlayerId, PlayerState, PokemonInPlay
from pokemon_companion.ui.anim import Animator, par, pause, prop, seq
from pokemon_companion.ui.art import ArtProvider, paint_card_back
from pokemon_companion.ui.items import (
    AttackButton,
    Banner,
    CardPile,
    ChoicePanel,
    DiscardPile,
    EndTurnButton,
    EnergyOrbItem,
    FloatingText,
    GameOverOverlay,
    HandCard,
    HintBubble,
    InspectPanel,
    OpponentHandFan,
    Particle,
    PokemonToken,
    PrizeGrid,
    RetreatButton,
    Ring,
    StadiumCard,
    Toast,
    ZoneHighlight,
    draw_text,
)
from pokemon_companion.ui.theme import (
    BONE,
    DAMAGE_RED,
    ENERGY_COLORS,
    FELT_LIT,
    FELT_MID,
    GOLD,
    HEAL_GREEN,
    LAMP,
    MAT_BOTTOM,
    ZONE_FILL,
    ZONE_STROKE,
    energy_color,
    primary_type,
    ui_font,
    with_alpha,
)

SCENE_W, SCENE_H = 1280.0, 900.0
CENTER_Y = 405.0

ACTIVE_SCALE = 0.92
BENCH_SCALE = 0.62
BENCH_XS = (412.0, 526.0, 640.0, 754.0, 868.0)

PLAYER_ACTIVE = QPointF(640, 522)
PLAYER_BENCH_Y = 700.0
OPPONENT_ACTIVE = QPointF(640, 288)
OPPONENT_BENCH_Y = 110.0

PLAYER_PRIZES = QPointF(120, 560)
PLAYER_DECK = QPointF(1160, 480)
PLAYER_DISCARD = QPointF(1160, 632)
OPPONENT_PRIZES = QPointF(1160, 250)
OPPONENT_DECK = QPointF(120, 330)
OPPONENT_DISCARD = QPointF(120, 178)
OPPONENT_HAND = QPointF(640, 2)

END_TURN_POS = QPointF(1000, CENTER_Y)
ATTACK_BUTTON_X = 884.0
ATTACK_BUTTON_YS = (476.0, 540.0, 604.0)
RETREAT_BUTTON_POS = QPointF(458, 522)
INSPECT_POS = QPointF(170, 420)
TOAST_ANCHOR = QPointF(372, CENTER_Y - 30)  # espaço livre à esquerda do ativo do oponente
HINT_POS = QPointF(372, 232)  # acima dos toasts, abaixo do banco do oponente

HAND_CENTER_X = 640.0
HAND_BASE_Y = 848.0

BENCH_ZONE = QRectF(350, 630, 580, 142)
ACTIVE_ZONE = QRectF(556, 424, 168, 196)
PLAY_ZONE = QRectF(300, 190, 680, 440)  # soltar Treinadores/Estádios "no tabuleiro"
STADIUM_POS = QPointF(1000, 305)

Target = tuple[str, object]  # ("token", id(mon)) | ("zone", "bench" | "active" | "play")
ItemT = TypeVar("ItemT", bound=QGraphicsObject)

#: partículas usam um gerador próprio: o `random` global é o da partida
#: (replays reproduzem a partida a partir da semente e das ações)
_FX_RANDOM = random.Random()


def fan_layout(count: int) -> list[tuple[QPointF, float]]:
    """Posições/rotações das cartas na mão: arco suave, cartas das pontas
    mais baixas e inclinadas para fora (leque estilo Hearthstone)."""
    if count == 0:
        return []
    spacing = min(98.0, 620.0 / max(count - 1, 1))
    angle = min(5.5, 30.0 / max(count - 1, 1))
    middle = (count - 1) / 2
    layout = []
    for i in range(count):
        t = i - middle
        layout.append((QPointF(HAND_CENTER_X + t * spacing, HAND_BASE_Y + t * t * 2.4), t * angle))
    return layout


def humanize(message: str, player: str = "Você", opponent: str = "IA") -> str:
    """Troca os ids internos do motor por nomes amigáveis na tela."""
    message = re.sub(r"\bplayer\b", player, message)
    return re.sub(r"\bopponent\b", opponent, message)


class BoardRoot(QGraphicsObject):
    """Pai de todos os itens do tabuleiro — tremer a tela é animar a posição
    dele (mão, botões e painéis ficam fixos, como uma HUD)."""

    def boundingRect(self) -> QRectF:
        return QRectF()

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        pass


class BattleScene(QGraphicsScene):
    token_clicked = pyqtSignal(object)
    hand_card_clicked = pyqtSignal(object)
    hand_drag_started = pyqtSignal(object)
    hand_card_dropped = pyqtSignal(object, QPointF)
    attack_clicked = pyqtSignal(int)
    retreat_clicked = pyqtSignal()
    end_turn_clicked = pyqtSignal()
    restart_clicked = pyqtSignal()
    background_clicked = pyqtSignal()
    stadium_clicked = pyqtSignal()
    choice_made = pyqtSignal(int)

    def __init__(
        self, art: ArtProvider, opponent_label: str = "IA", player_label: str = "VOCÊ"
    ) -> None:
        super().__init__(0, 0, SCENE_W, SCENE_H)
        self.art = art
        self._names = {PlayerId.PLAYER: "Você", PlayerId.OPPONENT: "IA"}
        self.root = BoardRoot()
        self.addItem(self.root)

        self.tokens: dict[int, PokemonToken] = {}
        self.hand_items: list[HandCard] = []
        self._temp_items: list[QGraphicsItem] = []
        self._targets: dict[Target, object] = {}
        self._tints: dict[PlayerId, QColor] = {}
        self._zone_items: list[ZoneHighlight] = []
        self._toasts: list[Toast] = []
        self._hint: HintBubble | None = None
        self._toast_animations: list[QAbstractAnimation] = []
        self._game_over: GameOverOverlay | None = None

        self.player_prizes = self._board_item(PrizeGrid(player_label), PLAYER_PRIZES)
        self.opponent_prizes = self._board_item(PrizeGrid(opponent_label), OPPONENT_PRIZES)
        self.player_deck = self._board_item(CardPile("Deck"), PLAYER_DECK)
        self.opponent_deck = self._board_item(CardPile("Deck"), OPPONENT_DECK)
        self.player_discard = self._board_item(DiscardPile(), PLAYER_DISCARD)
        self.opponent_discard = self._board_item(DiscardPile(), OPPONENT_DISCARD)
        self.opponent_hand = self._board_item(OpponentHandFan(), OPPONENT_HAND)

        self.end_turn_button = EndTurnButton()
        self.end_turn_button.setPos(END_TURN_POS)
        self.end_turn_button.setZValue(300)
        self.end_turn_button.clicked.connect(self.end_turn_clicked.emit)
        self.addItem(self.end_turn_button)

        self.retreat_button = RetreatButton()
        self.retreat_button.setPos(RETREAT_BUTTON_POS)
        self.retreat_button.setZValue(300)
        self.retreat_button.clicked.connect(self.retreat_clicked.emit)
        self.addItem(self.retreat_button)
        self.retreat_button.hide()

        self.attack_buttons: list[AttackButton] = []

        self.stadium_item = StadiumCard()
        self.stadium_item.setPos(STADIUM_POS)
        self.stadium_item.setZValue(250)
        self.stadium_item.clicked.connect(self.stadium_clicked.emit)
        self.stadium_item.hide()
        self.addItem(self.stadium_item)
        self._choice: ChoicePanel | None = None

        self.inspect = InspectPanel()
        self.inspect.setPos(INSPECT_POS)
        self.inspect.setZValue(2000)
        self.inspect.hide()
        self.addItem(self.inspect)

    def set_names(self, player: str, opponent: str) -> None:
        """Nomes usados nas mensagens/banners (ex: nomes dos decks no modo espectador)."""
        self._names = {PlayerId.PLAYER: player, PlayerId.OPPONENT: opponent}

    def name_of(self, side: PlayerId) -> str:
        return self._names[side]

    def _board_item(self, item: ItemT, pos: QPointF) -> ItemT:
        item.setParentItem(self.root)
        item.setPos(pos)
        return item

    # ------------------------------------------------------------------
    # fundo: o tapete de jogo sob a luz do abajur
    def side_tint(self, side: PlayerId) -> QColor:
        """Cor do tipo do Pokémon ativo daquele lado (tinge o feltro)."""
        return self._tints.get(side, QColor(ENERGY_COLORS["Colorless"]))

    def _paint_felt(self, painter: QPainter, mat: QRectF) -> None:
        weave = QLinearGradient(mat.topLeft(), mat.bottomLeft())
        weave.setColorAt(0.0, FELT_MID)
        weave.setColorAt(0.5, FELT_LIT)
        weave.setColorAt(1.0, FELT_MID)
        path = QPainterPath()
        path.addRoundedRect(mat, 26, 26)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QBrush(weave))

        painter.save()
        painter.setClipPath(path)
        # trama do feltro: fios finos na diagonal, quase invisíveis de perto
        painter.setPen(QPen(QColor(255, 255, 255, 7), 1))
        for x in range(int(mat.left()) - int(mat.height()), int(mat.right()), 7):
            painter.drawLine(QPointF(x, mat.bottom()), QPointF(x + mat.height(), mat.top()))

        # luz do abajur, vinda de cima
        lamp = QRadialGradient(QPointF(SCENE_W / 2, 210), 760)
        lamp.setColorAt(0.0, with_alpha(LAMP, 38))
        lamp.setColorAt(0.55, with_alpha(LAMP, 12))
        lamp.setColorAt(1.0, with_alpha(LAMP, 0))
        painter.fillRect(mat, QBrush(lamp))

        # cada lado recebe a cor do tipo do seu Pokémon ativo
        for side, center in (
            (PlayerId.OPPONENT, OPPONENT_ACTIVE),
            (PlayerId.PLAYER, PLAYER_ACTIVE),
        ):
            glow = QRadialGradient(center, 430)
            glow.setColorAt(0.0, with_alpha(self.side_tint(side), 60))
            glow.setColorAt(1.0, with_alpha(self.side_tint(side), 0))
            painter.fillRect(mat, QBrush(glow))
        painter.restore()

        painter.setPen(QPen(with_alpha(BONE, 40), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_slots(self, painter: QPainter) -> None:
        """Marcação impressa do tapete: retângulos das posições e a linha
        central, com um arco na cor de cada lado."""
        painter.setPen(QPen(ZONE_STROKE, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(ZONE_FILL)
        for active in (PLAYER_ACTIVE, OPPONENT_ACTIVE):
            painter.drawRoundedRect(self._token_rect(active, ACTIVE_SCALE), 18, 18)
        for y in (PLAYER_BENCH_Y, OPPONENT_BENCH_Y):
            for x in BENCH_XS:
                painter.drawRoundedRect(self._token_rect(QPointF(x, y), BENCH_SCALE), 12, 12)

        line = QLinearGradient(QPointF(0, CENTER_Y), QPointF(SCENE_W, CENTER_Y))
        line.setColorAt(0.0, with_alpha(BONE, 0))
        line.setColorAt(0.5, with_alpha(BONE, 90))
        line.setColorAt(1.0, with_alpha(BONE, 0))
        painter.setPen(QPen(QBrush(line), 2))
        painter.drawLine(QPointF(70, CENTER_Y), QPointF(SCENE_W - 70, CENTER_Y))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for side, start_angle in ((PlayerId.OPPONENT, 0), (PlayerId.PLAYER, 180)):
            painter.setPen(QPen(with_alpha(self.side_tint(side), 150), 3))
            painter.drawArc(QRectF(640 - 38, CENTER_Y - 38, 76, 76), start_angle * 16, 180 * 16)

        painter.setPen(with_alpha(BONE, 70))
        painter.setFont(ui_font(9.5))
        draw_text(painter, QRectF(350, 774, 580, 16), "Banco")
        draw_text(painter, QRectF(350, 28, 580, 16), "Banco")

    def drawBackground(self, painter: QPainter | None, rect: QRectF) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, MAT_BOTTOM)  # faixas fora da cena (janela com outra proporção)
        self._paint_felt(painter, QRectF(18, 18, SCENE_W - 36, SCENE_H - 36))
        self._paint_slots(painter)

    @staticmethod
    def _token_rect(center: QPointF, scale: float) -> QRectF:
        width, height = 150 * scale, 200 * scale
        return QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

    # ------------------------------------------------------------------
    # geometria
    @staticmethod
    def slot_geometry(side: PlayerId, slot: object) -> tuple[QPointF, float]:
        if slot == "active":
            return (PLAYER_ACTIVE if side == PlayerId.PLAYER else OPPONENT_ACTIVE), ACTIVE_SCALE
        y = PLAYER_BENCH_Y if side == PlayerId.PLAYER else OPPONENT_BENCH_Y
        return QPointF(BENCH_XS[cast(int, slot)], y), BENCH_SCALE

    def _art_unless_energy(self, card: Card) -> QPixmap | None:
        # Energias são desenhadas como orbes vetoriais, sem imagem.
        return None if card.supertype == Supertype.ENERGY else self.art.card_art(card)

    def token_for(self, mon: PokemonInPlay | None) -> PokemonToken | None:
        return None if mon is None else self.tokens.get(id(mon))

    # ------------------------------------------------------------------
    # sincronização
    def reset(self) -> None:
        for token in self.tokens.values():
            self.removeItem(token)
        for item in self.hand_items:
            self.removeItem(item)
        self.tokens.clear()
        self.hand_items.clear()
        self.clear_temp_items()
        self.clear_targets()
        if self._game_over is not None:
            self.removeItem(self._game_over)
            self._game_over = None
        self.close_choice()
        self.player_prizes.set_count(6)
        self.opponent_prizes.set_count(6)

    def sync(
        self,
        state: GameState,
        animate: bool = True,
        card_source: QPointF | None = None,
        opponent_source: QPointF | None = None,
    ) -> QAbstractAnimation | None:
        animations: list[QAbstractAnimation | None] = []
        present: dict[int, tuple[PlayerId, object, PokemonInPlay]] = {}
        for side, player_state in (
            (PlayerId.PLAYER, state.player),
            (PlayerId.OPPONENT, state.opponent),
        ):
            if player_state.active is not None:
                present[id(player_state.active)] = (side, "active", player_state.active)
            for index, mon in enumerate(player_state.bench):
                present[id(mon)] = (side, index, mon)

        vanished = {key: token for key, token in self.tokens.items() if key not in present}
        evolved: set[int] = set()
        for key, (side, slot, mon) in present.items():
            if key in self.tokens:
                continue
            for old_key, token in list(vanished.items()):
                if (
                    token.side == side
                    and token.slot == slot
                    and token.card.name in {card.name for card in mon.prior_cards}
                ):
                    del self.tokens[old_key]
                    del vanished[old_key]
                    self.tokens[key] = token
                    evolved.add(key)
                    animations.append(self._evolve(token, mon, animate))
                    break

        for old_key, token in vanished.items():
            del self.tokens[old_key]
            animations.append(self._knockout(token, animate))

        for key, (side, slot, mon) in present.items():
            position, scale = self.slot_geometry(side, slot)
            source = card_source if side == PlayerId.PLAYER else opponent_source
            existing = self.tokens.get(key)
            if existing is None:
                piece = PokemonToken(mon, self.art.card_art(mon.card), side)
                piece.setParentItem(self.root)
                piece.clicked.connect(self.token_clicked.emit)
                piece.hovered.connect(self._show_inspect)
                piece.unhovered.connect(self._hide_inspect)
                self.tokens[key] = piece
                animations.append(self._enter(piece, position, scale, source, animate))
            elif key in evolved:
                piece = existing  # a animação de evolução já cuida da troca e do HP
            else:
                piece = existing
                animations.append(self._update_token(piece, mon, position, scale, source, animate))
            piece.slot = slot
            resting_z = 20.0 if slot == "active" else 10.0
            if animate and piece.zValue() > resting_z:
                # Atacante continua por cima até terminar de voltar da investida.
                animations.append(seq(pause(420), prop(piece, b"z", resting_z, 1)))
            else:
                piece.setZValue(resting_z)

        for side, player_state in (
            (PlayerId.PLAYER, state.player),
            (PlayerId.OPPONENT, state.opponent),
        ):
            active = player_state.active
            self._tints[side] = energy_color(
                primary_type(active.card.types) if active is not None else "Colorless"
            )
        self.update()
        animations.append(self._sync_hand(state.player.hand, animate))
        self.opponent_hand.set_count(len(state.opponent.hand))
        owner = "" if state.stadium_owner is None else self.name_of(state.stadium_owner)
        self.stadium_item.set_stadium(state.stadium, owner)
        animations.append(
            self._sync_piles(
                state.player,
                self.player_prizes,
                self.player_deck,
                self.player_discard,
                animate,
                True,
            )
        )
        animations.append(
            self._sync_piles(
                state.opponent,
                self.opponent_prizes,
                self.opponent_deck,
                self.opponent_discard,
                animate,
                False,
            )
        )
        if not animate:
            return None
        return par(*animations)

    # -- Pokémon -----------------------------------------------------------
    def _enter(
        self,
        token: PokemonToken,
        position: QPointF,
        scale: float,
        source: QPointF | None,
        animate: bool,
    ) -> QAbstractAnimation | None:
        if not animate:
            token.setPos(position)
            token.setScale(scale)
            return None
        token.setPos(source or position)
        token.setScale(scale * (0.45 if source else 0.1))
        token.setOpacity(0.0 if source is None else 1.0)
        landing = self._ring(position, GOLD, 70, 380)
        return par(
            prop(token, b"pos", position, 420, easing=QEasingCurve.Type.OutCubic),
            prop(token, b"scale", scale, 520, easing=QEasingCurve.Type.OutBack),
            prop(token, b"opacity", 1.0, 200),
            seq(pause(330), landing),
        )

    def _update_token(
        self,
        token: PokemonToken,
        mon: PokemonInPlay,
        position: QPointF,
        scale: float,
        source: QPointF | None,
        animate: bool,
    ) -> QAbstractAnimation | None:
        old_hp = token.hp_value
        old_energy = token.energy_count
        token.update_from(mon)
        new_hp = mon.current_hp
        added = len(mon.attached_energies) - old_energy

        if not animate:
            token.setPos(position)
            token.setScale(scale)
            token.hp_display = float(new_hp)
            token.hidden_energies = 0
            return None

        parts: list[QAbstractAnimation | None] = []
        if token.pos() != position or abs(token.scale() - scale) > 1e-6:
            parts.append(
                par(
                    prop(token, b"pos", position, 380, easing=QEasingCurve.Type.InOutCubic),
                    prop(token, b"scale", scale, 380, easing=QEasingCurve.Type.InOutCubic),
                )
            )
        if added > 0:
            token.hidden_energies = added
            parts.append(self._energy_arrival(token, mon.attached_energies[-added:], source))
        elif added < 0:
            parts.append(
                self._burst(token.scenePos() + QPointF(0, 80 * scale), QColor(255, 255, 255), 8, 50)
            )
        if new_hp != old_hp:
            parts.append(self._hp_change(token, old_hp, new_hp))
        return par(*parts)

    def _energy_arrival(
        self, token: PokemonToken, energies: list[str], source: QPointF | None
    ) -> QAbstractAnimation:
        start = source or (
            QPointF(HAND_CENTER_X, HAND_BASE_Y) if token.side == PlayerId.PLAYER else OPPONENT_HAND
        )
        total = token.energy_count
        first_new = total - len(energies)
        steps = []
        for offset, energy in enumerate(energies):
            anchor = token.mapToScene(token.energy_anchor(first_new + offset, total))
            orb = self._temp(EnergyOrbItem(energy, 34), start, z=1500)
            orb.setScale(1.4)
            color = energy_color(energy)
            flight = par(
                prop(orb, b"pos", anchor, 460, easing=QEasingCurve.Type.InOutCubic),
                prop(orb, b"scale", 0.75, 460, easing=QEasingCurve.Type.InCubic),
                prop(orb, b"rotation", 360.0, 460),
            )
            arrival = par(
                prop(
                    token,
                    b"hidden_energies",
                    len(energies) - offset - 1,
                    1,
                    start=len(energies) - offset,
                ),
                prop(orb, b"opacity", 0.0, 120),
                self._ring(anchor, color, 46, 420),
                self._burst(anchor, color, 12, 70),
                self._flash(token, color, 0.55),
            )
            steps.append(seq(pause(140 * offset), flight, arrival))
        return par(*steps)

    def _hp_change(self, token: PokemonToken, old_hp: int, new_hp: int) -> QAbstractAnimation:
        delta = new_hp - old_hp
        color = DAMAGE_RED if delta < 0 else HEAL_GREEN
        text = f"{delta}" if delta < 0 else f"+{delta}"
        start = token.scenePos() + QPointF(0, -60 * token.scale())
        number = self._temp(FloatingText(text, color, 30), start, z=1800)
        number.setScale(0.4)
        return seq(
            pause(90),
            par(
                prop(
                    token,
                    b"hp_display",
                    float(new_hp),
                    560,
                    start=float(old_hp),
                    easing=QEasingCurve.Type.InOutQuad,
                ),
                prop(number, b"scale", 1.25, 260, easing=QEasingCurve.Type.OutBack),
                seq(
                    pause(250),
                    par(
                        prop(number, b"pos", start + QPointF(0, -70), 700),
                        prop(number, b"opacity", 0.0, 700, easing=QEasingCurve.Type.InQuad),
                    ),
                ),
            ),
        )

    def _knockout(self, token: PokemonToken, animate: bool) -> QAbstractAnimation | None:
        self._temp_items.append(token)
        if not animate:
            token.hide()
            return None
        base = token.scale()
        return seq(
            pause(420),
            par(
                self._flash(token, DAMAGE_RED, 0.9, 380),
                prop(token, b"scale", base * 0.35, 520, easing=QEasingCurve.Type.InBack),
                prop(token, b"rotation", 24.0, 520),
                prop(token, b"opacity", 0.0, 520, easing=QEasingCurve.Type.InQuad),
                self._burst(token.scenePos(), QColor(230, 230, 240), 18, 110),
            ),
        )

    def _evolve(
        self, token: PokemonToken, mon: PokemonInPlay, animate: bool
    ) -> QAbstractAnimation | None:
        art = self.art.card_art(mon.card)
        if not animate:
            token.stage_evolution(mon, art)
            token.commit = 1
            token.hp_display = float(mon.current_hp)
            return None
        token.stage_evolution(mon, art)
        base = token.scale()
        center = token.scenePos()
        return par(
            seq(
                self._flash(token, QColor(255, 255, 255), 1.0, duration=0, fade_in=260),
                prop(token, b"commit", 1, 1, start=0),
                par(
                    self._flash(token, QColor(255, 255, 255), 1.0, duration=480, fade_in=0),
                    prop(token, b"hp_display", float(mon.current_hp), 300),
                ),
            ),
            seq(
                prop(token, b"scale", base * 1.18, 260, easing=QEasingCurve.Type.OutCubic),
                prop(token, b"scale", base, 420, easing=QEasingCurve.Type.OutBack),
            ),
            seq(pause(240), self._ring(center, GOLD, 110, 520)),
            seq(pause(240), self._burst(center, GOLD, 20, 130)),
        )

    # -- mão ------------------------------------------------------------
    def _sync_hand(self, hand: list[Card], animate: bool) -> QAbstractAnimation | None:
        old_items = self.hand_items
        matcher = difflib.SequenceMatcher(
            a=[item.card.id for item in old_items], b=[card.id for card in hand], autojunk=False
        )
        new_items: list[HandCard | None] = [None] * len(hand)
        removed: list[HandCard] = []
        created: list[HandCard] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    new_items[j1 + k] = old_items[i1 + k]
                continue
            removed.extend(old_items[i1:i2])
            for j in range(j1, j2):
                item = HandCard(hand[j], self._art_unless_energy(hand[j]), j)
                item.clicked.connect(self.hand_card_clicked.emit)
                item.drag_started.connect(self.hand_drag_started.emit)
                item.dropped.connect(self.hand_card_dropped.emit)
                item.hovered.connect(self._show_hand_inspect)
                item.unhovered.connect(lambda _item: self.inspect.hide())
                self.addItem(item)
                new_items[j] = item
                created.append(item)

        items = [item for item in new_items if item is not None]
        for index, item in enumerate(items):
            item.hand_index = index
        self.hand_items = items

        animations: list[QAbstractAnimation | None] = []
        for item in removed:
            self._temp_items.append(item)
            if animate:
                animations.append(
                    par(
                        prop(item, b"opacity", 0.0, 160),
                        prop(item, b"scale", item.scale() * 0.7, 160),
                    )
                )
            else:
                item.hide()

        draw_delay = 0.0
        for (position, rotation), item in zip(fan_layout(len(items)), items, strict=True):
            item.set_fan_pose(position, rotation, float(item.hand_index))
            if not animate:
                item.place_at_fan()
                continue
            if item in created:
                item.setPos(PLAYER_DECK)
                item.setRotation(-25)
                item.setScale(0.45)
                item.setOpacity(0.0)
                item.setZValue(item.fan_z)
                animations.append(seq(pause(draw_delay), item.fan_animation(460)))
                draw_delay += 130
            elif not item.dragging:
                animations.append(item.fan_animation(320))
        return par(*animations) if animate else None

    # -- pilhas e prêmios -----------------------------------------------
    def _sync_piles(
        self,
        player: PlayerState,
        prizes: PrizeGrid,
        deck: CardPile,
        discard: DiscardPile,
        animate: bool,
        is_player: bool,
    ) -> QAbstractAnimation | None:
        taken = prizes.count - len(player.prizes)
        animation: QAbstractAnimation | None = None
        if taken > 0 and animate:
            flights = []
            target = QPointF(HAND_CENTER_X, HAND_BASE_Y) if is_player else OPPONENT_HAND
            for i in range(taken):
                start = prizes.mapToScene(prizes.slot_rect(prizes.count - 1 - i).center())
                card = self._temp(_FlyingBack(), start, z=1600)
                flights.append(
                    seq(
                        pause(i * 120),
                        par(
                            prop(card, b"pos", target, 620, easing=QEasingCurve.Type.InOutCubic),
                            prop(card, b"rotation", 20.0 if is_player else -20.0, 620),
                            seq(pause(420), prop(card, b"opacity", 0.0, 200)),
                        ),
                    )
                )
            animation = par(*flights, self._ring(prizes.scenePos(), GOLD, 80, 500))
        prizes.set_count(len(player.prizes))
        deck.set_count(len(player.deck))
        top = player.discard[-1] if player.discard else None
        discard.set_top(
            top,
            self._art_unless_energy(top) if top is not None else None,
            len(player.discard),
        )
        return animation

    # ------------------------------------------------------------------
    # efeitos
    def _temp(self, item: QGraphicsObject, pos: QPointF, z: float) -> QGraphicsObject:
        item.setPos(pos)
        item.setZValue(z)
        self.addItem(item)
        self._temp_items.append(item)
        return item

    def clear_temp_items(self) -> None:
        for item in self._temp_items:
            if item.scene() is self:
                self.removeItem(item)
        self._temp_items.clear()

    def _ring(
        self, center: QPointF, color: QColor, radius: float, duration: float
    ) -> QAbstractAnimation:
        ring = self._temp(Ring(color, radius / 2.2), center, z=1400)
        ring.setScale(0.3)
        return par(
            prop(ring, b"scale", 2.2, duration, easing=QEasingCurve.Type.OutCubic),
            prop(ring, b"opacity", 0.0, duration, start=1.0, easing=QEasingCurve.Type.InQuad),
        )

    def _burst(
        self, center: QPointF, color: QColor, count: int, distance: float
    ) -> QAbstractAnimation:
        animations = []
        for i in range(count):
            angle = (2 * math.pi * i / count) + _FX_RANDOM.uniform(-0.25, 0.25)
            reach = distance * _FX_RANDOM.uniform(0.6, 1.15)
            particle = self._temp(Particle(color, _FX_RANDOM.uniform(4, 9)), center, z=1450)
            end = center + QPointF(math.cos(angle) * reach, math.sin(angle) * reach)
            duration = _FX_RANDOM.uniform(380, 620)
            animations.append(
                par(
                    prop(particle, b"pos", end, duration, easing=QEasingCurve.Type.OutQuad),
                    prop(
                        particle,
                        b"opacity",
                        0.0,
                        duration,
                        start=1.0,
                        easing=QEasingCurve.Type.InQuad,
                    ),
                    prop(particle, b"scale", 0.3, duration, start=1.0),
                )
            )
        return par(*animations)

    def _flash(
        self,
        token: PokemonToken,
        color: QColor,
        strength: float,
        duration: float = 320,
        fade_in: float = 60,
    ) -> QAbstractAnimation:
        if Animator.reduce_motion:
            strength = min(strength, 0.25)
        token.set_flash_color(color)
        return seq(
            prop(token, b"flash", strength, fade_in, start=0.0) if fade_in else None,
            prop(token, b"flash", 0.0, duration, start=strength) if duration else None,
        )

    def shake(self, strength: float = 10) -> QAbstractAnimation:
        if Animator.reduce_motion:
            return pause(300)
        animation = QPropertyAnimation(self.root, b"pos")
        animation.setDuration(Animator.ms(300))
        offsets = [(1, -0.6), (-0.9, 0.7), (0.7, 0.4), (-0.5, -0.5), (0.3, 0.3), (-0.15, 0.1)]
        animation.setKeyValueAt(0.0, QPointF(0, 0))
        for i, (dx, dy) in enumerate(offsets, start=1):
            animation.setKeyValueAt(i / (len(offsets) + 1), QPointF(dx * strength, dy * strength))
        animation.setKeyValueAt(1.0, QPointF(0, 0))
        return animation

    def lunge(
        self, attacker: PokemonInPlay, defender: PokemonInPlay | None
    ) -> QAbstractAnimation | None:
        """Investida do atacante na direção do defensor + hit-stop."""
        token = self.token_for(attacker)
        target = self.token_for(defender)
        if token is None:
            return None
        start = token.pos()
        goal = target.pos() if target is not None else start
        forward = start + (goal - start) * 0.42
        wind_up = start - (goal - start) * 0.06
        token.setZValue(60)
        return seq(
            prop(token, b"pos", wind_up, 140, easing=QEasingCurve.Type.OutQuad),
            prop(token, b"pos", forward, 150, easing=QEasingCurve.Type.InQuad),
            pause(70),  # hit-stop: congela no impacto por um instante
        )

    def impact(self, defender_token: PokemonToken, energy_type: str) -> QAbstractAnimation:
        color = energy_color(energy_type)
        center = defender_token.scenePos()
        return par(
            self.shake(11),
            self._flash(defender_token, QColor(255, 255, 255), 0.95, 300),
            self._ring(center, color, 150, 460),
            self._burst(center, color, 22, 150),
            self._burst(center, QColor(255, 255, 255), 8, 80),
        )

    def banner(self, title: str, color: QColor) -> QAbstractAnimation:
        item = self._temp(Banner(title, color), QPointF(SCENE_W / 2, CENTER_Y), z=2500)
        item.setOpacity(0.0)
        item.setScale(0.7)
        return seq(
            par(
                prop(item, b"opacity", 1.0, 180),
                prop(item, b"scale", 1.0, 320, easing=QEasingCurve.Type.OutBack),
            ),
            pause(520),
            par(
                prop(item, b"opacity", 0.0, 260),
                prop(item, b"scale", 1.12, 260),
            ),
        )

    # ------------------------------------------------------------------
    # toasts (mensagens do motor), não bloqueiam o jogo
    def show_toast(self, message: str) -> None:
        toast = Toast(
            humanize(message, self._names[PlayerId.PLAYER], self._names[PlayerId.OPPONENT])
        )
        toast.setZValue(2200)
        toast.setPos(TOAST_ANCHOR)
        toast.setOpacity(0.0)
        self.addItem(toast)
        self._toasts.append(toast)
        while len(self._toasts) > 3:
            self._drop_toast(self._toasts[0])
        for i, existing in enumerate(reversed(self._toasts)):
            target = TOAST_ANCHOR - QPointF(0, i * 40)
            self._run_toast_animation(prop(existing, b"pos", target, 220))
        self._run_toast_animation(prop(toast, b"opacity", 1.0, 160))
        QTimer.singleShot(Animator.ms(2600), lambda: self._fade_toast(toast))

    def show_hint(self, key: str, text: str) -> HintBubble:
        """Dica para iniciantes (uma por vez; troca a anterior)."""
        self.hide_hint()
        bubble = HintBubble(key, text)
        bubble.setZValue(2150)
        bubble.setPos(HINT_POS)
        bubble.setOpacity(0.0)
        bubble.dismissed.connect(self.hide_hint)
        self.addItem(bubble)
        self._hint = bubble
        self._run_toast_animation(prop(bubble, b"opacity", 1.0, 220))
        return bubble

    def hide_hint(self) -> None:
        bubble, self._hint = self._hint, None
        if bubble is not None and bubble.scene() is self:
            self.removeItem(bubble)

    @property
    def hint(self) -> HintBubble | None:
        return self._hint

    def _run_toast_animation(self, animation: QAbstractAnimation) -> None:
        self._toast_animations = [
            a for a in self._toast_animations if a.state() != QAbstractAnimation.State.Stopped
        ]
        self._toast_animations.append(animation)
        animation.start()

    def _fade_toast(self, toast: Toast) -> None:
        if toast not in self._toasts:
            return
        fade = prop(toast, b"opacity", 0.0, 300)
        fade.finished.connect(lambda: self._drop_toast(toast))
        self._run_toast_animation(fade)
        if Animator.speed == 0:
            self._drop_toast(toast)

    def _drop_toast(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast.scene() is self:
            self.removeItem(toast)

    @property
    def toasts(self) -> list[Toast]:
        return list(self._toasts)

    # ------------------------------------------------------------------
    # controles do jogador
    def set_player_controls(
        self,
        *,
        state: GameState,
        my_turn: bool,
        ready_attacks: set[int],
        can_retreat: bool,
        retreat_mode: bool,
        playable_hand: set[int],
        end_turn_mode: str,
        ability_tokens: frozenset[int] = frozenset(),
        stadium_usable: bool = False,
    ) -> None:
        for key, token in self.tokens.items():
            token.set_ability_ready(key in ability_tokens and my_turn)
        self.stadium_item.set_usable(stadium_usable and my_turn)
        for item in self.hand_items:
            item.set_playable(item.hand_index in playable_hand, my_turn)
        self.end_turn_button.set_mode(end_turn_mode)

        active = state.player.active
        attacks = (
            passives.available_attacks(state, PlayerId.PLAYER, active) if active is not None else []
        )
        while len(self.attack_buttons) < len(attacks):
            button = AttackButton(len(self.attack_buttons))
            button.setZValue(300)
            button.clicked.connect(lambda b=button: self.attack_clicked.emit(b.index))
            self.addItem(button)
            self.attack_buttons.append(button)
        for i, button in enumerate(self.attack_buttons):
            if active is None or i >= len(attacks) or end_turn_mode == "over":
                button.hide()
                continue
            button.show()
            button.setPos(ATTACK_BUTTON_X, ATTACK_BUTTON_YS[min(i, len(ATTACK_BUTTON_YS) - 1)])
            ready = i in ready_attacks
            button.set_attack(attacks[i], primary_type(active.card.types), ready)
            button.set_enabled(ready and my_turn)
            button.setOpacity(1.0 if my_turn else 0.55)

        if active is None or end_turn_mode == "over":
            self.retreat_button.hide()
        else:
            self.retreat_button.show()
            self.retreat_button.set_cost(list(active.card.retreat_cost))
            self.retreat_button.set_enabled(can_retreat and my_turn)
            self.retreat_button.set_highlight(retreat_mode)
            self.retreat_button.setOpacity(1.0 if my_turn else 0.55)

    # -- alvos (soltar carta / escolher Pokémon) -------------------------
    def highlight_targets(self, targets: list[Target]) -> None:
        self.clear_targets()
        for target in targets:
            kind, value = target
            if kind == "token":
                token = self.tokens.get(cast(int, value))
                if token is not None:
                    token.set_targetable(True)
                    self._targets[target] = token
            elif kind == "zone":
                rect, label = {
                    "bench": (BENCH_ZONE, "SOLTE NO BANCO"),
                    "play": (PLAY_ZONE, "SOLTE PARA JOGAR"),
                }.get(cast(str, value), (ACTIVE_ZONE, "SOLTE AQUI"))
                zone = ZoneHighlight(rect, label)
                zone.setZValue(5)
                self.addItem(zone)
                self._zone_items.append(zone)
                self._targets[target] = zone

    def clear_targets(self) -> None:
        for target, item in self._targets.items():
            if target[0] == "token" and isinstance(item, PokemonToken):
                item.set_targetable(False)
        for zone in self._zone_items:
            zone.stop()
            if zone.scene() is self:
                self.removeItem(zone)
        self._zone_items.clear()
        self._targets.clear()

    @property
    def targets(self) -> list[Target]:
        return list(self._targets)

    def target_at(self, scene_pos: QPointF) -> Target | None:
        for target, item in self._targets.items():
            if isinstance(item, PokemonToken) and item.card_scene_rect().contains(scene_pos):
                return target
        for target, item in self._targets.items():
            if isinstance(item, ZoneHighlight) and item.zone_rect.contains(scene_pos):
                return target
        return None

    def target_of_token(self, token: PokemonToken) -> Target | None:
        for key, existing in self.tokens.items():
            if existing is token and ("token", key) in self._targets:
                return ("token", key)
        return None

    # -- painel de inspeção ---------------------------------------------
    def _show_inspect(self, token: PokemonToken) -> None:
        self.inspect.show_card(token.card, self.art.card_art(token.card), token.hp_value)
        on_left = token.scenePos().x() >= SCENE_W / 2 or token.side == PlayerId.PLAYER
        self.inspect.setPos(
            INSPECT_POS if on_left else QPointF(SCENE_W - INSPECT_POS.x(), INSPECT_POS.y())
        )
        self.inspect.show()

    def _show_hand_inspect(self, item: HandCard) -> None:
        if item.card.supertype == Supertype.ENERGY:
            return
        self.inspect.show_card(item.card, self._art_unless_energy(item.card), item.card.hp or 0)
        self.inspect.setPos(INSPECT_POS)
        self.inspect.show()

    def _hide_inspect(self, token: PokemonToken) -> None:
        self.inspect.hide()

    # -- escolhas ---------------------------------------------------------
    def show_choice(self, title: str, options: list[str]) -> ChoicePanel:
        self.close_choice()
        panel = ChoicePanel(SCENE_W, SCENE_H, title, options)
        panel.setZValue(4000)
        panel.chosen.connect(self._on_choice)
        self.addItem(panel)
        self._choice = panel
        return panel

    def _on_choice(self, index: int) -> None:
        self.close_choice()
        self.choice_made.emit(index)

    def close_choice(self) -> None:
        if self._choice is not None:
            self._choice.hide()
            if self._choice.scene() is self:
                self.removeItem(self._choice)
            self._choice = None

    @property
    def choice_panel(self) -> ChoicePanel | None:
        return self._choice

    # -- fim de jogo ----------------------------------------------------
    def show_game_over(self, title: str, won: bool) -> None:
        if self._game_over is not None:
            return
        overlay = GameOverOverlay(SCENE_W, SCENE_H, title, GOLD if won else QColor("#ff6b6b"))
        overlay.setZValue(5000)
        overlay.button.clicked.connect(self.restart_clicked.emit)
        overlay.setOpacity(0.0)
        self.addItem(overlay)
        self._game_over = overlay
        self._run_toast_animation(prop(overlay, b"opacity", 1.0, 500))

    @property
    def game_over_overlay(self) -> GameOverOverlay | None:
        return self._game_over

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        super().mousePressEvent(event)
        if event is not None and not event.isAccepted():
            self.background_clicked.emit()

    def opponent_hand_position(self) -> QPointF:
        return OPPONENT_HAND + QPointF(0, 40)


class _FlyingBack(QGraphicsObject):
    """Verso de carta temporário (prêmio voando para a mão)."""

    RECT = QRectF(-25, -35, 50, 70)

    def boundingRect(self) -> QRectF:
        return self.RECT

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        paint_card_back(painter, self.RECT)
