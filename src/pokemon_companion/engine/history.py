"""Gravação do histórico de uma partida para exportação em JSON.

Escopo do MVP: grava a sequência de ações + mensagens para consulta
posterior (ex: revisar o que aconteceu numa partida). Não inclui um player
de replay visual — isso fica para uma iteração futura.
"""

from __future__ import annotations

import json
from pathlib import Path

from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState


def _action_to_dict(action: Action) -> dict:
    # Todas as subclasses de Action são dataclasses "rasas" (campos apenas
    # int/bool/None), então vars() já basta — evita o overload de
    # dataclasses.asdict(), que exige um DataclassInstance estático.
    return {"type": type(action).__name__, **vars(action)}


class MatchRecorder:
    def __init__(self) -> None:
        self._entries: list[dict] = []

    def record(self, state: GameState, action: Action, messages: list[str]) -> None:
        self._entries.append(
            {
                "turn_number": state.turn_number,
                "active_player": state.active_player.value,
                "action": _action_to_dict(action),
                "messages": messages,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self._entries, ensure_ascii=False, indent=2)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._entries)
