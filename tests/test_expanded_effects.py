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


def _rule_mon(name: str, *subtypes: str, hp: int = 180) -> Card:
    return dataclasses.replace(mon(name, hp), subtypes=["Basic", *subtypes])


def test_great_catcher_only_pulls_gx_or_ex(state):
    state.player.hand = [mon("A"), mon("B"), mon("Great Catcher")]
    state.opponent.bench = [
        PokemonInPlay(card=mon("Small", 30)),
        PokemonInPlay(card=_rule_mon("Shaymin-EX", "EX")),
    ]
    _play(
        state,
        "You can play this card only if you discard 2 other cards from your hand. Switch 1 of "
        "your opponent's Benched Pokémon-GX or Pokémon-EX with their Active Pokémon.",
    )
    assert state.opponent.active.card.name == "Shaymin-EX"
    assert len(state.player.discard) == 2


def test_brigette_takes_the_option_that_brings_more_pokemon(state):
    state.player.deck = [_rule_mon("Shaymin-EX", "EX")] + [mon(f"Joltik{i}", 40) for i in range(3)]
    _play(
        state,
        "Search your deck for 1 Basic Pokémon-EX or 3 Basic Pokémon (except for Pokémon-EX) and "
        "put them onto your Bench. Shuffle your deck afterward.",
    )
    assert len(state.player.bench) == 3


def test_roxie_discards_only_non_rule_box_pokemon_and_draws_for_each(state):
    state.player.hand = [_rule_mon("Tapu Lele-GX", "GX"), mon("Zubat"), mon("Golbat")]
    _play(
        state,
        "Discard up to 2 Pokémon that aren't Pokémon-GX or Pokémon-EX from your hand. Draw 3 "
        "cards for each card you discarded in this way.",
    )
    assert {c.name for c in state.player.discard} == {"Zubat", "Golbat"}
    assert len(state.player.hand) == 1 + 6


def test_guzma_and_hala_pays_the_optional_cost_for_the_extra_search(state):
    tool = Card(id="t", name="Choice Band", supertype=Supertype.TRAINER, subtypes=["Tool"])
    stadium = Card(id="s", name="Shrine", supertype=Supertype.TRAINER, subtypes=["Stadium"])
    energy = _special("Double Colorless Energy", "Double Colorless Energy provides {C}{C} Energy.")
    state.player.deck = [tool, stadium, energy] + state.player.deck
    state.player.hand = [mon("A"), mon("B"), mon("C"), mon("D")]
    _play(
        state,
        "Search your deck for a Stadium card, reveal it, and put it into your hand. Then, shuffle "
        "your deck. When you play this card, you may discard 2 other cards from your hand. If "
        "you do, you may also search for a Pokémon Tool card and a Special Energy card in this "
        "way.",
    )
    names = {c.name for c in state.player.hand}
    assert {"Shrine", "Choice Band", "Double Colorless Energy"} <= names
    assert len(state.player.discard) == 2


def test_faba_removes_the_opponents_special_energy_from_the_game(state):
    dce = _special("Double Colorless Energy", "Double Colorless Energy provides {C}{C} Energy.")
    core.attach_energy_card(state.opponent.active, dce)
    _play(
        state,
        "Choose a Pokémon Tool or Special Energy card attached to 1 of your opponent's Pokémon, "
        "or any Stadium card in play, and put it in the Lost Zone.",
    )
    assert state.opponent.active.attached_energies == []
    assert dce not in state.opponent.discard


def test_az_picks_up_the_damaged_pokemon_and_discards_what_was_attached(state):
    benched = PokemonInPlay(card=mon("Hurt"), attached_energies=["Fire"], damage_counters=90)
    state.player.bench = [benched]
    _play(
        state, "Put 1 of your Pokémon into your hand. (Discard all cards attached to that Pokémon.)"
    )
    assert [c.name for c in state.player.hand] == ["Hurt"]
    assert [c.name for c in state.player.discard] == ["Fire Energy"]


