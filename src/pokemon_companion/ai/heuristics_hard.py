"""IA difícil: mesmo framework da média, com lookahead de um turno inteiro.

Para cada ação candidata: aplica a ação, completa o resto do próprio turno
de forma gulosa, simula o turno inteiro do oponente (também guloso) e só
então pontua o estado. Assim toda candidata é comparada no mesmo ponto do
jogo. (Uma versão anterior só simulava a resposta do oponente quando a
ação encerrava o turno — "passar a vez" parecia sempre pior que qualquer
ação que não encerrasse o turno, e a IA recuava em loop jogando energia
fora.)
"""

from __future__ import annotations

import copy

from pokemon_companion.ai.heuristics_medium import (
    DEFAULT_WEIGHTS,
    MediumAI,
    evaluate_state,
    retreats_last,
)
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action, EndTurn
from pokemon_companion.engine.game_state import GameState, PlayerId

MAX_SIMULATED_STEPS_PER_TURN = 12


def play_out_turn(state: GameState, side: PlayerId, model: MediumAI) -> None:
    """Joga (in place) o resto do turno de `side` com o modelo guloso."""
    for _ in range(MAX_SIMULATED_STEPS_PER_TURN):
        if state.winner is not None or state.active_player != side:
            return
        actions = rules.legal_actions(state)
        if not actions:
            return
        rules.apply_action(state, model.choose_action(state, actions))
    if state.winner is None and state.active_player == side:
        rules.apply_action(state, EndTurn())


class HardAI:
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        opponent_model: MediumAI | None = None,
    ) -> None:
        self._weights = weights or DEFAULT_WEIGHTS
        self._opponent_model = opponent_model or MediumAI(self._weights)
        self._own_model = MediumAI(self._weights)

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        perspective = state.active_player
        best_action = legal_actions[0]
        best_score = float("-inf")

        for action in retreats_last(legal_actions):
            simulated = copy.deepcopy(state)
            rules.apply_action(simulated, action)
            play_out_turn(simulated, perspective, self._own_model)
            play_out_turn(simulated, perspective.other, self._opponent_model)

            score = evaluate_state(simulated, perspective, self._weights)
            if score > best_score:
                best_score = score
                best_action = action

        return best_action
