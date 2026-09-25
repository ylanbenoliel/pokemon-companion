"""Ferramentas compiladas do texto (como Habilidades de quem está equipado)."""

from __future__ import annotations

import dataclasses

from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import UseAttack
from pokemon_companion.engine.effects import passives
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay

from .test_effects import _attacker, mon, state  # noqa: F401


def tool(name: str, text: str) -> Card:
    return Card(
        id=f"t-{name}",
        name=name,
        supertype=Supertype.TRAINER,
        subtypes=["Item", "Pokémon Tool"],
        rules=[text],
    )


def ex(name: str, hp: int = 300) -> Card:
    return dataclasses.replace(mon(name, hp=hp), subtypes=["Basic", "ex"])


def test_maximum_belt_adds_damage_only_against_ex(state):
    _attacker(state, "Smash", "100")
    state.player.active.tool = tool(
        "Maximum Belt",
        "Attacks used by the Pokémon this card is attached to do 50 more damage to your "
        "opponent's Active Pokémon ex (before applying Weakness and Resistance).",
    )
    state.opponent.active = PokemonInPlay(card=ex("Big"))
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.damage_counters == 150


def test_berry_reduces_matching_type_and_is_discarded(state):
    _attacker(state, "Smash", "100")
    state.player.active.card = dataclasses.replace(state.player.active.card, types=["Fire"])
    berry = tool(
        "Occa Berry",
        "If the Pokémon this card is attached to is damaged by an attack from your opponent's "
        "{R} Pokémon, it takes 60 less damage (after applying Weakness and Resistance), and "
        "discard this card.",
    )
    state.opponent.active = PokemonInPlay(card=mon("Target", hp=300), tool=berry)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.damage_counters == 40
    assert state.opponent.active.tool is None and berry in state.opponent.discard


def test_survival_brace_keeps_10_hp_once(state):
    _attacker(state, "Smash", "200")
    brace = tool(
        "Survival Brace",
        "If the Pokémon this card is attached to has full HP and would be Knocked Out by damage "
        "from an attack from your opponent's Pokémon, it is not Knocked Out, and its remaining "
        "HP becomes 10. Then, discard this card.",
    )
    state.opponent.active = PokemonInPlay(card=mon("Target", hp=100), tool=brace)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.current_hp == 10
    assert state.opponent.active.tool is None


def test_rescue_board_lowers_retreat_and_frees_it_at_low_hp(state):
    board = tool(
        "Rescue Board",
        "The Retreat Cost of the Pokémon this card is attached to is {C} less. If that "
        "Pokémon's remaining HP is 30 or less, it has no Retreat Cost.",
    )
    heavy = dataclasses.replace(mon("Heavy", hp=100), retreat_cost=["Colorless"] * 3)
    state.player.active = PokemonInPlay(card=heavy, tool=board)
    assert passives.retreat_cost(state, PlayerId.PLAYER, state.player.active) == 2
    state.player.active.damage_counters = 80
    assert passives.retreat_cost(state, PlayerId.PLAYER, state.player.active) == 0


def test_amulet_of_hope_searches_when_knocked_out(state):
    _attacker(state, "Smash", "200")
    amulet = tool(
        "Amulet of Hope",
        "If the Pokémon this card is attached to is Knocked Out by damage from an attack from "
        "your opponent's Pokémon, search your deck for up to 3 cards and put them into your "
        "hand. Then, shuffle your deck.",
    )
    state.opponent.active = PokemonInPlay(card=mon("Target", hp=100), tool=amulet)
    state.opponent.bench = [PokemonInPlay(card=mon("Backup"))]
    hand = len(state.opponent.hand)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert len(state.opponent.hand) == hand + 3 + 1  # + a compra do turno dele


def test_heavy_baton_moves_basic_energy_to_the_bench_on_knock_out(state):
    _attacker(state, "Smash", "300")
    baton = tool(
        "Heavy Baton",
        "If the Pokémon this card is attached to has a Retreat Cost of exactly 4, is in the "
        "Active Spot, and is Knocked Out by damage from an attack from your opponent's Pokémon, "
        "move up to 3 Basic Energy cards from that Pokémon to your Benched Pokémon in any way "
        "you like.",
    )
    heavy = dataclasses.replace(mon("Heavy", hp=100), retreat_cost=["Colorless"] * 4)
    state.opponent.active = PokemonInPlay(
        card=heavy, tool=baton, attached_energies=["Fire", "Fire", "Fire", "Fire"]
    )
    state.opponent.bench = [PokemonInPlay(card=mon("Backup"))]
    rules.apply_action(state, UseAttack(attack_index=0))
    backup = next(m for m in state.opponent.all_pokemon_in_play() if m.card.name == "Backup")
    assert backup.attached_energies == ["Fire", "Fire", "Fire"]


BOMB = (
    "If the Pokémon this card is attached to isn't a Mega Evolution Pokémon ex, is in the Active "
    "Spot, and takes 240 or more damage from an attack from your opponent's Mega Evolution "
    "Pokémon ex (even if this Pokémon is Knocked Out), place 12 damage counters on the Attacking "
    "Pokémon. If you placed any damage counters in this way, discard this card."
)


def _bomb_target(state) -> None:
    state.opponent.active = PokemonInPlay(
        card=mon("Wall", hp=400), tool=tool("Tremendous Bomb", BOMB)
    )


def test_tremendous_bomb_ignores_attackers_that_are_not_mega_ex(state):
    _attacker(state, "Smash", "250")
    _bomb_target(state)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.player.active.damage_counters == 0
    assert state.opponent.active.tool is not None


def test_tremendous_bomb_blows_up_on_a_big_mega_ex_hit(state):
    _attacker(state, "Smash", "250")
    state.player.active.card = dataclasses.replace(
        state.player.active.card, name="Mega Big ex", subtypes=["Basic", "ex", "Mega"]
    )
    _bomb_target(state)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.player.active.damage_counters == 120
    assert state.opponent.active.tool is None
