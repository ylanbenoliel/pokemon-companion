"""Itens gráficos do tabuleiro (QGraphicsObject), todos desenhados com
QPainter em coordenadas lógicas — a cena inteira escala com a janela.

Convenção: a origem (0, 0) de cada item é o seu centro, para que escala e
rotação (usadas nas animações) aconteçam em torno do meio do item.
"""

from __future__ import annotations

from PyQt6 import QtCore
from PyQt6.QtCore import (
    QAbstractAnimation,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextOption,
    QTransform,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from pokemon_companion.cards_db.models import Attack, Card, Supertype
from pokemon_companion.engine.effects.cardinfo import has_rule_box
from pokemon_companion.engine.effects.descriptions import describe_ability, describe_card
from pokemon_companion.engine.effects.trainers import is_implemented
from pokemon_companion.engine.game_state import PokemonInPlay
from pokemon_companion.ui.anim import Animator, par, prop, seq
from pokemon_companion.ui.art import (
    paint_card_back,
    paint_energy_orb,
    paint_placeholder_art,
)
from pokemon_companion.ui.theme import (
    BONE,
    ENERGY_NAMES_PT,
    GOLD,
    INK,
    PLAYABLE_GLOW,
    STATUS_COLORS,
    STATUS_LABELS,
    VOLT,
    display_font,
    energy_color,
    hp_color,
    primary_type,
    ui_font,
    with_alpha,
)

TRAINER_COLORS = {
    "Item": "#2f6fd6",
    "Supporter": "#e07b1a",
    "Stadium": "#2e9e5b",
    "Tool": "#8a4fd1",
}
TRAINER_NAMES_PT = {
    "Item": "Item",
    "Supporter": "Apoiador",
    "Stadium": "Estádio",
    "Tool": "Ferramenta",
}


def trainer_kind(card: Card) -> str:
    return next((kind for kind in TRAINER_COLORS if kind in card.subtypes), "")


# pyqtProperty existe em runtime, mas falta nos stubs de tipo do PyQt6.
pyqtProperty = QtCore.pyqtProperty  # type: ignore[attr-defined]

# --------------------------------------------------------------------------
# helpers de desenho


def draw_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter,
    wrap: bool = False,
) -> None:
    option = QTextOption(align)
    option.setWrapMode(QTextOption.WrapMode.WordWrap if wrap else QTextOption.WrapMode.NoWrap)
    painter.drawText(rect, text, option)


def draw_outlined_text(
    painter: QPainter,
    center: QPointF,
    text: str,
    font: QFont,
    fill: QColor,
    outline: QColor | None = None,
    outline_width: float = 4.0,
) -> None:
    metrics = QFontMetricsF(font)
    width = metrics.horizontalAdvance(text)
    baseline = QPointF(center.x() - width / 2, center.y() + metrics.ascent() / 2 - 2)
    path = QPainterPath()
    path.addText(baseline, font, text)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.strokePath(
        path,
        QPen(
            outline or QColor(0, 0, 0, 200),
            outline_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        ),
    )
    painter.fillPath(path, fill)
    painter.restore()


def paint_soft_shadow(painter: QPainter, rect: QRectF, radius: float, offset: float = 6) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    base = rect.translated(0, offset)
    for grow, alpha in ((10, 14), (6, 22), (3, 32)):
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawRoundedRect(
            base.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow
        )
    painter.restore()


def paint_glow(
    painter: QPainter, rect: QRectF, radius: float, color: QColor, amount: float
) -> None:
    """Halo suave (faixa larga translúcida) + borda nítida colada no card."""
    if amount <= 0:
        return
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    halo = QColor(color)
    halo.setAlphaF(min(1.0, 0.28 * amount))
    painter.setPen(QPen(halo, 10))
    painter.drawRoundedRect(rect.adjusted(-5, -5, 5, 5), radius + 5, radius + 5)
    edge = QColor(color)
    edge.setAlphaF(min(1.0, 0.95 * amount))
    painter.setPen(QPen(edge, 2.5))
    painter.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1.5, radius + 1.5)
    painter.restore()


def draw_art(painter: QPainter, rect: QRectF, art: QPixmap | None, energy_type: str) -> None:
    if art is None or art.isNull():
        paint_placeholder_art(painter, rect, energy_type)
        return
    scaled = art.size().scaled(
        int(rect.width() * 4), int(rect.height() * 4), Qt.AspectRatioMode.KeepAspectRatio
    )
    target = QRectF(0, 0, scaled.width() / 4, scaled.height() / 4)
    target.moveCenter(rect.center())
    painter.drawPixmap(target, art, QRectF(art.rect()))