def test_devolution_spray_keeps_the_damage_and_drops_a_stage(state):
    basic, evolved = mon("Abra", 50), mon("Kadabra", 80)
    state.player.bench = [PokemonInPlay(card=evolved, prior_cards=[basic], damage_counters=40)]
    _play(
        state,
        "Devolve 1 of your evolved Pokémon and put the highest Stage Evolution card on it into "
        "your hand. (That Pokémon can't evolve this turn.)",
    )
    assert state.player.bench[0].card.name == "Abra"
    assert state.player.bench[0].damage_counters == 40
    assert [c.name for c in state.player.hand] == ["Kadabra"]


def test_pokemon_ranger_clears_attack_effects_but_not_damage(state):
    target = state.player.active
    target.cannot_attack_turn, target.damage_counters = state.turn_number + 1, 30
    state.player.items_blocked_turn = state.turn_number + 1
    _play(state, "Remove all effects of attacks on each player and his or her Pokémon.")
    assert target.cannot_attack_turn is None and target.damage_counters == 30
    assert state.player.items_blocked_turn is None


def test_hex_maniac_locks_every_ability_until_the_end_of_the_next_turn(state):
    from pokemon_companion.cards_db.models import Ability

    holder = PokemonInPlay(
        card=dataclasses.replace(mon("Shaymin"), abilities=[Ability(name="Set Up", text="x")])
    )
    state.opponent.bench = [holder]
    assert passives.ability_active(state, holder, "Set Up")
    _play(
        state,
        "Until the end of your opponent's next turn, each Pokémon in play, in each player's "
        "hand, and in each player's discard pile has no Abilities. (This includes cards that "
        "come into play on that turn.)",
    )
    assert not passives.ability_active(state, holder, "Set Up")
    state.turn_number += 2
    assert passives.ability_active(state, holder, "Set Up")


# --- formas que se repetem (2+ cartas) -------------------------------------


@pytest.mark.parametrize(
    "old",
    [
        "Put 1 damage counter on your opponent's Active Pokémon.",
        "If any of your Pokémon were Knocked Out by damage from an opponent's attack during their "
        "last turn, this attack does 60 more damage.",
        "Both this Pokémon and the Defending Pokémon are now Asleep.",
        "Look at the top 4 cards of your deck and put them back on top of your deck in any order.",
        "Discard all Energy from both Active Pokémon.",
        "Flip a coin. If heads, switch 1 of your opponent's Benched Pokémon with the Defending "
        "Pokémon.",
        "Before doing damage, discard a Pokémon Tool card attached to the Defending Pokémon.",
        "Heal all damage from all of your Pokémon.",
        "If the Defending Pokémon is a Pokémon V, it can't attack during your opponent's next turn.",
        "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
        "Pokémon-GX and Pokémon-EX.",
    ],
)
def test_repeated_old_forms_compile(old):
    assert text_effects.compile_text(old) is not None, text_effects.unknown_sentences(old)


def test_look_at_top_and_keep_some(state):
    state.player.deck = [mon(f"Top{i}") for i in range(5)] + state.player.deck
    _play(
        state,
        "Look at the top 5 cards of your deck and put 2 of them into your hand. Shuffle the other "
        "cards back into your deck.",
    )
    assert len(state.player.hand) == 2


def test_random_cards_from_the_opponents_hand_go_back_to_their_deck(state):
    state.opponent.hand = [mon("X"), mon("Y"), mon("Z")]
    attack_with(
        state,
        "Choose 2 random cards from your opponent's hand. Your opponent reveals those cards and "
        "shuffles them into their deck.",
    )
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    from pokemon_companion.engine.effects import attacks

    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert len(state.opponent.hand) == 1


def test_damage_to_a_damaged_or_rule_box_target_only(state):
    from pokemon_companion.engine.effects import attacks

    hurt = PokemonInPlay(card=mon("Hurt", 100), damage_counters=60)
    healthy = PokemonInPlay(card=mon("Healthy", 60))
    v = PokemonInPlay(card=_rule_mon("Zacian V", "V"))
    state.opponent.bench = [healthy, hurt, v]
    attack_with(
        state,
        "This attack does 40 damage to 1 of your opponent's Pokémon that has any damage counters "
        "on it.",
    )
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert hurt.damage_counters == 100 and healthy.damage_counters == 0

    attack_with(state, "This attack does 50 damage to 1 of your opponent's Pokémon V.")
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert v.damage_counters == 50 and healthy.damage_counters == 0


