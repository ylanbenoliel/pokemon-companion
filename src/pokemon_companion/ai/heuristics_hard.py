"""IA difícil: mesmo framework da média, com um ply extra de lookahead —
para cada ação candidata, simula também a melhor resposta prevista do
oponente antes de pontuar o estado resultante."""

from __future__ import annotations

import copy

from pokemon_companion.ai.heuristics_medium import DEFAULT_WEIGHTS, MediumAI, evaluate_state
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState


class HardAI:
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        opponent_model: MediumAI | None = None,
    ) -> None:
        self._weights = weights or DEFAULT_WEIGHTS
        self._opponent_model = opponent_model or MediumAI(self._weights)

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        perspective = state.active_player
        best_action = legal_actions[0]
        best_score = float("-inf")

        for action in legal_actions:
            simulated = copy.deepcopy(state)
            rules.apply_action(simulated, action)

            if simulated.winner is None and simulated.active_player != perspective:
                opponent_actions = rules.legal_actions(simulated)
                if opponent_actions:
                    opponent_action = self._opponent_model.choose_action(
                        simulated, opponent_actions
                    )
                    rules.apply_action(simulated, opponent_action)

            score = evaluate_state(simulated, perspective, self._weights)
            if score > best_score:
                best_score = score
                best_action = action

        return best_action
