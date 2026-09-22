"""Legalidade no Standard: assinaturas, validação de deck e download do pool
(com a rede substituída por dados fixos)."""

from __future__ import annotations

import dataclasses

import pytest

from pokemon_companion.cards_db import standard
from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.deck_rules import validate_deck
from pokemon_companion.cards_db.models import Ability, Card, Supertype

from .conftest import make_basic_pokemon, make_energy


def _trainer(name: str) -> Card:
    return Card(id=f"t-{name}", name=name, supertype=Supertype.TRAINER, subtypes=["Item"])


def test_signature_separates_reprints_with_different_text():
    pikachu = make_basic_pokemon("Pikachu", 60, "Lightning", attack_name="Thunder Jolt")
    other = make_basic_pokemon("Pikachu", 60, "Lightning", attack_name="Gnaw")
    with_ability = dataclasses.replace(pikachu, abilities=[Ability(name="Static")])

    assert standard.signature(pikachu) == "Pikachu|Thunder Jolt"
    assert len({standard.signature(c) for c in (pikachu, other, with_ability)}) == 3
    assert standard.signature(_trainer("Ultra Ball")) == "Ultra Ball"


def test_illegal_cards_ignores_basic_energy_and_older_printings_of_legal_text():
    legal = {"Pikachu|Thunder Jolt", "Ultra Ball"}
    old_print = dataclasses.replace(
        make_basic_pokemon("Pikachu", 60, "Lightning", attack_name="Thunder Jolt"), id="old-set-1"
    )
    rotated = make_basic_pokemon("Miraidon ex", 220, "Lightning", attack_name="Photon Blaster")
    deck = [old_print, rotated, _trainer("Ultra Ball"), make_energy("Fire Energy", "Fire")]

    assert standard.illegal_cards(deck, legal) == ["Miraidon ex"]


def test_validate_deck_warns_about_rotated_cards_only_when_legality_is_known():
    rotated = make_basic_pokemon("Miraidon ex", 220, "Lightning", attack_name="Photon Blaster")
    deck = [rotated] * 4 + [BASIC_ENERGIES["Fire"]] * 56

    assert validate_deck(deck) == []
    assert validate_deck(deck, legal=set()) == ["fora da rotação do Standard: Miraidon ex"]


def test_fetch_pool_converts_every_legal_card_and_collects_marks(tmp_path):
    details = {
        "sv06-130": {
            "id": "sv06-130",
            "name": "Dragapult ex",
            "category": "Pokemon",
            "stage": "Stage2",
            "regulationMark": "H",
            "attacks": [{"name": "Phantom Dive", "damage": 200}],
        },
        "me01-100": {
            "id": "me01-100",
            "name": "Boss's Orders",
            "category": "Trainer",
            "trainerType": "Supporter",
            "regulationMark": "I",
        },
    }

    def fake_get(url: str, params: dict[str, str] | None = None) -> object:
        if params == {"legal.standard": "true"}:
            return [{"id": card_id} for card_id in details]
        return details[url.rsplit("/", 1)[1]]

    cards, marks = standard.fetch_pool(workers=2, get=fake_get)
    assert [c.name for c in cards] == ["Dragapult ex", "Boss's Orders"]
    assert marks == ["H", "I"]

    pool, legal = tmp_path / "pool.json", tmp_path / "legal.json"
    standard.save_pool(cards, marks, pool_file=pool, legal_file=legal)
    assert [c.name for c in standard.load_pool(pool)] == ["Dragapult ex", "Boss's Orders"]
    assert standard.load_legal(legal) == {"Dragapult ex|Phantom Dive", "Boss's Orders"}


def test_fetch_pool_fails_loudly_when_the_source_returns_nothing():
    with pytest.raises(RuntimeError):
        standard.fetch_pool(get=lambda url, params=None: [])


def test_missing_legal_file_disables_the_check(tmp_path):
    assert standard.load_legal(tmp_path / "nope.json") is None
