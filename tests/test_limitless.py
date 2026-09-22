"""Busca e download de decklists do limitlesstcg.com — parsing puro (sem
rede: as páginas são passadas como texto) e uso de `fetch` isolado por
monkeypatch para as funções que fazem requisições."""

from __future__ import annotations

import pytest

from pokemon_companion.cards_db import limitless
from pokemon_companion.cards_db.limitless import Archetype

ARCHETYPES_PAGE = """
<table>
<tr><td>1</td><td><a href="/decks/45">Dragapult ex</a></td><td>36.2%</td></tr>
<tr><td>2</td><td><a href="/decks/12">Gardevoir ex</a></td><td>9.4%</td></tr>
</table>
"""

DECK_PAGE_WITH_LIST_LINK = '<a href="/decks/list/789">Melhor lista</a>'

DECKLIST_PAGE = (
    "<title>Dragapult ex – Limitless</title>"
    "<div>data-text-decklist</div>"
    '<div class="decklist-column-heading">Pokémon (4)</div>'
    '<span data-set="TWM" data-number="128"></span>'
    '<span class="card-count">4</span><span class="card-name">Dreepy</span>'
    '<div class="decklist-column-heading">Energy (2)</div>'
    '<span data-set="SVE" data-number="5"></span>'
    '<span class="card-count">2</span><span class="card-name">Fire Energy</span>'
)


def test_parse_archetypes_reads_rank_id_name_and_share():
    archetypes = limitless.parse_archetypes(ARCHETYPES_PAGE)

    assert archetypes == [
        Archetype(1, "45", "Dragapult ex", 36.2),
        Archetype(2, "12", "Gardevoir ex", 9.4),
    ]


def test_archetype_label_mentions_rank_and_share():
    archetype = Archetype(1, "45", "Dragapult ex", 36.2)

    assert archetype.label == "Dragapult ex — #1 do meta (36.2% dos pontos)"


def test_search_archetypes_matches_words_ignoring_case_and_accents():
    found = limitless.search_archetypes("dragapult", page=ARCHETYPES_PAGE)

    assert [a.name for a in found] == ["Dragapult ex"]


def test_search_archetypes_without_query_returns_everything():
    found = limitless.search_archetypes("", page=ARCHETYPES_PAGE)

    assert len(found) == 2


def test_search_archetypes_no_match_returns_empty():
    assert limitless.search_archetypes("zoroark", page=ARCHETYPES_PAGE) == []


def test_first_list_id_finds_the_link():
    assert limitless.first_list_id(DECK_PAGE_WITH_LIST_LINK) == "789"


def test_first_list_id_missing_returns_none():
    assert limitless.first_list_id("<p>nada aqui</p>") is None


def test_parse_decklist_builds_title_text_and_total():
    title, text, total = limitless.parse_decklist(DECKLIST_PAGE)

    assert title == "Dragapult ex – Limitless"
    assert text == "Pokémon (4)\n4 Dreepy TWM 128\nEnergy (2)\n2 Fire Energy SVE 5"
    assert total == 6


def test_parse_decklist_without_title_falls_back():
    title, _, _ = limitless.parse_decklist(DECKLIST_PAGE.replace("<title>", "<x>", 1))

    assert title == "Deck"


def test_archetype_decklist_follows_deck_page_then_list_page(monkeypatch):
    requested: list[str] = []

    def fake_fetch(url: str) -> str:
        requested.append(url)
        return DECK_PAGE_WITH_LIST_LINK if "/decks/45" in url else DECKLIST_PAGE

    monkeypatch.setattr(limitless, "fetch", fake_fetch)

    title, _, total = limitless.archetype_decklist(Archetype(1, "45", "Dragapult ex", 36.2))

    assert requested == [f"{limitless.BASE}/decks/45", f"{limitless.BASE}/decks/list/789"]
    assert title == "Dragapult ex – Limitless"
    assert total == 6


def test_archetype_decklist_raises_without_a_published_list(monkeypatch):
    monkeypatch.setattr(limitless, "fetch", lambda url: "<p>vazio</p>")

    with pytest.raises(ValueError):
        limitless.archetype_decklist(Archetype(1, "45", "Dragapult ex", 36.2))


def test_deck_text_from_url_accepts_a_list_link_directly(monkeypatch):
    monkeypatch.setattr(limitless, "fetch", lambda url: DECKLIST_PAGE)

    title, _, total = limitless.deck_text_from_url(f"{limitless.BASE}/decks/list/789")

    assert title == "Dragapult ex – Limitless"
    assert total == 6


def test_deck_text_from_url_resolves_an_archetype_link_first(monkeypatch):
    requested: list[str] = []

    def fake_fetch(url: str) -> str:
        requested.append(url)
        return DECK_PAGE_WITH_LIST_LINK if url.endswith("/decks/45") else DECKLIST_PAGE

    monkeypatch.setattr(limitless, "fetch", fake_fetch)

    limitless.deck_text_from_url(f"{limitless.BASE}/decks/45")

    assert requested == [f"{limitless.BASE}/decks/45", f"{limitless.BASE}/decks/list/789"]


def test_deck_text_from_url_raises_when_page_has_no_list(monkeypatch):
    monkeypatch.setattr(limitless, "fetch", lambda url: "<p>vazio</p>")

    with pytest.raises(ValueError):
        limitless.deck_text_from_url(f"{limitless.BASE}/decks/45")


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Dragapult ex", "dragapult_ex"),
        ("N's Zoroark ex", "ns_zoroark_ex"),
        ("Mr. Mime & Friends!", "mr_mime_friends"),
        ("", "deck"),
    ],
)
def test_slug_normalizes_names(name, expected):
    assert limitless.slug(name) == expected


def test_save_deck_writes_header_and_text(tmp_path):
    path = limitless.save_deck(
        tmp_path, "Meu Dragapult", "Dragapult ex", "4 Dreepy TWM 128", source="https://x"
    )

    assert path == tmp_path / "meu_dragapult.txt"
    content = path.read_text(encoding="utf-8")
    assert content == ("# Meu Dragapult\n# Dragapult ex\n# fonte: https://x\n4 Dreepy TWM 128\n")


def test_save_deck_without_source_omits_that_line(tmp_path):
    path = limitless.save_deck(tmp_path, "Meu Deck", "Título", "texto")

    assert "fonte:" not in path.read_text(encoding="utf-8")


def test_save_deck_creates_missing_folders(tmp_path):
    folder = tmp_path / "does" / "not" / "exist"

    path = limitless.save_deck(folder, "Meu Deck", "Título", "texto")

    assert path.exists()
