"""Interface comum que toda IA oponente implementa."""

from __future__ import annotations

from typing import Protocol

from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState


class AIPlayer(Protocol):
    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action: ...
