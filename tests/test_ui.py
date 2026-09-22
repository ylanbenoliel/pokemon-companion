from __future__ import annotations

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.cards_db.models import Card
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.game_state import PlayerState, PokemonInPlay
from pokemon_companion.ui.app import AI_TURN_DELAY_MS, MainWindow
from pokemon_companion.ui.board_view import BoardView, format_board
from pokemon_companion.ui.confirmation_dialog import ConfirmationDialog


def test_format_board_shows_active_bench_and_hand(charmander, squirtle):
    player = PlayerState(
        active=PokemonInPlay(card=charmander),
        bench=[PokemonInPlay(card=squirtle)],
        hand=[squirtle],
    )

    text = format_board("Você", player, show_hand=True)

    assert "Charmander" in text
    assert "Banco 0: Squirtle" in text
    assert "Mão: Squirtle" in text
    assert "prêmios restantes: 0" in text


def test_board_view_widget_updates_text(qtbot, charmander):
    view = BoardView("IA")
    qtbot.addWidget(view)
    player = PlayerState(active=PokemonInPlay(card=charmander))

    view.update_state(player)

    assert "Charmander" in view.text()


def test_confirmation_dialog_confirm_selects_top_candidate(qtbot, charmander, squirtle):
    candidates: list[tuple[Card, int]] = [(charmander, 2), (squirtle, 9)]
    dialog = ConfirmationDialog("carta na zona ativa", candidates)
    qtbot.addWidget(dialog)

    dialog._on_confirm()

    assert dialog.selected_card is charmander


def test_confirmation_dialog_reject_leaves_selection_none(qtbot, charmander):
    dialog = ConfirmationDialog("carta na zona ativa", [(charmander, 2)])
    qtbot.addWidget(dialog)

    dialog.reject()

    assert dialog.selected_card is None


def test_main_window_populates_actions_on_player_turn(qtbot):
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    window = MainWindow(state, EasyAI())
    qtbot.addWidget(window)

    assert window.action_list.count() > 0
    assert window.play_button.isEnabled()


def test_full_game_playable_end_to_end_via_ui(qtbot):
    """Sempre seleciona a primeira ação disponível (geralmente 'passar o
    turno' ou a primeira opção legal) até o jogo terminar — equivalente ao
    fuzz de self-play do CLI, mas passando pela camada de UI real."""
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    window = MainWindow(state, EasyAI())
    qtbot.addWidget(window)

    for _ in range(500):
        if rules.is_game_over(state):
            break
        if state.active_player.value == "player":
            window.action_list.setCurrentRow(len(window._current_actions) - 1)  # "Passar o turno"
            window.play_selected_action()
        else:
            qtbot.wait(AI_TURN_DELAY_MS + 50)

    assert state.winner is not None
