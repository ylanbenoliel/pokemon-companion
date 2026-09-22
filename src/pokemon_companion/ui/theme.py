"""Paleta visual do tabuleiro: cores por tipo de energia (tons vivos, no
espírito do Pokémon TCG Pocket), cores de destaque e a fonte da UI."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont

ENERGY_COLORS: dict[str, str] = {
    "Grass": "#3fae49",
    "Fire": "#f0512e",
    "Water": "#2f8fe0",
    "Lightning": "#f5c211",
    "Psychic": "#a45ec9",
    "Fighting": "#c0733c",
    "Darkness": "#3f4c5c",
    "Metal": "#8f9ba6",
    "Fairy": "#e56aa6",
    "Dragon": "#b08a2e",
    "Colorless": "#cfcfc4",
}

ENERGY_NAMES_PT: dict[str, str] = {
    "Grass": "Planta",
    "Fire": "Fogo",
    "Water": "Água",
    "Lightning": "Elétrica",
    "Psychic": "Psíquica",
    "Fighting": "Luta",
    "Darkness": "Escuridão",
    "Metal": "Metal",
    "Fairy": "Fada",
    "Dragon": "Dragão",
    "Colorless": "Incolor",
}

STATUS_LABELS: dict[str, str] = {
    "ASLEEP": "DORMINDO",
    "CONFUSED": "CONFUSO",
    "PARALYZED": "PARALISADO",
    "POISONED": "ENVENENADO",
    "BURNED": "QUEIMADO",
}

STATUS_COLORS: dict[str, str] = {
    "ASLEEP": "#5c6bc0",
    "CONFUSED": "#ec407a",
    "PARALYZED": "#e0b400",
    "POISONED": "#8e24aa",
    "BURNED": "#e64a19",
}

GOLD = QColor("#ffcb45")
PLAYABLE_GLOW = QColor("#5dfc8d")
DAMAGE_RED = QColor("#ff4d57")
HEAL_GREEN = QColor("#4cd964")
MAT_TOP = QColor("#15294d")
MAT_BOTTOM = QColor("#0b1730")
ZONE_FILL = QColor(255, 255, 255, 18)
ZONE_STROKE = QColor(255, 255, 255, 60)
TEXT_DARK = QColor("#1f2933")

_FONT_FAMILIES = ["Avenir Next", "Segoe UI", "Helvetica Neue", "Roboto", "Arial"]


def energy_color(energy_type: str) -> QColor:
    return QColor(ENERGY_COLORS.get(energy_type, ENERGY_COLORS["Colorless"]))


def primary_type(types: list[str]) -> str:
    return types[0] if types else "Colorless"


def hp_color(current: int, maximum: int) -> QColor:
    ratio = current / maximum if maximum else 0.0
    if ratio > 0.5:
        return QColor("#4cd964")
    if ratio > 0.25:
        return QColor("#ffc233")
    return QColor("#ff4d57")


def ui_font(point_size: float, weight: QFont.Weight = QFont.Weight.Bold) -> QFont:
    font = QFont()
    font.setFamilies(_FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font
