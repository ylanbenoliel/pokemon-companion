"""Registry de efeitos especiais de ataques, por id de carta.

Limitação conhecida do MVP: a API pokemontcg.io só fornece o texto do ataque
em inglês, não estruturado. Dano numérico sempre é aplicado via
`Attack.base_damage`; efeitos adicionais (coin flip, descarte de energia,
etc.) só existem para cartas explicitamente cadastradas aqui. Cartas sem
entrada aplicam apenas o dano base.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay

# Um efeito recebe (game_state, attacker_id, attacker, defender, attack_name)
# e pode mutar o game_state livremente (ex: dano extra, descarte de energia).
AttackEffect = Callable[["GameState", "PlayerId", "PokemonInPlay", "PokemonInPlay", str], None]

_REGISTRY: dict[tuple[str, str], AttackEffect] = {}


def register(card_id: str, attack_name: str) -> Callable[[AttackEffect], AttackEffect]:
    def decorator(func: AttackEffect) -> AttackEffect:
        _REGISTRY[(card_id, attack_name)] = func
        return func

    return decorator


def get_effect(card_id: str, attack_name: str) -> AttackEffect | None:
    return _REGISTRY.get((card_id, attack_name))
