"""Tela inicial: leitura das listas e montagem do confronto."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialog

from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.deck_menu import (
    OPPONENT,
    PLAYER,
    DeckEntry,
    DeckMenu,
    discover_decks,
    read_entry,
)


@pytest.fixture
def decks(tmp_path):
    folder = tmp_path / "top"
    folder.mkdir()
    (folder / "01_dragapult_ex.txt").write_text(
        "# Dragapult ex — #1 do meta (36% dos pontos)\n# lista\nPokémon (1)\n4 Dreepy TWM 128\n",
        encoding="utf-8",
    )
    (folder / "02_basic_box.txt").write_text("Pokémon (1)\n4 Meowth ex POR 62\n", encoding="utf-8")
    worlds = tmp_path / "worlds2026"
    worlds.mkdir()
    (worlds / "1_hedrick_dragapult.txt").write_text(
        "# Dragapult — 1º no torneio, Andrew Hedrick\nPokémon (1)\n4 Dreepy TWM 128\n",
        encoding="utf-8",
    )
    return tmp_path


def test_read_entry_uses_header_comment(decks):
    entry = read_entry(decks / "top" / "01_dragapult_ex.txt")
    assert entry.title == "Dragapult ex"
    assert entry.subtitle == "#1 do meta (36% dos pontos)"
    assert entry.group == "Meta atual"


def test_read_entry_falls_back_to_file_name(decks):
    entry = read_entry(decks / "top" / "02_basic_box.txt")
    assert entry.title == "Basic Box"
    assert entry.subtitle == ""


def test_discover_decks_skips_missing_folders(decks):
    entries = discover_decks((decks / "top", decks / "não_existe", decks / "worlds2026"))
    assert [e.title for e in entries] == ["Dragapult ex", "Basic Box", "Dragapult"]


def _menu(entries: list[DeckEntry], tmp_path) -> DeckMenu:
    art = ArtProvider(art_dir=tmp_path / "art", fetch=lambda url: None)
    return DeckMenu(entries=entries, art=art)


def test_menu_starts_with_two_decks_and_emits_the_match(qtbot, decks, tmp_path):
    entries = discover_decks((decks / "top",))
    menu = _menu(entries, tmp_path)
    qtbot.addWidget(menu)

    assert menu.selection(PLAYER).title == "Dragapult ex"
    assert menu.selection(OPPONENT).title == "Basic Box"
    assert menu.play_button.isEnabled()

    chosen: list[tuple] = []
    menu.start_requested.connect(lambda *args: chosen.append(args))
    menu.play_button.click()

    player_path, opponent_path, difficulty = chosen[0]
    assert player_path.name == "01_dragapult_ex.txt"
    assert opponent_path.name == "02_basic_box.txt"
    assert difficulty == "medium"


def test_choosing_in_the_strip_fills_the_armed_side_then_swaps(qtbot, decks, tmp_path):
    entries = discover_decks((decks / "top",))
    menu = _menu(entries, tmp_path)
    qtbot.addWidget(menu)
    assert menu.armed == PLAYER

    menu.strip.tiles[1].clicked.emit(entries[1])  # vira o seu deck
    assert menu.selection(PLAYER).title == "Basic Box"
    assert menu.armed == OPPONENT

    menu.strip.tiles[0].clicked.emit(entries[0])  # e agora o da IA
    assert menu.selection(OPPONENT).title == "Dragapult ex"
    assert menu.armed == PLAYER


def test_clicking_a_side_arms_it(qtbot, decks, tmp_path):
    menu = _menu(discover_decks((decks / "top",)), tmp_path)
    qtbot.addWidget(menu)

    menu.heroes[OPPONENT].clicked.emit(None)

    assert menu.armed == OPPONENT
    assert menu.heroes[OPPONENT].highlighted and not menu.heroes[PLAYER].highlighted


def test_group_filter_hides_other_decks(qtbot, decks, tmp_path):
    menu = _menu(discover_decks((decks / "top", decks / "worlds2026")), tmp_path)
    qtbot.addWidget(menu)
    menu.show()

    menu.strip.filter_group("Mundial 2026")

    visible = [t.entry.title for t in menu.strip.tiles if t.isVisible()]
    assert visible == ["Dragapult"]


def test_difficulty_buttons_change_selection(qtbot, decks, tmp_path):
    menu = _menu(discover_decks((decks / "top",)), tmp_path)
    qtbot.addWidget(menu)

    next(b for b in menu.difficulty_buttons.buttons() if b.text() == "Difícil").click()

    assert menu.difficulty == "hard"


class _FakeImportDialog:
    """Substitui `DeckImportDialog`: já "importado", sem abrir janela real."""

    def __init__(self, imported_path):
        self.imported_path = imported_path

    def exec(self) -> int:
        return QDialog.DialogCode.Accepted


def test_importing_a_deck_adds_it_to_the_strip_and_fills_the_armed_side(qtbot, decks, tmp_path):
    imported = decks / "data"
    imported.mkdir()
    imported_path = imported / "minha_lista.txt"
    imported_path.write_text(
        "# Minha Lista — importado agora\nPokémon (1)\n4 Dreepy TWM 128\n", encoding="utf-8"
    )
    entries = discover_decks((decks / "top",))
    menu = DeckMenu(
        entries=entries,
        art=ArtProvider(art_dir=tmp_path / "art", fetch=lambda url: None),
        import_dialog=lambda parent: _FakeImportDialog(imported_path),
    )
    qtbot.addWidget(menu)
    assert menu.armed == PLAYER

    menu.import_button.click()

    assert menu.selection(PLAYER).title == "Minha Lista"
    assert menu.armed == OPPONENT
    assert "Minha Lista" in [t.entry.title for t in menu.strip.tiles]
    assert "Importado" in menu.status.text()


def test_importing_nothing_leaves_the_menu_untouched(qtbot, decks, tmp_path):
    entries = discover_decks((decks / "top",))
    menu = DeckMenu(
        entries=entries,
        art=ArtProvider(art_dir=tmp_path / "art", fetch=lambda url: None),
        import_dialog=lambda parent: _FakeImportDialog(None),
    )
    qtbot.addWidget(menu)
    before = menu.selection(PLAYER)

    menu.import_button.click()

    assert menu.selection(PLAYER) == before
