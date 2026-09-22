"""Condições de status básicas do Pokémon TCG.

Cada função recebe o `PokemonInPlay` ativo afetado e um `flip_coin` (injetado
para permitir testes determinísticos) e retorna uma mensagem descrevendo o
que aconteceu, para exibição na UI/CLI.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from pokemon_companion.engine.game_state import PokemonInPlay, StatusCondition

CoinFlip = Callable[[], bool]  # True = cara/heads


def default_coin_flip() -> bool:
    return random.random() < 0.5


def apply_between_turns_effects(
    pokemon: PokemonInPlay, flip_coin: CoinFlip = default_coin_flip
) -> list[str]:
    """Aplica efeitos de status que ocorrem no fim/início de turno.

    Ordem oficial: dano de veneno, depois dano de queimadura + coin flip de cura.
    Sono/paralisia não causam dano aqui — afetam apenas se o Pokémon pode agir
    (ver `can_attack`/`can_retreat`), e o sono tenta "acordar" no início do turno.
    """
    messages: list[str] = []

    if pokemon.status == StatusCondition.POISONED:
        pokemon.damage_counters += 10
        messages.append(f"{pokemon.card.name} sofreu 10 de dano por veneno.")

    if pokemon.status == StatusCondition.BURNED:
        pokemon.damage_counters += 20
        messages.append(f"{pokemon.card.name} sofreu 20 de dano por queimadura.")
        if flip_coin():
            pokemon.status = StatusCondition.NONE
            messages.append(f"{pokemon.card.name} curou a queimadura (cara).")
        else:
            messages.append(f"{pokemon.card.name} continua queimado (coroa).")

    return messages


def try_wake_up(pokemon: PokemonInPlay, flip_coin: CoinFlip = default_coin_flip) -> list[str]:
    """Chamado no início do turno do jogador dono do Pokémon dormindo."""
    if pokemon.status != StatusCondition.ASLEEP:
        return []
    if flip_coin():
        pokemon.status = StatusCondition.NONE
        return [f"{pokemon.card.name} acordou (cara)."]
    return [f"{pokemon.card.name} continua dormindo (coroa)."]


def can_attack(pokemon: PokemonInPlay) -> bool:
    return pokemon.status not in (StatusCondition.ASLEEP, StatusCondition.PARALYZED)


def can_retreat(pokemon: PokemonInPlay) -> bool:
    return pokemon.status not in (StatusCondition.ASLEEP, StatusCondition.PARALYZED)


def check_confusion_self_damage(
    pokemon: PokemonInPlay, flip_coin: CoinFlip = default_coin_flip
) -> tuple[bool, list[str]]:
    """Se confuso, sorteia se o ataque falha e causa 30 de dano a si mesmo.

    Retorna (pode_atacar, mensagens).
    """
    if pokemon.status != StatusCondition.CONFUSED:
        return True, []
    if flip_coin():
        return True, [f"{pokemon.card.name} está confuso, mas o ataque prosseguiu (cara)."]
    pokemon.damage_counters += 30
    return False, [
        f"{pokemon.card.name} está confuso e falhou o ataque, sofrendo 30 de dano (coroa)."
    ]
