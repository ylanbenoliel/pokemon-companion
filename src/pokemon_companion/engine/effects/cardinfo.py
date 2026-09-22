"""Classificação de cartas usada pelos efeitos ("Regra de Prêmio", Tera,
Antigo/Futuro, Pokémon de treinador como "Team Rocket's", estágio...).

A API não expõe Tera/Antigo/Futuro, então esses grupos são listas por nome,
cobrindo as cartas dos decks do meta (ampliar ao adicionar decks novos).
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Card, Supertype

TERA_NAMES = {
    "Teal Mask Ogerpon ex",
    "Wellspring Mask Ogerpon ex",
    "Hearthflame Mask Ogerpon ex",
    "Cornerstone Mask Ogerpon ex",
    "Hydrapple ex",
    "Terapagos ex",
    "Lapras ex",
    "Pikachu ex",
    "Dragapult ex",
    "Noctowl",
}
ANCIENT_NAMES = {
    "Raging Bolt ex",
    "Roaring Moon",
    "Roaring Moon ex",
    "Great Tusk",
    "Brute Bonnet",
    "Walking Wake ex",
    "Scream Tail",
    "Flutter Mane",
    "Slither Wing",
    "Sandy Shocks",
    "Gouging Fire ex",
    "Koraidon",
    "Koraidon ex",
}
BASIC_TYPES = (
    "Grass",
    "Fire",
    "Water",
    "Lightning",
    "Psychic",
    "Fighting",
    "Darkness",
    "Metal",
    "Fairy",
)
RULE_BOX_SUBTYPES = {"ex", "EX", "V", "VMAX", "VSTAR", "GX"}
TRAINER_GROUPS = ("Team Rocket's", "N's", "Cynthia's", "Marnie's", "Lillie's", "Ethan's", "Hop's")


def has_rule_box(card: Card) -> bool:
    return bool(set(card.subtypes) & RULE_BOX_SUBTYPES) or card.name.startswith("Radiant ")


def is_ex(card: Card) -> bool:
    return "ex" in card.subtypes or "EX" in card.subtypes


def is_mega(card: Card) -> bool:
    return "Mega" in card.subtypes


def is_tera(card: Card) -> bool:
    return card.name in TERA_NAMES


def is_ancient(card: Card) -> bool:
    return card.name in ANCIENT_NAMES


def is_future(card: Card) -> bool:
    return card.name.startswith("Iron ") or card.name.startswith("Miraidon")


def in_group(card: Card, prefix: str) -> bool:
    return card.name.startswith(prefix)


def stage_of(card: Card) -> str:
    for stage in ("Stage 2", "Stage 1", "Basic"):
        if stage in card.subtypes:
            return stage
    return "Basic" if card.evolves_from is None else "Stage 1"


def is_evolution(card: Card) -> bool:
    return card.is_pokemon and card.evolves_from is not None


BASIC_ENERGY_NAMES = {f"{t} Energy" for t in BASIC_TYPES}

#: especiais que fornecem um tipo (a TCGdex nem sempre as marca como "Special")
TYPED_SPECIAL_ENERGIES = {
    "Telepathic Psychic Energy": "Psychic",
    "Growing Grass Energy": "Grass",
    "Rocky Fighting Energy": "Fighting",
}


def is_basic_energy(card: Card) -> bool:
    return (
        card.supertype == Supertype.ENERGY
        and "Special" not in card.subtypes
        and card.name in BASIC_ENERGY_NAMES
    )


def is_special_energy(card: Card) -> bool:
    return card.supertype == Supertype.ENERGY and not is_basic_energy(card)


def energy_type_of(card: Card) -> str:
    """Tipo de uma energia básica ("Fire") ou o nome de uma especial."""
    if is_special_energy(card):
        return card.name
    return card.types[0] if card.types else card.name.replace(" Energy", "")


def trainer_kind(card: Card) -> str:
    for kind in ("Supporter", "Item", "Stadium", "Tool"):
        if kind in card.subtypes:
            return kind
    return ""


def pokemon_type(card: Card) -> str:
    return card.types[0] if card.types else "Colorless"


def has_ability(card: Card, name: str) -> bool:
    return any(ability.name == name for ability in card.abilities)
