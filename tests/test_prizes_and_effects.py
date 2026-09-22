from __future__ import annotations

import dataclasses

from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import UseAttack
from pokemon_companion.engine.game_state import PokemonInPlay

from .test_rules import build_state


def test_prize_count_for_regular_pokemon_is_one(charmander):
    assert rules.prize_count_for(charmander) == 1


def test_prize_count_for_v_pokemon_is_two(charmander):
    v_card = dataclasses.replace(charmander, subtypes=["Basic", "V"])
    assert rules.prize_count_for(v_card) == 2


def test_prize_count_for_vmax_pokemon_is_three(charmander):
    vmax_card = dataclasses.replace(charmander, subtypes=["VMAX"])
    assert rules.prize_count_for(vmax_card) == 3


def test_knockout_of_v_pokemon_awards_two_prizes(charmander, squirtle):
    v_squirtle = dataclasses.replace(squirtle, subtypes=["Basic", "V"])
    state = build_state(player_active=charmander, opponent_active=v_squirtle)
    state.player.active.attached_energies.append("Fire")
    state.opponent.active.damage_counters = 50
    state.opponent.bench = [PokemonInPlay(card=squirtle)]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert len(state.player.prizes) == 4  # 6 - 2


def test_registered_attack_effect_discards_energy_on_heads(monkeypatch, charmander, squirtle):
    # "Whirlpool" (Goldeen): cara -> descarta 1 energia do Ativo do oponente.
    whirl_card = dataclasses.replace(
        charmander,
        attacks=[dataclasses.replace(charmander.attacks[0], name="Whirlpool", damage="10")],
    )
    state = build_state(player_active=whirl_card, opponent_active=squirtle)
    state.player.active.attached_energies.append("Fire")
    state.opponent.active.attached_energies = ["Water"]

    monkeypatch.setattr("pokemon_companion.engine.effects.core.random.random", lambda: 0.0)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.attached_energies == []
    assert state.opponent.discard[-1].name == "Water Energy"
