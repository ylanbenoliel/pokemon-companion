"""Parser de decklists no formato Limitless/PTCGO.

Formato esperado (cabeçalhos de seção e linhas em branco são ignorados):

    Pokémon: 12
    4 Charmander SVI 26
    2 Charmeleon SVI 27
    Trainer: 10
    ...
    Energy: 8
    8 Fire Energy

Duas etapas deliberadamente separadas:
- `parse_decklist_text` é pura (sem I/O), fácil de testar.
- `resolve_entries` resolve cada linha em um `Card` real via cache/API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pokemon_companion.cards_db.api_client import PokemonTcgApiClient
from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.models import Card

_SECTION_HEADER_RE = re.compile(r"^(Pokémon|Pokemon|Trainer|Energy)\s*:\s*\d+$", re.IGNORECASE)
_CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s+([A-Za-z0-9]{2,6})\s+(\d+[A-Za-z]?)$")
_BASIC_ENERGY_LINE_RE = re.compile(r"^(\d+)\s+(\w+)\s+Energy$", re.IGNORECASE)


@dataclass(frozen=True)
class DecklistEntry:
    quantity: int
    name: str
    set_code: str | None  # None => energia básica, resolvida sem API
    number: str | None


@dataclass(frozen=True)
class DecklistError:
    line_number: int
    line: str
    reason: str

    def __str__(self) -> str:
        return f"linha {self.line_number}: {self.reason} ({self.line!r})"


def parse_decklist_text(text: str) -> tuple[list[DecklistEntry], list[DecklistError]]:
    entries: list[DecklistEntry] = []
    errors: list[DecklistError] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or _SECTION_HEADER_RE.match(line):
            continue

        basic_energy_match = _BASIC_ENERGY_LINE_RE.match(line)
        card_match = _CARD_LINE_RE.match(line)

        if basic_energy_match:
            quantity, energy_type = basic_energy_match.groups()
            entries.append(
                DecklistEntry(
                    quantity=int(quantity),
                    name=f"{energy_type.title()} Energy",
                    set_code=None,
                    number=None,
                )
            )
        elif card_match:
            quantity, name, set_code, number = card_match.groups()
            entries.append(
                DecklistEntry(
                    quantity=int(quantity),
                    name=name,
                    set_code=set_code.upper(),
                    number=number,
                )
            )
        else:
            errors.append(DecklistError(line_number, line, "formato não reconhecido"))

    return entries, errors


def resolve_entries(
    entries: list[DecklistEntry],
    cache: CardCache,
    api_client: PokemonTcgApiClient,
) -> tuple[list[Card], list[str]]:
    cards: list[Card] = []
    errors: list[str] = []

    for entry in entries:
        if entry.set_code is None:
            energy_type = entry.name.replace(" Energy", "")
            base_card = BASIC_ENERGIES.get(energy_type)
            if base_card is None:
                errors.append(f"Energia básica desconhecida: {entry.name}")
                continue
            cards.extend([base_card] * entry.quantity)
            continue

        assert entry.number is not None
        card = cache.find(entry.name, entry.set_code, entry.number)
        if card is None:
            try:
                card = api_client.find_card(entry.name, entry.set_code, entry.number)
            except (
                Exception
            ) as exc:  # noqa: BLE001 — reportado como erro de resolução, não propagado
                errors.append(
                    f"Erro ao buscar {entry.name} ({entry.set_code} {entry.number}): {exc}"
                )
                continue
            if card is None:
                errors.append(
                    f"Carta não encontrada: {entry.quantity}x {entry.name} "
                    f"{entry.set_code} {entry.number}"
                )
                continue
            cache.save(card, entry.set_code, entry.number)

        cards.extend([card] * entry.quantity)

    return cards, errors


def load_deck(
    path: Path,
    cache: CardCache,
    api_client: PokemonTcgApiClient,
) -> tuple[list[Card], list[str]]:
    """Lê um arquivo de decklist, faz parse e resolve as cartas.

    Retorna (cartas_resolvidas, mensagens_de_erro) — erros de parse e de
    resolução são combinados numa única lista de strings legíveis.
    """
    entries, parse_errors = parse_decklist_text(path.read_text(encoding="utf-8"))
    cards, resolve_errors = resolve_entries(entries, cache, api_client)
    all_errors = [str(e) for e in parse_errors] + resolve_errors
    return cards, all_errors
