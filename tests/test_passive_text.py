"""Habilidades passivas compiladas do texto: valem nos pontos em que as
regras já consultam as passivas escritas à mão."""

from __future__ import annotations

import dataclasses

import pytest

from pokemon_companion.cards_db.models import Ability, Attack, Card, Supertype
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import PlayTrainer, UseAttack
from pokemon_companion.engine.effects import passive_text, passives
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay, StatusCondition

from .conftest import make_basic_pokemon
from .test_rules import build_state


def mon(name: str, hp: int = 300, ability: str = "", text: str = "", damage: str = "100") -> Card:
    card = make_basic_pokemon(
        name, hp, "Colorless", attack_cost=["Colorless"], attack_damage=damage
    )
    if text:
        card = dataclasses.replace(card, abilities=[Ability(name=ability or name, text=text)])
    return card


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender"))
    s.player.active.attached_energies = ["Colorless"]
    s.player.deck = [mon(f"Deck{i}") for i in range(10)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(10)]
    return s


def test_unrecognized_passive_does_nothing():
    assert passive_text.compile_passive("This Pokémon sings a lovely song.") is None


def test_hand_written_passives_are_not_compiled_twice():
    curly = Ability(name="Curly Wall", text="This Pokémon takes 60 less damage from attacks.")
    assert passive_text.passives_of(curly) == ()


def test_self_damage_reduction(state):
    state.opponent.active = PokemonInPlay(
        card=mon(
            "Tank", ability="Solid Body", text="This Pokémon takes 30 less damage from attacks."
        )
    )

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 70


def test_team_bonus_only_for_matching_pokemon(state):
    cheer = mon(
        "Cheerleader",
        ability="Regal Cheer",
        text="Attacks used by your Pokémon do 20 more damage to your opponent's Active Pokémon.",
    )
    state.player.bench = [PokemonInPlay(card=cheer)]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 120


def test_non_stacking_passive_counts_once(state):
    text = "All of your Pokémon in play get +40 HP. The effect of Vibrant Dance doesn't stack."
    dancer = mon("Dancer", ability="Vibrant Dance", text=text)
    state.player.bench = [PokemonInPlay(card=dancer), PokemonInPlay(card=dancer)]

    passives.refresh_hp_bonuses(state)

    assert state.player.active.max_hp == 340


def test_bench_only_prevention(state):
    text = (
        "As long as this Pokémon is on your Bench, prevent all damage from and effects of attacks "
        "from your opponent's Pokémon done to this Pokémon."
    )
    hider = mon("Hider", ability="So Submerged", text=text)
    state.opponent.bench = [PokemonInPlay(card=hider)]
    sniper = dataclasses.replace(
        mon("Sniper"),
        attacks=[
            Attack(
                name="Snipe",
                cost=["Colorless"],
                damage="",
                text="This attack does 50 damage to 1 of your opponent's Benched Pokémon.",
            )
        ],
    )
    state.player.active = PokemonInPlay(card=sniper, attached_energies=["Colorless"])

    rules.apply_action(state, UseAttack(attack_index=0, target=("opp", 0)))

    assert state.opponent.bench[0].damage_counters == 0


def test_counterattack_when_active_is_hit(state):
    text = (
        "If this Pokémon is in the Active Spot and is damaged by an attack from your opponent's "
        "Pokémon, the Attacking Pokémon is now Poisoned."
    )
    state.opponent.active = PokemonInPlay(card=mon("Spiky", ability="Poison Point", text=text))

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.player.active.status == StatusCondition.POISONED


def test_sturdy_survives_only_from_full_hp(state):
    text = (
        "If this Pokémon has full HP and would be Knocked Out by damage from an attack, it is not "
        "Knocked Out, and its remaining HP becomes 10."
    )
    state.opponent.active = PokemonInPlay(card=mon("Sturdy", hp=60, ability="Sturdy", text=text))

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.current_hp == 10
    assert len(state.player.prizes) == 6


def test_item_lock_while_in_the_active_spot(state):
    text = (
        "As long as this Pokémon is in the Active Spot, your opponent can't play any Item cards "
        "from their hand."
    )
    state.opponent.active = PokemonInPlay(card=mon("Gazer", ability="Daunting Gaze", text=text))
    ball = Card(id="t", name="Poké Pad", supertype=Supertype.TRAINER, subtypes=["Item"])
    state.player.hand = [ball]

    assert not any(isinstance(a, PlayTrainer) for a in rules.legal_actions(state))

    state.opponent.bench = [state.opponent.active]
    state.opponent.active = PokemonInPlay(card=mon("Other"))
    assert any(isinstance(a, PlayTrainer) for a in rules.legal_actions(state))


def test_weaken_applies_before_weakness(state):
    text = (
        "As long as this Pokémon is in the Active Spot, attacks used by your opponent's Active "
        "Pokémon do 30 less damage."
    )
    weak = dataclasses.replace(
        mon("Scary", ability="Intimidating Fang", text=text),
        weaknesses=[dataclasses.replace(passive_weakness(), energy_type="Colorless")],
    )
    state.opponent.active = PokemonInPlay(card=weak)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == (100 - 30) * 2


def passive_weakness():
    from pokemon_companion.cards_db.models import WeaknessResistance

    return WeaknessResistance(energy_type="Colorless", value="×2")


