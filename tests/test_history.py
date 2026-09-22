from __future__ import annotations

import json

from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import EndTurn, UseAttack
from pokemon_companion.engine.game_state import PlayerId
from pokemon_companion.engine.history import MatchRecorder

from .test_rules import build_state


def test_recorder_attributes_turn_ending_actions_to_who_acted(charmander, squirtle, tmp_path):
    # Regressão: atacar/passar troca o jogador ativo; o log atribuía a jogada
    # ao turno e ao jogador seguintes.
    state = build_state(player_active=charmander, opponent_active=squirtle)
    state.player.active.attached_energies = ["Fire"]
    recorder = MatchRecorder()

    for action in (UseAttack(attack_index=0), EndTurn()):
        turn, actor = state.turn_number, state.active_player
        recorder.record(turn, actor, action, rules.apply_action(state, action))

    path = tmp_path / "log.json"
    recorder.save(path)
    entries = json.loads(path.read_text())
    assert [(e["turn_number"], e["active_player"], e["action"]["type"]) for e in entries] == [
        (3, PlayerId.PLAYER.value, "UseAttack"),
        (4, PlayerId.OPPONENT.value, "EndTurn"),
    ]
