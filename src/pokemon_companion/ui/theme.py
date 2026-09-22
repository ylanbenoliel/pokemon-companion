"""Sistema visual do app: tipografia, paleta e cores por tipo de energia.

A direção é a mesa de torneio vista sob a luz quente do abajur: o tapete é
feltro verde-pinho, as cartas são cartolina creme e a cor forte vem de onde
ela existe no jogo de verdade — o **tipo de energia** do Pokémon ativo, que
tinge o lado do tabuleiro, e o brilho holográfico das cartas com regra
especial (ex/Mega). Fora isso, o tabuleiro fica calmo: o olho precisa achar
o HP, a energia e o ataque em um relance.

As fontes (Archivo Black para números e títulos, Barlow para o resto) são
empacotadas com o app — a interface tem a mesma cara nos três sistemas, sem
depender do que estiver instalado.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QFontDatabase

# --------------------------------------------------------------------------
# tipografia

FONTS_DIR = Path(__file__).parent / "fonts"
DISPLAY_FAMILY = "Archivo Black"
UI_FAMILY = "Barlow"
_FALLBACKS = ["Avenir Next", "Segoe UI", "Helvetica Neue", "Roboto", "Arial"]
_loaded = False


def load_fonts() -> None:
    """Registra as fontes empacotadas (idempotente; exige um QApplication)."""
    global _loaded
    if _loaded:
        return
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))
    _loaded = True


def ui_font(point_size: float, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """Texto da interface: nomes, rótulos, mensagens."""
    load_fonts()
    font = QFont()
    font.setFamilies([UI_FAMILY, *_FALLBACKS])
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    return font


def display_font(point_size: float, spacing: float = 0.0) -> QFont:
    """Números e títulos: HP, dano, nome do deck, banners."""
    load_fonts()
    font = QFont()
    font.setFamilies([DISPLAY_FAMILY, UI_FAMILY, *_FALLBACKS])
    font.setPointSizeF(point_size)
    font.setWeight(QFont.Weight.Black)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing)
    return font


# --------------------------------------------------------------------------
# paleta

#: feltro do tapete, do centro (sob a luz) para as bordas
FELT_LIT = QColor("#1b5c52")
FELT_MID = QColor("#124540")
FELT_DEEP = QColor("#0a2b2b")
#: luz quente do abajur, sobre o tapete
LAMP = QColor("#ffcf8a")
#: cartolina das cartas e o texto sobre ela
BONE = QColor("#f7efe1")
INK = QColor("#20242b")
#: ação principal (amarelo de energia Elétrica) e dano
VOLT = QColor("#ffe03d")
FLARE = QColor("#ff5a36")

GOLD = VOLT  # nome antigo do destaque principal
PLAYABLE_GLOW = QColor("#6bffa8")
DAMAGE_RED = FLARE
HEAL_GREEN = QColor("#49e08a")
MAT_TOP = FELT_MID
MAT_BOTTOM = FELT_DEEP
ZONE_FILL = QColor(255, 255, 255, 12)
ZONE_STROKE = QColor(233, 255, 246, 48)
TEXT_DARK = INK

ENERGY_COLORS: dict[str, str] = {
    "Grass": "#35c759",
    "Fire": "#ff5b2e",
    "Water": "#2f9bff",
    "Lightning": "#ffd426",
    "Psychic": "#c158e8",
    "Fighting": "#e0813f",
    "Darkness": "#5a6b82",
    "Metal": "#a8b6c4",
    "Fairy": "#ff6fb5",
    "Dragon": "#d5a637",
    "Colorless": "#e6e2d6",
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
    "ASLEEP": "Dormindo",
    "CONFUSED": "Confuso",
    "PARALYZED": "Paralisado",
    "POISONED": "Envenenado",
    "BURNED": "Queimado",
}

STATUS_COLORS: dict[str, str] = {
    "ASLEEP": "#6f7bdc",
    "CONFUSED": "#ff4f95",
    "PARALYZED": "#ffd426",
    "POISONED": "#a844d6",
    "BURNED": "#ff6b2c",
}


def energy_color(energy_type: str) -> QColor:
    return QColor(ENERGY_COLORS.get(energy_type, ENERGY_COLORS["Colorless"]))


def primary_type(types: list[str]) -> str:
    return types[0] if types else "Colorless"


def hp_color(current: int, maximum: int) -> QColor:
    ratio = current / maximum if maximum else 0.0
    if ratio > 0.5:
        return HEAL_GREEN
    if ratio > 0.25:
        return QColor("#ffc233")
    return FLARE


def mix(first: QColor, second: QColor, amount: float) -> QColor:
    """Interpolação simples entre duas cores (0 = primeira, 1 = segunda)."""
    amount = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
    )


def with_alpha(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(alpha)
    return tinted
