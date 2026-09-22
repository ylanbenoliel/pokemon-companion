"""Tema visual da UI: paleta por tipo de energia e o QSS da aplicação,
inspirado no estilo colorido/arredondado do Pokémon TCG Pocket (fundo em
degradê, painéis de carta claros e arredondados, badges de status)."""

from __future__ import annotations

ENERGY_COLORS: dict[str, str] = {
    "Grass": "#4caf50",
    "Fire": "#ff7043",
    "Water": "#42a5f5",
    "Lightning": "#ffca28",
    "Psychic": "#ab47bc",
    "Fighting": "#8d6e63",
    "Darkness": "#546e7a",
    "Metal": "#90a4ae",
    "Fairy": "#f06292",
    "Dragon": "#7e57c2",
    "Colorless": "#bdbdbd",
}

STATUS_LABELS: dict[str, str] = {
    "ASLEEP": "DORMINDO",
    "CONFUSED": "CONFUSO",
    "PARALYZED": "PARALISADO",
    "POISONED": "ENVENENADO",
    "BURNED": "QUEIMADO",
}


def energy_color(energy_type: str) -> str:
    return ENERGY_COLORS.get(energy_type, ENERGY_COLORS["Colorless"])


def hp_bar_color(current: int, maximum: int) -> str:
    ratio = current / maximum if maximum else 0.0
    if ratio > 0.5:
        return "#66bb6a"
    if ratio > 0.2:
        return "#ffca28"
    return "#ef5350"


_ENERGY_BORDER_RULES = "\n".join(
    f'QFrame#pokemonCard[energyType="{energy_type}"] {{ border-color: {color}; }}'
    for energy_type, color in ENERGY_COLORS.items()
)

APP_STYLESHEET = f"""
QWidget#rootBackground {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b1055, stop:0.5 #4a148c, stop:1 #6a1b9a);
}}

QLabel#appTitle {{
    color: #ffffff;
    font-size: 19px;
    font-weight: 800;
    letter-spacing: 2px;
    padding: 6px 2px 2px 2px;
}}

QLabel#sectionTitle {{
    color: #ffffff;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}}

QLabel#prizesLabel {{
    color: #f5f5f5;
    font-size: 11px;
}}

QFrame#pokemonCard {{
    background-color: rgba(255, 255, 255, 0.96);
    border-radius: 12px;
    border: 3px solid #cfd8dc;
}}
QFrame#pokemonCard[empty="true"] {{
    background-color: rgba(255, 255, 255, 0.12);
    border: 2px dashed rgba(255, 255, 255, 0.35);
}}
{_ENERGY_BORDER_RULES}

/* A cor/peso do nome (#cardName) é definida diretamente em
   pokemon_card_widget.py, não aqui — um seletor QSS descendente
   `[empty="true"] QLabel#cardName` não se reavalia de forma confiável
   quando só o QFrame pai é repolido, deixando o texto preso na cor do
   estado anterior. */

QProgressBar#hpBar {{
    border: none;
    border-radius: 6px;
    background-color: #eceff1;
    text-align: center;
    font-size: 9px;
    font-weight: 700;
    color: #263238;
}}
QProgressBar#hpBar::chunk {{
    border-radius: 6px;
}}

QLabel#statusBadge {{
    background-color: #d81b60;
    color: white;
    border-radius: 8px;
    padding: 1px 4px;
    font-size: 9px;
    font-weight: 700;
}}

QListWidget#actionList {{
    background-color: rgba(255, 255, 255, 0.94);
    border-radius: 10px;
    padding: 4px;
    font-size: 13px;
    color: #263238;
}}
QListWidget#actionList::item {{
    border-radius: 8px;
    padding: 8px 10px;
    margin: 2px;
}}
QListWidget#actionList::item:selected {{
    background-color: #ffca28;
    color: #263238;
}}

QPushButton#playButton {{
    background-color: #29b6f6;
    color: white;
    border: none;
    border-radius: 10px;
    padding: 10px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton#playButton:disabled {{
    background-color: #64748b;
    color: rgba(255, 255, 255, 0.55);
}}
QPushButton#playButton:hover:!disabled {{
    background-color: #4fc3f7;
}}

QListWidget#logView {{
    background-color: rgba(0, 0, 0, 0.28);
    color: #eceff1;
    border-radius: 10px;
    font-size: 11px;
    padding: 4px;
}}
"""
