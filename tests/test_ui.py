from __future__ import annotations

import dataclasses
import random

import pytest
from PyQt6.QtCore import QPointF

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import AttachEnergy, EndTurn, PlayBasicToBench, UseAttack
from pokemon_companion.engine.game_state import GameState, PlayerId, PlayerState, PokemonInPlay
from pokemon_companion.ui.anim import Animator
from pokemon_companion.ui.app import BattleController, MainWindow
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.battle_scene import BattleScene, fan_layout, humanize
from pokemon_companion.ui.confirmation_dialog import ConfirmationDialog


@pytest.fixture(autouse=True)
def instant_animations():
    Animator.speed = 0
    yield
    Animator.speed = 1.0


@pytest.fixture
def offline_art(tmp_path) -> ArtProvider:
    return ArtProvider(art_dir=tmp_path, fetch=lambda url: None)


def _energy(card_type: str):
    return next(
        c for c in build_demo_deck() if c.supertype.value == "Energy" and c.types == [card_type]
    )


def _deck_card(name: str):
    return next(c for c in build_demo_deck() if c.name == name)


def _state(player_active, opponent_active, hand=None, bench=None) -> GameState:
    filler = [_energy("Fire")] * 10
    return GameState(
        player=PlayerState(
            active=PokemonInPlay(card=player_active),
            bench=[PokemonInPlay(card=c) for c in (bench or [])],
            hand=list(hand or []),
            deck=list(filler),
            prizes=[_energy("Fire")] * 6,
        ),
        opponent=PlayerState(
            active=PokemonInPlay(card=opponent_active),
            deck=list(filler),
            prizes=[_energy("Fire")] * 6,
            hand=[_energy("Water")] * 3,
        ),
        turn_number=3,  # meio de partida: fora das restrições de 1º turno
    )


@pytest.fixture
def controller(qtbot, offline_art):
    def make(state: GameState) -> BattleController:
        scene = BattleScene(offline_art)
        return BattleController(scene, state_factory=lambda: state, ai_factory=EasyAI)

    return make


# --------------------------------------------------------------------------
# funções puras


def test_fan_layout_is_symmetric_and_tilts_outward():
    layout = fan_layout(5)
    rotations = [rotation for _, rotation in layout]
    assert rotations[0] < 0 < rotations[-1]
    assert rotations[2] == 0
    assert layout[0][0].y() > layout[2][0].y()  # pontas mais baixas: arco
    assert layout[0][0].x() < layout[-1][0].x()


def test_fan_layout_empty_and_single():
    assert fan_layout(0) == []
    ((_, rotation),) = fan_layout(1)
    assert rotation == 0


def test_humanize_replaces_engine_ids():
    assert humanize("player colocou Charmander no banco.") == "Você colocou Charmander no banco."
    assert humanize("Charmander (opponent) foi nocauteado!") == "Charmander (IA) foi nocauteado!"


# --------------------------------------------------------------------------
# arte


def test_art_provider_uses_official_artwork_by_pokedex_number(tmp_path, qapp):
    requested = []
    png = _tiny_png()

    def fetch(url: str) -> bytes:
        requested.append(url)
        return png

    provider = ArtProvider(art_dir=tmp_path, fetch=fetch)
    art = provider.card_art(_deck_card("Charmander"))

    assert art is not None and not art.isNull()
    assert requested and requested[0].endswith("/official-artwork/4.png")
    assert (tmp_path / "4.png").exists()


def test_art_provider_caches_and_returns_none_offline(tmp_path, qapp):
    calls = []
    provider = ArtProvider(art_dir=tmp_path, fetch=lambda url: calls.append(url))
    card = dataclasses.replace(_deck_card("Squirtle"), image_url=None)

    assert provider.card_art(card) is None
    assert provider.card_art(card) is None
    assert len(calls) == 1  # segunda chamada veio do cache em memória


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# sincronização da cena


def test_initial_sync_creates_tokens_and_hand(controller):
    squirtle = _deck_card("Squirtle")
    state = _state(
        _deck_card("Charmander"), squirtle, hand=[squirtle, _energy("Fire")], bench=[squirtle]
    )
    ctrl = controller(state)

    assert len(ctrl.scene.tokens) == 3
    assert [item.card.name for item in ctrl.scene.hand_items] == ["Squirtle", "Fire Energy"]
    assert ctrl.scene.token_for(state.player.active).scale() == pytest.approx(0.92)
    assert ctrl.scene.token_for(state.player.bench[0]).scale() == pytest.approx(0.62)


def test_playing_basic_to_bench_adds_token_and_removes_hand_card(controller):
    squirtle = _deck_card("Squirtle")
    state = _state(_deck_card("Charmander"), squirtle, hand=[squirtle])
    ctrl = controller(state)

    targets = ctrl.targets_for_hand(0)
    assert list(targets) == [("zone", "bench")]
    ctrl.perform(targets[("zone", "bench")][0], card_source=QPointF(640, 800))

    assert len(state.player.bench) == 1
    assert ctrl.scene.token_for(state.player.bench[0]) is not None
    assert ctrl.scene.hand_items == []


