"""Regras de construção de deck do livro oficial (checadas ao importar uma
decklist; violações viram avisos, não bloqueiam a partida):

- exatamente 60 cartas;
- no máximo 4 cópias com o mesmo nome (energia básica é ilimitada);
- pelo menos 1 Pokémon Básico;
- no máximo 1 carta ACE SPEC e no máximo 1 Pokémon Radiante;
- no máximo 1 cópia de cada carta Prism Star (◇, só no Expanded);
- só cartas do formato do deck (ver `standard.py`).

O formato fica num comentário do arquivo do deck (`# formato: expanded`);
sem ele, o deck é Standard.
"""

from __future__ import annotations

import re
from collections import Counter

from pokemon_companion.cards_db import standard
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects.cardinfo import is_ace_spec, is_prism_star

DECK_SIZE = 60
MAX_COPIES = 4
_FORMAT_RE = re.compile(r"^#\s*formato:\s*(\w+)", re.IGNORECASE | re.MULTILINE)


def deck_format(text: str) -> str:
    match = _FORMAT_RE.search(text)
    fmt = match.group(1).lower() if match else "standard"
    return fmt if fmt in standard.FORMATS else "standard"


def format_header(fmt: str) -> str:
    """Linha de comentário que marca o formato (o Standard não precisa)."""
    return f"# formato: {fmt}\n" if fmt != "standard" else ""


def _is_basic_energy(card: Card) -> bool:
    return card.supertype == Supertype.ENERGY and card.id.startswith("basic-energy-")


def max_copies(card: Card) -> int:
    return 1 if is_prism_star(card) else MAX_COPIES


def validate_deck(
    cards: list[Card], legal: set[str] | None = None, fmt: str = "standard"
) -> list[str]:
    """`legal`: assinaturas do formato; sem ela (arquivo ausente), não checa."""
    problems: list[str] = []
    if legal is not None:
        outside = standard.illegal_cards(cards, legal)
        if outside:
            where = "da rotação do Standard" if fmt == "standard" else "do Expandido (ou banidas)"
            problems.append(f"fora {where}: {', '.join(outside)}")
    if len(cards) != DECK_SIZE:
        problems.append(f"o deck tem {len(cards)} cartas (o oficial é exatamente {DECK_SIZE})")

    by_name = {card.name: card for card in cards}
    counts = Counter(card.name for card in cards if not _is_basic_energy(card))
    for name, count in sorted(counts.items()):
        limit = max_copies(by_name[name])
        if count > limit:
            problems.append(f"{count} cópias de {name} (máximo {limit})")

    if not any(card.is_basic for card in cards):
        problems.append("o deck não tem nenhum Pokémon Básico")

    ace_specs = sum(1 for card in cards if is_ace_spec(card))
    if ace_specs > 1:
        problems.append(f"{ace_specs} cartas ACE SPEC (máximo 1)")
    radiants = sum(1 for card in cards if card.name.startswith("Radiant "))
    if radiants > 1:
        problems.append(f"{radiants} Pokémon Radiantes (máximo 1)")
    return problems
