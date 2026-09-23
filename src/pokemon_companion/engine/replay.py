"""Replays: a partida inteira num arquivo JSON pequeno e compartilhável.

Um replay guarda o estado logo depois de criada a partida (decks já
embaralhados, mão inicial), o estado do gerador aleatório naquele instante e
a lista de ações. Reaplicar as ações sobre o mesmo estado, com o mesmo
gerador, reproduz a partida inteira — moedas e embaralhamentos inclusive —,
porque o motor só usa o `random` global e o app preserva esse gerador nas
decisões da IA e nos efeitos visuais/sonoros.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pokemon_companion import paths
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState
from pokemon_companion.engine.serialization import (
    SerializationError,
    decode_action,
    decode_state,
    encode_action,
    encode_state,
)

REPLAY_VERSION = 1
#: replays guardados automaticamente (os mais antigos saem)
KEEP_REPLAYS = 50


def replays_dir() -> Path:
    return paths.USER_DATA / "replays"


@dataclass
class Replay:
    initial: dict[str, Any]
    rng: list[Any]
    actions: list[dict[str, Any]] = field(default_factory=list)
    #: rótulos para a lista (decks, dificuldade, data, resultado)
    info: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, state: GameState, info: dict[str, Any] | None = None) -> Replay:
        """Chame logo depois de criar a partida, antes de qualquer ação."""
        return cls(
            initial=encode_state(state),
            rng=_rng_to_json(random.getstate()),
            info={"date": datetime.now().isoformat(timespec="seconds"), **(info or {})},
        )

    def record(self, action: Action) -> None:
        self.actions.append(encode_action(action))

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": REPLAY_VERSION,
                "info": self.info,
                "initial": self.initial,
                "rng": self.rng,
                "actions": self.actions,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Replay:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SerializationError(f"replay corrompido: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != REPLAY_VERSION:
            raise SerializationError("replay de uma versão desconhecida")
        return cls(
            initial=data["initial"],
            rng=data["rng"],
            actions=list(data.get("actions", [])),
            info=dict(data.get("info", {})),
        )

    # -- reprodução ---------------------------------------------------------
    def begin(self) -> GameState:
        """O estado inicial, com o gerador aleatório de volta ao ponto da
        gravação (chame antes de reaplicar as ações)."""
        state = decode_state(self.initial)
        random.setstate(_rng_from_json(self.rng))
        return state

    def decoded_actions(self) -> list[Action]:
        return [decode_action(a) for a in self.actions]

    def states(self) -> Iterator[tuple[Action, GameState]]:
        """Reaplica cada ação e devolve o estado depois dela."""
        state = self.begin()
        for action in self.decoded_actions():
            if action not in rules.legal_actions(state):
                raise SerializationError(f"o replay diverge em {action}")
            rules.apply_action(state, action)
            yield action, state


def _rng_to_json(value: tuple[Any, ...]) -> list[Any]:
    version, internal, gauss = value
    return [version, list(internal), gauss]


def _rng_from_json(value: list[Any]) -> tuple[Any, ...]:
    version, internal, gauss = value
    return (version, tuple(internal), gauss)


# ---------------------------------------------------------------------------
# arquivos


def save_replay(replay: Replay, folder: Path | None = None) -> Path:
    folder = folder or replays_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}.json"
    counter = 1
    while path.exists():
        counter += 1
        path = folder / f"{stamp}-{counter}.json"
    path.write_text(replay.to_json(), encoding="utf-8")
    for old in sorted(folder.glob("*.json"))[:-KEEP_REPLAYS]:
        old.unlink(missing_ok=True)
    return path


def list_replays(folder: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """(arquivo, info) dos replays, do mais novo ao mais antigo; arquivos
    ilegíveis ficam de fora."""
    folder = folder or replays_dir()
    found = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append((path, dict(data.get("info", {}))))
        except (OSError, ValueError):
            continue
    return found


def load_replay(path: Path) -> Replay:
    return Replay.from_json(path.read_text(encoding="utf-8"))
