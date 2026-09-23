"""Efeitos sonoros por grupo de efeito: o que cada ação faz tocar."""

from __future__ import annotations

import dataclasses
import re

import pytest

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.models import Attack, Card, Supertype
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import AttachEnergy, Evolve, PlayTrainer, UseAttack
from pokemon_companion.engine.effects import core
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay
from pokemon_companion.ui import sound_cues
from pokemon_companion.ui.sound import SOUNDS_DIR

from .conftest import make_basic_pokemon
from .test_rules import build_state


def mon(name: str, hp: int = 300, types: str = "Fire", damage: str = "100", text: str = "") -> Card:
    card = make_basic_pokemon(name, hp, types, attack_cost=["Colorless"], attack_damage=damage)
    if text:
        card = dataclasses.replace(
            card, attacks=[Attack(name="Test", cost=["Colorless"], damage=damage, text=text)]
        )
    return card


@pytest.fixture
def state():
    s = build_state(player_active=mon("Attacker"), opponent_active=mon("Defender", types="Water"))
    s.player.active.attached_energies = ["Colorless"]
    s.player.deck = [mon(f"Deck{i}") for i in range(10)]
    s.opponent.deck = [mon(f"ODeck{i}") for i in range(10)]
    return s


def play(state, action) -> list[str]:
    actor = rules.decision_player(state)
    before = sound_cues.snapshot(state, actor, action)
    messages = rules.apply_action(state, action)
    return [group for _, group in sound_cues.cues_for(action, before, state, actor, messages)]


def test_every_group_has_a_sound_file():
    files = {re.sub(r"_\d+$", "", p.stem) for p in SOUNDS_DIR.glob("*.wav")}
    assert files >= sound_cues.ALL_GROUPS


def test_attack_sounds_by_type_then_impact_by_damage(state):
    assert play(state, UseAttack(attack_index=0))[:2] == ["attack_fire", "hit_heavy"]


def test_knockout_adds_ko_and_prize(state):
    state.opponent.active = PokemonInPlay(card=mon("Small", hp=60, types="Water"))
    state.opponent.bench = [PokemonInPlay(card=mon("Next"))]

    groups = play(state, UseAttack(attack_index=0))

    assert groups[:4] == ["attack_fire", "hit_heavy", "ko", "prize"]  # nocaute soa pesado


def test_prevented_damage_sounds_like_a_shield(state):
    state.opponent.active.protected_turn = state.turn_number
    assert "shield" in play(state, UseAttack(attack_index=0))


def test_status_and_coin_are_heard(state, monkeypatch):
    monkeypatch.setattr(core, "coin", lambda: True)
    text = "Flip a coin. If heads, your opponent's Active Pokémon is now Paralyzed."
    state.player.active = PokemonInPlay(
        card=mon("Zapper", text=text, damage="10"), attached_energies=["Colorless"]
    )

    groups = play(state, UseAttack(attack_index=0))

    assert groups[:2] == ["coin_flip", "coin_heads"]
    assert "status_paralysis" in groups


def test_energy_sounds_by_its_type(state):
    state.player.hand = [BASIC_ENERGIES["Water"]]
    groups = play(state, AttachEnergy(hand_index=0, target_is_active=True))
    assert groups == ["energy_water"]


def test_trainer_sounds_by_category_and_draw(state):
    supporter = Card(
        id="s",
        name="Draw Friend",
        supertype=Supertype.TRAINER,
        subtypes=["Supporter"],
        rules=["Draw 3 cards."],
    )
    state.player.hand = [supporter]
    assert play(state, PlayTrainer(hand_index=0)) == ["trainer_supporter", "card_draw"]


def test_mega_evolution_has_its_own_sound(state):
    mega = dataclasses.replace(
        mon("Mega Attacker ex"), evolves_from="Attacker", subtypes=["Stage 1", "ex", "Mega"]
    )
    state.player.active.turn_played = 0
    state.turn_number = 3
    state.player.hand = [mega]
    groups = play(state, Evolve(hand_index=0, target_is_active=True))
    assert groups[0] == "evolve_mega"


def test_healing_is_heard(state):
    text = "Heal 30 damage from this Pokémon."
    state.player.active = PokemonInPlay(
        card=mon("Healer", text=text, damage="10"), attached_energies=["Colorless"]
    )
    state.player.active.damage_counters = 50
    assert "heal" in play(state, UseAttack(attack_index=0))


def test_cues_are_ordered_in_time(state):
    cues = sound_cues.cues_for(
        UseAttack(attack_index=0),
        sound_cues.snapshot(state, PlayerId.PLAYER, UseAttack(attack_index=0)),
        state,
        PlayerId.PLAYER,
        ["2 cara(s).", "Defender (opponent) foi nocauteado!"],
    )
    delays = [delay for delay, _ in cues]
    assert delays == sorted(delays) and len(cues) <= sound_cues.MAX_CUES


def test_music_tracks_exist_and_loop_cleanly():
    import wave

    import numpy as np

    from pokemon_companion.ui.sound import MUSIC_DIR

    for track in ("menu", "battle"):
        with wave.open(str(MUSIC_DIR / f"{track}.wav")) as f:
            samples = np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(float) / 32768
            assert f.getnframes() / f.getframerate() > 15
        # loop sem estalo: o fim encaixa no começo
        assert abs(samples[0] - samples[-1]) < 0.05
