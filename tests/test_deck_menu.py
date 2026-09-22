"""Tela de seleção de decks (menu inicial)."""

from __future__ import annotations

import pytest

from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.deck_menu import DeckEntry, DeckMenu, discover_decks, read_entry


@pytest.fixture
def decks(tmp_path):
    folder = tmp_path / "top"
    folder.mkdir()
    (folder / "01_dragapult_ex.txt").write_text(
        "# Dragapult ex — #1 do meta (36% dos pontos)\n# lista\nPokémon (1)\n4 Dreepy TWM 128\n",
        encoding="utf-8",
    )
    (folder / "02_basic_box.txt").write_text("Pokémon (1)\n4 Meowth ex POR 62\n", encoding="utf-8")
    return folder


def test_read_entry_uses_header_comment(decks):
    entry = read_entry(decks / "01_dragapult_ex.txt")
    assert entry.title == "Dragapult ex"
    assert entry.subtitle == "#1 do meta (36% dos pontos)"
    assert entry.group == "Meta atual"


def test_read_entry_falls_back_to_file_name(decks):
    entry = read_entry(decks / "02_basic_box.txt")
    assert entry.title == "Basic Box"
    assert entry.subtitle == ""


def test_discover_decks_skips_missing_folders(decks, tmp_path):
    entries = discover_decks((decks, tmp_path / "não_existe"))
    assert [e.title for e in entries] == ["Dragapult ex", "Basic Box"]


def _menu(entries: list[DeckEntry], tmp_path) -> DeckMenu:
    art = ArtProvider(art_dir=tmp_path / "art", fetch=lambda url: None)
    menu = DeckMenu(entries=entries, art=art)
    return menu


def test_menu_preselects_two_decks_and_emits_choice(qtbot, decks, tmp_path):
    entries = discover_decks((decks,))
    menu = _menu(entries, tmp_path)
    qtbot.addWidget(menu)

    assert menu.player_column.selection.title == "Dragapult ex"
    assert menu.opponent_column.selection.title == "Basic Box"
    assert menu.play_button.isEnabled()

    chosen: list[tuple] = []
    menu.start_requested.connect(lambda *args: chosen.append(args))
    menu.opponent_column.tiles[0].clicked.emit(entries[0])  # IA também com Dragapult
    menu.play_button.click()

    player_path, opponent_path, difficulty = chosen[0]
    assert player_path.name == "01_dragapult_ex.txt"
    assert opponent_path.name == "01_dragapult_ex.txt"
    assert difficulty == "medium"


def test_menu_difficulty_buttons_change_selection(qtbot, decks, tmp_path):
    menu = _menu(discover_decks((decks,)), tmp_path)
    qtbot.addWidget(menu)

    hard = next(b for b in menu.difficulty_buttons.buttons() if b.text() == "DIFÍCIL")
    hard.click()

    assert menu.difficulty == "hard"


def test_tile_selection_is_exclusive(qtbot, decks, tmp_path):
    entries = discover_decks((decks,))
    menu = _menu(entries, tmp_path)
    qtbot.addWidget(menu)

    menu.player_column.tiles[1].clicked.emit(entries[1])

    assert [tile.selected for tile in menu.player_column.tiles] == [False, True]
    assert menu.player_column.selection.title == "Basic Box"
