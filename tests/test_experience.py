"""Experiência do app: configurações, pausa, desistência, ajuda."""

from __future__ import annotations

import pytest

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.game_state import PlayerId
from pokemon_companion.ui import settings as settings_module
from pokemon_companion.ui.anim import Animator
from pokemon_companion.ui.app import BattleController, apply_settings
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.battle_scene import BattleScene
from pokemon_companion.ui.dialogs import PauseMenu, SettingsDialog
from pokemon_companion.ui.help import PAGES, HelpDialog
from pokemon_companion.ui.settings import Settings, load_settings, save_settings


@pytest.fixture(autouse=True)
def instant_animations():
    Animator.speed = 0
    yield
    Animator.speed = 1.0
    Animator.reduce_motion = False


@pytest.fixture
def controller(qtbot, tmp_path):
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    scene = BattleScene(ArtProvider(art_dir=tmp_path, fetch=lambda url: None))
    return BattleController(scene, state_factory=lambda: state, ai_factory=EasyAI)


def test_settings_survive_a_restart():
    save_settings(Settings(volume=0.3, muted=True, speed=2.5, difficulty="hard", hints=False))
    loaded = load_settings()
    assert (loaded.volume, loaded.muted, loaded.speed, loaded.difficulty, loaded.hints) == (
        0.3,
        True,
        2.5,
        "hard",
        False,
    )


def test_settings_default_when_nothing_was_saved():
    assert load_settings() == Settings()


def test_settings_dialog_applies_and_saves_each_change(qtbot):
    dialog = SettingsDialog(Settings())
    qtbot.addWidget(dialog)
    seen: list[Settings] = []
    dialog.changed.connect(seen.append)

    dialog.reduce_motion.setChecked(True)
    dialog.volume.setValue(40)

    assert seen[-1].reduce_motion and seen[-1].volume == 0.4
    assert load_settings().reduce_motion


def test_apply_settings_sets_speed_and_motion():
    apply_settings(Settings(speed=2.0, reduce_motion=True))
    assert Animator.speed == 0.5 and Animator.reduce_motion


def test_hints_can_be_reset():
    settings_module.mark_hint_seen("energy")
    assert "energy" in settings_module.seen_hints()
    settings_module.reset_hints()
    assert settings_module.seen_hints() == set()


def test_paused_controller_lets_the_ai_wait(controller):
    controller.set_paused(True)
    controller.state.pending_setup = ()
    controller.state.active_player = PlayerId.OPPONENT
    before = controller.state.turn_number
    controller._ai_step()
    assert controller.state.turn_number == before


def test_conceding_gives_the_game_to_the_opponent(controller):
    controller.concede()
    assert rules.is_game_over(controller.state)
    assert controller.state.winner == PlayerId.OPPONENT


def test_pause_menu_offers_concede_only_during_the_game(qtbot):
    during = PauseMenu(game_over=False)
    after = PauseMenu(game_over=True)
    qtbot.addWidget(during)
    qtbot.addWidget(after)
    assert PauseMenu.CONCEDE in during.buttons and PauseMenu.LEAVE not in during.buttons
    assert PauseMenu.LEAVE in after.buttons and PauseMenu.CONCEDE not in after.buttons


def test_help_has_rules_and_controls(qtbot):
    dialog = HelpDialog()
    qtbot.addWidget(dialog)
    assert dialog.tabs.count() == len(PAGES)
    assert "Prêmio" in PAGES[0][1] and "Esc" in PAGES[-1][1]
