from __future__ import annotations

import requests

from pokemon_companion.cards_db.api_client import PokemonTcgApiClient, api_card_to_card


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def get(self, url: str, params: dict, headers: dict, timeout: int) -> FakeResponse:
        self.calls += 1
        return self._responses.pop(0)


def test_api_card_to_card_converts_raw_json(sample_api_card_json):
    card = api_card_to_card(sample_api_card_json)

    assert card.name == "Charmander"
    assert card.hp == 60
    assert card.types == ["Fire"]
    assert card.attacks[0].name == "Ember"
    assert card.weaknesses[0].energy_type == "Water"
    assert card.retreat_cost == ["Colorless"]
    assert card.image_url == "https://images.pokemontcg.io/svi/26_hires.png"


def test_find_card_returns_none_when_no_results():
    session = FakeSession([FakeResponse(200, {"data": []})])
    client = PokemonTcgApiClient(session=session, max_retries=1)

    assert client.find_card("Inexistente", "XXX", "1") is None


def test_find_card_parses_first_result(sample_api_card_json):
    session = FakeSession([FakeResponse(200, {"data": [sample_api_card_json]})])
    client = PokemonTcgApiClient(session=session, max_retries=1)

    card = client.find_card("Charmander", "SVI", "26")

    assert card is not None
    assert card.name == "Charmander"


def test_find_card_retries_on_rate_limit_then_succeeds():
    session = FakeSession([FakeResponse(429), FakeResponse(200, {"data": []})])
    client = PokemonTcgApiClient(session=session, max_retries=2, backoff_seconds=0.001)

    result = client.find_card("X", "XXX", "1")

    assert result is None
    assert session.calls == 2
