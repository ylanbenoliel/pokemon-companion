"""Compilador de texto de ataque: frases reais de cartas viram efeitos, e o
que não é reconhecido fica sem compilar (nunca com o significado errado)."""

from __future__ import annotations

import dataclasses

import pytest

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.models import Attack, Card, Supertype
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import UseAttack
from pokemon_companion.engine.effects import attacks, core, text_effects
from pokemon_companion.engine.game_state import PokemonInPlay

from .conftest import make_basic_pokemon
from .test_rules import build_state


def mon(name: str, hp: int = 300, **kwargs: object) -> Card:
    return make_basic_pokemon(name, hp, "Colorless", **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender"))
    s.player.deck = [mon(f"Deck{i}") for i in range(20)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(20)]
    return s


def attack_with(state, text: str, damage: str = "", name: str = "Compiled Test") -> None:
    attack = Attack(name=name, cost=["Colorless"], damage=damage, text=text)
    card = dataclasses.replace(mon("Attacker"), attacks=[attack])
    state.player.active = PokemonInPlay(card=card, attached_energies=["Colorless"])


def heads(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(core, "coin", lambda: value)


def test_unrecognized_text_does_not_compile():
    assert text_effects.compile_text("Do a little dance and make the opponent cry.") is None
    assert text_effects.compiled_spec("Do a little dance.") is None


def test_hand_registered_attacks_win_over_the_compiler():
    attack = Attack(name="Phantom Dive", cost=[], damage="200", text="Draw a card.")
    assert attacks.spec_for(attack) is attacks.ATTACKS["Phantom Dive"]


@pytest.mark.parametrize(
    "text",
    [
        # "a number of cards" não é o nome de uma carta
        "Search your deck for a number of cards up to the number of heads and put them "
        "into your hand. Then, shuffle your deck.",
        # "3 or more Energy" não é o nome de um Pokémon
        "If you have 3 or more Pokémon named Foo in play, this attack does 70 more damage.",
    ],
)
def test_ambiguous_sentences_stay_uncompiled(text):
    assert text_effects.compile_text(text) is None


def test_coins_and_damage_per_heads(state, monkeypatch):
    heads(monkeypatch, True)
    attack_with(state, "Flip 3 coins. This attack does 20 damage for each heads.", "20×")

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 60


@pytest.mark.parametrize("coin, expected", [(True, 50), (False, 0)])
def test_tails_gate_cancels_the_attack(state, monkeypatch, coin, expected):
    heads(monkeypatch, coin)
    attack_with(state, "Flip a coin. If tails, this attack does nothing.", "50")

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == expected


def test_condition_on_the_defender(state):
    attack_with(
        state,
        "If your opponent's Active Pokémon is a Pokémon ex, this attack does 90 more damage.",
        "30+",
    )
    ex_card = dataclasses.replace(mon("Big ex"), subtypes=["Basic", "ex"])
    state.opponent.active = PokemonInPlay(card=ex_card)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 120


def test_energy_in_play_condition_counts_energy(state):
    text = "If you have 3 or more Energy in play, this attack does 70 more damage."
    attack_with(state, text, "20+")
    state.player.bench = [PokemonInPlay(card=mon("B"), attached_energies=["Fire", "Water"])]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 90


def test_damage_to_any_target_becomes_an_option(state):
    attack_with(state, "This attack does 30 damage to 1 of your opponent's Pokémon.")
    state.opponent.bench = [PokemonInPlay(card=mon("Frail", hp=30))]
    targets = {a.target for a in rules.legal_actions(state) if isinstance(a, UseAttack)}
    assert targets == {("opp", -1), ("opp", 0)}

    rules.apply_action(state, UseAttack(attack_index=0, target=("opp", 0)))

    assert state.opponent.bench == []
    assert state.opponent.active.damage_counters == 0


def test_attach_to_benched_pokemon_never_goes_to_the_active(state):
    attack_with(
        state, "Attach a Basic Energy card from your discard pile to 1 of your Benched Pokémon."
    )
    state.player.bench = [PokemonInPlay(card=mon("Benched"))]
    state.player.discard = [BASIC_ENERGIES["Fire"]]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.player.bench[0].attached_energies == ["Fire"]
    assert state.player.active.attached_energies == ["Colorless"]


def test_search_by_card_name_puts_them_on_the_bench(state):
    attack_with(state, "Search your deck for up to 2 Froakie and put them onto your Bench.")
    state.player.deck = [mon("Froakie", 70), mon("Froakie", 70), mon("Other")]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert [m.card.name for m in state.player.bench] == ["Froakie", "Froakie"]


def test_before_doing_damage_runs_first(state):
    attack_with(
        state,
        "Before doing damage, discard all Pokémon Tools from your opponent's Active Pokémon.",
        "80",
    )
    cape = Card(id="t", name="Hero's Cape", supertype=Supertype.TRAINER, subtypes=["Tool"])
    state.opponent.active.tool = cape

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.tool is None
    assert state.opponent.discard[-1].name == "Hero's Cape"


def test_estimate_uses_expected_coins(state):
    attack_with(state, "Flip 4 coins. This attack does 50 damage for each heads.", "50×")
    attack = state.player.active.card.attacks[0]

    estimate = attacks.estimated_damage(state, state.active_player, state.player.active, attack)

    assert estimate == 100


def test_explain_lists_the_compiled_steps():
    lines = text_effects.explain(
        "Flip a coin. If heads, your opponent's Active Pokémon is now Paralyzed."
    )
    assert lines == ["pre: flip_one()", "after: se cara → status(Paralyzed)"]
