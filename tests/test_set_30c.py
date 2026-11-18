"""Set 30C: texto da TCGdex com HTML e os mecanismos novos das cartas dele."""

from __future__ import annotations

import dataclasses

import pytest

from pokemon_companion.cards_db.models import (
    Attack,
    Card,
    Supertype,
    WeaknessResistance,
    plain_text,
)
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import PlayTrainer, UseAttack
from pokemon_companion.engine.effects import attacks, core, passives
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay, StatusCondition

from .conftest import make_energy
from .test_passive_text import mon
from .test_rules import build_state


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender"))
    s.player.active.attached_energies = ["Colorless"]
    s.player.deck = [mon(f"Deck{i}") for i in range(10)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(10)]
    return s


def with_text(card: Card, text: str, damage: str = "") -> Card:
    return dataclasses.replace(
        card, attacks=[Attack(name="Test", cost=["Colorless"], damage=damage, text=text)]
    )


def attack(state, text: str, damage: str = "") -> None:
    state.player.active.card = with_text(state.player.active.card, text, damage)
    rules.apply_action(state, UseAttack(attack_index=0))


def item(name: str = "Poké Pad") -> Card:
    return Card(id=f"t-{name}", name=name, supertype=Supertype.TRAINER, subtypes=["Item"])


def test_plain_text_turns_tcgdex_html_into_symbols():
    raw = (
        'Discard all <span class="energy-symbol Lightning" title="Lightning">Lightning</span> '
        "Energy.\t<em>(Don't apply Weakness.)</em> Retreat Cost is ColorlessColorless less."
    )
    assert plain_text(raw) == (
        "Discard all {L} Energy. (Don't apply Weakness.) Retreat Cost is {C}{C} less."
    )
    assert plain_text("Draw 2 cards.") == "Draw 2 cards."


@pytest.mark.parametrize(
    "text",
    [
        "The Defending Pokémon's Weakness is now {L} until the end of your next turn. "
        "(Apply Weakness as ×2.)",
        "Place damage counters on your opponent's Active Pokémon until its remaining HP is 50.",
        "Discard a {R} Energy, a {W} Energy, and a {L} Energy from this Pokémon.",
        "Flip a coin for each Maushold you have in play. For each heads, discard the top 2 cards "
        "of your opponent's deck.",
        "Flip a coin until you get tails. Search your deck for an amount of Basic {L} Energy up "
        "to the number of heads and attach it to this Pokémon. Then, shuffle your deck.",
    ],
)
def test_reworded_texts_compile_through_effects_json(text):
    assert attacks.spec_for(Attack(name="X", text=text)) is not None


def test_ability_needs_both_named_pokemon_in_play(state):
    from pokemon_companion.engine.effects import abilities
    from pokemon_companion.engine.effects.core import Ctx

    text = (
        "Once during your turn, if you have Articuno and Zapdos in play, you may use this "
        "Ability. Attach a Basic Fire Energy card from your hand to this Pokémon."
    )
    moltres = PokemonInPlay(card=mon("Moltres", ability="Fiery Flapping", text=text))
    state.player.bench = [moltres, PokemonInPlay(card=mon("Articuno"))]
    state.player.hand = [make_energy("Fire Energy", "Fire")]

    def usable() -> bool:
        ctx = Ctx(state, PlayerId.PLAYER, None, None, [])
        return bool(abilities.usable_abilities(ctx, moltres))

    assert not usable()
    state.player.bench.append(PokemonInPlay(card=mon("Zapdos")))
    assert usable()


def test_knocked_out_holder_knocks_out_attacker_on_heads(state, monkeypatch):
    text = (
        "If this Pokémon is Knocked Out by damage from an attack from your opponent's Pokémon, "
        "flip a coin. If heads, the Attacking Pokémon is Knocked Out."
    )
    state.opponent.active = PokemonInPlay(card=mon("Gengar ex", hp=100, text=text))
    state.opponent.bench = [PokemonInPlay(card=mon("Next"))]
    state.player.bench = [PokemonInPlay(card=mon("Spare"))]
    monkeypatch.setattr(core, "coin", lambda: True)

    rules.apply_action(state, UseAttack(attack_index=0))

    assert any(c.name == "Attacker" for c in state.player.discard)


def test_opponent_active_cant_be_healed(state):
    text = "Your opponent's Active Pokémon can't be healed."
    state.opponent.bench = [PokemonInPlay(card=mon("Yveltal", text=text))]
    state.player.active.damage_counters = 50
    state.opponent.active.damage_counters = 50

    passives.refresh_hp_bonuses(state)

    assert core.heal(state.player.active, 30) == 0
    assert core.heal(state.opponent.active, 30) == 30


def test_heals_all_damage_if_still_asleep_at_checkup(state):
    text = (
        "If this Pokémon remains Asleep during Pokémon Checkup, heal all damage from this Pokémon."
    )
    snorlax = PokemonInPlay(card=mon("Snorlax", text=text), damage_counters=120)
    state.player.active = snorlax

    passives.checkup_extra(state, PlayerId.PLAYER)
    assert snorlax.damage_counters == 120  # acordado: nada

    snorlax.status = StatusCondition.ASLEEP
    passives.checkup_extra(state, PlayerId.PLAYER)
    assert snorlax.damage_counters == 0