def test_energy_drag_targets_pokemon_and_attach_reveals_orb(controller):
    charmander = _deck_card("Charmander")
    state = _state(charmander, _deck_card("Squirtle"), hand=[_energy("Fire")], bench=[charmander])
    ctrl = controller(state)

    targets = ctrl.targets_for_hand(0)
    assert set(targets) == {
        ("token", id(state.player.active)),
        ("token", id(state.player.bench[0])),
    }

    ctrl._on_drag_started(ctrl.scene.hand_items[0])
    assert set(ctrl.scene.targets) == set(targets)
    token = ctrl.scene.token_for(state.player.active)
    ctrl._on_dropped(ctrl.scene.hand_items[0], token.card_scene_rect().center())

    assert state.player.active.attached_energies == ["Fire"]
    assert token.visible_energy_count == 1
    assert ctrl.scene.targets == []


def test_drop_outside_targets_returns_card_to_hand(controller):
    charmander = _deck_card("Charmander")
    state = _state(charmander, _deck_card("Squirtle"), hand=[_energy("Fire")])
    ctrl = controller(state)
    item = ctrl.scene.hand_items[0]

    ctrl._on_drag_started(item)
    ctrl._on_dropped(item, QPointF(5, 5))

    assert state.player.active.attached_energies == []
    assert ctrl.scene.hand_items == [item]


def test_click_with_multiple_targets_enters_targeting_then_token_click_plays(controller):
    charmander = _deck_card("Charmander")
    state = _state(charmander, _deck_card("Squirtle"), hand=[_energy("Fire")], bench=[charmander])
    ctrl = controller(state)

    ctrl._on_hand_clicked(ctrl.scene.hand_items[0])
    assert len(ctrl.scene.targets) == 2
    bench_token = ctrl.scene.token_for(state.player.bench[0])
    ctrl._on_token_clicked(bench_token)

    assert state.player.bench[0].attached_energies == ["Fire"]


def test_attack_reduces_opponent_hp_and_ends_turn(controller):
    charmander = _deck_card("Charmander")
    state = _state(charmander, _deck_card("Squirtle"))
    state.player.active.attached_energies = ["Fire"]
    ctrl = controller(state)
    ctrl._ai_timer.stop()
    defender = state.opponent.active
    defender_token = ctrl.scene.token_for(defender)

    ctrl._on_attack_clicked(0)

    assert defender.current_hp == 40
    assert defender_token.hp_value == 40
    assert state.active_player == PlayerId.OPPONENT