def scaled_art(art: QPixmap | None, width: float, height: float) -> QPixmap | None:
    """Pré-escala a arte (2x para telas retina) para não reamostrar a cada
    frame de animação."""
    if art is None or art.isNull():
        return None
    return art.scaled(
        int(width * 2),
        int(height * 2),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def orb_row_positions(
    count: int, center_x: float, y: float, size: float, gap: float = 3
) -> list[QRectF]:
    total = count * size + max(count - 1, 0) * gap
    left = center_x - total / 2
    return [QRectF(left + i * (size + gap), y, size, size) for i in range(count)]


def _pulse_animation(target: QGraphicsObject, name: bytes) -> QPropertyAnimation | None:
    """Brilho pulsante em loop (sinaliza "dá pra interagir aqui")."""
    if Animator.speed == 0:
        return None
    animation = QPropertyAnimation(target, name)
    animation.setDuration(900)
    animation.setKeyValueAt(0.0, 0.35)
    animation.setKeyValueAt(0.5, 1.0)
    animation.setKeyValueAt(1.0, 0.35)
    animation.setLoopCount(-1)
    return animation


# --------------------------------------------------------------------------
# Pokémon em jogo


TOKEN_RECT = QRectF(-75, -100, 150, 200)


class PokemonToken(QGraphicsObject):
    """Pokémon no campo (ativo ou banco): arte, nome, HP animável, orbes de
    energia, status. A diferença de tamanho ativo/banco é só `scale`, o que
    deixa recuo/promoção animarem suavemente."""

    clicked = pyqtSignal(object)
    hovered = pyqtSignal(object)
    unhovered = pyqtSignal(object)

    def __init__(self, mon: PokemonInPlay, art: QPixmap | None, side: object) -> None:
        super().__init__()
        self.side = side
        self.slot: object = "active"
        self.card: Card = mon.card
        self._art = scaled_art(art, 122, 116)
        self._max_hp = mon.max_hp
        self._hp_display = float(mon.current_hp)
        self._tool: str | None = mon.tool.name if mon.tool is not None else None
        self._ability_ready = False
        self._energies: list[str] = list(mon.attached_energies)
        self._hidden_energies = 0
        self._status = mon.status.name
        self._glow = 0.0
        self._flash = 0.0
        self._flash_color = QColor(255, 255, 255)
        self._pending: tuple[PokemonInPlay, QPixmap | None] | None = None
        self._pulse: QPropertyAnimation | None = None
        self._clickable = False
        self.setAcceptHoverEvents(True)

    # -- propriedades animáveis -------------------------------------------
    def _get_hp(self) -> float:
        return self._hp_display

    def _set_hp(self, value: float) -> None:
        self._hp_display = value
        self.update()

    hp_display = pyqtProperty(float, fget=_get_hp, fset=_set_hp)

    def _get_hidden(self) -> int:
        return self._hidden_energies

    def _set_hidden(self, value: int) -> None:
        self._hidden_energies = max(0, value)
        self.update()

    hidden_energies = pyqtProperty(int, fget=_get_hidden, fset=_set_hidden)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

    def _get_flash(self) -> float:
        return self._flash

    def _set_flash(self, value: float) -> None:
        self._flash = value
        self.update()

    flash = pyqtProperty(float, fget=_get_flash, fset=_set_flash)

    def _get_commit(self) -> int:
        return 0

    def _set_commit(self, value: int) -> None:
        # Usado dentro de sequências de animação: aplica a troca de carta
        # (evolução) exatamente no pico do flash branco.
        if value and self._pending is not None:
            mon, art = self._pending
            self._pending = None
            self._apply(mon, art)

    commit = pyqtProperty(int, fget=_get_commit, fset=_set_commit)

    # -- estado ---------------------------------------------------------
    @property
    def hp_value(self) -> int:
        return int(round(self._hp_display))

    @property
    def energy_count(self) -> int:
        return len(self._energies)

    @property
    def visible_energy_count(self) -> int:
        return len(self._energies) - self._hidden_energies

    @property
    def max_hp(self) -> int:
        return self._max_hp

    def set_flash_color(self, color: QColor) -> None:
        self._flash_color = QColor(color)

    def update_from(self, mon: PokemonInPlay) -> None:
        """Atualiza energias/status/carta. HP visual é animado à parte."""
        self.card = mon.card
        self._max_hp = mon.max_hp
        self._energies = list(mon.attached_energies)
        self._status = mon.status.name
        self._tool = mon.tool.name if mon.tool is not None else None
        self.update()

    @property
    def tool_name(self) -> str | None:
        return self._tool

    @property
    def ability_ready(self) -> bool:
        return self._ability_ready

    def set_ability_ready(self, ready: bool) -> None:
        if ready != self._ability_ready:
            self._ability_ready = ready
            self.update()

    def stage_evolution(self, mon: PokemonInPlay, art: QPixmap | None) -> None:
        self._pending = (mon, art)

    def _apply(self, mon: PokemonInPlay, art: QPixmap | None) -> None:
        self._art = scaled_art(art, 122, 116)
        self.update_from(mon)

    def energy_anchor(self, index: int, count: int) -> QPointF:
        rects = orb_row_positions(count, 0, 88, 24)
        return rects[index].center() if 0 <= index < len(rects) else QPointF(0, 100)

    def set_targetable(self, targetable: bool) -> None:
        if self._pulse is not None:
            self._pulse.stop()
            self._pulse = None
        if targetable:
            self._pulse = _pulse_animation(self, b"glow")
            if self._pulse is None:
                self.glow = 1.0
            else:
                self._pulse.start()
        else:
            self.glow = 0.0
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if targetable else Qt.CursorShape.ArrowCursor
        )

    @property
    def targetable(self) -> bool:
        return self._glow > 0

    def card_scene_rect(self) -> QRectF:
        return self.mapRectToScene(TOKEN_RECT)

    # -- QGraphicsItem --------------------------------------------------
    def boundingRect(self) -> QRectF:
        return TOKEN_RECT.adjusted(-18, -18, 18, 30)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        energy_type = primary_type(self.card.types)
        type_color = energy_color(energy_type)
        card = TOKEN_RECT

        paint_glow(painter, card, 16, GOLD, self._glow)
        paint_soft_shadow(painter, card, 16)

        # cartolina da carta, com a faixa do tipo atrás da arte
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BONE)
        painter.drawRoundedRect(card, 14, 14)
        art_rect = QRectF(-67, -92, 134, 128)
        clip = QPainterPath()
        clip.addRoundedRect(art_rect, 10, 10)
        painter.save()
        painter.setClipPath(clip)
        band = QLinearGradient(art_rect.topLeft(), art_rect.bottomLeft())
        band.setColorAt(0.0, type_color.lighter(128))
        band.setColorAt(1.0, type_color.lighter(168))
        painter.fillRect(art_rect, QBrush(band))
        if has_rule_box(self.card):
            # brilho holográfico das cartas com regra especial (ex/Mega)
            holo = QLinearGradient(art_rect.bottomLeft(), art_rect.topRight())
            for stop, color in (
                (0.10, QColor(255, 120, 190, 55)),
                (0.35, QColor(120, 215, 255, 60)),
                (0.60, QColor(255, 236, 130, 65)),
                (0.85, QColor(150, 255, 200, 45)),
            ):
                holo.setColorAt(stop, color)
            holo.setColorAt(0.0, QColor(255, 255, 255, 0))
            holo.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(art_rect, QBrush(holo))
        draw_art(painter, art_rect.adjusted(4, 4, -4, -2), self._art, energy_type)
        painter.restore()
        painter.setPen(QPen(with_alpha(INK, 40), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(art_rect, 10, 10)

        plate = QRectF(-67, 40, 134, 26)
        painter.setPen(INK)
        painter.setFont(ui_font(10.5, QFont.Weight.DemiBold))
        name = QFontMetricsF(painter.font()).elidedText(
            self.card.name, Qt.TextElideMode.ElideRight, 92
        )
        draw_text(painter, plate.adjusted(2, 0, -2, 0), name, Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(display_font(9))
        painter.setPen(with_alpha(INK, 140))
        draw_text(
            painter,
            plate.adjusted(2, 0, -2, 0),
            str(self._max_hp),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )

        bar = QRectF(-67, 70, 134, 14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(INK, 45))
        painter.drawRoundedRect(bar, 7, 7)
        ratio = max(0.0, min(1.0, self._hp_display / self._max_hp))
        if ratio > 0:
            fill = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
            painter.setBrush(hp_color(self.hp_value, self._max_hp))
            painter.drawRoundedRect(fill, 7, 7)
        painter.setPen(INK)
        painter.setFont(display_font(8.5))
        draw_text(painter, bar, f"{self.hp_value}")

        visible = self._energies[: self.visible_energy_count]
        for rect, energy in zip(orb_row_positions(len(visible), 0, 89, 22), visible, strict=True):
            paint_energy_orb(painter, rect, energy)

        if self._status != "NONE":
            label = STATUS_LABELS.get(self._status, self._status)
            painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
            width = QFontMetricsF(painter.font()).horizontalAdvance(label) + 14
            badge = QRectF(card.left() + 6, card.top() + 6, width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(STATUS_COLORS.get(self._status, "#d81b60")))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(QColor("white"))
            draw_text(painter, badge, label)

        if self._tool:
            painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
            label = QFontMetricsF(painter.font()).elidedText(
                self._tool, Qt.TextElideMode.ElideRight, 104
            )
            width = QFontMetricsF(painter.font()).horizontalAdvance(label) + 16
            pill = QRectF(-width / 2, 20, width, 17)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TRAINER_COLORS["Tool"]))
            painter.drawRoundedRect(pill, 8.5, 8.5)
            painter.setPen(QColor("white"))
            draw_text(painter, pill, label)

        if self._ability_ready:
            painter.setFont(ui_font(8.5, QFont.Weight.DemiBold))
            badge = QRectF(card.right() - 78, card.top() + 6, 72, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(VOLT)
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(INK)
            draw_text(painter, badge, "Habilidade")

        if self._flash > 0:
            overlay = QColor(self._flash_color)
            overlay.setAlphaF(min(1.0, self._flash) * 0.8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(overlay)
            painter.drawRoundedRect(card, 16, 16)

    # -- input ----------------------------------------------------------
    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is not None:
            event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        self.clicked.emit(self)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        self.hovered.emit(self)

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        self.unhovered.emit(self)


# --------------------------------------------------------------------------
# cartas na mão


HAND_RECT = QRectF(-62, -86, 124, 172)
HAND_RAISE_Y = 742.0
HAND_RAISE_SCALE = 1.4


class HandCard(QGraphicsObject):
    """Carta na mão em leque. Passar o mouse levanta e amplia; arrastar leva
    a carta até um alvo destacado; carta que não pode ser jogada fica
    esmaecida e "treme" ao ser clicada."""

    clicked = pyqtSignal(object)
    drag_started = pyqtSignal(object)
    dropped = pyqtSignal(object, QPointF)
    hovered = pyqtSignal(object)
    unhovered = pyqtSignal(object)

    def __init__(self, card: Card, art: QPixmap | None, hand_index: int) -> None:
        super().__init__()
        self.card = card
        self.hand_index = hand_index
        self._art = scaled_art(art, 104, 92)
        self.fan_pos = QPointF()
        self.fan_rotation = 0.0
        self.fan_z = 0.0
        self._playable = False
        self._interactive = False
        self.dragging = False
        self._press_scene: QPointF | None = None
        self._grab_offset = QPointF()
        self._rejected = False
        self._motion: QAbstractAnimation | None = None
        self._glow = 0.0
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

    @property
    def playable(self) -> bool:
        return self._playable and self._interactive

    def set_playable(self, playable: bool, interactive: bool) -> None:
        self._playable = playable
        self._interactive = interactive
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if self.playable else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def set_fan_pose(self, pos: QPointF, rotation: float, z: float) -> None:
        self.fan_pos = pos
        self.fan_rotation = rotation
        self.fan_z = z

    def place_at_fan(self) -> None:
        self.setPos(self.fan_pos)
        self.setRotation(self.fan_rotation)
        self.setScale(1.0)
        self.setZValue(self.fan_z)

    def fan_animation(self, duration: float = 320) -> QAbstractAnimation:
        self.setZValue(self.fan_z)
        return par(
            prop(self, b"pos", self.fan_pos, duration),
            prop(self, b"rotation", self.fan_rotation, duration),
            prop(self, b"scale", 1.0, duration),
            prop(self, b"opacity", 1.0, duration * 0.6),
        )

    def _play(self, animation: QAbstractAnimation) -> None:
        if self._motion is not None:
            self._motion.stop()
        self._motion = animation
        animation.start()

    def return_to_fan(self) -> None:
        self._play(self.fan_animation(260))

    # -- QGraphicsItem --------------------------------------------------
    def boundingRect(self) -> QRectF:
        return HAND_RECT.adjusted(-16, -16, 16, 22)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = HAND_RECT
        if self.playable:
            paint_glow(painter, rect, 12, PLAYABLE_GLOW, 0.75)
        paint_soft_shadow(painter, rect, 12, offset=5)

        if self.card.supertype == Supertype.ENERGY:
            self._paint_energy(painter, rect)
        elif self.card.is_pokemon:
            self._paint_pokemon(painter, rect)
        else:
            self._paint_trainer(painter, rect)

        if self._interactive and not self._playable:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(10, 14, 28, 125))
            painter.drawRoundedRect(rect, 12, 12)

    def _paint_pokemon(self, painter: QPainter, rect: QRectF) -> None:
        energy_type = primary_type(self.card.types)
        type_color = energy_color(energy_type)
        frame = QLinearGradient(rect.topLeft(), rect.bottomRight())
        frame.setColorAt(0.0, type_color.lighter(145))
        frame.setColorAt(1.0, type_color.darker(130))
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1.5))
        painter.setBrush(QBrush(frame))
        painter.drawRoundedRect(rect, 12, 12)

        painter.setPen(QColor("white"))
        painter.setFont(ui_font(10))
        header = QRectF(rect.left() + 8, rect.top() + 5, rect.width() - 16, 20)
        name = QFontMetricsF(painter.font()).elidedText(
            self.card.name, Qt.TextElideMode.ElideRight, 76
        )
        draw_text(painter, header, name, Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(ui_font(8.5))
        draw_text(
            painter,
            header,
            f"HP {self.card.hp or 0}",
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )

        art_rect = QRectF(rect.left() + 7, rect.top() + 27, rect.width() - 14, 96)
        background = QRadialGradient(art_rect.center(), 70)
        background.setColorAt(0.0, QColor(255, 255, 255))
        background.setColorAt(1.0, type_color.lighter(170))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(art_rect, 9, 9)
        draw_art(painter, art_rect.adjusted(3, 3, -3, -3), self._art, energy_type)

        stage = "BÁSICO" if self.card.is_basic else f"EVOLUI DE {self.card.evolves_from}".upper()
        painter.setFont(ui_font(7.5))
        pill = QRectF(rect.left() + 8, rect.bottom() - 40, rect.width() - 16, 16)
        painter.setBrush(QColor(8, 14, 30, 170))
        painter.drawRoundedRect(pill, 8, 8)
        painter.setPen(QColor("white"))
        stage = QFontMetricsF(painter.font()).elidedText(
            stage, Qt.TextElideMode.ElideRight, pill.width() - 8
        )
        draw_text(painter, pill, stage)

        costs = sorted({c for attack in self.card.attacks for c in attack.cost}) or [energy_type]
        for orb_rect, cost in zip(
            orb_row_positions(len(costs), 0, rect.bottom() - 21, 15), costs, strict=True
        ):
            paint_energy_orb(painter, orb_rect, cost)

    def _paint_energy(self, painter: QPainter, rect: QRectF) -> None:
        energy_type = primary_type(self.card.types)
        type_color = energy_color(energy_type)
        background = QRadialGradient(rect.center() - QPointF(0, 18), 120)
        background.setColorAt(0.0, type_color.lighter(170))
        background.setColorAt(1.0, type_color.darker(125))
        painter.setPen(QPen(QColor(255, 255, 255, 160), 1.5))
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(rect, 12, 12)
        orb = QRectF(-38, -52, 76, 76)
        paint_energy_orb(painter, orb, energy_type)
        painter.setPen(QColor("white"))
        painter.setFont(ui_font(9))
        draw_text(painter, QRectF(rect.left(), 36, rect.width(), 18), "ENERGIA")
        painter.setFont(ui_font(11))
        draw_text(
            painter,
            QRectF(rect.left(), 54, rect.width(), 20),
            ENERGY_NAMES_PT.get(energy_type, energy_type).upper(),
        )

    def _paint_trainer(self, painter: QPainter, rect: QRectF) -> None:
        kind = trainer_kind(self.card)
        accent = QColor(TRAINER_COLORS.get(kind, "#5b6b82"))
        frame = QLinearGradient(rect.topLeft(), rect.bottomRight())
        frame.setColorAt(0.0, BONE)
        frame.setColorAt(1.0, BONE.darker(107))
        painter.setPen(QPen(with_alpha(INK, 60), 1.5))
        painter.setBrush(QBrush(frame))
        painter.drawRoundedRect(rect, 12, 12)

        header = QRectF(rect.left() + 6, rect.top() + 5, rect.width() - 12, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(header, 7, 7)
        painter.setPen(QColor("white"))
        painter.setFont(ui_font(9))
        name = QFontMetricsF(painter.font()).elidedText(
            self.card.name, Qt.TextElideMode.ElideRight, header.width() - 10
        )
        draw_text(painter, header, name)

        art_rect = QRectF(rect.left() + 7, rect.top() + 29, rect.width() - 14, 74)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 24, 34))
        painter.drawRoundedRect(art_rect, 8, 8)
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(art_rect, 8, 8)
        painter.setClipPath(clip)
        if self._art is not None:
            draw_art(painter, art_rect, self._art, "Colorless")
        else:
            painter.setPen(QColor(255, 255, 255, 150))
            painter.setFont(ui_font(22, QFont.Weight.Black))
            draw_text(painter, art_rect, "T")
        painter.restore()

        # o que a carta faz, em português, direto na carta
        painter.setPen(with_alpha(INK, 210))
        painter.setFont(ui_font(7.2, QFont.Weight.Medium))
        draw_text(
            painter,
            QRectF(rect.left() + 9, art_rect.bottom() + 4, rect.width() - 18, 44),
            describe_card(self.card),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            wrap=True,
        )

        pill = QRectF(rect.left() + 16, rect.bottom() - 21, rect.width() - 32, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(pill, 8, 8)
        painter.setPen(QColor("white"))
        painter.setFont(ui_font(7.5, QFont.Weight.DemiBold))
        draw_text(painter, pill, TRAINER_NAMES_PT.get(kind, "Treinador"))

    # -- input ----------------------------------------------------------
    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        if self.dragging:
            return
        self.hovered.emit(self)
        self.setZValue(500)
        self._play(
            par(
                prop(self, b"pos", QPointF(self.fan_pos.x(), HAND_RAISE_Y), 160),
                prop(self, b"rotation", 0.0, 160),
                prop(self, b"scale", HAND_RAISE_SCALE, 160),
            )
        )

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        self.unhovered.emit(self)
        if not self.dragging:
            self.return_to_fan()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None:
            return
        event.accept()
        self._press_scene = event.scenePos()
        self._rejected = not self.playable
        if self._rejected and self._interactive:
            base = self.rotation()
            self._play(
                seq(
                    prop(self, b"rotation", base - 6, 50),
                    prop(self, b"rotation", base + 6, 70),
                    prop(self, b"rotation", base, 60),
                )
            )

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None or self._rejected or self._press_scene is None:
            return
        if not self.dragging:
            delta = event.scenePos() - self._press_scene
            if abs(delta.x()) + abs(delta.y()) < 8:
                return
            self.dragging = True
            if self._motion is not None:
                self._motion.stop()
            self.setZValue(1000)
            self.setRotation(0)
            self.setScale(1.1)
            self._grab_offset = QPointF(0, 0)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.drag_started.emit(self)
        self.setPos(event.scenePos() - self._grab_offset)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None:
            return
        was_dragging = self.dragging
        self.dragging = False
        self._press_scene = None
        if was_dragging:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.dropped.emit(self, event.scenePos())
        elif not self._rejected:
            self.clicked.emit(self)


# --------------------------------------------------------------------------
# pilhas, prêmios e mão do oponente


class CardPile(QGraphicsObject):
    """Deck: pilha de versos com contador."""

    RECT = QRectF(-40, -56, 80, 112)

    def __init__(self, label: str) -> None:
        super().__init__()
        self._count = 0
        self._label = label

    def set_count(self, count: int) -> None:
        self._count = count
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def boundingRect(self) -> QRectF:
        return self.RECT.adjusted(-14, -14, 20, 34)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        layers = min(self._count, 5)
        if layers == 0:
            _paint_empty_slot(painter, self.RECT)
        else:
            paint_soft_shadow(painter, self.RECT, 8, offset=4)
            for i in range(layers):
                offset = (layers - 1 - i) * 2.0
                paint_card_back(painter, self.RECT.translated(offset, offset))
        _paint_count_badge(
            painter, QPointF(self.RECT.right() - 4, self.RECT.bottom() - 4), self._count
        )
        painter.setPen(QColor(255, 255, 255, 150))
        painter.setFont(ui_font(8))
        draw_text(
            painter,
            QRectF(self.RECT.left(), self.RECT.bottom() + 8, self.RECT.width(), 16),
            self._label,
        )


class DiscardPile(QGraphicsObject):
    RECT = QRectF(-40, -56, 80, 112)

    def __init__(self) -> None:
        super().__init__()
        self._count = 0
        self._top: Card | None = None
        self._art: QPixmap | None = None

    def set_top(self, card: Card | None, art: QPixmap | None, count: int) -> None:
        self._top = card
        self._art = scaled_art(art, 70, 70)
        self._count = count
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def boundingRect(self) -> QRectF:
        return self.RECT.adjusted(-14, -14, 20, 34)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._top is None:
            _paint_empty_slot(painter, self.RECT)
        else:
            energy_type = primary_type(self._top.types)
            color = energy_color(energy_type)
            painter.setOpacity(0.85)
            gradient = QLinearGradient(self.RECT.topLeft(), self.RECT.bottomRight())
            gradient.setColorAt(0, color.lighter(130))
            gradient.setColorAt(1, color.darker(160))
            painter.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(self.RECT, 8, 8)
            inner = self.RECT.adjusted(6, 10, -6, -10)
            if self._top.supertype == Supertype.ENERGY:
                orb = QRectF(0, 0, 44, 44)
                orb.moveCenter(inner.center())
                paint_energy_orb(painter, orb, energy_type)
            else:
                draw_art(painter, inner, self._art, energy_type)
            painter.setOpacity(1.0)
        _paint_count_badge(
            painter, QPointF(self.RECT.right() - 4, self.RECT.bottom() - 4), self._count
        )
        painter.setPen(QColor(255, 255, 255, 150))
        painter.setFont(ui_font(8))
        draw_text(
            painter,
            QRectF(self.RECT.left() - 10, self.RECT.bottom() + 8, self.RECT.width() + 20, 16),
            "Descarte",
        )


class PrizeGrid(QGraphicsObject):
    """6 prêmios virados em grade 2x3, como no tapete oficial."""

    CARD_W, CARD_H, GAP = 50.0, 70.0, 8.0

    def __init__(self, label: str) -> None:
        super().__init__()
        self._count = 6
        self._label = label

    def set_count(self, count: int) -> None:
        self._count = count
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def slot_rect(self, index: int) -> QRectF:
        column, row = index % 2, index // 2
        width = 2 * self.CARD_W + self.GAP
        height = 3 * self.CARD_H + 2 * self.GAP
        return QRectF(
            -width / 2 + column * (self.CARD_W + self.GAP),
            -height / 2 + row * (self.CARD_H + self.GAP),
            self.CARD_W,
            self.CARD_H,
        )

    def boundingRect(self) -> QRectF:
        return QRectF(-112, -150, 224, 300)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(6):
            rect = self.slot_rect(i)
            if i < self._count:
                paint_soft_shadow(painter, rect, 6, offset=3)
                paint_card_back(painter, rect)
            else:
                _paint_empty_slot(painter, rect, radius=6)
        painter.setPen(QColor(255, 255, 255, 170))
        painter.setFont(ui_font(8.5))
        painter.setFont(ui_font(9.5, QFont.Weight.DemiBold))
        draw_text(painter, QRectF(-110, 122, 220, 16), self._label)
        painter.setFont(ui_font(8.5))
        painter.setPen(QColor(255, 255, 255, 130))
        prizes = "1 prêmio" if self._count == 1 else f"{self._count} prêmios"
        draw_text(painter, QRectF(-110, 138, 220, 14), prizes)


class OpponentHandFan(QGraphicsObject):
    """Mão do oponente: só versos em leque no topo da tela (quantidade)."""

    CARD = QRectF(-30, -42, 60, 84)

    def __init__(self) -> None:
        super().__init__()
        self._count = 0

    def set_count(self, count: int) -> None:
        self._count = count
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def boundingRect(self) -> QRectF:
        return QRectF(-320, -80, 640, 160)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = self._count
        spacing = min(34.0, 400 / max(count, 1))
        for i in range(count):
            t = i - (count - 1) / 2
            painter.save()
            painter.translate(t * spacing, -abs(t) * abs(t) * 1.2)
            painter.rotate(-t * 4)
            paint_card_back(painter, self.CARD)
            painter.restore()


def _paint_empty_slot(painter: QPainter, rect: QRectF, radius: float = 8) -> None:
    painter.save()
    pen = QPen(QColor(255, 255, 255, 70), 1.5, Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(QColor(255, 255, 255, 12))
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()


def _paint_count_badge(painter: QPainter, center: QPointF, count: int) -> None:
    painter.save()
    badge = QRectF(0, 0, 26, 26)
    badge.moveCenter(center)
    painter.setPen(QPen(QColor("white"), 1.5))
    painter.setBrush(QColor(8, 14, 30, 220))
    painter.drawEllipse(badge)
    painter.setPen(QColor("white"))
    painter.setFont(ui_font(9))
    draw_text(painter, badge, str(count))
    painter.restore()


# --------------------------------------------------------------------------
# botões de jogo


class GameButton(QGraphicsObject):
    clicked = pyqtSignal()

    def __init__(self, width: float, height: float) -> None:
        super().__init__()
        self._w = width
        self._h = height
        self._enabled = True
        self._hovered = False
        self._glow = 0.0
        self._pulse: QPropertyAnimation | None = None
        self._press: QAbstractAnimation | None = None
        self.setAcceptHoverEvents(True)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_highlight(self, highlight: bool) -> None:
        if self._pulse is not None:
            self._pulse.stop()
            self._pulse = None
        if highlight:
            self._pulse = _pulse_animation(self, b"glow")
            if self._pulse is None:
                self.glow = 1.0
            else:
                self._pulse.start()
        else:
            self.glow = 0.0

    def rect(self) -> QRectF:
        return QRectF(-self._w / 2, -self._h / 2, self._w, self._h)

    def shape_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.rect(), self._h / 2, self._h / 2)
        return path

    def base_colors(self) -> tuple[QColor, QColor]:
        return QColor("#3a8dff"), QColor("#1c4fc4")

    def boundingRect(self) -> QRectF:
        return self.rect().adjusted(-18, -18, 18, 22)

    def shape(self) -> QPainterPath:
        return self.shape_path()

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = self.shape_path()
        if self._glow > 0:
            painter.save()
            for index, grow in enumerate((11, 6, 2)):
                color = QColor(GOLD)
                color.setAlphaF(min(1.0, self._glow * (0.16 + 0.2 * index)))
                painter.setPen(QPen(color, 4 - index))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(_grow_path(path, grow))
            painter.restore()

        top, bottom = self.base_colors()
        if not self._enabled:
            top, bottom = QColor("#2c4a48"), QColor("#1d3634")
        elif self._hovered:
            top, bottom = top.lighter(108), bottom.lighter(108)

        # relevo: a "borda" da placa aparece embaixo, como uma tecla de verdade
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bottom.darker(150))
        painter.drawPath(path.translated(0, 5))
        painter.setBrush(QBrush(top))
        painter.drawPath(path)
        painter.setPen(QPen(bottom.darker(125), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setOpacity(1.0 if self._enabled else 0.6)
        self.paint_content(painter)
        painter.setOpacity(1.0)

    def paint_content(self, painter: QPainter) -> None:
        pass

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        self._hovered = True
        self.update()

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent | None) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None or not self._enabled:
            return
        event.accept()
        self._press = prop(self, b"scale", 0.93, 70)
        self._press.start()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None or not self._enabled:
            return
        self._press = prop(self, b"scale", 1.0, 160)
        self._press.start()
        if self.shape_path().contains(event.pos()):
            self.clicked.emit()


def _grow_path(path: QPainterPath, amount: float) -> QPainterPath:
    bounds = path.boundingRect()
    if bounds.width() == 0 or bounds.height() == 0:
        return path
    sx = (bounds.width() + 2 * amount) / bounds.width()
    sy = (bounds.height() + 2 * amount) / bounds.height()
    transform = QTransform()
    transform.translate(bounds.center().x(), bounds.center().y())
    transform.scale(sx, sy)
    transform.translate(-bounds.center().x(), -bounds.center().y())
    return transform.map(path)


class AttackButton(GameButton):
    """Uma linha de ataque como a impressa na carta: custo em orbes à
    esquerda, nome no meio, dano em números grandes à direita. Pronta para
    usar = cartolina clara; sem energia = placa apagada."""

    def __init__(self, index: int) -> None:
        super().__init__(322, 58)
        self.index = index
        self._attack: Attack | None = None
        self._ready = False
        self._type = "Colorless"

    def set_attack(self, attack: Attack, energy_type: str, ready: bool) -> None:
        self._attack = attack
        self._type = energy_type
        self._ready = ready
        self.update()

    @property
    def ready(self) -> bool:
        return self._ready

    def shape_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 10, 10)
        return path

    def base_colors(self) -> tuple[QColor, QColor]:
        if self._ready:
            return BONE, BONE.darker(112)
        return QColor("#19403c"), QColor("#123330")

    def paint_content(self, painter: QPainter) -> None:
        if self._attack is None:
            return
        rect = self.rect()
        accent = energy_color(self._type)
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 10, 10)
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent if self._ready else accent.darker(190))
        painter.drawRect(QRectF(rect.left(), rect.top(), 7, rect.height()))
        painter.restore()

        costs = self._attack.cost or ["Colorless"]
        orbs = orb_row_positions(len(costs), rect.left() + 32 + len(costs) * 11, -11, 23, gap=3)
        for orb, cost in zip(orbs, costs, strict=True):
            paint_energy_orb(painter, orb, cost)

        text_color = INK if self._ready else QColor(240, 245, 243, 150)
        name_left = orbs[-1].right() + 12
        damage = self._attack.damage or ""
        painter.setFont(ui_font(12.5, QFont.Weight.DemiBold))
        painter.setPen(text_color)
        limit = rect.right() - (78 if damage else 16) - name_left
        name = QFontMetricsF(painter.font()).elidedText(
            self._attack.name, Qt.TextElideMode.ElideRight, limit
        )
        draw_text(
            painter,
            QRectF(name_left, rect.top(), limit, rect.height()),
            name,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        )
        if damage:
            painter.setFont(display_font(18))
            painter.setPen(text_color)
            draw_text(
                painter,
                QRectF(rect.right() - 74, rect.top(), 62, rect.height()),
                damage,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            )


