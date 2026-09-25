"""Textos de carta reescritos pelo pacote de efeitos (`effects.json`, que
chega por `cards_db.updates`): a carta nova passa a funcionar com frases que
os compiladores já conhecem, sem código novo no app instalado."""

from __future__ import annotations

import json
from functools import cache

from pokemon_companion.cards_db import updates


@cache
def rewrites() -> dict[str, str]:
    try:
        data = json.loads(updates.data_file("effects.json").read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data["rewrites"].items()}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def rewrite(text: str) -> str:
    return rewrites().get(text.strip(), text)
