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


# --------------------------------------------------------------------------
# lote 2: marcadores com duração, custos e escolhas

from pokemon_companion.engine.actions import EndTurn  # noqa: E402
from pokemon_companion.engine.game_state import StatusCondition  # noqa: E402


def test_delayed_knock_out_happens_at_the_end_of_the_opponents_turn(state):
    attack_with(
        state,
        "At the end of your opponent's next turn, the Defending Pokémon will be Knocked Out.",
    )
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.card.name == "Defender"

    rules.apply_action(state, EndTurn())  # fim do turno do oponente

    assert len(state.player.prizes) == 5


def test_shield_blocks_only_the_matching_attackers(state):
    text = (
        "During your opponent's next turn, prevent all damage done to this Pokémon by attacks "
        "from Basic non-{C} Pokémon."
    )
    attack_with(state, text, "10")
    rules.apply_action(state, UseAttack(attack_index=0))
    # o Defender é Básico Incolor: o escudo não vale contra ele
    state.opponent.active.attached_energies = ["Colorless"]
    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.player.active.damage_counters == 20


def test_stronger_poison_in_the_checkup(state):
    attack_with(
        state,
        "Your opponent's Active Pokémon is now Poisoned. During Pokémon Checkup, put 8 damage "
        "counters on that Pokémon instead of 1.",
        "100",
    )

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.status == StatusCondition.POISONED
    assert state.opponent.active.damage_counters == 100 + 80


def test_discard_for_damage_stops_at_what_knocks_out(state):
    text = (
        "Discard up to 2 Energy cards from this Pokémon, and this attack does 120 damage for "
        "each card you discarded in this way."
    )
    attack_with(state, text, "120×")
    state.player.active.attached_energies = ["Fire", "Fire", "Fire"]
    state.opponent.active = PokemonInPlay(card=mon("Small", hp=100))

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.player.active.attached_energies == ["Fire", "Fire"]  # 1 bastou
    assert len(state.player.prizes) == 5


def test_hand_cost_not_paid_cancels(state):
    text = (
        "Discard 2 Basic {R} Energy cards from your hand. If you can't discard 2 cards in this "
        "way, this attack does nothing."
    )
    attack_with(state, text, "220")
    state.player.hand = [BASIC_ENERGIES["Fire"]]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 0
    assert state.player.hand == [BASIC_ENERGIES["Fire"]]


def test_if_you_do_follows_the_previous_action(state):
    text = (
        "Attach a Basic {G} Energy card from your hand to 1 of your Benched Pokémon. If you do, "
        "heal all damage from that Pokémon."
    )
    attack_with(state, text)
    hurt = PokemonInPlay(card=mon("Hurt"), damage_counters=90)
    state.player.bench = [hurt]
    state.player.hand = [BASIC_ENERGIES["Grass"]]

    rules.apply_action(state, UseAttack(attack_index=0))

    assert hurt.attached_energies == ["Grass"] and hurt.damage_counters == 0


def test_next_turn_bonus_applies_only_to_the_named_attack(state):
    attack_with(
        state,
        "During your next turn, this Pokémon's Echoed Voice attack does 80 more damage.",
        "30",
        name="Echoed Voice",
    )
    rules.apply_action(state, UseAttack(attack_index=0))
    rules.apply_action(state, EndTurn())  # turno do oponente sem atacar

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 30 + 110


def test_choose_then_shuffle_those_pokemon_into_the_deck(state):
    attack_with(
        state,
        "Choose 2 of your opponent's Benched Pokémon. Shuffle those Pokémon and all attached "
        "cards into your opponent's deck.",
    )
    state.opponent.bench = [PokemonInPlay(card=mon(n)) for n in ("A", "B", "C")]
    deck_before = len(state.opponent.deck)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert len(state.opponent.bench) == 1
    assert len(state.opponent.deck) == deck_before + 2 - 1  # +2 embaralhados, -1 compra


# --------------------------------------------------------------------------
# Treinadores e Estádios compilados

from pokemon_companion.engine.actions import PlayTrainer, UseStadium  # noqa: E402


def trainer_card(name: str, kind: str, text: str) -> Card:
    return Card(
        id=f"t-{name}", name=name, supertype=Supertype.TRAINER, subtypes=[kind], rules=[text]
    )


def test_compiled_supporter_draws_instead_when_the_condition_holds(state):
    lacey = trainer_card(
        "Test Lacey",
        "Supporter",
        "Shuffle your hand into your deck. Then, draw 4 cards. If your opponent has 3 or fewer "
        "Prize cards remaining, draw 8 cards instead.",
    )
    state.player.hand = [lacey, mon("X")]
    state.opponent.prizes = state.opponent.prizes[:2]

    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert len(state.player.hand) == 8


def test_requirement_and_discard_cost(state):
    iris = trainer_card(
        "Test Iris",
        "Supporter",
        "You can use this card only if you discard another card from your hand. Draw cards "
        "until you have 6 cards in your hand.",
    )
    state.player.hand = [iris]
    assert not any(isinstance(a, PlayTrainer) for a in rules.legal_actions(state))

    state.player.hand = [iris, mon("Junk")]
    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert [c.name for c in state.player.discard] == ["Junk", "Test Iris"]
    assert len(state.player.hand) == 6


def test_compiled_stadium_is_used_by_the_current_player(state):
    state.stadium = trainer_card(
        "Test Levincia",
        "Stadium",
        "Once during each player's turn, that player may put up to 2 Basic {L} Energy cards "
        "from their discard pile into their hand.",
    )
    state.player.discard = [BASIC_ENERGIES["Lightning"]] * 3
    assert UseStadium() in rules.legal_actions(state)

    rules.apply_action(state, UseStadium())

    assert [c.name for c in state.player.hand] == ["Lightning Energy"] * 2
