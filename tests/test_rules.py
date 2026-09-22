from __future__ import annotations

import dataclasses

from pokemon_companion.cards_db.models import Card, Supertype, WeaknessResistance
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import (
    AttachEnergy,
    EndTurn,
    Evolve,
    PlayBasicToBench,
    Retreat,
    UseAttack,
)
from pokemon_companion.engine.game_state import (
    GameState,
    PlayerId,
    PlayerState,
    PokemonInPlay,
)


def _dummy_card(id_: str, name: str = "Dummy") -> Card:
    return Card(id=id_, name=name, supertype=Supertype.POKEMON)


def build_state(
    player_active: Card | None = None,
    opponent_active: Card | None = None,
    player_hand: list[Card] | None = None,
    player_prizes: int = 6,
    opponent_prizes: int = 6,
    active_player: PlayerId = PlayerId.PLAYER,
    player_deck: list[Card] | None = None,
) -> GameState:
    player = PlayerState(
        active=PokemonInPlay(card=player_active) if player_active else None,
        hand=player_hand or [],
        prizes=[_dummy_card(f"prize-{i}") for i in range(player_prizes)],
        deck=player_deck if player_deck is not None else [_dummy_card("deck-1")],
    )
    opponent = PlayerState(
        active=PokemonInPlay(card=opponent_active) if opponent_active else None,
        prizes=[_dummy_card(f"oprize-{i}") for i in range(opponent_prizes)],
        deck=[_dummy_card("odeck-1")],
    )
    return GameState(player=player, opponent=opponent, active_player=active_player)


def test_calculate_damage_applies_weakness(charmander, squirtle):
    attacker = PokemonInPlay(card=squirtle)
    defender = PokemonInPlay(card=charmander)
    attack = squirtle.attacks[0]

    damage = rules.calculate_damage(attacker, attack, defender)

    assert damage == 40  # 20 base * 2 (fraqueza de Charmander a Water)


def test_calculate_damage_applies_resistance(charmander):
    attacker = PokemonInPlay(card=charmander)
    resistant_card = dataclasses.replace(
        charmander,
        id="resistant-mon",
        name="Resistant",
        weaknesses=[],
        resistances=[WeaknessResistance("Fire", "-30")],
    )
    defender = PokemonInPlay(card=resistant_card)

    damage = rules.calculate_damage(attacker, charmander.attacks[0], defender)

    assert damage == 0  # 20 base - 30 resistência, piso em 0


def test_energy_satisfies_cost():
    assert rules.energy_satisfies_cost(["Fire", "Colorless"], ["Fire", "Colorless"])
    assert rules.energy_satisfies_cost(["Fire", "Fire"], ["Fire", "Colorless"])
    assert not rules.energy_satisfies_cost(["Water"], ["Fire"])
    assert not rules.energy_satisfies_cost(["Fire"], ["Fire", "Colorless"])


def test_legal_actions_include_attack_only_with_enough_energy(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle)

    actions = rules.legal_actions(state)

    assert not any(isinstance(a, UseAttack) for a in actions)

    state.player.active.attached_energies.append("Fire")
    actions = rules.legal_actions(state)

    assert any(isinstance(a, UseAttack) for a in actions)


def test_attach_energy_marks_flag_and_prevents_second_attach(charmander, fire_energy):
    state = build_state(player_active=charmander, player_hand=[fire_energy, fire_energy])

    messages = rules.apply_action(state, AttachEnergy(hand_index=0, target_is_active=True))

    assert state.player.active.attached_energies == ["Fire"]
    assert state.player.has_attached_energy_this_turn is True
    assert messages

    actions = rules.legal_actions(state)
    assert not any(isinstance(a, AttachEnergy) for a in actions)


def test_attack_knocks_out_defender_and_awards_prize(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle, opponent_prizes=6)
    state.player.active.attached_energies.append("Fire")
    state.opponent.active.damage_counters = 50  # 10 hp restante, ataque de 20+ nocauteia
    state.opponent.bench = [PokemonInPlay(card=squirtle)]  # garante que a partida continua

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active is not None
    assert state.opponent.active.card.name == "Squirtle"
    assert len(state.player.prizes) == 5  # jogador atacante pegou 1 prêmio
    assert state.winner is None


def test_win_condition_when_prizes_exhausted(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle, player_prizes=1)
    state.player.active.attached_energies.append("Fire")
    state.opponent.active.damage_counters = 50

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.winner == PlayerId.PLAYER


def test_win_condition_when_opponent_has_no_pokemon_left(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle)
    state.player.active.attached_energies.append("Fire")
    state.opponent.active.damage_counters = 50
    state.opponent.bench = []

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.winner == PlayerId.PLAYER


def test_evolve_preserves_damage_and_attached_energy(charmander, charmeleon):
    state = build_state(player_active=charmander, player_hand=[charmeleon])
    state.player.active.damage_counters = 20
    state.player.active.attached_energies = ["Fire"]

    rules.apply_action(state, Evolve(hand_index=0, target_is_active=True))

    assert state.player.active.card.name == "Charmeleon"
    assert state.player.active.damage_counters == 20
    assert state.player.active.attached_energies == ["Fire"]


def test_cannot_evolve_pokemon_played_same_turn(charmander, charmeleon):
    state = build_state(player_hand=[charmeleon])
    state.player.active = PokemonInPlay(card=charmander, turn_played=state.turn_number)

    actions = rules.legal_actions(state)

    assert not any(isinstance(a, Evolve) for a in actions)


def test_retreat_switches_active_and_pays_energy_cost(charmander, squirtle):
    state = build_state(player_active=charmander)
    state.player.active.attached_energies = ["Fire"]
    state.player.bench = [PokemonInPlay(card=squirtle)]

    rules.apply_action(state, Retreat(bench_index=0))

    assert state.player.active.card.name == "Squirtle"
    assert state.player.bench[0].card.name == "Charmander"
    assert state.player.bench[0].attached_energies == []
    assert state.player.has_retreated_this_turn is True


def test_play_basic_to_bench_respects_max_bench_size(charmander, squirtle):
    state = build_state(player_active=charmander, player_hand=[squirtle])
    state.player.bench = [PokemonInPlay(card=charmander) for _ in range(5)]

    actions = rules.legal_actions(state)

    assert not any(isinstance(a, PlayBasicToBench) for a in actions)


def test_end_turn_draws_card_and_switches_active_player(charmander, squirtle):
    state = build_state(player_active=charmander, opponent_active=squirtle)
    state.opponent.deck = [_dummy_card("draw-me", "Drawn")]

    rules.apply_action(state, EndTurn())

    assert state.active_player == PlayerId.OPPONENT
    assert len(state.opponent.hand) == 1
    assert state.player.has_attached_energy_this_turn is False
