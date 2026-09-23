"""Replays (estado em JSON + ações) e estatísticas das partidas."""

from __future__ import annotations

import json
import random

import pytest

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import AttachEnergy, UseAttack
from pokemon_companion.engine.game_state import PlayerId, StatusCondition
from pokemon_companion.engine.replay import Replay, list_replays, load_replay, save_replay
from pokemon_companion.engine.serialization import (
    SerializationError,
    decode_action,
    decode_state,
    encode_action,
    encode_state,
)
from pokemon_companion.stats import MatchRecord, load_matches, record_match, summarize
from pokemon_companion.ui.anim import Animator
from pokemon_companion.ui.app import BattleController
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.battle_scene import BattleScene
from pokemon_companion.ui.profile_dialogs import ReplaysDialog, StatsDialog, describe_replay
from pokemon_companion.ui.replay_view import ReplayController


@pytest.fixture(autouse=True)
def instant_animations():
    Animator.speed = 0
    yield
    Animator.speed = 1.0


@pytest.fixture
def art(tmp_path):
    return ArtProvider(art_dir=tmp_path, fetch=lambda url: None)


def _play_ai_game(seed: int) -> tuple[Replay, str]:
    """Partida IA × IA gravada como a UI grava; devolve o replay e o estado
    final em JSON (para comparar com a reprodução)."""
    random.seed(seed)
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    replay = Replay.start(state)
    ais = {pid: EasyAI() for pid in PlayerId}
    for _ in range(2000):
        if state.winner is not None:
            break
        legal = rules.legal_actions(state)
        saved = random.getstate()
        action = ais[rules.decision_player(state)].choose_action(state, legal)
        random.setstate(saved)
        replay.record(action)
        rules.apply_action(state, action)
    return replay, json.dumps(encode_state(state), sort_keys=True)


def test_state_round_trips_through_json():
    random.seed(1)
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    if state.opponent.active is not None:
        state.opponent.active.status = StatusCondition.POISONED
        state.opponent.active.shield = ("ex", 3)
    raw = json.dumps(encode_state(state))
    again = decode_state(json.loads(raw))
    assert json.dumps(encode_state(again), sort_keys=True) == json.dumps(
        encode_state(state), sort_keys=True
    )


def test_actions_round_trip():
    for action in (UseAttack(attack_index=1, target=("opp", 0)), AttachEnergy(2, False, 1)):
        assert decode_action(json.loads(json.dumps(encode_action(action)))) == action


def test_unknown_classes_are_refused():
    with pytest.raises(SerializationError):
        decode_state({"cards": {}, "state": {"$dc": "os.system", "x": 1}})
    with pytest.raises(SerializationError):
        decode_action({"type": "Exploit"})


@pytest.mark.parametrize("seed", [2, 5, 9])
def test_replay_reproduces_the_game_exactly(seed):
    replay, final = _play_ai_game(seed)
    again = Replay.from_json(replay.to_json())
    last = None
    for _, state in again.states():
        last = state
    assert last is not None
    assert json.dumps(encode_state(last), sort_keys=True) == final


def test_replays_are_saved_listed_and_trimmed(tmp_path):
    replay, _ = _play_ai_game(4)
    replay.info.update(player_deck="Fogo", opponent_deck="Água", winner="player", turns=9)
    path = save_replay(replay, tmp_path)
    listed = list_replays(tmp_path)
    assert listed[0][0] == path and listed[0][1]["player_deck"] == "Fogo"
    assert load_replay(path).actions == replay.actions
    assert "Fogo × Água · Vitória em 9 turnos" in describe_replay(listed[0][1])


def test_stats_summary():
    for won, deck in ((True, "Fogo"), (True, "Fogo"), (False, "Água"), (True, "Fogo")):
        record_match(MatchRecord("2026-09-23", deck, "IA", "hard", won, 10, 6 if won else 2, 3))
    summary = summarize(load_matches())
    assert summary.overall.games == 4 and summary.overall.wins == 3
    assert summary.by_deck["Fogo"].rate == 1.0
    assert summary.streak == 1 and summary.best_streak == 2


def test_controller_saves_replay_and_stats_when_the_game_ends(qtbot, art):
    random.seed(3)
    scene = BattleScene(art)
    ctrl = BattleController(
        scene,
        state_factory=lambda: turn_manager.start_new_game(build_demo_deck(), build_demo_deck()),
        ai_factory=EasyAI,
        match_info={"player_deck": "Demo", "opponent_deck": "Demo IA", "difficulty": "easy"},
    )
    ctrl.concede()
    assert ctrl.replay_path is not None and ctrl.replay_path.exists()
    (record,) = load_matches()
    assert record.conceded and not record.won and record.player_deck == "Demo"


def test_replay_controller_replays_to_the_same_winner(qtbot, art):
    replay, _ = _play_ai_game(6)
    expected = None
    for _, state in Replay.from_json(replay.to_json()).states():
        expected = state.winner
    scene = BattleScene(art)
    ctrl = ReplayController(scene, Replay.from_json(replay.to_json()))
    qtbot.waitUntil(lambda: rules.is_game_over(ctrl.state), timeout=20000)
    assert ctrl.state.winner == expected and not ctrl.diverged


def test_profile_dialogs_open(qtbot, tmp_path):
    stats = StatsDialog()
    replays = ReplaysDialog(folder=tmp_path)
    qtbot.addWidget(stats)
    qtbot.addWidget(replays)
    assert not replays.watch.isEnabled()
