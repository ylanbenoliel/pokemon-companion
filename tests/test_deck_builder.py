"""Construtor de deck: busca no catálogo, regras ao vivo, salvar e reabrir."""

from __future__ import annotations

import pytest

from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.catalog import load_catalog, split_id
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.ui.deck_builder import DeckBuilderDialog, deck_text, slugify


class _Offline:
    def find_card(self, name, set_code, number):
        return None


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture
def builder(qtbot, tmp_path, catalog):
    dialog = DeckBuilderDialog(
        catalog=catalog,
        folder=tmp_path / "decks",
        cache_factory=lambda: CardCache(tmp_path / "cache.db"),
    )
    qtbot.addWidget(dialog)
    return dialog


def _entry(catalog, name, attack=None):
    return next(
        e
        for e in catalog
        if e.card.name == name and (attack is None or attack in [a.name for a in e.card.attacks])
    )


def test_catalog_has_the_standard_with_limitless_codes(catalog):
    assert len(catalog) > 1500
    charizard = _entry(catalog, "Mega Charizard X ex")
    assert charizard.line_code == "PFL 13"
    assert split_id("tcgdex-me02-013") == ("me02", "13")
    assert split_id("tcgdex-sv10.5w-084") == ("sv10.5w", "84")


def test_search_ignores_accents_and_finds_attack_text(builder):
    names = {e.card.name for e in builder.matches("pokemon ex", None, None)}
    assert names  # "Pokémon ex" no texto das regras
    by_attack = {e.card.name for e in builder.matches("inferno x", None, None)}
    assert "Mega Charizard X ex" in by_attack


def test_copy_limits_are_enforced_while_building(builder, catalog):
    candy = _entry(catalog, "Rare Candy")
    assert builder.add(candy, 6) == 4
    assert "No máximo 4" in builder.status.text()
    fire = _entry(catalog, "Fire Energy")
    assert builder.add(fire, 56) == 56  # energia básica não tem limite
    assert builder.add(fire, 1) == 0  # 60 cartas


def test_live_validation_lists_what_is_missing(builder, catalog):
    builder.add(_entry(catalog, "Rare Candy"), 4)
    problems = builder.problems_now()
    assert any("4 cartas" in p for p in problems)
    assert any("Básico" in p for p in problems)


def test_saved_deck_reopens_offline(builder, catalog, tmp_path):
    builder.name.setText("Fogo Teste")
    builder.add(_entry(catalog, "Charmander", "Live Coal"), 4)
    builder.add(_entry(catalog, "Mega Charizard X ex"), 2)
    builder.add(_entry(catalog, "Fire Energy"), 10)
    path = builder.save()

    assert path is not None and path.name == "fogo_teste.txt"
    text = path.read_text()
    assert "4 Charmander PFL 11" in text and "10 Fire Energy" in text
    with CardCache(tmp_path / "cache.db") as cache:
        cards, errors = load_deck(path, cache, _Offline())
    assert errors == [] and len(cards) == 16


def test_open_existing_deck_into_the_builder(builder, catalog, tmp_path):
    builder.name.setText("Base")
    builder.add(_entry(catalog, "Charmander", "Live Coal"), 3)
    builder.add(_entry(catalog, "Fire Energy"), 5)
    path = builder.save()
    builder.rows.clear()

    builder.load_deck_file(path)

    assert sum(row.count for row in builder.rows) == 8


def test_deck_text_and_slug():
    assert slugify("Meu Dragapult — v2!") == "meu_dragapult_v2"
    assert deck_text("X", []).startswith("# X")
