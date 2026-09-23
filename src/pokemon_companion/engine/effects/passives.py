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
    if name == "Cursed Blast" and any_ability_in_play(state, None, "Damp"):
        return False
    return not ability_suppressed(state, mon, name)


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
    if stadium_is(state, "Lively Stadium") and stage_of(mon.card) == "Basic":
        bonus += 30
    if stadium_is(state, "Ange Floette") and mon.card.name == "Mega Floette ex":
        bonus += 150
    owner = owner_of(state, mon)
    for passive, holder in compiled(state, owner, "hp"):
        if _applies(passive, holder, mon):
            bonus += passive.value(state, owner, holder)  # type: ignore[attr-defined]
    return bonus


def refresh_hp_bonuses(state: GameState) -> None:
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        for mon in state.state_of(pid).all_pokemon_in_play():
            mon.hp_bonus = hp_bonus(state, mon)


def retreat_cost(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> int:
    cost = len(mon.card.retreat_cost)
    if mon.taxed_turn == state.turn_number:
        cost += 1
    if tool_active(state, mon, "Air Balloon"):
        cost -= 2
    if stage_of(mon.card) == "Basic" and any_ability_in_play(state, owner, "Skyliner"):
        cost = 0
    if mon is state.state_of(owner).active and any_ability_in_play(
        state, owner.other, "Binding Flame"
    ):
        cost += 1
    if any(_applies(p, h, mon) for p, h in compiled(state, owner, "no_retreat")):
        return 0
    if stadium_is(state, "N's Castle") and in_group(mon.card, "N's"):
        return 0
    if "Magnetic Metal Energy" in mon.attached_energies and pokemon_type(mon.card) == "Metal":
        return 0
    if stadium_is(state, "Paradise Resort") and mon.card.name == "Psyduck":
        cost -= 1
    is_active = mon is state.state_of(owner).active
    for passive, _holder in compiled(state, owner, "retreat"):
        if passive.scope == "own_active" and is_active:  # type: ignore[attr-defined]
            cost += passive.amount  # type: ignore[attr-defined]
    for passive, _holder in compiled(state, owner.other, "retreat"):
        if passive.scope == "opp_active" and is_active and passive.target(mon):  # type: ignore[attr-defined]
            cost += passive.amount  # type: ignore[attr-defined]
    return max(cost, 0)


def provided_energy(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> list[str]:
    """Unidades de energia fornecidas: tipo ("Fire"), "Any" (qualquer tipo) ou
    alternativas ("Psychic|Darkness")."""
    wild_growth = any_ability_in_play(state, owner, "Wild Growth")
    units: list[str] = []
    for energy in mon.attached_energies:
        if energy == "Legacy Energy":
            units.append("Any")
        elif energy == "Prism Energy":
            units.append("Any" if stage_of(mon.card) == "Basic" else "Colorless")
        elif energy == "Reversal Energy":
            behind = len(state.state_of(owner).prizes) > len(state.state_of(owner.other).prizes)
            evolved = stage_of(mon.card) != "Basic" and not has_rule_box(mon.card)
            units.append("Any" if behind and evolved else "Colorless")
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


def _cost_from_text(mon: PokemonInPlay, attack: Attack) -> list[str] | None:
    """Custos alternativos escritos no próprio ataque."""
    import re

    text = attack.text
    alt = re.search(
        r"If this Pokémon has any damage counters on it, this attack can be used for [\[{](\w)[\]}]",
        text,
    )
    if alt and mon.damage_counters:
        from pokemon_companion.engine.effects.attacks import ENERGY_SYMBOLS

        return [ENERGY_SYMBOLS.get(alt.group(1), "Colorless")]
    free = "If this Pokémon is affected by a Special Condition, ignore all Energy in this attack's cost"
    if free in text and mon.status.name != "NONE":
        return []
    return None


def attack_cost(state: GameState, owner: PlayerId, mon: PokemonInPlay, attack: Attack) -> list[str]:
    alternative = _cost_from_text(mon, attack)
    cost = list(attack.cost) if alternative is None else alternative
    if mon.taxed_turn == state.turn_number:
        cost.append("Colorless")
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
    return compiled_cost(state, owner, mon, cost)


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
    if any(holder is mon for _, holder in compiled(state, owner, "no_attack")):
        return False
    minimum = state.state_of(owner).attack_energy_min
    if minimum and minimum[1] == state.turn_number and len(mon.attached_energies) <= minimum[0]:
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
    if defender.no_weakness_turn == state.turn_number:
        return set()
    changed = defender.weakness_to
    if changed and state.turn_number <= changed[1]:
        return {changed[0]}
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
    bonus += compiled_bonus(state, attacker_owner, attacker, defender)
    if stadium_is(state, "Postwick") and in_group(attacker.card, "Hop's"):
        bonus += 30
    if (
        "Voltaic Lightning Energy" in attacker.attached_energies
        and pokemon_type(attacker.card) == "Lightning"
    ):
        bonus += 20 * attacker.attached_energies.count("Voltaic Lightning Energy")
    for passive, _holder in compiled(state, attacker_owner.other, "weaken"):
        if passive.attacker(attacker):  # type: ignore[attr-defined]
            bonus -= passive.amount  # type: ignore[attr-defined]
    return bonus


def static_damage_reduction(
    state: GameState, defender_owner: PlayerId, defender: PokemonInPlay
) -> int:
    """Reduções contínuas de dano vindas de Habilidades passivas (Curly
    Wall: seus Básicos {C} tomam 60 a menos com outro Bouffalant em jogo)."""
    stadium = 0
    if stadium_is(state, "Full Metal Lab") and pokemon_type(defender.card) == "Metal":
        stadium = 30
    if stadium_is(state, "Granite Cave") and in_group(defender.card, "Steven's"):
        stadium = 30
    if stage_of(defender.card) != "Basic" or pokemon_type(defender.card) != "Colorless":
        return stadium
    team = state.state_of(defender_owner).all_pokemon_in_play()
    walls = sum(1 for mon in team if ability_active(state, mon, "Curly Wall"))
    if walls - (1 if ability_active(state, defender, "Curly Wall") else 0) >= 1:
        return 60 + stadium
    return stadium


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
    if compiled_prevents(state, defender_owner, defender, attacker):
        return True
    if (
        stadium_is(state, "Neutralization Zone")
        and not has_rule_box(defender.card)
        and (is_ex(attacker.card) or "V" in attacker.card.subtypes)
    ):
        return True
    if (
        not is_active
        and "Shadowy Darkness Energy" in defender.attached_energies
        and pokemon_type(defender.card) == "Darkness"
    ):
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


def shield_blocks(
    state: GameState, defender: PokemonInPlay, attacker: PokemonInPlay, amount: int
) -> bool:
    """Prevenção condicional marcada por um ataque no turno anterior
    (`PokemonInPlay.shield`)."""
    if defender.shield is None or defender.shield[1] != state.turn_number:
        return False
    kind = defender.shield[0]
    if kind.startswith("le:"):
        return amount <= int(kind[3:])
    if kind == "ex":
        return is_ex(attacker.card)
    if kind == "evolution":
        return attacker.card.evolves_from is not None
    if kind.startswith("less:"):
        return False  # redução, não prevenção: ver `shield_reduction`
    if kind == "burned":
        return attacker.status.name == "BURNED"
    if kind == "ancient":
        from pokemon_companion.engine.effects.cardinfo import is_ancient

        return is_ancient(attacker.card)
    if kind == "ability":
        return bool(attacker.card.abilities)
    if kind.startswith("basic"):
        excluded = kind.partition(":")[2]
        return stage_of(attacker.card) == "Basic" and pokemon_type(attacker.card) != excluded
    return False


def shield_reduction(state: GameState, defender: PokemonInPlay, attacker: PokemonInPlay) -> int:
    """Escudo de redução: ("less:N:evolution", turno) — N a menos de Evolução."""
    shield = defender.shield
    if shield is None or shield[1] != state.turn_number or not shield[0].startswith("less:"):
        return 0
    _, amount, who = shield[0].split(":")
    if who == "evolution" and attacker.card.evolves_from is None:
        return 0
    return int(amount)


def prevents_attack_effects(
    state: GameState, defender_owner: PlayerId, defender: PokemonInPlay, is_active: bool
) -> bool:
    if ability_active(state, defender, "Hide 'n' Sneak"):
        return True
    if defender.protected_turn == state.turn_number:
        return True
    attacker = state.state_of(defender_owner.other).active
    for passive, holder in compiled(state, defender_owner, "prevent_effects"):
        if holder is defender and (attacker is None or passive.attacker(attacker)):  # type: ignore[attr-defined]
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
    if ability_active(state, defender, "Hide 'n' Sneak"):
        return True
    owner = owner_of(state, defender)
    return any(holder is defender for _, holder in compiled(state, owner, "ability_shield"))


def trainer_shielded(state: GameState, owner: PlayerId, mon: PokemonInPlay, playing: str) -> bool:
    """Pokémon protegido dos efeitos de Item/Apoiador do oponente (Unnerve,
    Snow Camouflage, Protective Sail, Wide Wall)."""
    if playing not in ("Item", "Supporter"):
        return False
    if ability_active(state, mon, "Unnerve") or ability_active(state, mon, "Snow Camouflage"):
        return True
    if playing == "Supporter":
        if ability_active(state, mon, "Protective Sail"):
            return True
        active = state.state_of(owner).active
        if active is not None and ability_active(state, active, "Wide Wall"):
            return True
    return False


def immune_to_special_conditions(state: GameState, mon: PokemonInPlay) -> bool:
    from pokemon_companion.engine.effects.cardinfo import is_fossil_pokemon

    if is_fossil_pokemon(mon.card):
        return True
    if stadium_is(state, "Festival Grounds") and bool(mon.attached_energies):
        return True
    return "Bubbly Water Energy" in mon.attached_energies and pokemon_type(mon.card) == "Water"


# ---------------------------------------------------------------------------
# Habilidades passivas compiladas do texto (`passive_text`)


def compiled(state: GameState, owner: PlayerId, kind: str) -> list[tuple[object, PokemonInPlay]]:
    from pokemon_companion.engine.effects.passive_text import rules_in_play

    return [(p, holder) for p, holder, _ in rules_in_play(state, owner, kind, ability_active)]


def _applies(passive: object, holder: PokemonInPlay, mon: PokemonInPlay) -> bool:
    """Regra de escopo "self" vale para o próprio dono; "team" para os
    Pokémon do mesmo jogador que passam no filtro."""
    scope = passive.scope  # type: ignore[attr-defined]
    if scope == "self":
        return holder is mon
    return bool(scope == "team" and passive.target(mon))  # type: ignore[attr-defined]


def compiled_reduction(
    state: GameState, owner: PlayerId, defender: PokemonInPlay, attacker: PokemonInPlay
) -> int:
    total = 0
    for passive, holder in compiled(state, owner, "reduce"):
        if _applies(passive, holder, defender) and passive.attacker(attacker):  # type: ignore[attr-defined]
            total += passive.value(state, owner, holder)  # type: ignore[attr-defined]
    return total


def compiled_prevents(
    state: GameState, owner: PlayerId, defender: PokemonInPlay, attacker: PokemonInPlay
) -> bool:
    return any(
        _applies(p, holder, defender) and p.attacker(attacker)  # type: ignore[attr-defined]
        for p, holder in compiled(state, owner, "prevent")
    )


def prevents_big_hit(
    state: GameState, owner: PlayerId, defender: PokemonInPlay, amount: int
) -> bool:
    return any(
        holder is defender and amount >= p.amount  # type: ignore[attr-defined]
        for p, holder in compiled(state, owner, "prevent_big")
    )


def compiled_bonus(
    state: GameState, owner: PlayerId, attacker: PokemonInPlay, defender: PokemonInPlay
) -> int:
    total = 0
    for passive, holder in compiled(state, owner, "bonus"):
        if _applies(passive, holder, attacker) and passive.defender(defender):  # type: ignore[attr-defined]
            total += passive.value(state, owner, holder)  # type: ignore[attr-defined]
    return total


def pierces(state: GameState, owner: PlayerId, attacker: PokemonInPlay) -> bool:
    return any(holder is attacker for _, holder in compiled(state, owner, "pierce"))


def damage_reactions(
    state: GameState, owner: PlayerId, defender: PokemonInPlay, knocked_out: bool
) -> list[tuple[object, PokemonInPlay]]:
    """Contra-ataques de quem foi atingido (e, se nocauteado, os de nocaute)."""
    found = []
    is_active = state.state_of(owner).active is defender
    for passive, holder in compiled(state, owner, "on_damaged"):
        if passive.scope == "team":  # type: ignore[attr-defined]
            if is_active and passive.target(defender):  # type: ignore[attr-defined]
                found.append((passive, holder))
        elif holder is defender:
            found.append((passive, holder))
    if knocked_out:
        found += [(p, h) for p, h in compiled(state, owner, "on_knocked_out") if h is defender]
    return found


def survival(state: GameState, owner: PlayerId, defender: PokemonInPlay) -> set[str]:
    """Tipos de sobrevivência/prevenção por moeda que o defensor tem."""
    kinds = ("survive_full", "survive_coin", "coin_prevent")
    return {k for k in kinds if any(h is defender for _, h in compiled(state, owner, k))}


def immune_to(state: GameState, mon: PokemonInPlay, status: object) -> bool:
    owner = owner_of(state, mon)
    return any(
        holder is mon and p.status == status  # type: ignore[attr-defined]
        for p, holder in compiled(state, owner, "immune")
    )


def trainer_locked(state: GameState, player_id: PlayerId, kind: str) -> bool:
    """Item/Ferramenta/Estádio travados por Habilidade do oponente."""
    lock = {"Item": "lock_items", "Tool": "lock_tools", "Stadium": "lock_stadiums"}.get(kind)
    return lock is not None and bool(compiled(state, player_id.other, lock))


def ability_suppressed(state: GameState, mon: PokemonInPlay, name: str) -> bool:
    """Habilidades anuladas por passivas compiladas (Midnight Fluttering,
    Initialization, Sticky Bind). O supressor é lido direto do texto, sem
    passar por `ability_active`, para não entrar em recursão."""
    from pokemon_companion.engine.effects.passive_text import kind_entries

    benched: bool | None = None
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        side = state.state_of(pid)
        for holder in side.all_pokemon_in_play():
            if holder is mon or not holder.card.abilities:
                continue
            for passive, _name in kind_entries(holder, "suppress"):
                if passive.holder_at == "active" and side.active is not holder:
                    continue
                if passive.holder_at == "bench" and not any(holder is b for b in side.bench):
                    continue
                if passive.scope == "opp_active":
                    if state.state_of(pid.other).active is mon and passive.event(name):
                        return True
                elif passive.scope == "all" and passive.target(mon):
                    return True
                elif passive.scope == "all_bench" and passive.target(mon):
                    if benched is None:
                        benched = any(mon is b for p in PlayerId for b in state.state_of(p).bench)
                    if benched:
                        return True
    return False


def prize_adjustment(
    state: GameState,
    owner: PlayerId,
    knocked_out: PokemonInPlay,
    attacker: PokemonInPlay | None,
    was_active: bool,
    prizes: int,
) -> int:
    """Quantos prêmios a mais (ou a menos) um nocaute por ataque rende."""
    from pokemon_companion.engine.effects.core import coin

    change = 0
    bounty = knocked_out.bounty
    if bounty and bounty[1] == state.turn_number:
        change += bounty[0]
    for passive, holder in compiled(state, owner, "prize_none"):
        if holder is knocked_out and (attacker is None or passive.attacker(attacker)):  # type: ignore[attr-defined]
            return -prizes
    for passive, holder in compiled(state, owner, "prize_minus"):
        applies = _applies(passive, holder, knocked_out)
        if applies and (attacker is None or passive.attacker(attacker)):  # type: ignore[attr-defined]
            change -= passive.amount  # type: ignore[attr-defined]
    for passive, holder in compiled(state, owner.other, "prize_plus"):
        if passive.scope == "team":  # type: ignore[attr-defined]
            if was_active and (not passive.coin or coin()):  # type: ignore[attr-defined]
                change += passive.amount  # type: ignore[attr-defined]
        elif holder is attacker and passive.target(knocked_out):  # type: ignore[attr-defined]
            change += passive.amount  # type: ignore[attr-defined]
    return change


def can_evolve_early(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> bool:
    return any(holder is mon for _, holder in compiled(state, owner, "early_evolve"))


def attacks_on_first_turn(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> bool:
    return any(holder is mon for _, holder in compiled(state, owner, "first_turn_attack"))


def no_normal_play(card: object) -> bool:
    from pokemon_companion.engine.effects.passive_text import card_passives

    return any(p.kind == "no_normal_play" for p in card_passives(card))  # type: ignore[arg-type]


def compiled_cost(
    state: GameState, owner: PlayerId, mon: PokemonInPlay, cost: list[str]
) -> list[str]:
    """Custo do ataque com as passivas compiladas (descontos e taxas)."""
    if any(holder is mon for _, holder in compiled(state, owner, "ignore_colorless")):
        cost = [c for c in cost if c != "Colorless"]
    for passive, holder in compiled(state, owner, "cost_minus"):
        if holder is mon:
            for _ in range(passive.value(state, owner, holder)):  # type: ignore[attr-defined]
                if "Colorless" not in cost:
                    break
                cost.remove("Colorless")
    for passive, _holder in compiled(state, owner.other, "tax_opponent"):
        if state.state_of(owner).active is mon and passive.target(mon):  # type: ignore[attr-defined]
            cost = cost + ["Colorless"] * passive.amount  # type: ignore[attr-defined]
    return cost


def after_hand_attach(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> None:
    """Gatilhos de "whenever ... attach an Energy card from ... hand"."""
    for passive, _holder in compiled(state, owner.other, "on_opp_attach"):
        mon.damage_counters += 10 * passive.counters  # type: ignore[attr-defined]
    for passive, _holder in compiled(state, owner, "on_attach_heal"):
        mon.damage_counters = max(mon.damage_counters - passive.amount, 0)  # type: ignore[attr-defined]


def after_evolve(state: GameState, owner: PlayerId, mon: PokemonInPlay) -> None:
    for passive, _holder in compiled(state, owner.other, "on_opp_evolve"):
        mon.damage_counters += 10 * passive.counters  # type: ignore[attr-defined]


def after_switch_to_bench(
    state: GameState, owner: PlayerId, benched: PokemonInPlay, new_active: PokemonInPlay
) -> None:
    """Gatilhos de "whenever your opponent's Active Pokémon moves to the Bench
    during their turn" (só no turno do dono dos Pokémon trocados)."""
    if state.active_player != owner:
        return
    for passive, _holder in compiled(state, owner.other, "on_opp_to_bench"):
        benched.damage_counters += 10 * passive.counters  # type: ignore[attr-defined]
        status = passive.status  # type: ignore[attr-defined]
        if status is not None and not immune_to_special_conditions(state, new_active):
            new_active.status = status


def checkup_extra(state: GameState, owner: PlayerId) -> list[str]:
    """Contadores extras no Checkup vindos de passivas do oponente de `owner`."""
    messages: list[str] = []
    player = state.state_of(owner)
    for passive, holder in compiled(state, owner.other, "checkup_burn"):
        active = player.active
        if active is not None and active.status.name == "BURNED":
            active.damage_counters += 10 * passive.counters  # type: ignore[attr-defined]
            messages.append(f"{holder.card.name}: +{passive.counters} contadores na queimadura.")  # type: ignore[attr-defined]
    for passive, holder in compiled(state, owner.other, "checkup_basics"):
        for mon in player.all_pokemon_in_play():
            if stage_of(mon.card) == "Basic":
                mon.damage_counters += 10 * passive.counters  # type: ignore[attr-defined]
        messages.append(f"{holder.card.name}: contadores nos Básicos do oponente.")
    return messages


def rescue_from_knockout(
    state: GameState, owner_id: PlayerId, mon: PokemonInPlay, messages: list[str]
) -> None:
    """Cartas que voltam do descarte para a mão num nocaute por ataque:
    Infinite Shadow (o próprio Pokémon) e Diver's Catch (Energias {W}
    básicas de um Pokémon {W})."""
    from pokemon_companion.engine.effects.cardinfo import is_basic_energy

    owner = state.state_of(owner_id)
    if ability_active(state, mon, "Infinite Shadow") and mon.card in owner.discard:
        owner.discard.remove(mon.card)
        owner.hand.append(mon.card)
        messages.append(f"{mon.card.name} voltou para a mão (Infinite Shadow).")
    catcher = any(
        ability_active(state, other, "Diver's Catch")
        for other in owner.all_pokemon_in_play()
        if other is not mon
    )
    if catcher and pokemon_type(mon.card) == "Water":
        waters = [c for c in owner.discard if is_basic_energy(c) and c.types == ["Water"]]
        energies = waters[: mon.attached_energies.count("Water")]
        for card in energies:
            owner.discard.remove(card)
            owner.hand.append(card)
        if energies:
            messages.append(
                f"{len(energies)} Energia(s) {{W}} voltaram para a mão (Diver's Catch)."
            )