class RetreatButton(GameButton):
    def __init__(self) -> None:
        super().__init__(190, 40)
        self._cost: list[str] = []
        self._active_mode = False

    def set_cost(self, cost: list[str]) -> None:
        self._cost = cost
        self.update()

    def base_colors(self) -> tuple[QColor, QColor]:
        return QColor("#1b433f"), QColor("#123330")

    def paint_content(self, painter: QPainter) -> None:
        r = self.rect()
        painter.setPen(QColor(240, 245, 243, 225))
        painter.setFont(ui_font(11, QFont.Weight.DemiBold))
        draw_text(
            painter,
            QRectF(r.left() + 18, r.top(), 90, r.height()),
            "Recuar",
            Qt.AlignmentFlag.AlignVCenter,
        )
        cost = self._cost or []
        for orb, energy in zip(
            orb_row_positions(len(cost), r.right() - 44, -9, 18, 2), cost, strict=True
        ):
            paint_energy_orb(painter, orb, energy)
        if not cost:
            painter.setFont(ui_font(9.5))
            painter.setPen(QColor(240, 245, 243, 160))
            draw_text(painter, QRectF(r.right() - 82, r.top(), 66, r.height()), "sem custo")


class EndTurnButton(GameButton):
    """Botão "Fim do turno" integrado ao tabuleiro (como no Hearthstone):
    azul quando ainda há jogadas, dourado pulsando quando não há mais nada a
    fazer, cinza no turno da IA."""

    def __init__(self) -> None:
        super().__init__(176, 62)
        self._mode = "play"

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.set_enabled(mode in ("play", "done", "setup"))
        self.set_highlight(mode == "done")
        self.update()

    @property
    def mode(self) -> str:
        return self._mode

    def base_colors(self) -> tuple[QColor, QColor]:
        if self._mode in ("done", "setup"):
            return VOLT, VOLT.darker(135)
        if self._mode in ("ai", "watch", "choose"):
            return QColor("#1b433f"), QColor("#123330")
        return BONE, BONE.darker(115)

    def paint_content(self, painter: QPainter) -> None:
        text = {
            "play": "Passar o turno",
            "done": "Passar o turno",
            "ai": "Vez da IA",
            "watch": "Assistindo",
            "setup": "Pronto",
            "choose": "Escolha no tabuleiro",
        }.get(self._mode, "Fim de jogo")
        dark = self._mode in ("play", "done", "setup")
        painter.setPen(INK if dark else QColor(240, 245, 243, 210))
        painter.setFont(display_font(11.5 if len(text) < 18 else 9.5))
        draw_text(painter, self.rect(), text)


