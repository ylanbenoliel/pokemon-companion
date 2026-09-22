"""Regras de construção de deck do livro oficial (checadas ao importar uma
decklist; violações viram avisos, não bloqueiam a partida):

- exatamente 60 cartas;
- no máximo 4 cópias com o mesmo nome (energia básica é ilimitada);
- pelo menos 1 Pokémon Básico;
- no máximo 1 carta ACE SPEC e no máximo 1 Pokémon Radiante;
- só cartas da rotação atual do Standard (ver `standard.py`).
"""

from __future__ import annotations

from collections import Counter

from pokemon_companion.cards_db import standard
from pokemon_companion.cards_db.models import Card, Supertype

DECK_SIZE = 60
MAX_COPIES = 4


def _is_basic_energy(card: Card) -> bool:
    return card.supertype == Supertype.ENERGY and card.id.startswith("basic-energy-")


def validate_deck(cards: list[Card], legal: set[str] | None = None) -> list[str]:
    """`legal`: assinaturas do Standard; sem ela (arquivo ausente), não checa."""
    problems: list[str] = []
    if legal is not None:
        outside = standard.illegal_cards(cards, legal)
        if outside:
            problems.append(f"fora da rotação do Standard: {', '.join(outside)}")
    if len(cards) != DECK_SIZE:
        problems.append(f"o deck tem {len(cards)} cartas (o oficial é exatamente {DECK_SIZE})")

    counts = Counter(card.name for card in cards if not _is_basic_energy(card))
    for name, count in sorted(counts.items()):
        if count > MAX_COPIES:
            problems.append(f"{count} cópias de {name} (máximo {MAX_COPIES})")

    if not any(card.is_basic for card in cards):
        problems.append("o deck não tem nenhum Pokémon Básico")

    ace_specs = sum(1 for card in cards if "ACE SPEC" in card.subtypes)
    if ace_specs > 1:
        problems.append(f"{ace_specs} cartas ACE SPEC (máximo 1)")
    radiants = sum(1 for card in cards if card.name.startswith("Radiant "))
    if radiants > 1:
        problems.append(f"{radiants} Pokémon Radiantes (máximo 1)")
    return problems
