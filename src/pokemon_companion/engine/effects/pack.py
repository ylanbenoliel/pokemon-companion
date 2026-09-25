"""Pacote de efeitos (`effects.json`, que chega por `cards_db.updates`):
textos de carta reescritos com frases que os compiladores já conhecem e os
grupos que a API não marca (Tera, Ancient, Future) — cartas novas funcionam
sem código novo no app instalado."""

from __future__ import annotations

import json
from functools import cache

from pokemon_companion.cards_db import updates


@cache
def _data() -> dict:
    try:
        data = json.loads(updates.data_file("effects.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@cache
def rewrites() -> dict[str, str]:
    raw = _data().get("rewrites")
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


@cache
def group(name: str) -> frozenset[str]:
    """Assinaturas das cartas de um grupo sem marca na API ("Tera", "Ancient", "Future")."""
    raw = _data().get("groups", {}).get(name)
    return frozenset(map(str, raw)) if isinstance(raw, list) else frozenset()


def rewrite(text: str) -> str:
    return rewrites().get(text.strip(), text)