def test_opponent_retreat_cost_goes_up(state):
    text = "Your opponent's Active Evolution Pokémon's Retreat Cost is {C} more."
    state.opponent.bench = [PokemonInPlay(card=mon("Netter", ability="Big Net", text=text))]
    evolved = dataclasses.replace(mon("Evolved"), evolves_from="Baby", subtypes=["Stage 1"])
    state.player.active = PokemonInPlay(card=evolved)

    assert passives.retreat_cost(state, PlayerId.PLAYER, state.player.active) == 2


# --------------------------------------------------------------------------
# prêmios, supressão, evolução, custo e gatilhos


def _ex(name: str, hp: int = 300, **kwargs: object) -> Card:
    return dataclasses.replace(mon(name, hp, **kwargs), subtypes=["Basic", "ex"])


def test_fragile_husk_gives_no_prizes_to_ex(state):
    text = (
        "If this Pokémon is Knocked Out by damage from an attack from your opponent's Pokémon ex, "
        "your opponent can't take any Prize cards for it."
    )
    state.player.active = PokemonInPlay(card=_ex("Big"), attached_energies=["Colorless"])
    state.opponent.active = PokemonInPlay(
        card=mon("Husk", hp=50, ability="Fragile Husk", text=text)
    )
    state.opponent.bench = [PokemonInPlay(card=mon("Next"))]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert len(state.player.prizes) == 6


def test_team_prize_reduction_against_ex(state):
    text = (
        "If 1 of your {D} Pokémon is Knocked Out by damage from an attack from your opponent's "
        "Pokémon ex, that player takes 1 fewer Prize card. The effect of Shadowy Concealment "
        "doesn't stack."
    )
    victim = dataclasses.replace(_ex("Dark ex", hp=50), types=["Darkness"])
    state.opponent.active = PokemonInPlay(card=victim)
    state.opponent.bench = [
        PokemonInPlay(card=mon("Gengar", ability="Shadowy Concealment", text=text))
    ]
    state.player.active = PokemonInPlay(card=_ex("Big"), attached_energies=["Colorless"])

    rules.apply_action(state, UseAttack(attack_index=0))

    assert len(state.player.prizes) == 6 - 1  # 2 do ex, menos 1


def test_opponent_active_loses_its_abilities(state):
    from pokemon_companion.engine.effects import passives

    silencer = mon(
        "Flutter Mane",
        ability="Midnight Fluttering",
        text=(
            "As long as this Pokémon is in the Active Spot, your opponent's Active Pokémon has no "
            "Abilities, except for Midnight Fluttering."
        ),
    )
    state.player.active = PokemonInPlay(card=silencer)
    tank = mon("Tank", ability="Solid Body", text="This Pokémon takes 30 less damage from attacks.")
    state.opponent.active = PokemonInPlay(card=tank)

    assert not passives.ability_active(state, state.opponent.active, "Solid Body")
    state.opponent.bench = [state.opponent.active]
    state.opponent.active = PokemonInPlay(card=mon("Other"))
    assert passives.ability_active(state, state.opponent.bench[0], "Solid Body")


def test_early_evolution_on_the_turn_it_was_played(state):
    from pokemon_companion.engine.actions import Evolve

    text = (
        "As long as this Pokémon is in the Active Spot, it can evolve during your first turn or "
        "the turn you play it."
    )
    eevee = mon("Eevee", ability="Boosted Evolution", text=text)
    state.player.active = PokemonInPlay(card=eevee, turn_played=state.turn_number)
    evolution = dataclasses.replace(mon("Vaporeon"), evolves_from="Eevee", subtypes=["Stage 1"])
    state.player.hand = [evolution]

    assert Evolve(hand_index=0, target_is_active=True) in rules.legal_actions(state)


def test_attack_cost_discount_per_opponent_bench(state):
    text = "Attacks used by this Pokémon cost {C} less for each of your opponent's Benched Pokémon."
    hustler = dataclasses.replace(
        mon("Incineroar ex", ability="Hustle Play", text=text),
        attacks=[Attack(name="Big", cost=["Colorless", "Colorless"], damage="200")],
    )
    state.player.active = PokemonInPlay(card=hustler)
    state.opponent.bench = [PokemonInPlay(card=mon("A")), PokemonInPlay(card=mon("B"))]

    assert (
        passives.attack_cost(state, PlayerId.PLAYER, state.player.active, hustler.attacks[0]) == []
    )


def test_energy_attach_trigger_places_counters(state):
    from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
    from pokemon_companion.engine.actions import AttachEnergy

    text = (
        "Whenever your opponent attaches an Energy card from their hand to 1 of their Pokémon, put "
        "2 damage counters on that Pokémon."
    )
    state.opponent.bench = [
        PokemonInPlay(card=mon("Gengar ex", ability="Gnawing Curse", text=text))
    ]
    state.player.hand = [BASIC_ENERGIES["Fire"]]

    rules.apply_action(state, AttachEnergy(hand_index=0, target_is_active=True))

    assert state.player.active.damage_counters == 20


def test_infinite_shadow_returns_to_hand(state):
    ghost = mon("Gengar", hp=50, ability="Infinite Shadow", text="(à mão)")
    state.opponent.active = PokemonInPlay(card=ghost)
    state.opponent.bench = [PokemonInPlay(card=mon("Next"))]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert ghost in state.opponent.hand and ghost not in state.opponent.discard
