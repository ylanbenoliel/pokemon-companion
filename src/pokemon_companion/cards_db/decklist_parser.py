"""Parser de decklists no formato Limitless/PTCGO.

Aceita os dois estilos de cabeçalho de seção e ignora comentários (`#`):

    Pokémon (19)            ou    Pokémon: 19
    4 Dreepy TWM 128
    Trainer (33)
    4 Buddy-Buddy Poffin TEF 144
    Energy (8)
    3 Fire Energy MEE 2     (ou "8 Fire Energy", sem set/número)

Duas etapas deliberadamente separadas:
- `parse_decklist_text` é pura (sem I/O), fácil de testar.
- `resolve_entries` resolve cada linha em um `Card` real via cache/API.

Energias básicas são resolvidas localmente (idênticas em qualquer edição).
Cartas de Treinador são buscadas na API só para ter imagem, tipo (Item,
Apoiador...) e texto na tela — o motor ainda não aplica seus efeitos. Se a
busca falhar, viram uma carta local só com o nome, sem erro: a partida não
depende dos dados delas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.lookup import CardLookup
from pokemon_companion.cards_db.models import Card, Supertype

_SECTION_HEADER_RE = re.compile(
    r"^(Pokémon|Pokemon|Trainer|Energy)\s*(:\s*\d+|\(\s*\d+\s*\))?$", re.IGNORECASE
)
_CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s+([A-Za-z0-9-]{2,8})\s+([A-Za-z]*\d+[A-Za-z]?)$")
_BASIC_ENERGY_LINE_RE = re.compile(r"^(\d+)\s+(\w+)\s+Energy$", re.IGNORECASE)
_BASIC_ENERGY_NAME_RE = re.compile(r"^(?:Basic\s+)?\{?([A-Za-z]+)\}?\s+Energy$", re.IGNORECASE)
_ENERGY_SYMBOLS = {
    "G": "Grass",
    "R": "Fire",
    "W": "Water",
    "L": "Lightning",
    "P": "Psychic",
    "F": "Fighting",
    "D": "Darkness",
    "M": "Metal",
    "Y": "Fairy",
}


@dataclass(frozen=True)
class DecklistEntry:
    quantity: int
    name: str
    set_code: str | None  # None => resolvida localmente, sem API
    number: str | None
    section: str | None = None


@dataclass(frozen=True)
class DecklistError:
    line_number: int
    line: str
    reason: str

    def __str__(self) -> str:
        return f"linha {self.line_number}: {self.reason} ({self.line!r})"


def _basic_energy_type(name: str) -> str | None:
    match = _BASIC_ENERGY_NAME_RE.match(name.strip())
    if match is None:
        return None
    raw = match.group(1)
    energy_type = _ENERGY_SYMBOLS.get(raw.upper(), raw.title())
    return energy_type if energy_type in BASIC_ENERGIES else None


def parse_decklist_text(text: str) -> tuple[list[DecklistEntry], list[DecklistError]]:
    entries: list[DecklistEntry] = []
    errors: list[DecklistError] = []
    section: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        header = _SECTION_HEADER_RE.match(line)
        if header:
            section = (
                "Pokémon" if header.group(1).lower().startswith("pok") else header.group(1).title()
            )
            continue

        basic_energy_match = _BASIC_ENERGY_LINE_RE.match(line)
        card_match = _CARD_LINE_RE.match(line)

        if basic_energy_match and _basic_energy_type(f"{basic_energy_match.group(2)} Energy"):
            quantity, energy_type = basic_energy_match.groups()
            entries.append(
                DecklistEntry(int(quantity), f"{energy_type.title()} Energy", None, None, "Energy")
            )
        elif card_match:
            quantity, name, set_code, number = card_match.groups()
            energy_type = _basic_energy_type(name)
            if energy_type is not None:
                entries.append(
                    DecklistEntry(int(quantity), f"{energy_type} Energy", None, None, "Energy")
                )
            else:
                entries.append(
                    DecklistEntry(int(quantity), name, set_code.upper(), number, section)
                )
        else:
            errors.append(DecklistError(line_number, line, "formato não reconhecido"))

    return entries, errors


def _local_trainer(entry: DecklistEntry) -> Card:
    return Card(
        id=f"trainer-{entry.set_code}-{entry.number}-{entry.name.lower()}",
        name=entry.name,
        supertype=Supertype.TRAINER,
    )


def resolve_entries(
    entries: list[DecklistEntry],
    cache: CardCache,
    api_client: CardLookup,
) -> tuple[list[Card], list[str]]:
    cards: list[Card] = []
    errors: list[str] = []

    for entry in entries:
        if entry.set_code is None:
            base_card = BASIC_ENERGIES.get(entry.name.replace(" Energy", ""))
            if base_card is None:
                errors.append(f"Energia básica desconhecida: {entry.name}")
                continue
            cards.extend([base_card] * entry.quantity)
            continue

        assert entry.number is not None
        is_trainer = entry.section == "Trainer"
        card = cache.find(entry.name, entry.set_code, entry.number)
        if card is None:
            try:
                card = api_client.find_card(entry.name, entry.set_code, entry.number)
            # Qualquer falha vira um erro de resolução reportado, não uma exceção.
            except Exception as exc:  # noqa: BLE001
                if is_trainer:
                    cards.extend([_local_trainer(entry)] * entry.quantity)
                    continue
                errors.append(
                    f"Erro ao buscar {entry.name} ({entry.set_code} {entry.number}): {exc}"
                )
                continue
            if card is None and is_trainer:
                cards.extend([_local_trainer(entry)] * entry.quantity)
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
    api_client: CardLookup,
) -> tuple[list[Card], list[str]]:
    """Lê um arquivo de decklist, faz parse e resolve as cartas.

    Retorna (cartas_resolvidas, mensagens_de_erro) — erros de parse e de
    resolução são combinados numa única lista de strings legíveis.
    """
    entries, parse_errors = parse_decklist_text(path.read_text(encoding="utf-8"))
    cards, resolve_errors = resolve_entries(entries, cache, api_client)
    all_errors = [str(e) for e in parse_errors] + resolve_errors
    return cards, all_errors