def test_trainer_lock_and_damage_cap(state):
    _play(
        state, "Your opponent can't play any Trainer cards from their hand during their next turn."
    )
    turn = state.turn_number + 1
    assert state.opponent.items_blocked_turn == state.opponent.supporters_blocked_turn == turn

    from pokemon_companion.engine.effects import attacks

    state.opponent.bench = [PokemonInPlay(card=mon(f"B{i}")) for i in range(5)]
    attack_with(
        state,
        "This attack does 20 more damage for each of your opponent's Benched Pokémon. You can't "
        "add more than 40 damage in this way.",
        "10+",
    )
    attack = state.player.active.card.attacks[0]
    assert attacks.estimated_damage(state, PlayerId.PLAYER, state.player.active, attack) == 50


def test_new_conditions_and_counts(state):
    from pokemon_companion.engine.effects import attacks

    state.player.active.evolved_this_turn = True
    attack_with(state, "If this Pokémon evolved during this turn, this attack does nothing.", "50")
    state.player.active.evolved_this_turn = True
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert state.opponent.active.damage_counters == 0

    fire = make_basic_pokemon("Fire", 60, "Fire")
    state.player.bench = [PokemonInPlay(card=fire), PokemonInPlay(card=mon("Plain"))]
    state.opponent.bench = [PokemonInPlay(card=_rule_mon("Eternatus VMAX", "VMAX"))]
    for text, expected in [
        ("This attack does 10 more damage for each of your Benched {R} Pokémon.", 20),
        ("This attack does 10 more damage for each different type of Pokémon on your Bench.", 30),
        ("If your opponent has any Pokémon VMAX in play, this attack does 50 more damage.", 60),
    ]:
        attack_with(state, text, "10+")
        attack = state.player.active.card.attacks[0]
        assert attacks.estimated_damage(state, PlayerId.PLAYER, state.player.active, attack) == (
            expected
        ), text


def test_old_passives_now_work(state):
    from pokemon_companion.engine.effects import passive_text

    holder = PokemonInPlay(
        card=dataclasses.replace(
            mon("Holder"),
            abilities=[
                passive_ability("This Pokémon can't be affected by any Special Conditions.")
            ],
        )
    )
    state.player.active = holder
    assert passives.immune_to(state, holder, StatusCondition.ASLEEP)

    for text in [
        "Your Basic Pokémon in play have no Retreat Cost.",
        "As long as this Pokémon is your Active Pokémon, your opponent's Active Pokémon can't "
        "retreat.",
        "This Pokémon's attacks do 30 more damage to your opponent's {W} Pokémon.",
        "Prevent all effects of attacks, including damage, done to this Pokémon by your "
        "opponent's Pokémon-GX or Pokémon-EX.",
        "If this Pokémon is your Active Pokémon and is damaged by an opponent's attack, put 3 "
        "damage counters on the Attacking Pokémon.",
        "As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by "
        "attacks.",
    ]:
        assert passive_text.compile_passive(text), text


def passive_ability(text: str):
    from pokemon_companion.cards_db.models import Ability

    return Ability(name="Old Passive", text=text)


def test_rule_kind_shield_blocks_only_those_attackers(state):
    _play_attack = text_effects.compile_text(
        "During your opponent's next turn, prevent all damage done to this Pokémon by attacks "
        "from Pokémon-GX and Pokémon-EX."
    )
    assert _play_attack is not None
    me = state.player.active
    me.shield = ("rule:Pokémon-GX and Pokémon-EX", state.turn_number)
    gx = PokemonInPlay(card=_rule_mon("Zoroark-GX", "GX"))
    plain = PokemonInPlay(card=mon("Plain"))
    assert passives.shield_blocks(state, me, gx, 100)
    assert not passives.shield_blocks(state, me, plain, 100)


# --- lote 4 -----------------------------------------------------------------


def _attack(state, text: str, damage: str = "") -> None:
    from pokemon_companion.engine.effects import attacks

    attack_with(state, text, damage)
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])


