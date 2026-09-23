"""Assistir a um replay no tabuleiro, com as mesmas animações e sons.

`ReplayController` é o controlador da partida em modo espectador, mas as
jogadas saem do arquivo em vez da IA. Espaço pausa/continua.
"""

from __future__ import annotations

from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState, PlayerId
from pokemon_companion.engine.replay import Replay
from pokemon_companion.ui.app import BattleController
from pokemon_companion.ui.battle_scene import BattleScene
from pokemon_companion.ui.sound import NullSounds


class _NoAI:
    """O replay não decide nada: as jogadas vêm do arquivo."""

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        raise RuntimeError("o replay não consulta a IA")


class ReplayController(BattleController):
    def __init__(
        self, scene: BattleScene, replay: Replay, sounds: NullSounds | None = None
    ) -> None:
        self._replay = replay
        super().__init__(
            scene,
            state_factory=replay.begin,
            ai_factory=_NoAI,
            player_ai_factory=_NoAI,
            sounds=sounds,
        )

    @property
    def spectating(self) -> bool:
        return True

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.actions)

    def new_game(self) -> None:
        self.actions = self._replay.decoded_actions()
        self.cursor = 0
        self.diverged = False
        super().new_game()

    def _ai_step(self) -> None:
        if self.busy or self.paused or rules.is_game_over(self.state):
            return
        if self.finished or self.diverged:
            self._end_of_recording()
            return
        action = self.actions[self.cursor]
        self.cursor += 1
        if action not in rules.legal_actions(self.state):
            self.diverged = True
            self.scene.show_toast("Este replay não bate com esta versão do jogo.")
            return
        self.perform(action)

    def _end_of_recording(self) -> None:
        """Fim das jogadas gravadas: a partida acabou por desistência."""
        winner = self._replay.info.get("winner")
        if winner and self.state.winner is None:
            self.state.winner = PlayerId(winner)
            self.scene.show_toast("Partida encerrada por desistência.")
            self._on_idle()
