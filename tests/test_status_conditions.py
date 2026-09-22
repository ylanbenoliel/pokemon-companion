from __future__ import annotations

from pokemon_companion.engine.game_state import PokemonInPlay, StatusCondition
from pokemon_companion.engine.status_conditions import (
    apply_between_turns_effects,
    can_attack,
    can_retreat,
    check_confusion_self_damage,
    try_wake_up,
)

HEADS = lambda: True  # noqa: E731
TAILS = lambda: False  # noqa: E731


def test_poison_applies_ten_damage(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.POISONED)

    messages = apply_between_turns_effects(mon, flip_coin=HEADS)

    assert mon.damage_counters == 10
    assert messages


def test_burn_applies_twenty_damage_and_may_heal_on_heads(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.BURNED)

    apply_between_turns_effects(mon, flip_coin=HEADS)

    assert mon.damage_counters == 20
    assert mon.status == StatusCondition.NONE


def test_burn_keeps_status_on_tails(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.BURNED)

    apply_between_turns_effects(mon, flip_coin=TAILS)

    assert mon.damage_counters == 20
    assert mon.status == StatusCondition.BURNED


def test_try_wake_up_clears_status_on_heads(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.ASLEEP)

    try_wake_up(mon, flip_coin=HEADS)

    assert mon.status == StatusCondition.NONE


def test_try_wake_up_keeps_asleep_on_tails(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.ASLEEP)

    try_wake_up(mon, flip_coin=TAILS)

    assert mon.status == StatusCondition.ASLEEP


def test_asleep_and_paralyzed_pokemon_cannot_act(charmander):
    asleep = PokemonInPlay(card=charmander, status=StatusCondition.ASLEEP)
    paralyzed = PokemonInPlay(card=charmander, status=StatusCondition.PARALYZED)
    healthy = PokemonInPlay(card=charmander, status=StatusCondition.NONE)

    assert not can_attack(asleep)
    assert not can_retreat(asleep)
    assert not can_attack(paralyzed)
    assert not can_retreat(paralyzed)
    assert can_attack(healthy)
    assert can_retreat(healthy)


def test_confused_pokemon_attacks_normally_on_heads(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.CONFUSED)

    can_proceed, _ = check_confusion_self_damage(mon, flip_coin=HEADS)

    assert can_proceed is True
    assert mon.damage_counters == 0


def test_confused_pokemon_hurts_itself_on_tails(charmander):
    mon = PokemonInPlay(card=charmander, status=StatusCondition.CONFUSED)

    can_proceed, _ = check_confusion_self_damage(mon, flip_coin=TAILS)

    assert can_proceed is False
    assert mon.damage_counters == 30