def test_timeless_gx_gives_the_same_player_another_turn():
    from pokemon_companion.cards_db.models import Attack
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.actions import UseAttack

    from .test_rules import build_state as fresh

    timeless = Attack(
        name="Timeless GX",
        cost=[],
        damage="",
        text="Take another turn after this one. (Skip the between-turns step.) (You can't use "
        "more than 1 GX attack in a game.)",
    )
    state = fresh(
        player_active=dataclasses.replace(mon("Dialga-GX"), attacks=[timeless]),
        opponent_active=mon("Target"),
        player_deck=[mon(f"D{i}") for i in range(5)],
    )
    turn = state.turn_number
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.active_player == PlayerId.PLAYER
    assert state.turn_number == turn + 2  # mantém a paridade dos efeitos com duração
    assert state.extra_turn_for is None


def test_intrepid_sword_attaches_metal_and_keeps_the_rest(state):
    from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
    from pokemon_companion.cards_db.models import Ability
    from pokemon_companion.engine.effects import abilities

    sword = Ability(
        name="Intrepid Sword",
        text="Once during your turn, you may look at the top 3 cards of your deck and attach any "
        "number of Metal Energy cards you find there to this Pokémon. Put the other cards into "
        "your hand. If you use this Ability, your turn ends.",
    )
    zacian = dataclasses.replace(mon("Zacian V"), abilities=[sword])
    state.player.active = PokemonInPlay(card=zacian)
    state.player.deck = [BASIC_ENERGIES["Metal"], mon("Other"), BASIC_ENERGIES["Metal"]]
    state.player.deck += [mon(f"Deep{i}") for i in range(5)]
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    abilities.use_ability(ctx, "Intrepid Sword")
    assert state.player.active.attached_energies == ["Metal", "Metal"]
    assert [c.name for c in state.player.hand] == ["Other"]
    assert ctx.ends_turn


def test_copy_anything_only_copies_what_it_can_pay(state):
    from pokemon_companion.cards_db.models import Attack

    big = Attack(name="Big", cost=["Fire", "Fire"], damage="200")
    small = Attack(name="Small", cost=["Colorless"], damage="30")
    state.opponent.active = PokemonInPlay(
        card=dataclasses.replace(mon("Foe"), attacks=[big, small])
    )
    attack_with(
        state,
        "Choose 1 of your opponent's Pokémon's attacks and use it as this attack. If this Pokémon "
        "doesn't have the necessary Energy to use that attack, this attack does nothing.",
    )
    state.player.active.attached_energies = ["Colorless"]
    from pokemon_companion.engine.effects import attacks

    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    attacks.resolve_attack(ctx, state.player.active.card.attacks[0])
    assert state.opponent.active.damage_counters == 30


def test_reveal_and_discard_a_trainer_from_the_opponents_hand(state):
    item = Card(id="i", name="Ultra Ball", supertype=Supertype.TRAINER, subtypes=["Item"])
    state.opponent.hand = [mon("Keep"), item]
    _attack(state, "Your opponent reveals his or her hand. Discard a Trainer card you find there.")
    assert [c.name for c in state.opponent.discard] == ["Ultra Ball"]


def test_heads_to_each_opponent_pokemon_and_discarding_their_energy(state, monkeypatch):
    heads(monkeypatch, True)
    state.opponent.bench = [PokemonInPlay(card=mon("Benched"), attached_energies=["Fire"])]
    state.opponent.active.attached_energies = ["Water"]
    _attack(
        state,
        "Flip 3 coins. This attack does 10 damage times the number of heads to each of your "
        "opponent's Pokémon.",
    )
    assert state.opponent.active.damage_counters == 30
    assert state.opponent.bench[0].damage_counters == 30
    _attack(state, "Discard 2 Energy from your opponent's Pokémon.")
    assert state.opponent.active.attached_energies == []
    assert state.opponent.bench[0].attached_energies == []


def test_discard_card_back_on_top_of_the_deck(state):
    state.player.discard = [mon("Fallen")]
    _attack(state, "Put a card from your discard pile on top of your deck.")
    assert state.player.deck[0].name == "Fallen"


