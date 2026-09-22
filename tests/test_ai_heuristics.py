from __future__ import annotations

import dataclasses
import random

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.ai.heuristics_hard import HardAI
from pokemon_companion.ai.heuristics_medium import MediumAI
from pokemon_companion.cards_db.models import Attack
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import Retreat, UseAttack
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay

from .test_rules import build_state


def test_easy_ai_prefers_attack_when_available(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle)
    state.player.active.attached_energies.append("Fire")

    actions = rules.legal_actions(state)
    chosen = EasyAI(rng=random.Random(0)).choose_action(state, actions)

    assert isinstance(chosen, UseAttack)


def test_medium_ai_prefers_lethal_attack_over_weaker_one(charmander, squirtle):
    lethal_card = dataclasses.replace(
        charmander,
        attacks=[
            Attack(name="Fraco", cost=["Fire"], damage="10"),
            Attack(name="Letal", cost=["Fire"], damage="100"),
        ],
        weaknesses=[],
    )
    state = build_state(player_active=lethal_card, opponent_active=squirtle)
    state.player.active.attached_energies.append("Fire")
    state.opponent.bench = [PokemonInPlay(card=squirtle)]  # partida continua após o KO

    actions = rules.legal_actions(state)
    chosen = MediumAI().choose_action(state, actions)

    assert isinstance(chosen, UseAttack)
    assert lethal_card.attacks[chosen.attack_index].name == "Letal"


def _play_full_game(player_ai, opponent_ai, rng: random.Random, max_actions: int = 500) -> None:
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck(), rng=rng)

    for _ in range(max_actions):
        if rules.is_game_over(state):
            break
        actions = rules.legal_actions(state)
        assert actions, "legal_actions nunca deve ficar vazio antes do fim de jogo"
        current_ai = player_ai if state.active_player == PlayerId.PLAYER else opponent_ai
        chosen = current_ai.choose_action(state, actions)
        rules.apply_action(state, chosen)

    assert state.winner is not None, "partida não terminou dentro do limite de ações"


def test_self_play_easy_vs_easy_terminates_without_exceptions():
    rng = random.Random(42)
    _play_full_game(EasyAI(random.Random(1)), EasyAI(random.Random(2)), rng)


def test_self_play_medium_vs_medium_terminates_without_exceptions():
    rng = random.Random(7)
    _play_full_game(MediumAI(), MediumAI(), rng)


def test_self_play_hard_vs_medium_terminates_without_exceptions():
    rng = random.Random(99)
    _play_full_game(HardAI(), MediumAI(), rng)


def test_hard_ai_does_not_retreat_just_to_dump_energy(charmander, squirtle):
    # Regressão: a IA difícil recuava em loop porque só simulava a resposta
    # do oponente para ações que encerram o turno.
    state = build_state(player_active=charmander, opponent_active=squirtle)
    state.player.active.attached_energies = ["Fire"]
    state.player.bench = [PokemonInPlay(card=squirtle)]

    actions = [a for a in rules.legal_actions(state) if not isinstance(a, UseAttack)]
    chosen = HardAI().choose_action(state, actions)

    assert not isinstance(chosen, Retreat)
