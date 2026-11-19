"""Classificação de cartas usada pelos efeitos ("Regra de Prêmio", Tera,
Antigo/Futuro, Pokémon de treinador como "Team Rocket's", estágio...).

A TCGdex não marca Tera/Antigo/Futuro nem as ACE SPEC antigas, então esses grupos vêm do pacote de
dados (`effects.json`, chave "groups", por assinatura da carta), que chega
atualizado pelo endpoint. Isso importa: Pokémon Tera no Banco não recebem
dano de ataques (`passives.damage_prevented`) e liberam banco de 8 com Area
Zero Underdepths.
"""

from __future__ import annotations

import dataclasses
import re

from pokemon_companion.cards_db.models import Card, Supertype, signature
from pokemon_companion.engine.effects import pack

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
RULE_BOX_SUBTYPES = {
    "ex",
    "EX",
    "V",
    "VMAX",
    "VSTAR",
    "GX",
    "TAG TEAM",
    "TAG TEAM-GX",
    "V-UNION",
    "BREAK",
    "MEGA",
    "Legend",
}


def has_rule_box(card: Card) -> bool:
    return (
        bool(set(card.subtypes) & RULE_BOX_SUBTYPES)
        or card.name.startswith("Radiant ")
        or is_prism_star(card)
    )


def is_ex(card: Card) -> bool:
    return "ex" in card.subtypes or "EX" in card.subtypes


def is_mega(card: Card) -> bool:
    return "Mega" in card.subtypes


def is_tera(card: Card) -> bool:
    return signature(card) in pack.group("Tera")


def is_ancient(card: Card) -> bool:
    return signature(card) in pack.group("Ancient")


def is_future(card: Card) -> bool:
    return signature(card) in pack.group("Future")


def is_pokemon_v(card: Card) -> bool:
    """Pokémon V inclui VMAX, VSTAR e V-UNION."""
    return bool({"V", "VMAX", "VSTAR", "V-UNION"} & set(card.subtypes))


def is_ace_spec(card: Card) -> bool:
    """A TCGdex só marca as ACE SPEC novas (raridade); as de Black & White
    vêm do grupo "ACE SPEC" do pacote de dados."""
    return "ACE SPEC" in card.subtypes or signature(card) in pack.group("ACE SPEC")


def is_prism_star(card: Card) -> bool:
    return "◇" in card.name or "Prism Star" in card.subtypes


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
    "Bubbly Water Energy": "Water",
    "Shadowy Darkness Energy": "Darkness",
    "Voltaic Lightning Energy": "Lightning",
    "Magnetic Metal Energy": "Metal",
    "Nitro Fire Energy": "Fire",
}


_ENERGY_WORDS = "|".join(BASIC_TYPES + ("Colorless",))
_PROVIDES = re.compile(
    rf"(?:This card|.+? Energy|As long as this card is attached to a Pokémon, it) provides "
    rf"(\d+ )?((?:[{{\[]\w[}}\]])+|{_ENERGY_WORDS}) Energy"
)
_ONE_AT_A_TIME = re.compile(
    r"While (?:in play|this card is attached to a Pokémon), (?:this card|it) provides "
    r"(every type of|.+?) Energy but provides only 1 Energy at a time"
)


def special_energy_units(card: Card) -> list[str] | None:
    """Unidades de uma Energia Especial cujo texto só diz o que ela fornece
    ("Double Colorless Energy provides {C}{C} Energy", "While in play, this
    card provides every type of Energy but provides only 1 Energy at a time");
    None se o texto tiver qualquer outro efeito."""
    text = re.sub(r"\s*\([^)]*\)", "", " ".join(card.rules)).replace("’", "'")
    units: list[str] | None = None
    for sentence in filter(None, (s.strip() for s in re.split(r"(?<=\.)\s+", text))):
        sentence = sentence.rstrip(".")
        if match := _PROVIDES.fullmatch(sentence):
            symbols = re.findall(r"[{\[](\w)[}\]]", match.group(2))
            names = [_SYMBOL_TYPES.get(s, "Colorless") for s in symbols] or [match.group(2)]
            units = names * int(match.group(1) or 1)
        elif match := _ONE_AT_A_TIME.fullmatch(sentence):
            kinds = re.split(r",? and |, ", match.group(1))
            units = ["Any" if match.group(1) == "every type of" else "|".join(kinds)]
        else:
            return None
    return units


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


# ---------------------------------------------------------------------------
# Fósseis: Itens jogados "como se fossem" um Pokémon Básico

_FOSSIL_RE = re.compile(r"Play this card as if it were a (\d+)-HP Basic [\[{](\w)[\]}] Pokémon")
_SYMBOL_TYPES = {
    "G": "Grass",
    "R": "Fire",
    "W": "Water",
    "L": "Lightning",
    "P": "Psychic",
    "F": "Fighting",
    "D": "Darkness",
    "M": "Metal",
    "N": "Dragon",
    "C": "Colorless",
}
#: id da versão em jogo → carta de Item original (volta assim para o descarte)
FOSSIL_ORIGINALS: dict[str, Card] = {}


def is_fossil_item(card: Card) -> bool:
    return card.supertype == Supertype.TRAINER and bool(
        card.rules and _FOSSIL_RE.search(card.rules[0])
    )


def fossil_pokemon(card: Card) -> Card:
    """O Item como Pokémon Básico em jogo (HP e tipo do texto, sem ataques,
    mantendo a Habilidade impressa)."""
    match = _FOSSIL_RE.search(card.rules[0])
    assert match is not None
    in_play = dataclasses.replace(
        card,
        id=f"fossil-{card.id}",
        supertype=Supertype.POKEMON,
        subtypes=["Basic"],
        hp=int(match.group(1)),
        types=[_SYMBOL_TYPES.get(match.group(2), "Colorless")],
        attacks=[],
        retreat_cost=[],
        evolves_from=None,
    )
    FOSSIL_ORIGINALS[in_play.id] = card
    return in_play


def is_fossil_pokemon(card: Card) -> bool:
    return card.id in FOSSIL_ORIGINALS