def test_go_first_attack_is_the_only_one_on_turn_one():
    from pokemon_companion.cards_db.models import Attack
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.actions import UseAttack

    from .test_rules import build_state as fresh

    early = Attack(
        name="Close Encounter",
        cost=[],
        damage="",
        text="If you go first, you can use this attack on your first turn. Draw 2 cards.",
    )
    late = Attack(name="Hit", cost=[], damage="30")
    state = fresh(
        player_active=dataclasses.replace(mon("Deoxys"), attacks=[early, late]),
        opponent_active=mon("Target"),
        player_deck=[mon(f"D{i}") for i in range(5)],
    )
    state.turn_number = 1
    attacks_now = [a for a in rules.legal_actions(state) if isinstance(a, UseAttack)]
    assert [a.attack_index for a in attacks_now] == [0]


# --- lote 5 -----------------------------------------------------------------


def test_knock_out_when_the_defender_has_exactly_n_counters(state):
    state.opponent.active.damage_counters = 30
    _attack(
        state,
        "If your opponent's Active Pokémon has exactly 3 damage counters on it, that Pokémon is "
        "Knocked Out.",
    )
    assert state.opponent.active.is_knocked_out


def test_put_a_card_from_the_revealed_hand_on_the_bottom(state):
    supporter = Card(
        id="s", name="Boss's Orders", supertype=Supertype.TRAINER, subtypes=["Supporter"]
    )
    state.opponent.hand = [mon("Keep"), supporter]
    _attack(
        state,
        "Your opponent reveals their hand. Choose a Supporter card you find there and put it on "
        "the bottom of their deck.",
    )
    assert state.opponent.deck[-1] is supporter and supporter not in state.opponent.hand


def test_attack_requirement_hides_the_attack_until_it_is_met():
    from pokemon_companion.cards_db.models import Attack
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.actions import UseAttack

    from .test_rules import build_state as fresh

    gated = Attack(
        name="Comeback",
        cost=[],
        damage="100",
        text="You can use this attack only if you have more Prize cards remaining than your "
        "opponent.",
    )
    state = fresh(
        player_active=dataclasses.replace(mon("Underdog"), attacks=[gated]),
        opponent_active=mon("Target"),
        player_deck=[mon(f"D{i}") for i in range(5)],
    )
    assert not [a for a in rules.legal_actions(state) if isinstance(a, UseAttack)]
    state.opponent.prizes = state.opponent.prizes[:2]
    assert [a for a in rules.legal_actions(state) if isinstance(a, UseAttack)]


def test_old_passive_conditions_and_damage_reduction(state):
    from pokemon_companion.engine.effects import passive_text

    for text in [
        "If there is any Stadium card in play, this Pokémon has no Retreat Cost.",
        "If your opponent has any Pokémon-GX or Pokémon-EX in play, this Pokémon has no Retreat "
        "Cost.",
        "Any damage done to this Pokémon by attacks from your opponent's Fire or {W} Pokémon is "
        "reduced by 20.",
        "As long as this Pokémon is your Active Pokémon, your opponent's Basic Pokémon's attacks "
        "cost Colorless more.",
    ]:
        assert passive_text.compile_passive(text), text


def test_also_damage_a_benched_pokemon_v(state):
    v = PokemonInPlay(card=_rule_mon("Zacian V", "V"))
    plain = PokemonInPlay(card=mon("Plain"))
    state.opponent.bench = [plain, v]
    _attack(
        state, "This attack also does 30 damage to 1 of your opponent's Benched Pokémon V.", "10"
    )
    assert v.damage_counters == 30 and plain.damage_counters == 0
    assert state.opponent.active.damage_counters == 10


def test_search_for_either_of_two_types(state):
    psychic = make_basic_pokemon("Mew", 60, "Psychic")
    dragon = make_basic_pokemon("Dratini", 60, "Dragon")
    state.player.deck = [mon("Plain"), dragon, psychic]
    state.player.hand = [mon("Fodder"), mon("Card itself")]
    _play(
        state,
        "Discard a card from your hand. If you do, search your deck for a Psychic or Dragon "
        "Pokémon, reveal it, and put it into your hand. Then, shuffle your deck.",
    )
    assert any(c.name in ("Mew", "Dratini") for c in state.player.hand)
