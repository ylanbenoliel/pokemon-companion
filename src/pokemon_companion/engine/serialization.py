"""Estado de jogo e ações em JSON — seguro para arquivos compartilhados.

Nada de `pickle` (abrir o replay de outra pessoa executaria código dela):
o formato é JSON com marcadores para o que o JSON não tem, e o decodificador
só reconstrói as classes desta lista.

- `{"$card": id}` → carta da tabela `cards` (cada carta é gravada uma vez);
- `{"$dc": "PokemonInPlay", ...campos}` → dataclass conhecida;
- `{"$enum": "StatusCondition", "value": ...}`, `{"$tuple": [...]}`,
  `{"$set": [...]}`, `{"$frozenset": [...]}`.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any

from pokemon_companion.cards_db.cache import card_from_json, card_to_json
from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine import actions as actions_module
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import (
    GameState,
    PlayerId,
    PlayerState,
    PokemonInPlay,
    StatusCondition,
)

_DATACLASSES: dict[str, type] = {
    cls.__name__: cls for cls in (GameState, PlayerState, PokemonInPlay)
}
_ENUMS: dict[str, type[Enum]] = {cls.__name__: cls for cls in (PlayerId, StatusCondition)}
_ACTIONS: dict[str, type[Action]] = {
    name: cls
    for name, cls in vars(actions_module).items()
    if isinstance(cls, type) and issubclass(cls, Action) and cls is not Action
}


class SerializationError(ValueError):
    """Arquivo que não é um estado/ação deste formato."""


# ---------------------------------------------------------------------------
# estado


def encode_state(state: GameState) -> dict[str, Any]:
    cards: dict[str, str] = {}
    body = _encode(state, cards)
    return {"cards": cards, "state": body}


def decode_state(data: dict[str, Any]) -> GameState:
    try:
        table = {cid: card_from_json(raw) for cid, raw in data["cards"].items()}
        state = _decode(data["state"], table)
    except (KeyError, TypeError, ValueError) as exc:
        raise SerializationError(f"estado inválido: {exc}") from exc
    if not isinstance(state, GameState):
        raise SerializationError("o arquivo não contém um estado de jogo")
    return state


def _encode(value: Any, cards: dict[str, str]) -> Any:
    if isinstance(value, Card):
        cards.setdefault(value.id, card_to_json(value))
        return {"$card": value.id}
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.name}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in _DATACLASSES:
            raise SerializationError(f"tipo não serializável: {name}")
        return {
            "$dc": name,
            **{f.name: _encode(getattr(value, f.name), cards) for f in dataclasses.fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(v, cards) for v in value]}
    if isinstance(value, frozenset):
        return {"$frozenset": [_encode(v, cards) for v in sorted(value, key=repr)]}
    if isinstance(value, set):
        return {"$set": [_encode(v, cards) for v in sorted(value, key=repr)]}
    if isinstance(value, list):
        return [_encode(v, cards) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise SerializationError(f"valor não serializável: {type(value).__name__}")


def _decode(value: Any, cards: dict[str, Card]) -> Any:
    if isinstance(value, list):
        return [_decode(v, cards) for v in value]
    if not isinstance(value, dict):
        return value
    if "$card" in value:
        return cards[value["$card"]]
    if "$enum" in value:
        return _ENUMS[value["$enum"]][value["value"]]
    if "$tuple" in value:
        return tuple(_decode(v, cards) for v in value["$tuple"])
    if "$set" in value:
        return {_decode(v, cards) for v in value["$set"]}
    if "$frozenset" in value:
        return frozenset(_decode(v, cards) for v in value["$frozenset"])
    if "$dc" in value:
        cls = _DATACLASSES[value["$dc"]]
        known = {f.name for f in dataclasses.fields(cls)}
        fields = {k: _decode(v, cards) for k, v in value.items() if k in known}
        return cls(**fields)
    raise SerializationError(f"objeto desconhecido: {sorted(value)[:3]}")


# ---------------------------------------------------------------------------
# ações


def encode_action(action: Action) -> dict[str, Any]:
    return {"type": type(action).__name__, **{k: _encode(v, {}) for k, v in vars(action).items()}}


def decode_action(data: dict[str, Any]) -> Action:
    cls = _ACTIONS.get(data.get("type", ""))
    if cls is None:
        raise SerializationError(f"ação desconhecida: {data.get('type')}")
    fields = {k: _decode(v, {}) for k, v in data.items() if k != "type"}
    return cls(**fields)
