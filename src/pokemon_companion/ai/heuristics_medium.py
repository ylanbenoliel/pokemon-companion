"""IA média: simula cada ação legal e escolhe a de maior pontuação heurística.

Os pesos são configuráveis (ver `ai/weights.yaml`) para facilitar ajuste sem
tocar na lógica de avaliação.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action, Retreat
from pokemon_companion.engine.game_state import GameState, PlayerId

DEFAULT_WEIGHTS: dict[str, float] = {
    "win": 1000.0,
    "prizes_taken": 25.0,
    "board_presence": 5.0,
    # HP *restante* em campo, não contadores de dano: com contadores, nocautear
    # "apagava" o dano já causado (o Pokémon vai para o descarte) e um KO
    # chegava a pontuar negativo — a IA deixava de atacar para não nocautear.
    "opponent_hp": 1.0,
    "own_hp": 0.4,
    "lose_own_active": 80.0,
    # Recuar descarta energia; sem este termo a IA recuava sem propósito.
    "energy_attached": 5.0,
}


def load_weights(path: Path | None = None, profile: str = "medium") -> dict[str, float]:
    if path is None:
        return dict(DEFAULT_WEIGHTS)
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return {**DEFAULT_WEIGHTS, **data.get(profile, {})}


def evaluate_state(state: GameState, perspective: PlayerId, weights: dict[str, float]) -> float:
    if state.winner == perspective:
        return weights["win"]
    if state.winner == perspective.other:
        return -weights["win"]

    me = state.state_of(perspective)
    opponent = state.state_of(perspective.other)
    score = 0.0

    score += weights["prizes_taken"] * (6 - len(me.prizes))
    score -= weights["prizes_taken"] * (6 - len(opponent.prizes))

    score += weights["board_presence"] * len(me.bench)
    score -= weights["board_presence"] * len(opponent.bench)

    my_mons, their_mons = me.all_pokemon_in_play(), opponent.all_pokemon_in_play()
    score += weights["own_hp"] * sum(m.current_hp for m in my_mons)
    score -= weights["opponent_hp"] * sum(m.current_hp for m in their_mons)
    energy = weights["energy_attached"]
    score += energy * sum(len(m.attached_energies) for m in my_mons)
    score -= energy * sum(len(m.attached_energies) for m in their_mons)

    if me.active is None:
        score -= weights["lose_own_active"]

    return score


def retreats_last(actions: list[Action]) -> list[Action]:
    """Em caso de empate de pontuação, prefere qualquer coisa a recuar (o
    primeiro melhor vence) — evita trocas de ativo sem propósito."""
    return sorted(actions, key=lambda action: isinstance(action, Retreat))


class MediumAI:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = weights or DEFAULT_WEIGHTS

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        perspective = state.active_player
        best_action = legal_actions[0]
        best_score = float("-inf")
        for action in retreats_last(legal_actions):
            simulated = copy.deepcopy(state)
            rules.apply_action(simulated, action)
            score = evaluate_state(simulated, perspective, self._weights)
            if score > best_score:
                best_score = score
                best_action = action
        return best_action