# --------------------------------------------------------------------------
# efeitos


class EnergyOrbItem(QGraphicsObject):
    def __init__(self, energy_type: str, size: float = 30) -> None:
        super().__init__()
        self._type = energy_type
        self._size = size

    def boundingRect(self) -> QRectF:
        s = self._size
        return QRectF(-s, -s, 2 * s, 2 * s)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        s = self._size
        halo = QRadialGradient(QPointF(0, 0), s)
        color = energy_color(self._type)
        glow = QColor(color)
        glow.setAlpha(150)
        halo.setColorAt(0.0, glow)
        halo.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QRectF(-s, -s, 2 * s, 2 * s))
        paint_energy_orb(painter, QRectF(-s / 2, -s / 2, s, s), self._type)


class Particle(QGraphicsObject):
    def __init__(self, color: QColor, radius: float) -> None:
        super().__init__()
        self._color = QColor(color)
        self._radius = radius

    def boundingRect(self) -> QRectF:
        r = self._radius
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        r = self._radius
        gradient = QRadialGradient(QPointF(0, 0), r)
        gradient.setColorAt(0.0, QColor(255, 255, 255))
        gradient.setColorAt(0.35, self._color)
        edge = QColor(self._color)
        edge.setAlpha(0)
        gradient.setColorAt(1.0, edge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))


