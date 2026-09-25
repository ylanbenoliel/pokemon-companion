"""A lista de nomes escritos à mão (`passive_text.HAND_WRITTEN`) acompanha o
código: o app empacotado não tem o fonte para procurar os nomes sozinho."""

from __future__ import annotations

import ast
import inspect
import io
import json
import tokenize

from pokemon_companion.engine import rules
from pokemon_companion.engine.effects import abilities, attacks, core, passives, trainers
from pokemon_companion.engine.effects.passive_text import HAND_WRITTEN
from pokemon_companion.paths import PACKAGE_DIR


def _catalog_names() -> set[str]:
    catalog = json.loads((PACKAGE_DIR / "cards_db" / "standard_catalog.json").read_text())
    names: set[str] = set()
    for entry in catalog:
        card = entry["card"]
        names.add(card["name"])
        names.update(ability["name"] for ability in card.get("abilities") or [])
    return names


def _string_literals(module: object) -> set[str]:
    source = inspect.getsource(module)  # type: ignore[arg-type]
    found: set[str] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING:
            value = ast.literal_eval(token.string)
            if isinstance(value, str):
                found.add(value)
    return found


def test_hand_written_matches_the_names_quoted_in_the_engine():
    quoted = set().union(
        *(_string_literals(m) for m in (passives, rules, core, trainers, abilities, attacks))
    )
    assert quoted & _catalog_names() == HAND_WRITTEN
