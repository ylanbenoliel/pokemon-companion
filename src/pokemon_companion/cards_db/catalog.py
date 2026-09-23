"""Catálogo do Standard para o construtor de deck: uma entrada por carta
distinta (mesmo nome, ataques e Habilidades = mesma carta para o jogo), com
o código oficial do set e o número no formato das listas do Limitless
("PFL 13"), para o deck salvo abrir em qualquer lugar.

O catálogo vai no pacote (`standard_catalog.json`, ~1 MB) — o construtor
funciona sem internet — e é refeito por `tools/maintenance.py --refresh`
junto com o pool legal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from pokemon_companion.cards_db.cache import card_from_json, card_to_json
from pokemon_companion.cards_db.models import Card
from pokemon_companion.cards_db.standard import signature
from pokemon_companion.engine.effects.cardinfo import is_basic_energy

CATALOG_FILE = Path(__file__).with_name("standard_catalog.json")
SETS_URL = "https://api.tcgdex.net/v2/en/sets/{}"
_ID_RE = re.compile(r"tcgdex-(.+)-([^-]+)$")
_BASIC_ALIAS = re.compile(r"Basic \w+ Energy")
#: sets preferidos para a impressão representativa de energias básicas
_ENERGY_SETS = ("MEE", "SVE")


@dataclass(frozen=True)
class CatalogEntry:
    card: Card
    set_code: str
    number: str

    @property
    def line_code(self) -> str:
        return f"{self.set_code} {self.number}"


def split_id(card_id: str) -> tuple[str, str] | None:
    """ "tcgdex-me02-013" → ("me02", "13")."""
    match = _ID_RE.fullmatch(card_id)
    if not match:
        return None
    number = match.group(2)
    if number.isdigit():
        number = number.lstrip("0") or "0"
    return match.group(1), number


def fetch_set_codes(set_ids: set[str]) -> dict[str, str]:
    """Abreviação oficial de cada set na TCGdex ("me02" → "PFL")."""
    codes: dict[str, str] = {}
    for set_id in sorted(set_ids):
        response = requests.get(SETS_URL.format(set_id), timeout=20)
        if not response.ok:
            continue
        official = (response.json().get("abbreviation") or {}).get("official")
        if official:
            codes[set_id] = official
    return codes


def _preference(entry: CatalogEntry, set_order: dict[str, int]) -> tuple[int, int, int]:
    """Impressão representativa: energia básica do MEE/SVE; senão o set mais
    novo, com o menor número (a arte comum, não a secreta)."""
    energy_rank = 0
    if is_basic_energy(entry.card):
        energy_rank = 0 if entry.set_code in _ENERGY_SETS else 1
    number = int(entry.number) if entry.number.isdigit() else 9999
    return (energy_rank, -set_order.get(entry.set_code, -1), number)


def build_catalog(pool: list[Card], set_codes: dict[str, str]) -> list[CatalogEntry]:
    by_signature: dict[str, list[CatalogEntry]] = {}
    order: dict[str, int] = {}
    for card in pool:
        if _BASIC_ALIAS.fullmatch(card.name):
            continue  # "Basic Fire Energy" é a mesma "Fire Energy" (que também está no pool)
        parts = split_id(card.id)
        if parts is None or parts[0] not in set_codes:
            continue
        set_id, number = parts
        code = set_codes[set_id]
        order.setdefault(code, _set_age(set_id))
        by_signature.setdefault(signature(card), []).append(CatalogEntry(card, code, number))
    chosen = [
        min(entries, key=lambda e: _preference(e, order)) for entries in by_signature.values()
    ]
    return sorted(chosen, key=lambda e: (str(e.card.supertype), e.card.name))


def _set_age(set_id: str) -> int:
    """Ordem aproximada de lançamento: Mega Evolução (me) > Escarlate e
    Violeta (sv) > o resto; dentro da série, pelo número."""
    series = 2 if set_id.startswith("me") else 1 if set_id.startswith("sv") else 0
    digits = re.findall(r"\d+(?:\.\d+)?", set_id)
    return series * 1000 + int(float(digits[0]) * 10 if digits else 0)


def save_catalog(entries: list[CatalogEntry], path: Path = CATALOG_FILE) -> None:
    rows = [
        {"set": e.set_code, "number": e.number, "card": json.loads(card_to_json(e.card))}
        for e in entries
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_catalog(path: Path = CATALOG_FILE) -> list[CatalogEntry]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        CatalogEntry(card_from_json(json.dumps(row["card"])), row["set"], row["number"])
        for row in rows
    ]