class Ring(QGraphicsObject):
    def __init__(self, color: QColor, radius: float = 40, width: float = 6) -> None:
        super().__init__()
        self._color = QColor(color)
        self._radius = radius
        self._width = width

    def boundingRect(self) -> QRectF:
        r = self._radius + self._width
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._color, self._width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = self._radius
        painter.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))


class FloatingText(QGraphicsObject):
    def __init__(self, text: str, color: QColor, size: float = 26) -> None:
        super().__init__()
        self._text = text
        self._color = QColor(color)
        self._font = ui_font(size, QFont.Weight.Black)
        width = QFontMetricsF(self._font).horizontalAdvance(text)
        self._rect = QRectF(-width / 2 - 10, -size, width + 20, size * 2)

    @property
    def text(self) -> str:
        return self._text

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        draw_outlined_text(
            painter, QPointF(0, 0), self._text, self._font, self._color, outline_width=6
        )


class Banner(QGraphicsObject):
    """Faixa "SEU TURNO" / "TURNO DA IA" que cruza o centro do tabuleiro."""

    def __init__(self, title: str, color: QColor) -> None:
        super().__init__()
        self._title = title
        self._color = QColor(color)

    def boundingRect(self) -> QRectF:
        return QRectF(-420, -54, 840, 108)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        band = QRectF(-420, -42, 840, 84)
        gradient = QLinearGradient(band.topLeft(), band.topRight())
        transparent = QColor(self._color)
        transparent.setAlpha(0)
        solid = QColor(self._color)
        solid.setAlpha(225)
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.25, solid)
        gradient.setColorAt(0.75, solid)
        gradient.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRect(band)
        line = QColor(255, 255, 255, 170)
        painter.setPen(QPen(line, 2))
        painter.drawLine(QPointF(-300, -42), QPointF(300, -42))
        painter.drawLine(QPointF(-300, 42), QPointF(300, 42))
        draw_outlined_text(
            painter,
            QPointF(0, 0),
            self._title,
            display_font(26),
            BONE,
            QColor(0, 0, 0, 150),
            6,
        )


