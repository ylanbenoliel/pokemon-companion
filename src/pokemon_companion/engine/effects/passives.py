"""Efeitos contínuos (Habilidades passivas, Ferramentas, Estádios, Energias
Especiais) calculados a partir do estado — nada aqui guarda estado próprio.

As regras consultam estas funções para: custo de recuo, energia fornecida,
custo de ataque, HP extra, fraqueza, bônus/redução de dano, prevenção de
dano e de efeitos, e restrições de ataque.
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Attack
from pokemon_companion.engine.effects.cardinfo import (
    TYPED_SPECIAL_ENERGIES,
    has_ability,
    has_rule_box,
    in_group,
    is_ex,
    is_future,
    is_tera,
    pokemon_type,
    stage_of,
)
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay

# ---------------------------------------------------------------------------
# Habilidades e Estádio


def stadium_is(state: GameState, name: str) -> bool:
    return state.stadium is not None and state.stadium.name == name


def ability_active(state: GameState, mon: PokemonInPlay, name: str) -> bool:
    if not has_ability(mon.card, name):
        return False
    if stadium_is(state, "Team Rocket's Watchtower") and pokemon_type(mon.card) == "Colorless":
        return False
    return not (name == "Cursed Blast" and any_ability_in_play(state, None, "Damp"))


def any_ability_in_play(state: GameState, owner: PlayerId | None, name: str) -> bool:
    owners = [owner] if owner is not None else [PlayerId.PLAYER, PlayerId.OPPONENT]
    return any(
        ability_active(state, mon, name)
        for pid in owners
        for mon in state.state_of(pid).all_pokemon_in_play()
    )


def tool_active(state: GameState, mon: PokemonInPlay, name: str) -> bool:
    return mon.tool is not None and mon.tool.name == name and not stadium_is(state, "Jamming Tower")


def owner_of(state: GameState, mon: PokemonInPlay) -> PlayerId:
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        if any(m is mon for m in state.state_of(pid).all_pokemon_in_play()):
            return pid
    return state.active_player


# ---------------------------------------------------------------------------
# HP, recuo, energia


def hp_bonus(state: GameState, mon: PokemonInPlay) -> int:
    bonus = 0
    if tool_active(state, mon, "Hero's Cape"):
        bonus += 100
    if tool_active(state, mon, "Cynthia's Power Weight") and in_group(mon.card, "Cynthia's"):
        bonus += 70
    if "Growing Grass Energy" in mon.attached_energies and pokemon_type(mon.card) == "Grass":
        bonus += 20 * mon.attached_energies.count("Growing Grass Energy")
    if stadium_is(state, "Gravity Mountain") and stage_of(mon.card) == "Stage 2":
        bonus -= 30
    return bonus


def refresh_hp_bonuses(state: GameState) -> None:
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        for mon in state.state_of(pid).all_pokemon_in_play():
            mon.hp_bonus = hp_bonus(state, mon)


def retreat_cost(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> int:
    cost = len(mon.card.retreat_cost)
    if tool_active(state, mon, "Air Balloon"):
        cost -= 2
    if stage_of(mon.card) == "Basic" and any_ability_in_play(state, owner, "Skyliner"):
        cost = 0
    if mon is state.state_of(owner).active and any_ability_in_play(
        state, owner.other, "Binding Flame"
    ):
        cost += 1
    return max(cost, 0)


def provided_energy(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> list[str]:
    """Unidades de energia fornecidas: tipo ("Fire"), "Any" (qualquer tipo) ou
    alternativas ("Psychic|Darkness")."""
    wild_growth = any_ability_in_play(state, owner, "Wild Growth")
    units: list[str] = []
    for energy in mon.attached_energies:
        if energy == "Legacy Energy":
            units.append("Any")
        elif energy == "Neo Upper Energy":
            units.extend(["Any", "Any"] if stage_of(mon.card) == "Stage 2" else ["Colorless"])
        elif energy == "Team Rocket's Energy":
            units.extend(["Psychic|Darkness", "Psychic|Darkness"])
        elif energy in TYPED_SPECIAL_ENERGIES:
            units.append(TYPED_SPECIAL_ENERGIES[energy])
        elif energy == "Ignition Energy":
            units.extend(["Colorless"] * (3 if stage_of(mon.card) != "Basic" else 1))
        elif energy.endswith(" Energy"):  # outras especiais: {C}
            units.append("Colorless")
        elif energy == "Grass" and wild_growth:
            units.extend(["Grass", "Grass"])
        else:
            units.append(energy)
    return units


def energy_satisfies(units: list[str], cost: list[str]) -> bool:
    specific = [c for c in cost if c != "Colorless"]
    colorless = cost.count("Colorless")

    def options(unit: str, need: str) -> bool:
        return unit == need or unit == "Any" or need in unit.split("|")

    def assign(index: int, remaining: list[str]) -> bool:
        if index == len(specific):
            return len(remaining) >= colorless
        need = specific[index]
        tried: set[str] = set()
        for i, unit in enumerate(remaining):
            if unit in tried or not options(unit, need):
                continue
            tried.add(unit)
            if assign(index + 1, remaining[:i] + remaining[i + 1 :]):
                return True
        return False

    return assign(0, list(units))


def attack_cost(state: GameState, owner: PlayerId, mon: PokemonInPlay, attack: Attack) -> list[str]:
    cost = list(attack.cost)
    if stadium_is(state, "Nighttime Mine") and is_tera(mon.card):
        cost.append("Colorless")
    if attack.name == "Blood Moon" and ability_active(state, mon, "Seasoned Skill"):
        taken = state.prize_count - len(state.state_of(owner.other).prizes)
        for _ in range(min(taken, cost.count("Colorless"))):
            cost.remove("Colorless")
    if attack.name == "Trifrost" and ability_active(state, mon, "Plasma Bane"):
        opponent_discard = state.state_of(owner.other).discard
        if any("Colress" in card.name for card in opponent_discard):
            cost = ["Colorless"]
    return cost


def cost_progress(units: list[str], cost: list[str]) -> float:
    """Fração do custo já coberta pelas energias (guloso; 1.0 = pode pagar)."""
    if not cost:
        return 1.0
    remaining = list(units)
    matched = 0
    for need in (c for c in cost if c != "Colorless"):
        unit = next((u for u in remaining if u == need or need in u.split("|")), None)
        if unit is None and "Any" in remaining:
            unit = "Any"
        if unit is not None:
            remaining.remove(unit)
            matched += 1
    matched += min(len(remaining), cost.count("Colorless"))
    return matched / len(cost)


def can_pay(state: GameState, owner: PlayerId, mon: PokemonInPlay, attack: Attack) -> bool:
    return energy_satisfies(
        provided_energy(state, owner, mon), attack_cost(state, owner, mon, attack)
    )


def attack_allowed(state: GameState, owner: PlayerId, mon: PokemonInPlay, attack: Attack) -> bool:
    """Restrições de efeitos: "não pode atacar no próximo turno", ataques
    bloqueados e Habilidades como Power Saver."""
    if mon.cannot_attack_turn == state.turn_number:
        return False
    if mon.blocked_attack == (attack.name, state.turn_number):
        return False
    going_second_lock = "If you go second, you can't use this attack during your first turn"
    if going_second_lock in attack.text and state.turn_number == 2:
        return False
    if ability_active(state, mon, "Power Saver"):
        team = [
            m
            for m in state.state_of(owner).all_pokemon_in_play()
            if in_group(m.card, "Team Rocket's")
        ]
        if len(team) < 4:
            return False
    return True


# ---------------------------------------------------------------------------
# fraqueza, dano e prevenções


def weakness_types(state: GameState, defender_owner: PlayerId, defender: PokemonInPlay) -> set[str]:
    if pokemon_type(defender.card) == "Dragon" and any_ability_in_play(
        state, defender_owner.other, "Fairy Zone"
    ):
        return {"Psychic"}
    return {weakness.energy_type for weakness in defender.card.weaknesses}


def attacker_bonus(
    state: GameState, attacker_owner: PlayerId, attacker: PokemonInPlay, defender: PokemonInPlay
) -> int:
    """Bônus aplicados antes de Fraqueza/Resistência, só contra o Ativo."""
    bonus = 0
    player = state.state_of(attacker_owner)
    for amount, condition in player.damage_bonus_this_turn:
        if (
            condition == "all"
            or (condition == "ex" and is_ex(defender.card))
            or (condition == "ex_v" and has_rule_box(defender.card))
            or (condition == "fighting" and pokemon_type(attacker.card) == "Fighting")
            or (condition == "no_rule_box" and not has_rule_box(attacker.card))
        ):
            bonus += amount
    if tool_active(state, attacker, "Binding Mochi") and attacker.status.name == "POISONED":
        bonus += 40
    if (
        tool_active(state, attacker, "Brave Bangle")
        and not has_rule_box(attacker.card)
        and is_ex(defender.card)
    ):
        bonus += 30
    if in_group(attacker.card, "Cynthia's"):
        bonus += 30 * sum(
            1
            for mon in player.all_pokemon_in_play()
            if ability_active(state, mon, "Cheer On to Glory")
        )
    if is_future(attacker.card) and attacker.card.name != "Iron Crown ex":
        bonus += 20 * sum(
            1
            for mon in player.all_pokemon_in_play()
            if ability_active(state, mon, "Cobalt Command")
        )
    if attacker.attack_debuff and attacker.attack_debuff[1] == state.turn_number:
        bonus -= attacker.attack_debuff[0]
    if ability_active(state, attacker, "Compound Eyes") and defender.card.abilities:
        bonus += 50
    return bonus


def static_damage_reduction(
    state: GameState, defender_owner: PlayerId, defender: PokemonInPlay
) -> int:
    """Reduções contínuas de dano vindas de Habilidades passivas (Curly
    Wall: seus Básicos {C} tomam 60 a menos com outro Bouffalant em jogo)."""
    if stage_of(defender.card) != "Basic" or pokemon_type(defender.card) != "Colorless":
        return 0
    team = state.state_of(defender_owner).all_pokemon_in_play()
    walls = sum(1 for mon in team if ability_active(state, mon, "Curly Wall"))
    if walls - (1 if ability_active(state, defender, "Curly Wall") else 0) >= 1:
        return 60
    return 0


def damage_prevented(
    state: GameState,
    defender_owner: PlayerId,
    defender: PokemonInPlay,
    attacker: PokemonInPlay,
    is_active: bool,
) -> bool:
    if defender.protected_turn == state.turn_number:
        return True
    if ability_active(state, defender, "Mysterious Rock Inn") and is_ex(attacker.card):
        return True
    owner_state = state.state_of(defender_owner)
    if not is_active:
        # Regra dos Pokémon Tera: no Banco, nenhum dano de ataque os atinge
        # (contadores de dano colocados por efeitos continuam valendo).
        if is_tera(defender.card):
            return True
        if not has_rule_box(defender.card) and any(
            ability_active(state, mon, "Flower Curtain")
            for mon in owner_state.all_pokemon_in_play()
        ):
            return True
        if any(
            ability_active(state, mon, "Spherical Shield")
            for mon in owner_state.all_pokemon_in_play()
        ):
            return True
    return False


def prevents_attack_effects(
    state: GameState, defender_owner: PlayerId, defender: PokemonInPlay, is_active: bool
) -> bool:
    if ability_active(state, defender, "Hide 'n' Sneak"):
        return True
    if defender.protected_turn == state.turn_number:
        return True
    if "Mist Energy" in defender.attached_energies:
        return True
    if (
        "Rocky Fighting Energy" in defender.attached_energies
        and pokemon_type(defender.card) == "Fighting"
    ):
        return True
    owner_state = state.state_of(defender_owner)
    if (
        stage_of(defender.card) == "Basic"
        and in_group(defender.card, "Team Rocket's")
        and any(
            ability_active(state, mon, "Repelling Veil")
            for mon in owner_state.all_pokemon_in_play()
        )
    ):
        return True
    return not is_active and any(
        ability_active(state, mon, "Spherical Shield") for mon in owner_state.all_pokemon_in_play()
    )


def counters_locked(state: GameState) -> bool:
    """Watchful Eye: contadores de dano não podem ser movidos."""
    return any_ability_in_play(state, None, "Watchful Eye")


def prevents_ability_effects(state: GameState, defender: PokemonInPlay) -> bool:
    return ability_active(state, defender, "Hide 'n' Sneak")


def immune_to_special_conditions(state: GameState, mon: PokemonInPlay) -> bool:
    if stadium_is(state, "Festival Grounds") and bool(mon.attached_energies):
        return True
    return "Bubbly Water Energy" in mon.attached_energies and pokemon_type(mon.card) == "Water"
