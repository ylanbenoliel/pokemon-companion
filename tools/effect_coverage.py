"""Cobertura de efeitos: quais textos de cartas dos decks ainda não têm
implementação (ataques com texto sem registro, Habilidades sem registro nem
efeito passivo, Treinadores sem registro).

Uso:
    uv run python tools/effect_coverage.py src/pokemon_companion/decks/top
"""

from __future__ import annotations

import argparse
import inspect
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.deck_loading import make_lookup
from pokemon_companion.engine import rules
from pokemon_companion.engine.effects import abilities, attacks, core, passives, trainers

PASSIVE_SOURCE = "".join(
    inspect.getsource(module) for module in (passives, rules, core, trainers, abilities, attacks)
)


def missing_effects(cards: Iterable[Card]) -> tuple[Counter[str], Counter[str]]:
    """(cópias por categoria, cópias por texto sem efeito implementado)."""
    missing: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for card in cards:
        if card.supertype == Supertype.POKEMON:
            for attack in card.attacks:
                if attack.text:
                    total["ataque"] += 1
                    if attack.name not in attacks.ATTACKS:
                        missing[f"ataque  {attack.name} ({card.name})"] += 1
            for ability in card.abilities:
                total["habilidade"] += 1
                known = ability.name in abilities.ABILITIES
                if not known and f'"{ability.name}"' not in PASSIVE_SOURCE:
                    missing[f"habilid {ability.name} ({card.name})"] += 1
        elif card.supertype == Supertype.TRAINER:
            total["treinador"] += 1
            if not trainers.is_implemented(card):
                missing[f"treinad {card.name}"] += 1
            elif (
                "Stadium" in card.subtypes
                and card.name not in trainers.STADIUMS
                and f'"{card.name}"' not in PASSIVE_SOURCE
            ):
                missing[f"estádio {card.name}"] += 1
        elif "Special" in card.subtypes or card.rules:
            total["energia especial"] += 1
            if f'"{card.name}"' not in PASSIVE_SOURCE:
                missing[f"energia {card.name}"] += 1
    return total, missing


def deck_cards(folder: Path) -> list[Card]:
    cards: list[Card] = []
    with CardCache() as cache:
        lookup = make_lookup()
        for path in sorted(folder.glob("*.txt")):
            cards += load_deck(path, cache, lookup)[0]
    return cards


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    total, missing = missing_effects(deck_cards(args.folder))
    print("Cartas (cópias) por categoria:", dict(total))
    print(f"Sem efeito implementado: {sum(missing.values())} cópias, {len(missing)} textos")
    for name, count in missing.most_common():
        print(f"  {count:3d}× {name}")


if __name__ == "__main__":
    main()