class Toast(QGraphicsObject):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self._font = ui_font(10, QFont.Weight.DemiBold)
        width = min(QFontMetricsF(self._font).horizontalAdvance(text) + 32, 390)
        self._rect = QRectF(-width / 2, -17, width, 34)

    @property
    def text(self) -> str:
        return self._text

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-4, -4, 4, 6)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.setBrush(QColor(6, 10, 24, 215))
        painter.drawRoundedRect(self._rect, 17, 17)
        painter.setPen(QColor("white"))
        painter.setFont(self._font)
        text = QFontMetricsF(self._font).elidedText(
            self._text, Qt.TextElideMode.ElideRight, self._rect.width() - 28
        )
        draw_text(painter, self._rect, text)


class InspectPanel(QGraphicsObject):
    """Painel de detalhes ao passar o mouse num Pokémon (ataques completos,
    fraqueza, recuo) — a informação fica disponível sem poluir o tabuleiro."""

    RECT = QRectF(-135, -210, 270, 420)

    def __init__(self) -> None:
        super().__init__()
        self._card: Card | None = None
        self._art: QPixmap | None = None
        self._hp = 0

    def show_card(self, card: Card, art: QPixmap | None, hp: int) -> None:
        self._card = card
        self._art = scaled_art(art, 140, 120)
        self._hp = hp
        self.update()

    def boundingRect(self) -> QRectF:
        return self.RECT.adjusted(-12, -12, 12, 16)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None or self._card is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = self._card
        energy_type = primary_type(card.types)
        color = energy_color(energy_type)
        rect = self.RECT
        paint_soft_shadow(painter, rect, 18, offset=8)
        painter.setPen(QPen(color.lighter(130), 2))
        painter.setBrush(QColor(10, 38, 36, 242))
        painter.drawRoundedRect(rect, 18, 18)

        header = QRectF(rect.left(), rect.top(), rect.width(), 150)
        gradient = QLinearGradient(header.topLeft(), header.bottomLeft())
        gradient.setColorAt(0.0, color.lighter(130))
        gradient.setColorAt(1.0, QColor(10, 38, 36, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        clip = QPainterPath()
        clip.addRoundedRect(rect, 18, 18)
        painter.save()
        painter.setClipPath(clip)
        painter.drawRect(header)
        painter.restore()
        draw_art(
            painter, QRectF(rect.left() + 60, rect.top() + 12, 150, 124), self._art, energy_type
        )

        y = rect.top() + 142
        painter.setPen(QColor("white"))
        name_width = 170.0 if card.is_pokemon else rect.width() - 32
        painter.setFont(ui_font(15 if len(card.name) <= 16 else 12, QFont.Weight.Black))
        name = QFontMetricsF(painter.font()).elidedText(
            card.name, Qt.TextElideMode.ElideRight, name_width
        )
        draw_text(
            painter,
            QRectF(rect.left() + 16, y, name_width, 26),
            name,
            Qt.AlignmentFlag.AlignVCenter,
        )
        if card.is_pokemon:
            painter.setFont(ui_font(11))
            draw_text(
                painter,
                QRectF(rect.left() + 16, y, rect.width() - 32, 26),
                f"HP {self._hp}/{card.hp or 0}",
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            )
        y += 28
        painter.setFont(ui_font(8.5, QFont.Weight.Medium))
        painter.setPen(QColor(255, 255, 255, 170))
        if not card.is_pokemon:
            self._paint_trainer_text(painter, rect, y)
            return
        stage = "Básico" if card.is_basic else f"Evolui de {card.evolves_from}"
        draw_text(
            painter, QRectF(rect.left() + 16, y, 238, 16), stage, Qt.AlignmentFlag.AlignVCenter
        )
        y += 22

        for ability in card.abilities:
            painter.setPen(VOLT)
            painter.setFont(ui_font(10.5, QFont.Weight.DemiBold))
            draw_text(
                painter,
                QRectF(rect.left() + 16, y, rect.width() - 32, 18),
                f"Habilidade: {ability.name}",
                Qt.AlignmentFlag.AlignVCenter,
            )
            y += 18
            painter.setPen(QColor(255, 255, 255, 195))
            painter.setFont(ui_font(8, QFont.Weight.Normal))
            text = describe_ability(ability.name, ability.text)
            draw_text(
                painter,
                QRectF(rect.left() + 16, y, rect.width() - 32, 34),
                text,
                Qt.AlignmentFlag.AlignLeft,
                wrap=True,
            )
            y += 38

        for attack in card.attacks:
            costs = attack.cost or ["Colorless"]
            for orb, cost in zip(
                orb_row_positions(len(costs), rect.left() + 16 + len(costs) * 9, y, 17, 1),
                costs,
                strict=True,
            ):
                paint_energy_orb(painter, orb, cost)
            left = rect.left() + 26 + len(costs) * 18
            painter.setPen(QColor("white"))
            painter.setFont(ui_font(11))
            draw_text(
                painter, QRectF(left, y - 2, 170, 22), attack.name, Qt.AlignmentFlag.AlignVCenter
            )
            painter.setFont(ui_font(14, QFont.Weight.Black))
            draw_text(
                painter,
                QRectF(rect.left() + 16, y - 3, rect.width() - 32, 24),
                attack.damage or "",
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            )
            y += 24
            if attack.text:
                painter.setFont(ui_font(8, QFont.Weight.Normal))
                painter.setPen(QColor(255, 255, 255, 180))
                text_rect = QRectF(rect.left() + 16, y, rect.width() - 32, 34)
                draw_text(painter, text_rect, attack.text, Qt.AlignmentFlag.AlignLeft, wrap=True)
                y += 36
            y += 6

        footer_y = rect.bottom() - 40
        painter.setPen(QColor(255, 255, 255, 40))
        painter.drawLine(
            QPointF(rect.left() + 14, footer_y - 6), QPointF(rect.right() - 14, footer_y - 6)
        )
        painter.setFont(ui_font(8))
        columns = (
            ("FRAQUEZA", [w.energy_type for w in card.weaknesses]),
            ("RESISTÊNCIA", [r.energy_type for r in card.resistances]),
            ("RECUO", card.retreat_cost),
        )
        for i, (label, energies) in enumerate(columns):
            left = rect.left() + 14 + i * 82
            painter.setPen(QColor(255, 255, 255, 150))
            draw_text(painter, QRectF(left, footer_y, 80, 14), label)
            if not energies:
                painter.setPen(QColor(255, 255, 255, 120))
                draw_text(painter, QRectF(left, footer_y + 14, 80, 18), "—")
            for orb, energy in zip(
                orb_row_positions(len(energies), left + 40, footer_y + 15, 16, 2),
                energies,
                strict=True,
            ):
                paint_energy_orb(painter, orb, energy)

    def _paint_trainer_text(self, painter: QPainter, rect: QRectF, y: float) -> None:
        assert self._card is not None
        card = self._card
        kind = trainer_kind(card)
        draw_text(
            painter,
            QRectF(rect.left() + 16, y, 238, 16),
            TRAINER_NAMES_PT.get(kind, "Treinador"),
            Qt.AlignmentFlag.AlignVCenter,
        )
        painter.setFont(ui_font(11, QFont.Weight.DemiBold))
        painter.setPen(QColor("white"))
        summary = describe_card(card)
        summary_rect = QRectF(rect.left() + 16, y + 22, rect.width() - 32, 84)
        draw_text(painter, summary_rect, summary, Qt.AlignmentFlag.AlignLeft, wrap=True)

        original = " ".join(card.rules).strip()
        if original and original != summary:
            painter.setFont(ui_font(8, QFont.Weight.Normal))
            painter.setPen(QColor(255, 255, 255, 130))
            draw_text(
                painter,
                QRectF(rect.left() + 16, y + 112, rect.width() - 32, 120),
                original,
                Qt.AlignmentFlag.AlignLeft,
                wrap=True,
            )
        if not is_implemented(card):
            note = QRectF(rect.left() + 14, rect.bottom() - 46, rect.width() - 28, 34)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(GOLD, 40))
            painter.drawRoundedRect(note, 8, 8)
            painter.setPen(GOLD)
            painter.setFont(ui_font(8))
            draw_text(painter, note, "O motor ainda não aplica este efeito", wrap=True)


class ZoneHighlight(QGraphicsObject):
    """Área de soltura pulsante (ex: "solte no banco")."""

    def __init__(self, rect: QRectF, label: str) -> None:
        super().__init__()
        self._rect = rect
        self._label = label
        self._glow = 0.6
        self._pulse = _pulse_animation(self, b"glow")
        if self._pulse is not None:
            self._pulse.start()

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

    @property
    def zone_rect(self) -> QRectF:
        return self._rect

    def stop(self) -> None:
        if self._pulse is not None:
            self._pulse.stop()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-16, -16, 16, 16)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_glow(painter, self._rect, 16, GOLD, self._glow)
        fill = QColor(GOLD)
        fill.setAlphaF(0.08 + 0.1 * self._glow)
        painter.setPen(QPen(GOLD, 2, Qt.PenStyle.DashLine))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect, 16, 16)
        draw_outlined_text(
            painter,
            self._rect.center(),
            self._label,
            ui_font(13, QFont.Weight.Black),
            QColor("white"),
            QColor(0, 0, 0, 170),
            4,
        )


