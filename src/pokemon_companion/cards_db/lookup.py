"""Fonte de cartas plugável: qualquer objeto com `find_card(nome, set, número)`.

`FallbackLookup` tenta várias fontes em ordem (ex: pokemontcg.io, depois
TCGdex). Uma fonte que falha com erro de rede/servidor é desligada pelo
resto da importação — assim uma API fora do ar custa uma única tentativa, e
não uma por carta da decklist.
"""

from __future__ import annotations

from typing import Protocol

import requests

from pokemon_companion.cards_db.models import Card


class CardLookup(Protocol):
    def find_card(self, name: str, set_code: str, number: str) -> Card | None: ...


class FallbackLookup:
    def __init__(self, sources: list[CardLookup]) -> None:
        self._sources = list(sources)
        self._dead: set[int] = set()

    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        last_error: Exception | None = None
        for index, source in enumerate(self._sources):
            if index in self._dead:
                continue
            try:
                card = source.find_card(name, set_code, number)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                self._dead.add(index)
                last_error = exc
                continue
            if card is not None:
                return card
        if last_error is not None and len(self._dead) == len(self._sources):
            raise RuntimeError(f"nenhuma fonte de cartas disponível ({last_error})")
        return None
