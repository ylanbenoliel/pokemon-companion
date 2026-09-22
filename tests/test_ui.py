from __future__ import annotations

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.cards_db.models import Card
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.game_state import PlayerState, PokemonInPlay
from pokemon_companion.ui.app import AI_TURN_DELAY_MS, MainWindow
from pokemon_companion.ui.board_view import BoardView
from pokemon_companion.ui.confirmation_dialog import ConfirmationDialog
from pokemon_companion.ui.pokemon_card_widget import PokemonCardWidget


def test_board_view_shows_active_bench_and_hand(qtbot, charmander, squirtle):
    view = BoardView("Você", show_hand=True)
    qtbot.addWidget(view)
    player = PlayerState(
        active=PokemonInPlay(card=charmander),
        bench=[PokemonInPlay(card=squirtle)],
        hand=[squirtle],
        prizes=[],
    )

    view.update_state(player)

    assert view._active_card.name_label.text() == "Charmander"
    assert view._bench_cards[0].name_label.text() == "Squirtle"
    assert view._bench_cards[1].property("empty") is True
    assert view._hand_label is not None
    assert "Squirtle" in view._hand_label.text()


def test_board_view_prize_pips_reflect_remaining_prizes(qtbot, charmander):
    view = BoardView("IA")
    qtbot.addWidget(view)
    player = PlayerState(active=PokemonInPlay(card=charmander), prizes=[charmander] * 4)

    view.update_state(player)

    assert "#ffca28" in view._prizes._pips[0].styleSheet()
    assert "#ffca28" not in view._prizes._pips[5].styleSheet()


def test_pokemon_card_widget_shows_hp_energy_and_status(qtbot, charmander):
    widget = PokemonCardWidget()
    qtbot.addWidget(widget)
    mon = PokemonInPlay(card=charmander, attached_energies=["Fire", "Fire"])
    mon.damage_counters = 20

    widget.update_pokemon(mon)

    assert widget.name_label.text() == "Charmander"
    assert widget.hp_bar.value() == charmander.hp - 20
    assert widget._energy_row.count() == 3  # 2 pips + 1 stretch
    assert widget.property("empty") is False
    assert widget.status_label.isHidden()


def test_pokemon_card_widget_set_empty_resets_state(qtbot, charmander):
    widget = PokemonCardWidget()
    qtbot.addWidget(widget)
    widget.update_pokemon(PokemonInPlay(card=charmander))

    widget.set_empty()

    assert widget.name_label.text() == "(vazio)"
    assert widget.property("empty") is True
    assert widget._energy_row.count() == 0


def test_pokemon_card_widget_shows_attacks_with_readiness(qtbot, charmander):
    # `charmander` (fixture) tem 1 ataque só, custando 1 energia Fire.
    widget = PokemonCardWidget()
    qtbot.addWidget(widget)
    assert widget._attacks_container is not None

    not_ready = PokemonInPlay(card=charmander)  # sem energia anexada
    widget.update_pokemon(not_ready)
    assert widget._attacks_container.count() == 1
    row = widget._attacks_container.itemAt(0).widget()
    assert row.name_label.text() == charmander.attacks[0].name
    assert "0.45" in row.name_label.styleSheet()  # esmaecido: não tem energia

    ready = PokemonInPlay(card=charmander, attached_energies=["Fire"])
    widget.update_pokemon(ready)
    row = widget._attacks_container.itemAt(0).widget()
    assert "#263238" in row.name_label.styleSheet()  # pronto para atacar


def test_compact_pokemon_card_widget_has_no_attacks_container(qtbot, charmander):
    widget = PokemonCardWidget(compact=True)
    qtbot.addWidget(widget)

    widget.update_pokemon(PokemonInPlay(card=charmander))

    assert widget._attacks_container is None


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