class GameOverOverlay(QGraphicsObject):
    def __init__(self, width: float, height: float, title: str, color: QColor) -> None:
        super().__init__()
        self._w = width
        self._h = height
        self.title = title
        self._color = QColor(color)
        self.button = GameButton(240, 60)
        self.button.setParentItem(self)
        self.button.setPos(width / 2, height / 2 + 90)
        self.button.paint_content = self._paint_button_label  # type: ignore[method-assign]

    def _paint_button_label(self, painter: QPainter) -> None:
        painter.setPen(INK)
        painter.setFont(display_font(12.5))
        draw_text(painter, self.button.rect(), "Jogar de novo")

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.fillRect(self.boundingRect(), QColor(4, 8, 20, 185))
        draw_outlined_text(
            painter,
            QPointF(self._w / 2, self._h / 2 - 20),
            self.title,
            ui_font(64 if len(self.title) <= 12 else 46, QFont.Weight.Black),
            self._color,
            QColor(0, 0, 0, 200),
            10,
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is not None:
            event.accept()  # bloqueia cliques no tabuleiro por trás


# --------------------------------------------------------------------------
# escolhas e Estádio


class ChoicePanel(QGraphicsObject):
    """Painel modal com uma pergunta e botões de opção (alvo de um efeito,
    Habilidade a usar, modo de um ataque...). Emite `chosen(índice)`, ou
    `chosen(-1)` ao cancelar."""

    chosen = pyqtSignal(int)

    BUTTON_W = 330.0
    BUTTON_H = 44.0
    GAP = 10.0

    def __init__(self, width: float, height: float, title: str, options: list[str]) -> None:
        super().__init__()
        self._w = width
        self._h = height
        self.title = title
        self.options = list(options)
        columns = 1 if len(options) <= 6 else 2
        rows = -(-len(options) // columns)
        panel_w = columns * self.BUTTON_W + (columns + 1) * 24
        panel_h = 96 + rows * (self.BUTTON_H + self.GAP) + self.BUTTON_H + 30
        self._panel = QRectF(width / 2 - panel_w / 2, height / 2 - panel_h / 2, panel_w, panel_h)
        self.buttons: list[GameButton] = []
        for index, label in enumerate(options):
            column, row = divmod(index, rows)
            button = self._button(label, BONE, BONE.darker(112))
            x = self._panel.left() + 24 + self.BUTTON_W / 2 + column * (self.BUTTON_W + 24)
            y = self._panel.top() + 86 + self.BUTTON_H / 2 + row * (self.BUTTON_H + self.GAP)
            button.setPos(x, y)
            button.clicked.connect(lambda i=index: self.chosen.emit(i))
            self.buttons.append(button)
        self.cancel_button = self._button("Cancelar", QColor("#1b433f"), QColor("#123330"))
        self.cancel_button.setPos(width / 2, self._panel.bottom() - 16 - self.BUTTON_H / 2)
        self.cancel_button.clicked.connect(lambda: self.chosen.emit(-1))

    def _button(self, label: str, top: QColor, bottom: QColor) -> GameButton:
        button = GameButton(self.BUTTON_W, self.BUTTON_H)
        button.setParentItem(self)

        def paint_label(painter: QPainter) -> None:
            painter.setPen(INK if top == BONE else QColor(240, 245, 243, 220))
            painter.setFont(ui_font(11.5, QFont.Weight.DemiBold))
            text = QFontMetricsF(painter.font()).elidedText(
                label, Qt.TextElideMode.ElideRight, self.BUTTON_W - 24
            )
            draw_text(painter, button.rect(), text)

        button.paint_content = paint_label  # type: ignore[method-assign]
        button.base_colors = lambda: (top, bottom)  # type: ignore[method-assign]
        return button

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.boundingRect(), QColor(4, 8, 20, 150))
        paint_soft_shadow(painter, self._panel, 20)
        gradient = QLinearGradient(self._panel.topLeft(), self._panel.bottomLeft())
        gradient.setColorAt(0.0, QColor("#124540"))
        gradient.setColorAt(1.0, QColor("#0a2b2b"))
        painter.setPen(QPen(QColor(GOLD), 2))
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(self._panel, 20, 20)
        painter.setPen(BONE)
        painter.setFont(display_font(14))
        draw_text(
            painter,
            QRectF(self._panel.left() + 16, self._panel.top() + 18, self._panel.width() - 32, 44),
            self.title,
            wrap=True,
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is not None:
            event.accept()  # modal: bloqueia o tabuleiro por trás


class StadiumCard(QGraphicsObject):
    """Estádio em jogo, no meio do tabuleiro. Brilha quando o seu efeito de
    "uma vez por turno" pode ser usado; clicar usa o efeito."""

    clicked = pyqtSignal()
    RECT = QRectF(-70, -48, 140, 96)

    def __init__(self) -> None:
        super().__init__()
        self._card: Card | None = None
        self._owner_label = ""
        self._glow = 0.0
        self._pulse: QPropertyAnimation | None = None
        self._usable = False
        self.setAcceptHoverEvents(True)

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = pyqtProperty(float, fget=_get_glow, fset=_set_glow)

    @property
    def card(self) -> Card | None:
        return self._card

    @property
    def usable(self) -> bool:
        return self._usable

    def set_stadium(self, card: Card | None, owner_label: str) -> None:
        self._card = card
        self._owner_label = owner_label
        self.setVisible(card is not None)
        self.update()

    def set_usable(self, usable: bool) -> None:
        if usable == self._usable:
            return
        self._usable = usable
        if self._pulse is not None:
            self._pulse.stop()
            self._pulse = None
        if usable:
            self._pulse = _pulse_animation(self, b"glow")
            if self._pulse is None:
                self.glow = 1.0
            else:
                self._pulse.start()
        else:
            self.glow = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if usable else Qt.CursorShape.ArrowCursor)

    def boundingRect(self) -> QRectF:
        return self.RECT.adjusted(-16, -16, 16, 16)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionGraphicsItem | None,
        widget: QWidget | None = None,
    ) -> None:
        if painter is None or self._card is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.RECT
        paint_glow(painter, rect, 12, GOLD, self._glow)
        paint_soft_shadow(painter, rect, 12, 4)
        accent = QColor(TRAINER_COLORS["Stadium"])
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, accent.lighter(130))
        gradient.setColorAt(1.0, accent.darker(150))
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(ui_font(8, QFont.Weight.Bold))
        draw_text(painter, QRectF(rect.left(), rect.top() + 6, rect.width(), 14), "Estádio")
        painter.setPen(QColor("white"))
        painter.setFont(ui_font(11, QFont.Weight.Black))
        draw_text(painter, rect.adjusted(8, 18, -8, -20), self._card.name, wrap=True)
        painter.setFont(ui_font(8))
        painter.setPen(QColor(255, 255, 255, 180))
        hint = "CLIQUE PARA USAR" if self._usable else self._owner_label
        draw_text(painter, QRectF(rect.left(), rect.bottom() - 20, rect.width(), 14), hint)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is not None:
            event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if self._usable:
            self.clicked.emit()
