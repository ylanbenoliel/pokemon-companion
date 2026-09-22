"""Interface comum que toda IA oponente implementa, e fábrica por nível de
dificuldade — usada tanto pelo CLI (`main.py`) quanto pela UI gráfica
(`ui/app.py`), para não duplicar a escolha de classe em cada entry point."""

from __future__ import annotations

from typing import Protocol

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.ai.heuristics_hard import HardAI
from pokemon_companion.ai.heuristics_medium import MediumAI
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState


class AIPlayer(Protocol):
    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action: ...


def build_ai(difficulty: str) -> AIPlayer:
    if difficulty == "easy":
        return EasyAI()
    if difficulty == "hard":
        return HardAI()
    return MediumAI()
