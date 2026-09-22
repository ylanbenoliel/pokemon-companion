"""Gravação do histórico de uma partida para exportação em JSON.

Escopo do MVP: grava a sequência de ações + mensagens para consulta
posterior (ex: revisar o que aconteceu numa partida). Não inclui um player
de replay visual — isso fica para uma iteração futura.
"""

from __future__ import annotations

import json
from pathlib import Path

from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import PlayerId


def _action_to_dict(action: Action) -> dict:
    # Todas as subclasses de Action são dataclasses "rasas" (campos apenas
    # int/bool/None), então vars() já basta — evita o overload de
    # dataclasses.asdict(), que exige um DataclassInstance estático.
    return {"type": type(action).__name__, **vars(action)}


class MatchRecorder:
    def __init__(self) -> None:
        self._entries: list[dict] = []

    def record(
        self, turn_number: int, actor: PlayerId, action: Action, messages: list[str]
    ) -> None:
        """Registre com o turno e o jogador de *antes* de aplicar a ação:
        atacar e passar a vez trocam o jogador ativo, então ler do estado
        depois da ação atribuía a jogada ao turno/jogador seguinte."""
        self._entries.append(
            {
                "turn_number": turn_number,
                "active_player": actor.value,
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
