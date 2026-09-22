"""IA fácil: sem avaliação de estado, apenas prioriza atacar."""

from __future__ import annotations

import random

from pokemon_companion.engine.actions import Action, UseAttack
from pokemon_companion.engine.game_state import GameState


class EasyAI:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        attacks = [a for a in legal_actions if isinstance(a, UseAttack)]
        if attacks:
            return self._rng.choice(attacks)
        return self._rng.choice(legal_actions)
