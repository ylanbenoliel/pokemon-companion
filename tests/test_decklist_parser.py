from __future__ import annotations

from pokemon_companion.cards_db.decklist_parser import (
    DecklistEntry,
    parse_decklist_text,
    resolve_entries,
)
from pokemon_companion.cards_db.models import Card


class FakeCache:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], Card] = {}

    def find(self, name: str, set_code: str, number: str) -> Card | None:
        return self._store.get((name, set_code, number))

    def save(self, card: Card, set_code: str, number: str) -> None:
        self._store[(card.name, set_code, number)] = card


class FakeApiClient:
    def __init__(self, cards: dict[tuple[str, str, str], Card]) -> None:
        self._cards = cards
        self.calls = 0

    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        self.calls += 1
        return self._cards.get((name, set_code, number))


def test_parse_decklist_text_handles_sections_and_basic_energy():
    text = """
    Pokémon: 1
    4 Charmander SVI 26
    Energy: 1
    8 Fire Energy
    """

    entries, errors = parse_decklist_text(text)

    assert not errors
    assert entries == [
        DecklistEntry(4, "Charmander", "SVI", "26", "Pokémon"),
        DecklistEntry(8, "Fire Energy", None, None, "Energy"),
    ]


def test_parse_limitless_export_format():
    text = """# Dragapult by Alguém – Limitless
    Pokémon (2)
    4 Dreepy TWM 128
    Trainer (1)
    4 Buddy-Buddy Poffin TEF 144
    Energy (2)
    3 Fire Energy MEE 2
    2 Basic {P} Energy SVE 5
    """

    entries, errors = parse_decklist_text(text)

    assert not errors
    assert entries == [
        DecklistEntry(4, "Dreepy", "TWM", "128", "Pokémon"),
        DecklistEntry(4, "Buddy-Buddy Poffin", "TEF", "144", "Trainer"),
        DecklistEntry(3, "Fire Energy", None, None, "Energy"),
        DecklistEntry(2, "Psychic Energy", None, None, "Energy"),
    ]


def test_trainers_resolve_locally_without_api(tmp_path):
    entry = DecklistEntry(4, "Ultra Ball", "MEG", "131", "Trainer")
    api = FakeApiClient({})

    cards, errors = resolve_entries([entry], FakeCache(), api)

    assert not errors
    assert len(cards) == 4
    assert cards[0].supertype.value == "Trainer"
    assert api.calls == 0


def test_parse_decklist_text_reports_unrecognized_line():
    text = "isso não é uma linha de decklist válida"

    entries, errors = parse_decklist_text(text)

    assert not entries
    assert len(errors) == 1
    assert errors[0].line_number == 1


def test_resolve_entries_uses_cache_before_hitting_api(charmander):
    entry = DecklistEntry(quantity=4, name="Charmander", set_code="SVI", number="26")
    api = FakeApiClient({("Charmander", "SVI", "26"): charmander})
    cache = FakeCache()

    cards, errors = resolve_entries([entry], cache, api)
    assert not errors
    assert len(cards) == 4
    assert api.calls == 1

    cards_again, errors_again = resolve_entries([entry], cache, api)
    assert not errors_again
    assert len(cards_again) == 4
    assert api.calls == 1  # segunda resolução veio do cache, sem nova chamada


def test_resolve_entries_reports_card_not_found():
    entry = DecklistEntry(quantity=1, name="Carta Inexistente", set_code="XXX", number="999")
    cards, errors = resolve_entries([entry], FakeCache(), FakeApiClient({}))

    assert not cards
    assert len(errors) == 1
    assert "não encontrada" in errors[0]


def test_resolve_entries_handles_basic_energy_without_api_call():
    entry = DecklistEntry(quantity=8, name="Fire Energy", set_code=None, number=None)
    api = FakeApiClient({})

    cards, errors = resolve_entries([entry], FakeCache(), api)

    assert not errors
    assert len(cards) == 8
    assert all(c.name == "Fire Energy" for c in cards)
    assert api.calls == 0
