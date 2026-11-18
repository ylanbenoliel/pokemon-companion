"""Modelos de dados para cartas do Pokémon TCG.

Usados tanto por cartas "mockadas" (Fase 1, jogo 100% virtual) quanto por
cartas reais resolvidas via API pokemontcg.io (Fase 2) — o motor de regras
depende apenas destas dataclasses, nunca da origem dos dados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

ENERGY_LETTERS = {
    "Grass": "G",
    "Fire": "R",
    "Water": "W",
    "Lightning": "L",
    "Psychic": "P",
    "Fighting": "F",
    "Darkness": "D",
    "Metal": "M",
    "Fairy": "Y",
    "Dragon": "N",
    "Colorless": "C",
}
_ENERGY_SPAN = re.compile(r'<span class="energy-symbol (\w+)"[^>]*>.*?</span>')
_ENERGY_RUN = re.compile(rf"\b(?:{'|'.join(ENERGY_LETTERS)}){{2,}}\b")


def plain_text(text: str) -> str:
    """Texto de carta no formato do resto do pool: alguns sets da TCGdex vêm
    com HTML (`<span class="energy-symbol Lightning">` em vez de `{L}`,
    `<em>`) ou com símbolos colados ("ColorlessColorless")."""
    if "<" not in text and "\t" not in text and not _ENERGY_RUN.search(text):
        return text
    text = _ENERGY_SPAN.sub(lambda m: f"{{{ENERGY_LETTERS.get(m.group(1), 'C')}}}", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _ENERGY_RUN.sub(
        lambda m: "".join(
            f"{{{ENERGY_LETTERS[name]}}}" for name in re.findall(r"[A-Z][a-z]+", m.group(0))
        ),
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


class Supertype(StrEnum):
    POKEMON = "Pokémon"
    TRAINER = "Trainer"
    ENERGY = "Energy"


@dataclass(frozen=True)
class Attack:
    name: str
    cost: list[str] = field(default_factory=list)
    damage: str = ""
    text: str = ""

    @property
    def base_damage(self) -> int:
        """Extrai a parte numérica de `damage` (ex: "60+" -> 60, "" -> 0)."""
        digits = "".join(c for c in self.damage if c.isdigit())
        return int(digits) if digits else 0


@dataclass(frozen=True)
class Ability:
    name: str
    text: str = ""
    ability_type: str = "Ability"


@dataclass(frozen=True)
class WeaknessResistance:
    energy_type: str
    value: str  # ex: "×2", "-30"


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    supertype: Supertype
    subtypes: list[str] = field(default_factory=list)
    hp: int | None = None
    types: list[str] = field(default_factory=list)
    attacks: list[Attack] = field(default_factory=list)
    weaknesses: list[WeaknessResistance] = field(default_factory=list)
    resistances: list[WeaknessResistance] = field(default_factory=list)
    retreat_cost: list[str] = field(default_factory=list)
    evolves_from: str | None = None
    abilities: list[Ability] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    image_url: str | None = None
    image_local_path: str | None = None
    national_pokedex_numbers: list[int] = field(default_factory=list)

    def __deepcopy__(self, memo: dict[int, object]) -> Card:
        # Carta é imutável: as simulações da IA (deepcopy do GameState) podem
        # compartilhar a mesma instância em vez de copiar ~120 cartas por jogada.
        return self

    @property
    def is_basic(self) -> bool:
        return self.supertype == Supertype.POKEMON and self.evolves_from is None

    @property
    def is_pokemon(self) -> bool:
        return self.supertype == Supertype.POKEMON


def signature(card: Card) -> str:
    """Identidade da carta para o jogo: nome + ataques + Habilidades (Pokémon)."""
    if card.supertype != Supertype.POKEMON:
        return card.name
    return "|".join([card.name, *(a.name for a in card.attacks), *(a.name for a in card.abilities)])
