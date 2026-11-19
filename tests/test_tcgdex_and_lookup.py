from __future__ import annotations

import pytest
import requests

from pokemon_companion.cards_db.lookup import FallbackLookup
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.cards_db.tcgdex_client import TcgdexClient, tcgdex_card_to_card
from pokemon_companion.engine.effects.cardinfo import has_rule_box
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


def test_gx_and_ex_names_use_the_printed_hyphen_and_are_found_either_way():
    card = tcgdex_card_to_card({"id": "sm3.5-53", "name": "Zoroark GX", "category": "Pokemon"})
    assert card.name == "Zoroark-GX"
    assert tcgdex_card_to_card({"id": "x", "name": "Charizard ex"}).name == "Charizard ex"

    asked = []

    class Client(TcgdexClient):
        def _get(self, path, params=None):
            asked.append(params and params["name"])
            if path == "/cards":
                hit = params["name"] == "Zoroark GX"
                return [{"id": "sm3.5-53", "localId": "53"}] if hit else []
            if path.startswith("/sets/"):
                return {"abbreviation": {"official": "SLG"}}
            return {"id": "sm3.5-53", "name": "Zoroark GX", "category": "Pokemon"}

    found = Client().find_card("Zoroark-GX", "SLG", "53")
    assert found is not None and found.name == "Zoroark-GX"
    assert asked[:2] == ["Zoroark-GX", "Zoroark GX"]
    assert tcgdex_card_to_card({"id": "x", "name": "Battle Compressor Team Flare Gear"}).name == (
        "Battle Compressor"
    )


def test_promo_numbers_match_without_the_letter_prefix_but_galleries_stay_exact():
    from pokemon_companion.cards_db.catalog import split_id
    from pokemon_companion.cards_db.tcgdex_client import set_code

    class Client(TcgdexClient):
        def _get(self, path, params=None):
            if path == "/cards":
                return [
                    {"id": "smp-SM45", "localId": "SM45"},
                    {"id": "swsh9-TG05", "localId": "TG05"},
                ]
            if path.startswith("/sets/"):
                return {"abbreviation": {"official": path.rsplit("/", 1)[1].upper()}}
            return {"id": path.rsplit("/", 1)[1], "name": "Tapu Lele GX", "category": "Pokemon"}

    found = Client().find_card("Tapu Lele-GX", "SMP", "45")
    assert found is not None and found.id == "tcgdex-smp-SM45"
    assert Client().find_card("Tapu Lele-GX", "SWSH9", "5") is None
    assert split_id("tcgdex-smp-SM45") == ("smp", "45")
    assert split_id("tcgdex-swsh9-TG05") == ("swsh9", "TG05")
    assert set_code("swshp", {"abbreviation": None}) == "SWSHP"
    assert set_code("sma", {}) is None


def test_expanded_prize_counts_from_tcgdex_subtypes():
    def card(name: str, suffix: str | None = None, stage: str = "Basic") -> Card:
        data = {"id": "x-1", "name": name, "category": "Pokemon", "stage": stage}
        return tcgdex_card_to_card({**data, "suffix": suffix} if suffix else data)

    assert prize_count_for(card("Pikachu & Zekrom GX", "TAG TEAM-GX")) == 3
    assert prize_count_for(card("Pikachu V-UNION", "V-UNION")) == 3
    assert prize_count_for(card("Greninja BREAK", "BREAK", "BREAK")) == 1
    assert prize_count_for(card("M Venusaur EX", stage="MEGA")) == 2  # Mega de XY: 2
    assert prize_count_for(card("Lunala ◇")) == 1
    assert has_rule_box(card("Lunala ◇")) and has_rule_box(card("Greninja BREAK", "BREAK"))