def test_weakness_becomes_x3_with_partner_in_play(state):
    text = "If you have Volbeat in play, apply Weakness for both Active Pokémon as ×3."
    weak = dataclasses.replace(
        mon("Target", hp=500), weaknesses=[WeaknessResistance("Colorless", "×2")]
    )
    state.opponent.active = PokemonInPlay(card=weak)
    state.opponent.bench = [PokemonInPlay(card=mon("Illumise", text=text))]

    assert passives.weakness_multiplier(state) == 2
    state.opponent.bench.append(PokemonInPlay(card=mon("Volbeat")))
    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.opponent.active.damage_counters == 300


def test_named_team_counterattack(state):
    text = (
        "If your Wishiwashi or Wishiwashi ex is in the Active Spot and is damaged by an attack "
        "from your opponent's Pokémon (even if your Pokémon is Knocked Out), place 3 damage "
        "counters on the Attacking Pokémon."
    )
    state.opponent.bench = [PokemonInPlay(card=mon("Wishiwashi", text=text))]
    state.opponent.active = PokemonInPlay(card=mon("Wishiwashi ex"))

    rules.apply_action(state, UseAttack(attack_index=0))

    assert state.player.active.damage_counters == 30


def test_hp_bonus_with_spelled_out_energy_count(state):
    text = "If this Pokémon has 6 or more Grass Energy attached, it gets +250 HP."
    tree = PokemonInPlay(card=mon("Alolan Exeggutor", hp=160, text=text))
    tree.attached_energies = ["Grass"] * 5
    state.player.bench = [tree]
    assert passives.hp_bonus(state, tree) == 0
    tree.attached_energies.append("Grass")
    assert passives.hp_bonus(state, tree) == 250


def test_damage_per_counter_on_chosen_target(state):
    hurt = PokemonInPlay(card=mon("Hurt"), damage_counters=50)
    state.opponent.bench = [hurt]

    attack(
        state,
        "This attack does 30 damage to 1 of your opponent's Pokémon for each damage counter on "
        "that Pokémon.",
    )

    assert hurt.damage_counters == 50 + 150
    assert state.opponent.active.damage_counters == 0


def test_trainer_coin_lock_discards_on_tails(state, monkeypatch):
    state.player.trainer_coin_turn = state.turn_number
    state.player.hand = [item()]
    monkeypatch.setattr(core, "coin", lambda: False)

    rules.apply_action(state, PlayTrainer(hand_index=0))

    assert [c.name for c in state.player.discard] == ["Poké Pad"]
    assert state.player.played_this_turn == []


def test_trainer_coin_lock_is_set_for_opponents_next_turn(state):
    attack(
        state,
        "During your opponent's next turn, whenever they try to use a Trainer card from their "
        "hand, they flip a coin. If tails, your opponent discards that Trainer card instead of "
        "using it.",
        "60",
    )
    assert state.opponent.trainer_coin_turn == state.turn_number


def test_mill_three_keep_one(state):
    attack(state, "Discard the top 3 cards of your deck and put 1 of them into your hand.")

    assert len(state.player.hand) == 1
    assert len(state.player.discard) == 2
    assert len(state.player.deck) == 7


def test_take_prizes_with_exact_hand_size_then_shuffle_hand(state):
    state.player.hand = [mon(f"H{i}") for i in range(30)]
    deck_before = len(state.player.deck)

    attack(
        state,
        "If you have exactly 30 cards in your hand, take 2 Prize cards. If you do, shuffle your "
        "hand into your deck.",
    )

    assert len(state.player.prizes) == 4
    assert state.player.hand == []
    assert len(state.player.deck) == deck_before + 32


def test_damage_per_basic_energy_type(state):
    state.player.bench = [PokemonInPlay(card=mon("Small", hp=30)) for _ in range(2)]
    state.player.bench.append(PokemonInPlay(card=mon("Big", hp=200)))
    state.player.bench[2].attached_energies = ["Fire", "Water", "Fire"]

    attack(
        state,
        "This attack does 50 damage for each type of Basic Energy attached to all of your Pokémon.",
        "50×",
    )
    assert state.opponent.active.damage_counters == 50 * 2  # Colorless não é tipo básico


def test_damage_per_benched_pokemon_with_max_hp(state):
    state.player.bench = [PokemonInPlay(card=mon("Small", hp=30)) for _ in range(2)]
    state.player.bench.append(PokemonInPlay(card=mon("Big", hp=200)))

    attack(
        state,
        "This attack does 30 damage for each of your Benched Pokémon that has a maximum HP of 30.",
        "30×",
    )
    assert state.opponent.active.damage_counters == 60


def test_devolve_all_into_opponents_hand(state):
    evolved = PokemonInPlay(card=mon("Stage1"), prior_cards=[mon("Basic")])
    state.opponent.bench = [evolved]

    attack(
        state,
        "Devolve each of your opponent's evolved Pokémon by putting the highest Stage Evolution "
        "card on it into your opponent's hand.",
    )
    assert evolved.card.name == "Basic"
    assert "Stage1" in [c.name for c in state.opponent.hand]


def test_item_from_opponents_hand_to_bottom_of_deck(state):
    state.opponent.hand = [item("Rare Candy")]

    attack(
        state,
        "Your opponent reveals their hand, and you put an Item card you find there on the bottom "
        "of your opponent's deck.",
    )
    assert state.opponent.deck[-1].name == "Rare Candy"


def test_opponent_shuffles_hand_and_draws(state):
    state.opponent.hand = [mon(f"O{i}") for i in range(7)]

    attack(state, "Your opponent shuffles their hand into their deck and draws 4 cards.")

    assert len(state.opponent.hand) == 4 + 1  # + a compra do início do turno dele