def test_attack_button_ready_only_with_energy(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"))
    ctrl = controller(state)
    first, second = ctrl.scene.attack_buttons[:2]
    assert not first.ready and not first.enabled

    state.player.active.attached_energies = ["Fire"]
    ctrl._refresh_controls()
    assert first.ready and first.enabled
    assert not second.ready


def test_end_turn_button_turns_gold_when_nothing_else_to_do(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"))
    ctrl = controller(state)
    assert ctrl.scene.end_turn_button.mode == "done"

    state.player.hand = [_energy("Fire")]
    ctrl.scene.sync(state, animate=False)
    ctrl._refresh_controls()
    assert ctrl.scene.end_turn_button.mode == "play"


def test_knockout_removes_token_and_takes_prize(controller):
    charmander = _deck_card("Charmander")
    squirtle = _deck_card("Squirtle")
    state = _state(charmander, squirtle)
    state.opponent.bench = [PokemonInPlay(card=squirtle)]
    state.opponent.active.damage_counters = 50
    state.player.active.attached_energies = ["Fire"]
    ctrl = controller(state)
    ctrl._ai_timer.stop()
    knocked = state.opponent.active

    ctrl._on_attack_clicked(0)

    assert ctrl.scene.token_for(knocked) is None
    assert ctrl.scene.player_prizes.count == 5
    promoted = ctrl.scene.token_for(state.opponent.active)
    assert promoted is not None and promoted.slot == "active"


def test_evolution_reuses_token_with_new_card(controller):
    charmander = _deck_card("Charmander")
    charmeleon = _deck_card("Charmeleon")
    state = _state(charmander, _deck_card("Squirtle"), hand=[charmeleon])
    state.turn_number = 3
    ctrl = controller(state)
    token = ctrl.scene.token_for(state.player.active)

    ((action,),) = ctrl.targets_for_hand(0).values()
    ctrl.perform(action)

    assert state.player.active.card.name == "Charmeleon"
    assert ctrl.scene.token_for(state.player.active) is token
    assert token.card.name == "Charmeleon"
    assert token.max_hp == 90


def test_unplayable_card_is_dimmed_and_does_nothing_on_click(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"), hand=[_deck_card("Wartortle")])
    ctrl = controller(state)
    item = ctrl.scene.hand_items[0]

    assert not item.playable
    ctrl._on_hand_clicked(item)
    assert state.player.hand == [_deck_card("Wartortle")]


def test_retreat_with_multiple_bench_enters_selection(controller):
    charmander = _deck_card("Charmander")
    squirtle = _deck_card("Squirtle")
    state = _state(charmander, squirtle, bench=[squirtle, charmander])
    state.player.active.attached_energies = ["Fire"]
    ctrl = controller(state)

    ctrl._on_retreat_clicked()
    assert len(ctrl.scene.targets) == 2
    ctrl._on_token_clicked(ctrl.scene.token_for(state.player.bench[0]))

    assert state.player.active.card.name == "Squirtle"


def test_ai_turn_runs_until_back_to_player(controller, qtbot):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"))
    ctrl = controller(state)

    ctrl._perform_if_legal(EndTurn())
    qtbot.waitUntil(lambda: state.active_player == PlayerId.PLAYER and not ctrl.busy, timeout=3000)

    assert state.turn_number >= 3


def test_full_game_via_controller_reaches_game_over(qtbot, offline_art):
    random.seed(3)
    scene = BattleScene(offline_art)
    ctrl = BattleController(
        scene,
        state_factory=lambda: turn_manager.start_new_game(build_demo_deck(), build_demo_deck()),
        ai_factory=EasyAI,
    )

    for _ in range(400):
        if rules.is_game_over(ctrl.state):
            break
        if ctrl.is_player_turn and not ctrl.busy:
            legal = ctrl.legal_actions()
            preferred = [
                a for a in legal if isinstance(a, UseAttack | AttachEnergy | PlayBasicToBench)
            ]
            ctrl.perform(preferred[0] if preferred else EndTurn())
        qtbot.wait(5)

    assert rules.is_game_over(ctrl.state)
    qtbot.waitUntil(lambda: scene.game_over_overlay is not None, timeout=2000)


def test_spectator_mode_plays_a_full_game_between_two_ais(qtbot, offline_art):
    random.seed(11)
    scene = BattleScene(offline_art, "DECK B", "DECK A")
    scene.set_names("Deck A", "Deck B")
    ctrl = BattleController(
        scene,
        state_factory=lambda: turn_manager.start_new_game(build_demo_deck(), build_demo_deck()),
        ai_factory=EasyAI,
        player_ai_factory=EasyAI,
    )

    assert ctrl.spectating and not ctrl.is_player_turn
    assert scene.end_turn_button.mode == "watch"
    qtbot.waitUntil(lambda: rules.is_game_over(ctrl.state), timeout=15000)
    qtbot.waitUntil(lambda: scene.game_over_overlay is not None, timeout=2000)
    assert scene.game_over_overlay.title.endswith("vence!")


def test_spectator_ignores_human_input(controller, offline_art):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"), hand=[_energy("Fire")])
    scene = BattleScene(offline_art)
    ctrl = BattleController(scene, lambda: state, EasyAI, player_ai_factory=EasyAI)
    ctrl._ai_timer.stop()

    assert ctrl.legal_actions() == []
    assert ctrl.targets_for_hand(0) == {}


def test_restart_starts_fresh_game(controller):
    state_holder = {"n": 0}

    def factory():
        state_holder["n"] += 1
        return _state(_deck_card("Charmander"), _deck_card("Squirtle"))

    ctrl = controller(factory())
    ctrl._state_factory = factory
    ctrl.new_game()
    assert state_holder["n"] == 2
    assert len(ctrl.scene.tokens) == 2


def test_main_window_builds_with_demo_decks(qtbot, offline_art):
    window = MainWindow(
        state_factory=lambda: turn_manager.start_new_game(build_demo_deck(), build_demo_deck()),
        ai_factory=EasyAI,
        art=offline_art,
    )
    qtbot.addWidget(window)
    assert window.scene.hand_items
    assert window.controller.is_player_turn


# --------------------------------------------------------------------------
# diálogo de confirmação (Fase 4)


def test_confirmation_dialog_confirm_selects_top_candidate(qtbot):
    charmander, squirtle = _deck_card("Charmander"), _deck_card("Squirtle")
    dialog = ConfirmationDialog("carta na zona ativa", [(charmander, 2), (squirtle, 9)])
    qtbot.addWidget(dialog)

    dialog._on_confirm()

    assert dialog.selected_card is charmander


def test_confirmation_dialog_reject_leaves_selection_none(qtbot):
    dialog = ConfirmationDialog("carta na zona ativa", [(_deck_card("Charmander"), 2)])
    qtbot.addWidget(dialog)

    dialog.reject()

    assert dialog.selected_card is None


# --------------------------------------------------------------------------
# Treinadores, Habilidades, Estádio, promoção e setup na interface


def _trainer(name: str, kind: str = "Item"):
    from pokemon_companion.cards_db.models import Card, Supertype

    return Card(id=f"t-{name}", name=name, supertype=Supertype.TRAINER, subtypes=[kind])


def test_item_is_played_by_dropping_on_board(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"), hand=[_trainer("Poké Pad")])
    state.player.deck = [_deck_card("Squirtle")] * 5
    ctrl = controller(state)
    item = ctrl.scene.hand_items[0]

    ctrl._on_drag_started(item)
    assert ctrl.scene.targets == [("zone", "play")]
    ctrl._on_dropped(item, QPointF(640, 400))

    assert [c.name for c in state.player.hand] == ["Squirtle"]
    assert state.player.discard[-1].name == "Poké Pad"


def test_boss_orders_highlights_opponent_bench_and_pulls_clicked_pokemon(controller):
    state = _state(
        _deck_card("Charmander"),
        _deck_card("Squirtle"),
        hand=[_trainer("Boss's Orders", "Supporter")],
    )
    state.opponent.bench = [
        PokemonInPlay(card=_deck_card("Charmander")),
        PokemonInPlay(card=_deck_card("Squirtle")),
    ]
    ctrl = controller(state)
    target = state.opponent.bench[1]

    ctrl._on_hand_clicked(ctrl.scene.hand_items[0])
    assert set(ctrl.scene.targets) == {("token", id(m)) for m in state.opponent.bench}
    ctrl._on_token_clicked(ctrl.scene.token_for(target))

    assert state.opponent.active is target


def test_ability_badge_and_confirmation_panel(controller):
    from pokemon_companion.cards_db.models import Ability

    kanga = dataclasses.replace(_deck_card("Charmander"), abilities=[Ability(name="Run Errand")])
    state = _state(kanga, _deck_card("Squirtle"))
    ctrl = controller(state)
    token = ctrl.scene.token_for(state.player.active)
    assert token.ability_ready

    ctrl._on_token_clicked(token)
    panel = ctrl.scene.choice_panel
    assert panel is not None and panel.options == ["Usar Run Errand"]
    panel.chosen.emit(0)

    assert len(state.player.hand) == 2
    assert ctrl.scene.choice_panel is None
    assert not token.ability_ready


def test_stadium_is_shown_and_used_by_click(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"), hand=[_energy("Fire")] * 3)
    state.stadium = _trainer("Prism Tower", "Stadium")
    ctrl = controller(state)

    assert ctrl.scene.stadium_item.isVisible() and ctrl.scene.stadium_item.usable
    ctrl.scene.stadium_item.clicked.emit()

    assert state.player.stadium_used_this_turn
    assert len(state.player.hand) == 2  # descartou 2, comprou 1
    assert not ctrl.scene.stadium_item.usable


def test_player_chooses_new_active_after_knockout(controller):
    state = _state(_deck_card("Charmander"), _deck_card("Squirtle"))
    state.manual_choices = frozenset({PlayerId.PLAYER})
    state.player.bench = [PokemonInPlay(card=_deck_card("Squirtle"))]
    state.player.active.damage_counters = 60  # Squirtle (água) nocauteia com fraqueza
    state.active_player = PlayerId.OPPONENT
    state.opponent.active.attached_energies = ["Water"]
    ctrl = controller(state)
    ctrl._ai_timer.stop()

    ctrl.perform(UseAttack(attack_index=0))

    assert state.pending_promotion == PlayerId.PLAYER
    assert ctrl.is_player_turn
    benched = state.player.bench[0]
    assert ctrl.scene.targets == [("token", id(benched))]
    ctrl._on_token_clicked(ctrl.scene.token_for(benched))

    assert state.player.active is benched
    assert state.active_player == PlayerId.PLAYER


def test_manual_setup_uses_ready_button(controller):
    charmander, squirtle = _deck_card("Charmander"), _deck_card("Squirtle")
    state = turn_manager.start_new_game(
        [charmander] * 30 + [squirtle] * 30,
        [squirtle] * 20 + [_energy("Water")] * 40,
        rng=random.Random(1),
        first_player=PlayerId.PLAYER,
        manual=frozenset({PlayerId.PLAYER}),
    )
    ctrl = controller(state)
    assert ctrl.scene.end_turn_button.mode == "choose"

    ((action,),) = ctrl.targets_for_hand(0).values()
    ctrl.perform(action)
    assert state.player.active is not None
    assert ctrl.scene.end_turn_button.mode == "setup"

    ctrl.scene.end_turn_button.clicked.emit()
    assert state.pending_setup == ()
    assert ctrl.scene.end_turn_button.mode in ("play", "done")
