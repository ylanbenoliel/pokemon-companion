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


def _with_ability(card, name: str, text: str):
    from pokemon_companion.cards_db.models import Ability

    return dataclasses.replace(card, abilities=[Ability(name=name, text=text)])


def test_emergency_rotation_benches_from_hand_against_stage_2(state):
    from pokemon_companion.engine.actions import UseHandAbility

    klinklang = _with_ability(
        dataclasses.replace(mon("Klinklang"), subtypes=["Stage 2"], evolves_from="Klang"),
        "Emergency Rotation",
        "Once during your turn, if this Pokémon is in your hand and your opponent has any "
        "Stage 2 Pokémon in play, you may put this Pokémon onto your Bench.",
    )
    state.player.hand = [klinklang]
    hand_uses = lambda: [a for a in rules.legal_actions(state) if isinstance(a, UseHandAbility)]  # noqa: E731
    assert hand_uses() == []
    state.opponent.bench = [
        PokemonInPlay(card=dataclasses.replace(mon("Big"), subtypes=["Stage 2"]))
    ]
    (action,) = hand_uses()
    rules.apply_action(state, action)
    assert [m.card.name for m in state.player.bench] == ["Klinklang"]
    assert state.player.hand == []


def test_explosiveness_can_start_in_the_active_spot():
    from pokemon_companion.engine import turn_manager

    cinderace = _with_ability(
        dataclasses.replace(mon("Cinderace", hp=180), subtypes=["Stage 2"], evolves_from="Raboot"),
        "Explosiveness",
        "If this Pokémon is in your hand when you are setting up to play, you may put it face "
        "down in the Active Spot.",
    )
    assert turn_manager.choose_starting_active([mon("Small", hp=60), cinderace]) == 1


def test_mentally_calm_blocks_scooping_up_your_own_pokemon(state):
    milotic = _with_ability(
        mon("Milotic"),
        "Mentally Calm",
        "Your opponent's Pokémon in play and all attached cards can't be put into your "
        "opponent's hand.",
    )
    state.opponent.active = PokemonInPlay(card=milotic)
    attacker(state, "Put this Pokémon and all attached cards into your hand.")
    state.player.bench = [PokemonInPlay(card=mon("Backup"))]
    rules.apply_action(state, UseAttack(attack_index=0))
    assert all(c.name != "Attacker" for c in state.player.hand)


def test_multi_adapter_allows_a_second_tool_on_rotom(state):
    from pokemon_companion.engine.actions import PlayTrainer

    rotom = _with_ability(
        mon("Rotom ex"),
        "Multi Adapter",
        'Each of your Pokémon that has "Rotom" in its name may have up to 2 Pokémon Tool cards '
        "attached. If this Ability goes away, discard Pokémon Tools from those Pokémon until only "
        "1 remains on each.",
    )
    first, second = trainer("Tool A", "Tool"), trainer("Tool B", "Tool")
    state.player.active = PokemonInPlay(card=rotom, tool=first)
    state.player.hand = [second]
    plays = [a for a in rules.legal_actions(state) if isinstance(a, PlayTrainer)]
    rules.apply_action(state, plays[0])
    assert [t.name for t in state.player.active.tools] == ["Tool A", "Tool B"]
