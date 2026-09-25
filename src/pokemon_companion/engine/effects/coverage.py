"""O que o motor ainda não aplica numa carta: a resposta única para o
construtor de deck, a carta ampliada na partida e `tools/effect_coverage.py`."""

from __future__ import annotations

import functools
import inspect

from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects import abilities, attacks, passive_text, trainers


@functools.cache
def _hand_written_source() -> str | None:
    """Código das regras escritas à mão, onde efeitos passivos aparecem pelo
    nome da carta entre aspas. None no app empacotado, que não tem o fonte."""
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.effects import core, passives

    try:
        return "".join(
            inspect.getsource(module)
            for module in (passives, rules, core, trainers, abilities, attacks)
        )
    except OSError:
        return None


def _named_in_source(name: str) -> bool:
    source = _hand_written_source()
    # ponytail: sem o fonte (app empacotado) assume implementado; gerar a lista no build se incomodar
    return source is None or f'"{name}"' in source


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
            and not _named_in_source(ability.name)
        ]
    elif card.supertype == Supertype.TRAINER:
        if not trainers.is_implemented(card):
            missing.append("efeito do Treinador")
        elif (
            "Stadium" in card.subtypes
            and trainers.stadium_spec_for(card) is None
            and not _named_in_source(card.name)
        ):
            missing.append("efeito do Estádio")
    elif ("Special" in card.subtypes or card.rules) and not _named_in_source(card.name):
        missing.append("efeito da Energia")
    return missing
