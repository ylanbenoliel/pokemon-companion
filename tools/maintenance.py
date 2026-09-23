"""Manutenção das regras: mantém o motor em dia com a rotação do Standard.

    uv run python tools/maintenance.py            # usa o pool já baixado
    uv run python tools/maintenance.py --refresh  # baixa pool legal + meta atual

1. baixa todas as cartas legais no Standard (TCGdex) e regrava as
   assinaturas de legalidade (`cards_db/standard_legal.json`);
2. atualiza os decks do meta (Limitless, `src/pokemon_companion/decks/top`);
3. mede a cobertura de efeitos no pool inteiro e nos decks do meta, e
   procura cartas fora da rotação nos decks.

Grava `data/maintenance_report.md`. Sai com código 1 se algum deck do meta
tiver texto sem efeito ou carta fora da rotação — o sinal para agir.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from collections import Counter
from pathlib import Path

from effect_coverage import missing_effects

from pokemon_companion.cards_db import catalog, standard
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.models import Card
from pokemon_companion.deck_loading import make_lookup
from pokemon_companion.paths import BUNDLED_DECKS

META_DIR = BUNDLED_DECKS / "top"
REPORT = Path("data/maintenance_report.md")
TOOLS = Path(__file__).parent


def refresh() -> None:
    print("Baixando o pool legal do Standard (TCGdex)…")
    cards, marks = standard.fetch_pool()
    standard.save_pool(cards, marks)
    print(f"  {len(cards)} impressões, marcas {', '.join(marks)}")
    set_ids = {parts[0] for card in cards if (parts := catalog.split_id(card.id))}
    entries = catalog.build_catalog(cards, catalog.fetch_set_codes(set_ids))
    catalog.save_catalog(entries)
    print(f"  catálogo do construtor de deck: {len(entries)} cartas distintas")
    print("Atualizando os decks do meta (Limitless)…")
    subprocess.run(
        [sys.executable, str(TOOLS / "fetch_top_decks.py"), "--top", "100", "--clean"],
        check=True,
    )


def meta_decks() -> dict[str, list[Card]]:
    decks: dict[str, list[Card]] = {}
    with CardCache() as cache:
        lookup = make_lookup()
        for path in sorted(META_DIR.glob("*.txt")):
            decks[path.stem] = load_deck(path, cache, lookup)[0]
    if not decks:
        raise SystemExit(f"Nenhum deck em {META_DIR}: o download do meta falhou?")
    return decks


def report(pool: list[Card], decks: dict[str, list[Card]], legal: set[str]) -> tuple[str, bool]:
    pool_total, pool_missing = missing_effects(pool)
    meta_cards = [card for cards in decks.values() for card in cards]
    _, meta_missing = missing_effects(meta_cards)
    illegal = {name: standard.illegal_cards(cards, legal) for name, cards in decks.items()}
    illegal = {name: cards for name, cards in illegal.items() if cards}

    implemented = sum(pool_total.values()) - sum(pool_missing.values())
    lines = [
        f"# Manutenção das regras — {datetime.date.today().isoformat()}",
        "",
        f"- Pool legal: {len(pool)} impressões; cobertura {implemented}/"
        f"{sum(pool_total.values())} textos-impressão ({len(pool_missing)} textos distintos faltando)",
        f"- Decks do meta: {len(decks)}; textos faltando: {len(meta_missing)}; "
        f"decks com carta fora da rotação: {len(illegal)}",
        "",
        "## Faltando nos decks do meta (prioridade máxima)",
        *[f"- {count}× {name}" for name, count in meta_missing.most_common()],
        "",
        "## Cartas fora da rotação nos decks do meta",
        *[f"- {deck}: {', '.join(cards)}" for deck, cards in illegal.items()],
        "",
        "## Faltando no pool legal (por impressões)",
        *[f"- {count}× {name}" for name, count in _by_category(pool_missing)],
    ]
    return "\n".join(lines) + "\n", bool(meta_missing or illegal)


def _by_category(missing: Counter[str]) -> list[tuple[str, int]]:
    return sorted(missing.items(), key=lambda item: (item[0][:7], -item[1], item[0]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="baixa pool e meta de novo")
    args = parser.parse_args()
    if args.refresh or not standard.POOL_FILE.exists():
        refresh()
    legal = standard.load_legal()
    assert legal is not None
    text, needs_work = report(standard.load_pool(), meta_decks(), legal)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text.split("\n## Faltando no pool")[0])
    print(f"Relatório completo: {REPORT}")
    sys.exit(1 if needs_work else 0)


if __name__ == "__main__":
    main()
