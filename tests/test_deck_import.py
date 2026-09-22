"""Janela de importação de deck: busca de arquétipo, link e texto colado —
as funções de rede são injetadas, então nada aqui toca a internet."""

from __future__ import annotations

from pokemon_companion.cards_db.limitless import Archetype
from pokemon_companion.ui.deck_import import DeckImportDialog

ARCHETYPES = [Archetype(1, "45", "Dragapult ex", 36.2), Archetype(2, "12", "Gardevoir ex", 9.4)]


def _dialog(tmp_path, **fakes):
    return DeckImportDialog(
        folder=tmp_path,
        search=fakes.get("search", lambda q: ARCHETYPES),
        download=fakes.get("download", lambda a: (a.name, "texto", 60)),
        from_url=fakes.get("from_url", lambda u: ("Lista via link", "texto", 60)),
    )


def test_search_fills_the_results_list(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.query.setText("dragapult")

    dialog.run_search()

    assert [dialog.results.item(i).text() for i in range(dialog.results.count())] == [
        a.label for a in ARCHETYPES
    ]
    assert "2 arquétipo" in dialog.status.text()


def test_search_with_no_matches_shows_a_message(qtbot, tmp_path):
    dialog = _dialog(tmp_path, search=lambda q: [])
    qtbot.addWidget(dialog)
    dialog.query.setText("nada")

    dialog.run_search()

    assert dialog.results.count() == 0
    assert "Nenhum arquétipo" in dialog.status.text()


def test_search_failure_becomes_a_status_message(qtbot, tmp_path):
    def boom(query: str) -> list[Archetype]:
        raise RuntimeError("sem rede")

    dialog = _dialog(tmp_path, search=boom)
    qtbot.addWidget(dialog)

    dialog.run_search()

    assert "sem rede" in dialog.status.text()


def test_pasting_a_link_skips_the_search(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.query.setText("https://limitlesstcg.com/decks/list/789")

    dialog.run_search()

    assert dialog.results.count() == 0
    assert "Link pronto" in dialog.status.text()


def test_importing_a_selected_archetype_downloads_and_saves(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.query.setText("dragapult")
    dialog.run_search()
    dialog.results.setCurrentRow(0)

    dialog.accept()

    saved = tmp_path / "dragapult_ex.txt"
    assert saved.exists()
    assert "texto" in saved.read_text(encoding="utf-8")
    assert dialog.imported_path == saved


def test_importing_without_choosing_an_archetype_shows_a_message(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)

    dialog.accept()

    assert dialog.imported_path is None
    assert "Procure um arquétipo" in dialog.status.text()


def test_importing_a_pasted_link_uses_from_url(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.query.setText("https://limitlesstcg.com/decks/list/789")

    dialog.accept()

    saved = tmp_path / "lista_via_link.txt"
    assert saved.exists()
    assert dialog.imported_path == saved


def test_importing_pasted_text_requires_a_name(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.paste.setPlainText("Pokémon (1)\n4 Dreepy TWM 128\n")

    dialog.accept()

    assert dialog.imported_path is None
    assert "nome" in dialog.status.text()


def test_importing_pasted_text_with_a_name_saves_it_locally(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog.paste.setPlainText("Pokémon (1)\n4 Dreepy TWM 128\n")
    dialog.name.setText("Meu Dragapult")

    dialog.accept()

    saved = tmp_path / "meu_dragapult.txt"
    assert saved.exists()
    assert "4 Dreepy TWM 128" in saved.read_text(encoding="utf-8")
    assert dialog.imported_path == saved


def test_download_failure_shows_falhou_and_does_not_close(qtbot, tmp_path):
    def boom(archetype: Archetype):
        raise RuntimeError("504")

    dialog = _dialog(tmp_path, download=boom)
    qtbot.addWidget(dialog)
    dialog.query.setText("dragapult")
    dialog.run_search()
    dialog.results.setCurrentRow(0)

    dialog.accept()

    assert dialog.imported_path is None
    assert "Falhou" in dialog.status.text()
