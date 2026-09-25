"""O que o motor ainda não aplica numa carta: a resposta única para o
construtor de deck, a carta ampliada na partida e `tools/effect_coverage.py`."""

from __future__ import annotations

from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects import abilities, attacks, passive_text, trainers
from pokemon_companion.engine.effects.passive_text import HAND_WRITTEN


def unimplemented(card: Card) -> list[str]:
    """Efeitos da carta sem implementação, como "ataque X" / "habilidade Y"."""
    missing: list[str] = []
    if card.supertype == Supertype.POKEMON:
        missing += [
            f"ataque {attack.name}"
            for attack in card.attacks
            if attack.text and attacks.spec_for(attack) is None
        ]
        missing += [
            f"habilidade {ability.name}"
            for ability in card.abilities
            if abilities.spec_for(ability) is None
            and not passive_text.passives_of(ability)
            and ability.name not in HAND_WRITTEN
        ]
    elif card.supertype == Supertype.TRAINER:
        if not trainers.is_implemented(card):
            missing.append("efeito do Treinador")
        elif (
            "Stadium" in card.subtypes
            and trainers.stadium_spec_for(card) is None
            and card.name not in HAND_WRITTEN
        ):
            missing.append("efeito do Estádio")
    elif ("Special" in card.subtypes or card.rules) and card.name not in HAND_WRITTEN:
        missing.append("efeito da Energia")
    return missing
