"""Texto de apresentação independente do meio (CLI ou UI gráfica) — gerar a
descrição de uma ação não depende de terminal nem de widgets."""

from __future__ import annotations

from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    Retreat,
    UseAttack,
)
from pokemon_companion.engine.game_state import GameState


def describe_action(state: GameState, action: Action) -> str:
    player = state.state_of(state.active_player)
    if isinstance(action, PlayBasicToActive):
        return f"Jogar {player.hand[action.hand_index].name} como ativo"
    if isinstance(action, PlayBasicToBench):
        return f"Jogar {player.hand[action.hand_index].name} no banco"
    if isinstance(action, Evolve):
        target = "ativo" if action.target_is_active else f"banco {action.bench_index}"
        return f"Evoluir {target} para {player.hand[action.hand_index].name}"
    if isinstance(action, AttachEnergy):
        target = "ativo" if action.target_is_active else f"banco {action.bench_index}"
        return f"Anexar {player.hand[action.hand_index].name} no {target}"
    if isinstance(action, UseAttack):
        assert player.active is not None
        attack = player.active.card.attacks[action.attack_index]
        return f"Atacar com {attack.name} ({attack.damage} dano)"
    if isinstance(action, Retreat):
        return f"Recuar para {player.bench[action.bench_index].card.name}"
    if isinstance(action, EndTurn):
        return "Passar o turno"
    return str(action)
