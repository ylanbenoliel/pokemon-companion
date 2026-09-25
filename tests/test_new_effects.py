"""Efeitos novos compilados do texto (ataques, Habilidades e Treinadores)."""

from __future__ import annotations

import dataclasses

from pokemon_companion.cards_db.models import Attack
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import UseAttack
from pokemon_companion.engine.game_state import PokemonInPlay

from .test_effects import mon, state, trainer  # noqa: F401


def attacker(state, text: str, damage: str = "", name: str = "Attacker", attacks=()) -> None:
    attack = Attack(name="Hit", cost=["Colorless"], damage=damage, text=text)
    card = dataclasses.replace(mon(name, hp=300), attacks=[attack, *attacks])
    state.player.active = PokemonInPlay(card=card, attached_energies=["Colorless"])


def test_majestic_sword_needs_a_future_supporter_this_turn(state):
    text = (
        "If you played a Future Supporter card from your hand during this turn, this attack "
        "does 100 more damage."
    )
    attacker(state, text, "100+")
    state.opponent.active = PokemonInPlay(card=mon("Wall", hp=500))
    state.player.played_this_turn = [trainer("Ciphermaniac's Codebreaking", "Supporter")]
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.damage_counters == 200


def test_land_collapse_mills_more_after_an_ancient_supporter(state):
    text = (
        "Discard the top card of your opponent's deck. If you played an Ancient Supporter card "
        "from your hand during this turn, discard 3 more cards in this way."
    )
    attacker(state, text)
    state.player.played_this_turn = [trainer("Explorer's Guidance", "Supporter")]
    deck = len(state.opponent.deck)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert len(state.opponent.deck) == deck - 4 - 1  # 4 descartadas + a compra do turno dele


def test_unrelenting_onslaught_counts_another_ancient_attacker(state):
    text = (
        "If 1 of your other Ancient Pokémon used an attack during your last turn, this attack "
        "does 150 more damage."
    )
    attacker(state, text, "30+")
    great_tusk = dataclasses.replace(
        mon("Great Tusk"), attacks=[Attack(name="Land Collapse"), Attack(name="Giant Tusk")]
    )
    state.player.bench = [PokemonInPlay(card=great_tusk, attacked_turn=state.turn_number - 2)]
    state.opponent.active = PokemonInPlay(card=mon("Wall", hp=500))
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.opponent.active.damage_counters == 180
