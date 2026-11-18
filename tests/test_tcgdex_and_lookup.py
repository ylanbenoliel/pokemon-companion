from __future__ import annotations

import pytest
import requests

from pokemon_companion.cards_db.lookup import FallbackLookup
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.cards_db.tcgdex_client import TcgdexClient, tcgdex_card_to_card
from pokemon_companion.engine.rules import prize_count_for

DRAGAPULT_JSON = {
    "category": "Pokemon",
    "id": "sv06-130",
    "localId": "130",
    "name": "Dragapult ex",
    "image": "https://assets.tcgdex.net/en/sv/sv06/130",
    "dexId": [887],
    "hp": 320,
    "types": ["Dragon"],
    "evolveFrom": "Drakloak",
    "stage": "Stage2",
    "suffix": "ex",
    "attacks": [
        {"cost": ["Colorless"], "name": "Jet Headbutt", "damage": 70},
        {"cost": ["Fire", "Psychic"], "name": "Phantom Dive", "effect": "…", "damage": 200},
    ],
    "retreat": 1,
}


def test_tcgdex_card_conversion():
    card = tcgdex_card_to_card(DRAGAPULT_JSON)

    assert card.name == "Dragapult ex"
    assert card.supertype == Supertype.POKEMON
    assert card.subtypes == ["Stage 2", "ex"]
    assert card.hp == 320
    assert card.evolves_from == "Drakloak"
    assert [a.damage for a in card.attacks] == ["70", "200"]
    assert card.retreat_cost == ["Colorless"]
    assert card.national_pokedex_numbers == [887]
    assert card.image_url == "https://assets.tcgdex.net/en/sv/sv06/130/high.png"
    assert prize_count_for(card) == 2


def test_mega_ex_without_suffix_is_three_prizes():
    card = tcgdex_card_to_card(
        {"category": "Pokemon", "id": "me02-084", "name": "Mega Lopunny ex", "stage": "Stage1"}
    )

    assert {"ex", "Mega"} <= set(card.subtypes)
    assert prize_count_for(card) == 3


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, url: str, params=None, timeout=None) -> _FakeResponse:  # noqa: ANN001
        path = url.split("/v2/en")[1]
        self.paths.append(path)
        if path == "/cards":
            return _FakeResponse(
                [
                    {"id": "sv06-130", "localId": "130", "name": "Dragapult ex"},
                    {"id": "svp-999", "localId": "130", "name": "Dragapult ex"},
                ]
            )
        if path == "/sets/sv06":
            return _FakeResponse({"abbreviation": {"official": "TWM"}})
        if path == "/sets/svp":
            return _FakeResponse({"abbreviation": {"official": "SVP"}})
        if path == "/cards/sv06-130":
            return _FakeResponse(DRAGAPULT_JSON)
        raise AssertionError(path)


def test_tcgdex_client_matches_by_set_code_and_number():
    session = _FakeSession()
    client = TcgdexClient(session=session)  # type: ignore[arg-type]

    card = client.find_card("Dragapult ex", "TWM", "130")

    assert card is not None and card.id == "tcgdex-sv06-130"
    assert session.paths.count("/sets/sv06") == 1  # sigla do set é cacheada


class _Down:
    def __init__(self) -> None:
        self.calls = 0

    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        self.calls += 1
        raise requests.HTTPError("500 Server Error")


class _Up:
    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        return Card(id=f"{name}-{number}", name=name, supertype=Supertype.POKEMON)


def test_fallback_skips_dead_source_after_first_failure():
    down = _Down()
    lookup = FallbackLookup([down, _Up()])

    assert lookup.find_card("Dreepy", "TWM", "128") is not None
    assert lookup.find_card("Drakloak", "TWM", "129") is not None
    assert down.calls == 1


def test_fallback_raises_when_every_source_is_down():
    lookup = FallbackLookup([_Down(), _Down()])

    with pytest.raises(RuntimeError):
        lookup.find_card("Dreepy", "TWM", "128")


def test_net_get_retries_server_errors_and_connection_drops(monkeypatch):
    import requests

    from pokemon_companion import net

    monkeypatch.setattr(net.time, "sleep", lambda _: None)
    answers: list[object] = [requests.ConnectionError(), 503, 200]

    def get(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        response = _FakeResponse({"ok": True})
        response.status_code = answer
        return response

    class Session:
        pass

    session = Session()
    session.get = get  # type: ignore[attr-defined]
    assert net.get("u", session=session).json() == {"ok": True}
    assert answers == []


def test_pokemontcg_stadium_text_compiles_like_tcgdex():
    from pokemon_companion.cards_db.models import Card, Supertype
    from pokemon_companion.engine.effects.coverage import unimplemented

    reminder = (
        "You may play only 1 Stadium card during your turn. Put it next to the Active Spot, and "
        "discard it if another Stadium comes into play. A Stadium with the same name can't be "
        "played."
    )
    garden = Card(
        id="me1-122",
        name="Mystery Garden",
        supertype=Supertype.TRAINER,
        subtypes=["Stadium"],
        rules=[
            "Once during each player's turn, that player may discard an Energy card from their "
            "hand in order to draw cards until they have as many cards in their hand as they "
            "have Psychic Pokémon in play.",
            reminder,
        ],
    )
    assert unimplemented(garden) == []
