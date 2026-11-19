"""Efeitos do Expanded: redação antiga traduzida para a atual (`wording`),
condições de Pokémon-EX/GX/V, Energias Especiais pelo texto e os Treinadores
do meta do Expanded."""

from __future__ import annotations

import dataclasses

import pytest

from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects import core, passives, text_effects, wording
from pokemon_companion.engine.effects.cardinfo import special_energy_units
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay, StatusCondition

from .conftest import make_basic_pokemon
from .test_rules import build_state
from .test_text_effects import attack_with, heads, mon


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender"))
    s.player.deck = [mon(f"Deck{i}") for i in range(20)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(20)]
    return s


def _play(state, text: str) -> None:
    spec = text_effects.compiled_trainer(text)
    assert spec is not None, text_effects.unknown_sentences(text)
    ctx = core.Ctx(state, PlayerId.PLAYER)
    assert spec.can_play(ctx)
    spec.fn(ctx)


def _special(name: str, text: str) -> Card:
    return Card(
        id=f"e-{name}", name=name, supertype=Supertype.ENERGY, subtypes=["Special"], rules=[text]
    )


@pytest.mark.parametrize(
    "old",
    [
        "Flip 3 coins. This attack does 20 damage times the number of heads.",
        "Search your deck for a card and put it into your hand. Shuffle your deck afterward.",
        "Discard an Energy attached to this Pokémon.",
        "Flip a coin. If heads, discard an Energy attached to the Defending Pokémon.",
        "The Defending Pokémon can't attack during your opponent's next turn.",
        "Your opponent switches the Defending Pokémon with 1 of his or her Benched Pokémon.",
        "During your opponent's next turn, any damage done to this Pokémon by attacks is "
        "reduced by 20.",
        "Flip a coin. If heads, prevent all effects of attacks, including damage, done to this "
        "Pokémon during your opponent's next turn.",
        "Does 10 damage to each of your Benched Pokémon.",
        "This attack does 100 damage minus 10 damage for each damage counter on this Pokémon.",
    ],
)
def test_old_wording_compiles_through_the_modern_phrases(old):
    assert text_effects.compile_text(old) is not None, text_effects.unknown_sentences(old)


def test_modernize_only_kicks_in_when_the_original_is_unknown():
    modern = "During your next turn, this Pokémon can't attack"
    assert wording.modernize(modern) == modern
    assert wording.modernize("This Pokémon can't attack during your next turn") == modern
    assert "their" in wording.modernize("Your opponent reveals his or her hand")


def test_times_the_number_of_heads_scales_with_the_coins(state, monkeypatch):
    from pokemon_companion.engine.effects import attacks

    heads(monkeypatch, True)
    attack_with(state, "Flip 3 coins. This attack does 20 damage times the number of heads.", "20×")
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert state.opponent.active.damage_counters == 60


def test_old_rule_kind_conditions_check_the_defender(state):
    text = "If your opponent's Active Pokémon is a Pokémon-GX or a Pokémon-EX, this attack does 30 more damage."
    program = text_effects.compile_text(text)
    assert program is not None
    gx = dataclasses.replace(mon("Big"), subtypes=["Basic", "TAG TEAM-GX"])
    state.opponent.active = PokemonInPlay(card=gx)
    attack_with(state, text, "30+")
    from pokemon_companion.engine.effects import attacks

    damage = attacks.estimated_damage(
        state, PlayerId.PLAYER, state.player.active, state.player.active.card.attacks[0]
    )
    assert damage == 60


def test_special_energy_text_says_what_it_provides(state):
    dce = _special("Double Colorless Energy", "Double Colorless Energy provides {C}{C} Energy.")
    unit = _special(
        "Unit Energy GrassFireWater",
        "This card provides Colorless Energy. While this card is attached to a Pokémon, it "
        "provides Grass, Fire, and Water Energy but provides only 1 Energy at a time.",
    )
    rainbow = _special(
        "Rainbow Energy",
        "This card provides Colorless Energy. When you attach this card from your hand to 1 of "
        "your Pokémon, put 1 damage counter on that Pokémon.",
    )
    assert special_energy_units(dce) == ["Colorless", "Colorless"]
    assert special_energy_units(unit) == ["Grass|Fire|Water"]
    assert special_energy_units(rainbow) is None  # tem outro efeito: não é só energia

    holder = state.player.active
    core.attach_energy_card(holder, dce)
    core.attach_energy_card(holder, unit)
    units = passives.provided_energy(state, PlayerId.PLAYER, holder)
    assert units == ["Colorless", "Colorless", "Grass|Fire|Water"]
    assert passives.energy_satisfies(units, ["Fire", "Colorless", "Colorless"])


