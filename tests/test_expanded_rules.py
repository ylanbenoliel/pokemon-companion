"""Regras de jogo do Expanded que não estão no texto de um efeito: ataque GX e
VSTAR Power uma vez por partida, Mega Evolução de XY encerrando o turno,
Prism Star na Zona Perdida e Pokémon BREAK com os ataques de baixo."""

from __future__ import annotations

import dataclasses

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.models import Ability, Attack, Card, Supertype, WeaknessResistance
from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import AttachEnergy, Evolve, UseAttack
from pokemon_companion.engine.effects import abilities, core, passives
from pokemon_companion.engine.game_state import PlayerId, PokemonInPlay

from .conftest import make_basic_pokemon
from .test_rules import build_state

_GX_TEXT = "Draw 2 cards. (You can't use more than 1 GX attack in a game.)"


def _mon(name: str, hp: int = 200, **changes: object) -> Card:
    return dataclasses.replace(make_basic_pokemon(name, hp, "Colorless"), **changes)


def test_gx_attack_works_once_per_game():
    gx = Attack(name="Big GX", cost=[], damage="10", text=_GX_TEXT)
    state = build_state(
        player_active=_mon("Zoroark-GX", attacks=[gx]),
        opponent_active=_mon("Target"),
        player_deck=[_mon(f"D{i}") for i in range(5)],
    )
    active = state.player.active
    assert passives.attack_allowed(state, PlayerId.PLAYER, active, gx)
    rules.apply_action(state, UseAttack(attack_index=0))
    assert state.player.once_per_game_used == {"GX attack"}
    assert not passives.attack_allowed(state, PlayerId.PLAYER, active, gx)


def test_vstar_power_ability_works_once_per_game():
    power = Ability(
        name="Star Draw",
        text="Once during your turn, you may draw 2 cards. (You can't use more than 1 VSTAR "
        "Power in a game.)",
    )
    state = build_state(
        player_active=_mon("Arceus VSTAR", abilities=[power]),
        opponent_active=_mon("Target"),
        player_deck=[_mon(f"D{i}") for i in range(5)],
    )
    ctx = core.Ctx(state, PlayerId.PLAYER, state.player.active)
    assert [name for name, _ in abilities.usable_abilities(ctx, state.player.active)] == [
        "Star Draw"
    ]
    abilities.use_ability(ctx, "Star Draw")
    state.player.active.abilities_used.clear()  # novo turno
    assert abilities.usable_abilities(ctx, state.player.active) == []


def _mega_state(tool: Card | None = None):
    basic = _mon("Venusaur-EX", subtypes=["Basic", "EX"])
    mega = _mon("M Venusaur-EX", 230, subtypes=["MEGA", "EX"], evolves_from="Venusaur-EX")
    state = build_state(player_active=basic, opponent_active=_mon("Target"), player_hand=[mega])
    state.player.active.tool = tool
    return state


def test_xy_mega_evolution_ends_the_turn():
    state = _mega_state()
    rules.apply_action(state, Evolve(hand_index=0, target_is_active=True))
    assert state.player.active.card.name == "M Venusaur-EX"
    assert state.active_player == PlayerId.OPPONENT


def test_spirit_link_keeps_the_turn_going():
    link = Card(
        id="sl", name="Venusaur Spirit Link", supertype=Supertype.TRAINER, subtypes=["Tool"]
    )
    state = _mega_state(link)
    rules.apply_action(state, Evolve(hand_index=0, target_is_active=True))
    assert state.active_player == PlayerId.PLAYER


def test_prism_star_goes_to_the_lost_zone_instead_of_the_discard():
    state = build_state(
        player_active=_mon("Active"),
        opponent_active=_mon("Target"),
        player_hand=[BASIC_ENERGIES["Fire"]],
    )
    prism = Card(id="ps", name="Lusamine ◇", supertype=Supertype.TRAINER, subtypes=["Supporter"])
    state.player.discard = [prism, _mon("Plain")]
    rules.apply_action(state, AttachEnergy(hand_index=0, target_is_active=True))
    assert [c.name for c in state.player.discard] == ["Plain"]
    assert state.player.lost_zone == [prism]


def test_break_keeps_the_attacks_weakness_and_retreat_from_below():
    below = _mon(
        "Greninja",
        attacks=[Attack(name="Shadow Stitching", cost=["Water"], damage="40")],
        weaknesses=[WeaknessResistance("Grass", "×2")],
        retreat_cost=["Colorless"],
    )
    breaker = _mon(
        "Greninja BREAK",
        170,
        subtypes=["BREAK"],
        evolves_from="Greninja",
        attacks=[Attack(name="Giant Water Shuriken", cost=["Water"], damage="")],
        weaknesses=[],
        retreat_cost=[],
    )
    state = build_state(player_active=below, opponent_active=_mon("Target"))
    mon = core.evolve_into(state, state.player, state.player.active, breaker)

    assert [a.name for a in mon.card.attacks] == ["Shadow Stitching", "Giant Water Shuriken"]
    assert mon.card.weaknesses == below.weaknesses and mon.card.retreat_cost == ["Colorless"]
    assert mon.max_hp == 170
    assert mon.all_cards()[0] is breaker  # a carta impressa sai do jogo, não a visão somada
    assert core.devolve(mon) is breaker and mon.card is below


def test_once_per_game_survives_a_clone():
    state = build_state(player_active=_mon("A"), opponent_active=_mon("B"))
    state.player.once_per_game_used.add("GX attack")
    twin = state.clone()
    twin.player.once_per_game_used.clear()
    assert state.player.once_per_game_used == {"GX attack"}
    assert isinstance(state.player.active, PokemonInPlay)
