"""Formato Standard: quais cartas estão na rotação atual.

A TCGdex marca cada impressão com `legal.standard`. Pela regra oficial, uma
impressão antiga continua valendo se existir uma reimpressão legal com o
mesmo nome e texto, então a legalidade é conferida por *assinatura* (nome +
ataques + Habilidades), não pelo id da impressão.

- `standard_legal.json` (versionado, pequeno): assinaturas legais, usadas
  offline pela validação de deck;
- `data/standard_pool.json` (cache local): todas as cartas legais completas,
  usadas pela cobertura de efeitos (`tools/maintenance.py`).
"""

from __future__ import annotations

import datetime
import json
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pokemon_companion import net
from pokemon_companion.cards_db import updates
from pokemon_companion.cards_db.cache import card_from_json, card_to_json
from pokemon_companion.cards_db.models import Card, signature
from pokemon_companion.cards_db.tcgdex_client import BASE_URL, tcgdex_card_to_card
from pokemon_companion.engine.effects.cardinfo import is_basic_energy

LEGAL_FILE = Path(__file__).with_name("standard_legal.json")
POOL_FILE = Path("data/standard_pool.json")
TIMEOUT = 60
MIN_CARDS_IN_ROTATION = 20


def _get(url: str, params: dict[str, str] | None = None) -> object:
    return net.get(url, params=params, timeout=TIMEOUT, tries=5).json()


def fetch_pool(
    workers: int = 16, get: Callable[..., object] = _get
) -> tuple[list[Card], list[str]]:
    """Todas as impressões legais no Standard e as marcas de regulação vistas."""
    briefs = get(f"{BASE_URL}/cards", {"legal.standard": "true"})
    if not isinstance(briefs, list) or not briefs:
        raise RuntimeError("A TCGdex não devolveu nenhuma carta legal no Standard.")

    def detail(card_id: str) -> dict:
        data = get(f"{BASE_URL}/cards/{card_id}")
        return data if isinstance(data, dict) else {}

    with ThreadPoolExecutor(workers) as executor:
        details = [d for d in executor.map(detail, [b["id"] for b in briefs]) if d]
    marks = sorted({str(d["regulationMark"]) for d in details if d.get("regulationMark")})
    return [tcgdex_card_to_card(d) for d in details], marks


def missing_sets(
    pool: list[Card], today: datetime.date | None = None, get: Callable[..., object] = _get
) -> list[tuple[str, str, str]]:
    """Sets lançados depois do mais novo da rotação, nas séries dela: (id,
    nome, data). A TCGdex demora a marcar um set novo como legal no Standard,
    e até lá as cartas dele não entram no app. Sets mais antigos fora do pool
    não contam: são de marca que já rodou (Paldean Fates, G) ou coleções de
    reimpressão que não valem no Standard."""
    today = today or datetime.date.today()
    per_set = Counter(card.id.removeprefix("tcgdex-").rsplit("-", 1)[0] for card in pool)
    in_pool = set(per_set)

    def detail(path: str) -> dict:
        data = get(f"{BASE_URL}{path}")
        return data if isinstance(data, dict) else {}

    # sets antigos aparecem no pool só por reimpressões legais (energia
    # básica etc.): a rotação de verdade são os sets com muitas cartas nele
    rotation = sorted(set_id for set_id, n in per_set.items() if n >= MIN_CARDS_IN_ROTATION)
    known = [detail(f"/sets/{set_id}") for set_id in rotation]
    dates = [d["releaseDate"] for d in known if d.get("releaseDate")]
    if not dates:
        return []
    newest = max(dates)
    series = {d["serie"]["id"] for d in known if isinstance(d.get("serie"), dict)}
    found = []
    for serie in sorted(series):
        for brief in detail(f"/series/{serie}").get("sets", []):
            official = (brief.get("cardCount") or {}).get("official", 0)
            if brief["id"] in in_pool or not official:
                continue
            released = detail(f"/sets/{brief['id']}").get("releaseDate", "")
            if newest < released <= today.isoformat():
                found.append((brief["id"], brief.get("name", ""), released))
    return sorted(found, key=lambda item: item[2])


def save_pool(
    cards: list[Card], marks: list[str], pool_file: Path = POOL_FILE, legal_file: Path = LEGAL_FILE
) -> None:
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text(
        "[\n" + ",\n".join(card_to_json(card) for card in cards) + "\n]\n", encoding="utf-8"
    )
    legal = {
        "updated": datetime.date.today().isoformat(),
        "regulation_marks": marks,
        "signatures": sorted({signature(card) for card in cards}),
    }
    legal_file.write_text(json.dumps(legal, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def load_pool(pool_file: Path = POOL_FILE) -> list[Card]:
    raw = json.loads(pool_file.read_text(encoding="utf-8"))
    return [card_from_json(json.dumps(item)) for item in raw]


def load_legal(legal_file: Path | None = None) -> set[str] | None:
    legal_file = legal_file or updates.data_file("standard_legal.json")
    if not legal_file.exists():
        return None
    return set(json.loads(legal_file.read_text(encoding="utf-8"))["signatures"])


def illegal_cards(cards: list[Card], legal: set[str]) -> list[str]:
    """Nomes das cartas do deck fora da rotação atual (energia básica sempre vale)."""
    names = {
        card.name for card in cards if not is_basic_energy(card) and signature(card) not in legal
    }
    return sorted(names)
