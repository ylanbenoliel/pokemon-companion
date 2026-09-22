"""Efeitos de ataques cadastrados manualmente para o card pool do MVP.

Limitação conhecida (ver README): a API só fornece o texto do ataque em
inglês, não estruturado — cada efeito precisa ser cadastrado manualmente
aqui, por (id da carta, nome do ataque). Cartas sem entrada aplicam apenas
o dano base (`Attack.base_damage`).

O exemplo abaixo cobre o ataque "Golpe Forte" do Charmander mockado usado em
`demo_data.py`/CLI: cara -> descarta 1 energia do defensor, além do dano
normal já aplicado por `rules.apply_action` antes de chamar este efeito.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pokemon_companion.engine.effects.registry import register
from pokemon_companion.engine.status_conditions import default_coin_flip

if TYPE_CHECKING:
    from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay


@register("basic-charmander", "Golpe Forte")
def _charmander_golpe_forte(
    state: GameState,
    attacker_id: PlayerId,
    attacker: PokemonInPlay,
    defender: PokemonInPlay,
    attack_name: str,
) -> None:
    """Se sair cara, descarta 1 energia anexada ao Pokémon defensor."""
    if defender.attached_energies and default_coin_flip():
        defender.attached_energies.pop()