def test_n_shuffles_both_hands_and_draws_by_remaining_prizes(state):
    state.player.hand = [mon("A"), mon("B")]
    state.opponent.hand = [mon("C")]
    state.player.prizes = [mon("P")] * 2
    state.opponent.prizes = [mon("Q")] * 5
    _play(
        state,
        "Each player shuffles his or her hand into his or her deck. Then, each player draws a "
        "card for each of his or her remaining Prize cards.",
    )
    assert len(state.player.hand) == 2 and len(state.opponent.hand) == 5


def test_marnie_draws_only_if_cards_went_to_the_bottom(state):
    text = (
        "Each player shuffles their hand and puts it on the bottom of their deck. If either "
        "player put any cards on the bottom of their deck in this way, you draw 5 cards, and "
        "your opponent draws 4 cards."
    )
    state.player.hand, state.opponent.hand = [], []
    _play(state, text)
    assert state.player.hand == [] and state.opponent.hand == []
    state.opponent.hand = [mon("C")]
    _play(state, text)
    assert len(state.player.hand) == 5 and len(state.opponent.hand) == 4


def test_typed_team_bonus_lasts_the_turn(state):
    _play(
        state,
        "During this turn, your {F} Pokémon's attacks do 30 more damage to your opponent's "
        "Active Pokémon.",
    )
    fighter = PokemonInPlay(card=make_basic_pokemon("Hitmon", 90, "Fighting"))
    other = PokemonInPlay(card=make_basic_pokemon("Pika", 60, "Lightning"))
    defender = state.opponent.active
    assert passives.attacker_bonus(state, PlayerId.PLAYER, fighter, defender) == 30
    assert passives.attacker_bonus(state, PlayerId.PLAYER, other, defender) == 0


def test_field_blower_hits_the_opponents_tools_then_their_stadium(state):
    tool = Card(id="t", name="Fighting Fury Belt", supertype=Supertype.TRAINER, subtypes=["Tool"])
    stadium = Card(id="s", name="Parallel City", supertype=Supertype.TRAINER, subtypes=["Stadium"])
    state.opponent.active.tool = tool
    state.stadium, state.stadium_owner = stadium, PlayerId.OPPONENT
    _play(
        state,
        "Choose up to 2 in any combination of Pokémon Tool cards and Stadium cards in play "
        "(yours or your opponent's) and discard them.",
    )
    assert state.opponent.active.tool is None and state.stadium is None
    assert {c.name for c in state.opponent.discard} == {"Fighting Fury Belt", "Parallel City"}


def test_choose_one_runs_the_first_option(state):
    state.player.discard = [mon("Fallen")]
    _play(
        state,
        "Choose 1:\n\n• Put a Pokémon from your discard pile into your hand.\n• Shuffle 3 "
        "Pokémon from your discard pile into your deck.",
    )
    assert [c.name for c in state.player.hand] == ["Fallen"]


def test_old_requirement_wording_still_charges_the_cost(state):
    state.player.hand = [mon("Fodder"), mon("Fodder 2")]
    state.player.deck = [mon("Basic")] + state.player.deck
    _play(
        state,
        "You can play this card only if you discard another card from your hand. Search your "
        "deck for a Basic Pokémon, reveal it, and put it into your hand. Then, shuffle your deck.",
    )
    assert len(state.player.discard) == 1  # o custo
    assert len(state.player.hand) == 2  # 1 que sobrou + o Básico buscado


def test_target_whistle_benches_the_opponents_weakest_basic(state):
    state.opponent.discard = [mon("Tank", 200), mon("Small", 30)]
    _play(state, "Put a Basic Pokémon from your opponent's discard pile onto his or her Bench.")
    assert [m.card.name for m in state.opponent.bench] == ["Small"]


def test_acerola_only_picks_up_a_damaged_pokemon(state):
    state.player.bench = [PokemonInPlay(card=mon("Hurt"), damage_counters=50)]
    _play(
        state,
        "Put 1 of your Pokémon that has any damage counters on it and all cards attached to it "
        "into your hand.",
    )
    assert [c.name for c in state.player.hand] == ["Hurt"]


def test_max_elixir_attaches_only_to_a_benched_basic(state):
    from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES

    state.player.deck = [BASIC_ENERGIES["Fire"]] + state.player.deck
    state.player.bench = [PokemonInPlay(card=mon("Bencher"))]
    _play(
        state,
        "Look at the top 6 cards of your deck and attach a basic Energy card you find there to a "
        "Basic Pokémon on your Bench. Shuffle the other cards back into your deck.",
    )
    assert state.player.bench[0].attached_energies == ["Fire"]
    assert state.player.active.attached_energies == []


def test_full_heal_and_hypnotoxic_laser(state, monkeypatch):
    state.player.active.status = StatusCondition.CONFUSED
    _play(state, "Remove all Special Conditions from your Active Pokémon.")
    assert state.player.active.status == StatusCondition.NONE
    heads(monkeypatch, True)
    _play(
        state,
        "Your opponent's Active Pokémon is now Poisoned. Flip a coin. If heads, your opponent's "
        "Active Pokémon is also Asleep.",
    )
    assert state.opponent.active.status == StatusCondition.ASLEEP
