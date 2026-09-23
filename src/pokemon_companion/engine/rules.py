"""Motor de regras: gera ações legais e aplica seus efeitos sobre o GameState.

Segue o livro de regras oficial: energia 1x por turno, 1 Apoiador por turno
(nenhum no 1º turno de quem começa), 1 Estádio por turno, Ferramentas,
Habilidades, ataques com Fraqueza/Resistência e efeitos de texto, recuo,
condições especiais com Pokémon Checkup, nocaute de qualquer Pokémon (Ativo
ou Banco) com prêmios por tipo, escolha do novo Ativo, as 3 condições de
vitória e Morte Súbita. Quem começa não ataca no turno 1 e ninguém evolui no
próprio primeiro turno.

Efeitos de cartas ficam em `effects/` (registros por nome); cartas sem
registro têm só o dano base (ataques) ou nenhum efeito (Treinadores).

Escolhas de jogadores em `state.manual_choices` (o humano) viram ações
pendentes (`PromoteActive`, montagem do setup); os demais lados escolhem
por heurística na hora. `decision_player()` diz quem deve agir.
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Attack, Card, Supertype
from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
    EndSetup,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    PlayTrainer,
    PromoteActive,
    Retreat,
    UseAbility,
    UseAttack,
    UseStadium,
)
from pokemon_companion.engine.effects import abilities, attacks, core, passives, trainers
from pokemon_companion.engine.effects.cardinfo import (
    in_group,
    is_basic_energy,
    is_tera,
    pokemon_type,
    trainer_kind,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import (
    GameState,
    PlayerId,
    PokemonInPlay,
    StatusCondition,
)
from pokemon_companion.engine.status_conditions import (
    apply_between_turns_effects,
    can_attack,
    can_retreat,
    check_confusion_self_damage,
    recover_from_paralysis,
)


def energy_satisfies_cost(attached: list[str], cost: list[str]) -> bool:
    return passives.energy_satisfies(attached, cost)


def calculate_damage(attacker: PokemonInPlay, attack: Attack, defender: PokemonInPlay) -> int:
    """Dano base com Fraqueza (×2) e Resistência (−30), sem efeitos."""
    damage = attack.base_damage
    attacker_types = set(attacker.card.types)
    if attacker_types & {w.energy_type for w in defender.card.weaknesses}:
        damage *= 2
    if attacker_types & {r.energy_type for r in defender.card.resistances}:
        damage = max(damage - 30, 0)
    return damage


_TRIPLE_PRIZE_SUBTYPES = {"VMAX", "VSTAR"}
_DOUBLE_PRIZE_SUBTYPES = {"ex", "EX", "GX", "V", "BREAK"}


def prize_count_for(card: Card) -> int:
    """Quantos prêmios o oponente leva ao nocautear esta carta (ex/GX/V = 2,
    VMAX/VSTAR/Mega Evolução ex = 3)."""
    subtypes = set(card.subtypes)
    if subtypes & _TRIPLE_PRIZE_SUBTYPES:
        return 3
    if "Mega" in subtypes and ("ex" in subtypes or "EX" in subtypes):
        return 3
    if subtypes & _DOUBLE_PRIZE_SUBTYPES:
        return 2
    return 1


def choose_promotion(
    bench: list[PokemonInPlay], state: GameState | None = None, owner: PlayerId | None = None
) -> int:
    """Heurística do novo Ativo: quem já consegue atacar, depois mais
    energia, depois mais HP restante."""

    def ready(mon: PokemonInPlay) -> bool:
        if state is not None and owner is not None:
            return any(passives.can_pay(state, owner, mon, a) for a in mon.card.attacks)
        return any(energy_satisfies_cost(mon.attached_energies, a.cost) for a in mon.card.attacks)

    def score(mon: PokemonInPlay) -> tuple[bool, int, int]:
        return (ready(mon), len(mon.attached_energies), mon.current_hp)

    return max(range(len(bench)), key=lambda index: score(bench[index]))


def decision_player(state: GameState) -> PlayerId:
    """Quem deve escolher a próxima ação (normalmente o jogador do turno)."""
    if state.pending_promotion is not None:
        return state.pending_promotion
    if state.pending_setup:
        return state.pending_setup[0]
    return state.active_player


# ---------------------------------------------------------------------------
# ações legais


def _mon_at(
    state: GameState, pid: PlayerId, is_active: bool, bench_index: int | None
) -> PokemonInPlay | None:
    player = state.state_of(pid)
    return player.active if is_active else core.mon_at(player, bench_index or 0)


def _can_evolve_onto(state: GameState, pid: PlayerId, card: Card, mon: PokemonInPlay) -> bool:
    if mon.card.name != card.evolves_from or mon.evolved_this_turn:
        return False
    if passives.no_normal_play(card):
        return False
    if passives.can_evolve_early(state, pid, mon):
        return True
    if state.turn_number <= 2:
        return False
    if mon.turn_played < state.turn_number:
        return True
    return (
        passives.stadium_is(state, "Forest of Vitality")
        and pokemon_type(card) == "Grass"
        and pokemon_type(mon.card) == "Grass"
    )


def _attach_locked(state: GameState, mon: PokemonInPlay) -> bool:
    trap = mon.attach_trap
    return trap is not None and trap == ("lock", state.turn_number)


def _can_attach(card: Card, mon: PokemonInPlay) -> bool:
    if card.name == "Team Rocket's Energy":
        return in_group(mon.card, "Team Rocket's")
    return True


def _trainer_playable(state: GameState, pid: PlayerId, card: Card) -> bool:
    player = state.state_of(pid)
    kind = trainer_kind(card)
    if "ACE SPEC" in card.subtypes and trainers.ace_spec_blocked(state, pid):
        return False
    if kind == "Supporter":
        if player.supporter_played_this_turn:
            return False
        if player.supporters_blocked_turn == state.turn_number:
            return False
        if state.turn_number == 1 and card.name != "Team Rocket's Proton":
            return False
    elif kind in ("Item", "Tool") or ("Item" in card.subtypes):
        if player.items_blocked_turn == state.turn_number:
            return False
        if passives.trainer_locked(state, pid, kind or "Item"):
            return False
    if kind == "Stadium" and passives.trainer_locked(state, pid, "Stadium"):
        return False
    if kind == "Stadium":
        if player.stadium_played_this_turn:
            return False
        if player.stadiums_blocked_turn == state.turn_number:
            return False
        if state.stadium is not None and state.stadium.name == card.name:
            return False
    return True


def _trainer_actions(state: GameState, pid: PlayerId) -> list[Action]:
    player = state.state_of(pid)
    actions: list[Action] = []
    seen: set[str] = set()
    for i, card in enumerate(player.hand):
        if card.supertype != Supertype.TRAINER or card.name in seen:
            continue
        seen.add(card.name)
        if not _trainer_playable(state, pid, card):
            continue
        ctx = Ctx(state, pid)
        kind = trainer_kind(card)
        if kind == "Tool":
            options = trainers.tool_targets(ctx)
        elif kind == "Stadium":
            options = [None]
        else:
            spec = trainers.spec_for(card)
            if spec is None:
                options = [None]
            elif not spec.can_play(ctx):
                continue
            else:
                options = spec.options(ctx) if spec.options else [None]
        actions.extend(PlayTrainer(hand_index=i, target=target) for target in options)
    return actions


def _ability_actions(state: GameState, pid: PlayerId) -> list[Action]:
    player = state.state_of(pid)
    actions: list[Action] = []
    for position in core.positions(player):
        mon = core.mon_at(player, position)
        assert mon is not None
        ctx = Ctx(state, pid, mon)
        for name, spec in abilities.usable_abilities(ctx, mon):
            for target in abilities.ability_options(ctx, spec):
                actions.append(UseAbility(position=position, ability_name=name, target=target))
    return actions


def _attack_actions(state: GameState, pid: PlayerId) -> list[Action]:
    player = state.state_of(pid)
    active = player.active
    if active is None or not can_attack(active):
        return []
    if state.turn_number <= 1 and not passives.attacks_on_first_turn(state, pid, active):
        return []
    if player.attacks_this_turn >= _attacks_allowed(state, pid):
        return []
    actions: list[Action] = []
    for index, attack in enumerate(active.card.attacks):
        if not passives.attack_allowed(state, pid, active, attack):
            continue
        if not passives.can_pay(state, pid, active, attack):
            continue
        ctx = Ctx(state, pid, active)
        for target in attacks.attack_options(ctx, attack):
            actions.append(UseAttack(attack_index=index, target=target))
    return actions


def _attacks_allowed(state: GameState, pid: PlayerId) -> int:
    active = state.state_of(pid).active
    if (
        active is not None
        and passives.stadium_is(state, "Festival Grounds")
        and passives.ability_active(state, active, "Festival Lead")
    ):
        return 2
    return 1


def _setup_actions(state: GameState, pid: PlayerId) -> list[Action]:
    player = state.state_of(pid)
    actions: list[Action] = []
    seen: set[str] = set()
    for i, card in enumerate(player.hand):
        if not card.is_basic or card.name in seen:
            continue
        seen.add(card.name)
        if player.active is None:
            actions.append(PlayBasicToActive(hand_index=i))
        elif core.bench_space(state, player) > 0:
            actions.append(PlayBasicToBench(hand_index=i))
    if player.active is not None:
        actions.append(EndSetup())
    return actions


def legal_actions(state: GameState) -> list[Action]:
    if state.winner is not None:
        return []
    if state.pending_promotion is not None:
        bench = state.state_of(state.pending_promotion).bench
        return [PromoteActive(bench_index=i) for i in range(len(bench))]
    if state.pending_setup:
        return _setup_actions(state, state.pending_setup[0])

    pid = state.active_player
    player = state.state_of(pid)

    # Depois do 1º ataque (Festival Lead), só dá para atacar de novo ou passar.
    if player.attacks_this_turn > 0:
        return [*_attack_actions(state, pid), EndTurn()]

    actions: list[Action] = []
    seen: set[tuple[str, str]] = set()

    def first(kind: str, card: Card) -> bool:
        key = (kind, card.name)
        if key in seen:
            return False
        seen.add(key)
        return True

    for i, card in enumerate(player.hand):
        if card.is_basic and first("basic", card):
            if player.active is None:
                actions.append(PlayBasicToActive(hand_index=i))
            elif core.bench_space(state, player) > 0:
                actions.append(PlayBasicToBench(hand_index=i))

    # Regras de 1º turno: turno 1 é o primeiro de quem começa, turno 2 o do
    # outro jogador — ninguém evolui no próprio primeiro turno.
    if player.evolution_blocked_turn != state.turn_number:
        for i, card in enumerate(player.hand):
            if not (card.is_pokemon and card.evolves_from) or not first("evolve", card):
                continue
            if player.active and _can_evolve_onto(state, pid, card, player.active):
                actions.append(Evolve(hand_index=i, target_is_active=True))
            for bi, mon in enumerate(player.bench):
                if _can_evolve_onto(state, pid, card, mon):
                    actions.append(Evolve(hand_index=i, target_is_active=False, bench_index=bi))

    if not player.has_attached_energy_this_turn:
        for i, card in enumerate(player.hand):
            if card.supertype != Supertype.ENERGY or not first("energy", card):
                continue
            if (
                player.active
                and _can_attach(card, player.active)
                and not _attach_locked(state, player.active)
            ):
                actions.append(AttachEnergy(hand_index=i, target_is_active=True))
            for bi, mon in enumerate(player.bench):
                if _can_attach(card, mon) and not _attach_locked(state, mon):
                    actions.append(
                        AttachEnergy(hand_index=i, target_is_active=False, bench_index=bi)
                    )

    actions.extend(_trainer_actions(state, pid))
    actions.extend(_ability_actions(state, pid))

    if state.stadium is not None and not player.stadium_used_this_turn:
        spec = trainers.stadium_spec_for(state.stadium)
        if spec is not None and spec.can_play(Ctx(state, pid)):
            actions.append(UseStadium())

    actions.extend(_attack_actions(state, pid))

    active = player.active
    if (
        active is not None
        and not player.has_retreated_this_turn
        and can_retreat(active)
        and active.cannot_retreat_turn != state.turn_number
        and len(active.attached_energies) >= passives.retreat_cost(state, pid, active)
    ):
        actions.extend(Retreat(bench_index=bi) for bi in range(len(player.bench)))

    actions.append(EndTurn())
    return actions


# ---------------------------------------------------------------------------
# nocaute, promoção e vitória


def _process_knockouts(
    state: GameState,
    messages: list[str],
    attacker: PlayerId | None = None,
    attacker_mon: PokemonInPlay | None = None,
) -> None:
    """Nocauteia todo Pokémon sem HP (Ativo ou Banco) e entrega os prêmios.
    `attacker` indica que os nocautes vieram do dano de um ataque dele."""
    for owner_id in (state.active_player.other, state.active_player):
        owner = state.state_of(owner_id)
        taker = state.state_of(owner_id.other)
        for mon in [m for m in owner.all_pokemon_in_play() if m.is_knocked_out]:
            if (
                attacker is not None
                and owner_id is attacker.other
                and passives.ability_active(state, mon, "Durable Body")
                and core.coin()
            ):
                mon.damage_counters = mon.max_hp - 10
                messages.append(f"{mon.card.name} resistiu com 10 de HP (Durable Body).")
                continue
            was_active = owner.active is mon
            prizes = prize_count_for(mon.card)
            if attacker is not None and owner_id is attacker.other:
                prizes += passives.prize_adjustment(
                    state, owner_id, mon, attacker_mon, was_active, prizes
                )
            if attacker is not None and owner_id is attacker.other:
                if passives.tool_active(state, mon, "Lillie's Pearl") and in_group(
                    mon.card, "Lillie's"
                ):
                    prizes -= 1
                if "Legacy Energy" in mon.attached_energies and not owner.legacy_energy_used:
                    owner.legacy_energy_used = True
                    prizes -= 1
                if (
                    was_active
                    and taker.extra_prize_turn == state.turn_number
                    and attacker_mon is not None
                    and is_tera(attacker_mon.card)
                ):
                    prizes += 1
            messages.append(f"{mon.card.name} ({owner_id.value}) foi nocauteado!")
            core.discard_pokemon(owner, mon)
            if was_active:
                owner.active = None
            else:
                del owner.bench[core.index_of(owner.bench, mon)]
            if owner.knocked_out_turn != state.turn_number:
                owner.knocked_out_names = []
            owner.knocked_out_turn = state.turn_number
            owner.knocked_out_names.append(mon.card.name)
            taken = min(max(prizes, 0), len(taker.prizes))
            last = taker.prizes_taken_last
            already = last[0] if last and last[1] == state.turn_number else 0
            taker.prizes_taken_last = (already + taken, state.turn_number)
            for _ in range(taken):
                taker.hand.append(taker.prizes.pop())
            if taken:
                messages.append(
                    f"{owner_id.other.value} pegou {taken} prêmio(s) ({len(taker.prizes)} restantes)."
                )


def _check_winner(state: GameState, messages: list[str]) -> None:
    if state.pending_setup or state.winner is not None:
        return
    winners = set()
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        me, other = state.state_of(pid), state.state_of(pid.other)
        if not me.prizes or not other.has_pokemon_in_play():
            winners.add(pid)
    if len(winners) == 2:
        _sudden_death(state, messages)
    elif winners:
        state.winner = winners.pop()
        messages.append(f"{state.winner.value} venceu a partida!")


def _sudden_death(state: GameState, messages: list[str]) -> None:
    """Os dois venceram ao mesmo tempo: nova partida com 1 prêmio cada."""
    from pokemon_companion.engine.turn_manager import start_sudden_death

    messages.append("Os dois jogadores venceram ao mesmo tempo: Morte Súbita (1 prêmio)!")
    fresh = start_sudden_death(state)
    state.__dict__.update(fresh.__dict__)


def _handle_promotions(state: GameState, messages: list[str]) -> None:
    if state.winner is not None or state.pending_promotion is not None:
        return
    order = (state.active_player.other, state.active_player)
    for pid in order:
        player = state.state_of(pid)
        if player.active is not None or not player.bench:
            continue
        if pid in state.manual_choices:
            state.pending_promotion = pid
            return
        index = choose_promotion(player.bench, state, pid)
        player.active = player.bench.pop(index)
        messages.append(f"{pid.value} promoveu {player.active.card.name} a Ativo.")


def _enforce_board(state: GameState, messages: list[str]) -> None:
    """Efeitos contínuos que mudam o tabuleiro: limite do banco (Area Zero
    Underdepths), Festival Grounds e Team Rocket's Energy em Pokémon errado."""
    passives.refresh_hp_bonuses(state)
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        player = state.state_of(pid)
        while len(player.bench) > core.bench_limit(state, player):
            mon = min(player.bench, key=lambda m: (len(m.attached_energies), m.current_hp))
            core.discard_pokemon(player, mon)
            del player.bench[core.index_of(player.bench, mon)]
            messages.append(f"{mon.card.name} foi descartado (limite do banco).")
        for mon in player.all_pokemon_in_play():
            if passives.stadium_is(state, "Festival Grounds") and mon.attached_energies:
                mon.status = StatusCondition.NONE
            while "Team Rocket's Energy" in mon.attached_energies and not in_group(
                mon.card, "Team Rocket's"
            ):
                player.discard.append(core.detach_energy(mon, "Team Rocket's Energy"))


def _after_action(
    state: GameState,
    messages: list[str],
    attacker: PlayerId | None = None,
    attacker_mon: PokemonInPlay | None = None,
) -> None:
    _enforce_board(state, messages)
    _process_knockouts(state, messages, attacker, attacker_mon)
    _check_winner(state, messages)
    _handle_promotions(state, messages)


# ---------------------------------------------------------------------------
# fim de turno


def _pokemon_checkup(state: GameState, messages: list[str]) -> None:
    """Pokémon Checkup (livro de regras): Envenenado, Queimado, Adormecido,
    Paralisado; depois Habilidades de Checkup."""
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        active = state.state_of(pid).active
        if active is None:
            continue
        messages.extend(apply_between_turns_effects(active))
        if (
            active.status == StatusCondition.POISONED
            and passives.stadium_is(state, "Perilous Jungle")
            and pokemon_type(active.card) != "Darkness"
        ):
            active.damage_counters += 20
            messages.append(f"Perilous Jungle: +2 contadores em {active.card.name}.")
        if active.status == StatusCondition.POISONED:
            opponent_active = state.state_of(pid.other).active
            if opponent_active is not None and passives.ability_active(
                state, opponent_active, "Toxic Subjugation"
            ):
                active.damage_counters += 50
                messages.append(f"Toxic Subjugation: +5 contadores em {active.card.name}.")
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        messages.extend(passives.checkup_extra(state, pid))
    current_active = state.state_of(state.active_player).active
    if current_active is not None:
        # Paralisado se recupera no checkup logo após o turno do próprio dono.
        messages.extend(recover_from_paralysis(current_active))

    shrouds = sum(
        1
        for pid in (PlayerId.PLAYER, PlayerId.OPPONENT)
        for mon in state.state_of(pid).all_pokemon_in_play()
        if passives.ability_active(state, mon, "Freezing Shroud")
    )
    if shrouds:
        for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
            for mon in state.state_of(pid).all_pokemon_in_play():
                if mon.card.abilities and mon.card.name != "Froslass":
                    mon.damage_counters += 10 * shrouds


def _end_of_turn_effects(state: GameState, messages: list[str]) -> None:
    pid = state.active_player
    player = state.state_of(pid)
    for mon in player.all_pokemon_in_play():
        while "Ignition Energy" in mon.attached_energies:
            player.discard.append(core.detach_energy(mon, "Ignition Energy"))
            messages.append(f"Ignition Energy de {mon.card.name} foi descartada.")
    limit = player.discard_hand_at_end
    if limit and limit[1] == state.turn_number and len(player.hand) >= limit[0]:
        player.discard.extend(player.hand)
        player.hand.clear()
        messages.append(f"{pid.value} descartou a mão no fim do turno.")
    active = player.active
    if active is not None and passives.tool_active(state, active, "Powerglass"):
        ctx = Ctx(state, pid, active, messages=messages)
        core.attach_from(ctx, player.discard, is_basic_energy, 1, allowed=lambda m: m is active)
    _resolve_dooms(state, messages)


def _resolve_dooms(state: GameState, messages: list[str]) -> None:
    """Efeitos marcados para o fim deste turno (`PokemonInPlay.doom`)."""
    for owner_id in (PlayerId.PLAYER, PlayerId.OPPONENT):
        owner = state.state_of(owner_id)
        for mon in list(owner.all_pokemon_in_play()):
            if mon.doom is None or mon.doom[1] != state.turn_number:
                continue
            kind, mon.doom = mon.doom[0], None
            if kind == "ko":
                mon.damage_counters = mon.max_hp
                messages.append(f"{mon.card.name} foi nocauteado (efeito de fim de turno).")
            elif kind == "discard":
                core.discard_pokemon(owner, mon)
                if owner.active is mon:
                    owner.active = None
                else:
                    del owner.bench[core.index_of(owner.bench, mon)]
                messages.append(f"{mon.card.name} foi descartado (efeito de fim de turno).")
            elif kind.startswith("counters:"):
                mon.damage_counters += 10 * int(kind.partition(":")[2])
                messages.append(f"{mon.card.name} recebeu contadores (efeito de fim de turno).")


def _end_turn(state: GameState) -> list[str]:
    messages: list[str] = []
    _end_of_turn_effects(state, messages)
    _pokemon_checkup(state, messages)
    _after_action(state, messages)
    if state.winner is not None:
        return messages
    if state.pending_promotion is not None:
        state.resume_phase = "next_turn"
        return messages
    messages.extend(_start_next_turn(state))
    return messages


def _reset_turn_flags(state: GameState, pid: PlayerId) -> None:
    player = state.state_of(pid)
    player.has_attached_energy_this_turn = False
    player.has_retreated_this_turn = False
    player.supporter_played_this_turn = False
    player.stadium_played_this_turn = False
    player.stadium_used_this_turn = False
    player.used_ability_names = set()
    player.damage_bonus_this_turn = []
    player.attacks_this_turn = 0
    player.played_this_turn = []
    for mon in player.all_pokemon_in_play():
        mon.evolved_this_turn = False
        mon.healed_this_turn = False
        mon.abilities_used = set()


def _start_next_turn(state: GameState) -> list[str]:
    state.resume_phase = ""
    _reset_turn_flags(state, state.active_player)
    state.active_player = state.active_player.other
    state.turn_number += 1
    _reset_turn_flags(state, state.active_player)
    new_player = state.state_of(state.active_player)
    if not new_player.deck:
        state.winner = state.active_player.other
        return [f"{state.active_player.value} não tem cartas para comprar e perde a partida!"]
    new_player.hand.append(new_player.deck.pop(0))
    return []


# ---------------------------------------------------------------------------
# aplicar ações


def apply_action(state: GameState, action: Action) -> list[str]:
    if state.winner is not None:
        return [f"A partida já terminou. Vencedor: {state.winner.value}."]

    if isinstance(action, PromoteActive):
        return _apply_promotion(state, action)
    if state.pending_setup:
        return _apply_setup(state, action)

    pid = state.active_player
    player = state.state_of(pid)
    messages: list[str] = []
    who = pid.value

    if isinstance(action, PlayBasicToActive):
        card = player.hand.pop(action.hand_index)
        player.active = PokemonInPlay(card=card, turn_played=state.turn_number)
        messages.append(f"{who} colocou {card.name} como Pokémon ativo.")

    elif isinstance(action, PlayBasicToBench):
        card = player.hand.pop(action.hand_index)
        benched = core.put_on_bench(state, player, card)
        benched.played_from_hand_turn = state.turn_number
        messages.append(f"{who} colocou {card.name} no banco.")

    elif isinstance(action, Evolve):
        card = player.hand.pop(action.hand_index)
        target = _mon_at(state, pid, action.target_is_active, action.bench_index)
        assert target is not None
        evolved = core.evolve_into(state, player, target, card)
        messages.append(f"{target.card.name} evoluiu para {card.name}.")
        passives.after_evolve(state, pid, evolved)

    elif isinstance(action, AttachEnergy):
        card = player.hand.pop(action.hand_index)
        target = _mon_at(state, pid, action.target_is_active, action.bench_index)
        assert target is not None
        core.attach_energy_card(target, card)
        passives.after_hand_attach(state, pid, target)
        player.has_attached_energy_this_turn = True
        trap = target.attach_trap
        if trap is not None and trap[1] == state.turn_number:
            if trap[0].startswith("counters:"):
                target.damage_counters += 10 * int(trap[0].partition(":")[2])
                messages.append(f"Armadilha: contadores em {target.card.name}.")
            elif trap[0] == "end_turn":
                messages.append("Anexar essa energia encerrou o turno.")
                _after_action(state, messages)
                if state.winner is None and state.pending_promotion is None:
                    messages.extend(_end_turn(state))
                return messages
        messages.append(f"{who} anexou {card.name} em {target.card.name}.")
        if card.name == "Enriching Energy":
            drawn = core.draw(player, 4)
            messages.append(f"{who} comprou {drawn} carta(s) (Enriching Energy).")
        if card.name == "Telepathic Psychic Energy" and pokemon_type(target.card) == "Psychic":
            core.search_deck(
                Ctx(state, pid, target, messages=messages),
                lambda c: c.is_basic and pokemon_type(c) == "Psychic",
                2,
                destination="bench",
            )

    elif isinstance(action, PlayTrainer):
        return _play_trainer(state, pid, action)

    elif isinstance(action, UseAbility):
        mon = core.mon_at(player, action.position)
        assert mon is not None
        ctx = Ctx(state, pid, mon, action.target, messages, is_ability=True)
        abilities.use_ability(ctx, action.ability_name)

    elif isinstance(action, UseStadium):
        assert state.stadium is not None
        player.stadium_used_this_turn = True
        ctx = Ctx(state, pid, messages=messages)
        messages.append(f"{who} usou o Estádio {state.stadium.name}.")
        spec = trainers.stadium_spec_for(state.stadium)
        assert spec is not None
        spec.fn(ctx)
        _after_action(state, messages)
        if ctx.ends_turn and state.winner is None:
            messages.extend(_end_turn(state))
        return messages

    elif isinstance(action, UseAttack):
        return messages + _apply_attack(state, pid, action)

    elif isinstance(action, Retreat):
        active = player.active
        assert active is not None
        for _ in range(passives.retreat_cost(state, pid, active)):
            if active.attached_energies:
                core.discard_energy(player, active)
        core.switch_active(state, player, action.bench_index)
        player.has_retreated_this_turn = True
        messages.append(f"{who} recuou para {player.active.card.name}.")  # type: ignore[union-attr]

    elif isinstance(action, EndTurn):
        return _end_turn(state)

    _after_action(state, messages)
    return messages


def _play_trainer(state: GameState, pid: PlayerId, action: PlayTrainer) -> list[str]:
    player = state.state_of(pid)
    messages: list[str] = []
    card = player.hand.pop(action.hand_index)
    kind = trainer_kind(card)
    messages.append(f"{pid.value} jogou {card.name}.")
    player.played_this_turn.append(card.name)
    ctx = Ctx(state, pid, None, action.target, messages)

    if kind == "Supporter":
        player.supporter_played_this_turn = True
        if "Team Rocket" in card.name:
            player.played_team_rocket_supporter_turn = state.turn_number
    if kind == "Stadium":
        trainers.discard_stadium(state)
        state.stadium = card
        state.stadium_owner = pid
        player.stadium_played_this_turn = True
        _after_action(state, messages)
        return messages
    if kind == "Tool":
        assert action.target is not None
        mon = core.mon_at(player, int(action.target[1]))  # type: ignore[call-overload]
        assert mon is not None
        mon.tool = card
        messages.append(f"{card.name} foi anexada em {mon.card.name}.")
        _after_action(state, messages)
        return messages

    spec = trainers.spec_for(card)
    if spec is None:
        messages.append(f"(efeito de {card.name} não implementado)")
    else:
        spec.fn(ctx)
    player.discard.append(card)
    _after_action(state, messages)
    return messages


def _apply_attack(state: GameState, pid: PlayerId, action: UseAttack) -> list[str]:
    player = state.state_of(pid)
    attacker = player.active
    assert attacker is not None
    attack = attacker.card.attacks[action.attack_index]
    messages: list[str] = []
    player.attacks_this_turn += 1
    can_proceed, confusion_messages = check_confusion_self_damage(attacker)
    messages.extend(confusion_messages)
    coin_check = can_proceed and attacker.attack_coin_turn == state.turn_number
    if coin_check and not all(core.coin() for _ in range(attacker.attack_coins)):
        messages.append(f"Coroa: {attacker.card.name} não consegue atacar.")
        can_proceed = False
    player.last_attack = (attack.name, state.turn_number)
    if can_proceed:
        messages.append(f"{attacker.card.name} usou {attack.name}.")
        ctx = Ctx(state, pid, attacker, action.target, messages)
        boomerangs = attacker.attached_energies.count("Boomerang Energy")
        attacks.resolve_attack(ctx, attack)
        _return_boomerangs(state, pid, attacker, boomerangs, messages)
    _after_action(state, messages, attacker=pid, attacker_mon=attacker)
    if state.winner is not None:
        return messages

    again = (
        player.attacks_this_turn < _attacks_allowed(state, pid)
        and player.active is attacker
        and can_proceed
    )
    if again:
        return messages  # Festival Lead: pode atacar de novo (ou passar)
    if state.pending_promotion is not None:
        state.resume_phase = "end_turn"
        return messages
    messages.extend(_end_turn(state))
    return messages


def _return_boomerangs(
    state: GameState, pid: PlayerId, attacker: PokemonInPlay, before: int, messages: list[str]
) -> None:
    """Boomerang Energy descartada pelo próprio ataque volta ao Pokémon."""
    player = state.state_of(pid)
    if not any(m is attacker for m in player.all_pokemon_in_play()):
        return
    for _ in range(before - attacker.attached_energies.count("Boomerang Energy")):
        card = next((c for c in player.discard if c.name == "Boomerang Energy"), None)
        if card is None:
            return
        player.discard.remove(card)
        core.attach_energy_card(attacker, card)
        messages.append(f"Boomerang Energy voltou para {attacker.card.name}.")


def _apply_promotion(state: GameState, action: PromoteActive) -> list[str]:
    pid = state.pending_promotion
    assert pid is not None
    player = state.state_of(pid)
    player.active = player.bench.pop(action.bench_index)
    state.pending_promotion = None
    messages = [f"{pid.value} promoveu {player.active.card.name} a Ativo."]
    _handle_promotions(state, messages)
    if state.pending_promotion is None:
        # retoma o fim de turno que parou esperando a escolha
        phase, state.resume_phase = state.resume_phase, ""
        if phase == "end_turn":
            messages.extend(_end_turn(state))
        elif phase == "next_turn":
            messages.extend(_start_next_turn(state))
    return messages


def _apply_setup(state: GameState, action: Action) -> list[str]:
    pid = state.pending_setup[0]
    player = state.state_of(pid)
    if isinstance(action, PlayBasicToActive):
        card = player.hand.pop(action.hand_index)
        player.active = PokemonInPlay(card=card, turn_played=0)
        return [f"{pid.value} escolheu {card.name} como Ativo."]
    if isinstance(action, PlayBasicToBench):
        card = player.hand.pop(action.hand_index)
        player.bench.append(PokemonInPlay(card=card, turn_played=0))
        return [f"{pid.value} colocou {card.name} no banco."]
    if isinstance(action, EndSetup):
        state.pending_setup = state.pending_setup[1:]
        return [f"{pid.value} terminou o setup."]
    return []


def is_game_over(state: GameState) -> bool:
    return state.winner is not None


def describe_target(state: GameState, pid: PlayerId, action: Action) -> str:
    """Texto curto do alvo de uma ação (para a UI e o histórico)."""
    target = getattr(action, "target", None)
    if not target:
        return ""
    player, opponent = state.state_of(pid), state.state_of(pid.other)
    kind = target[0]
    if kind in ("own", "opp"):
        mon = core.mon_at(player if kind == "own" else opponent, int(target[1]))  # type: ignore[call-overload]
        return mon.card.name if mon else "?"
    if kind == "copy":
        source = core.mon_at(player if int(target[1]) >= 0 else opponent, int(target[1]))  # type: ignore[call-overload]
        if source is None:
            return "?"
        return f"{source.card.name}: {source.card.attacks[int(target[2])].name}"  # type: ignore[call-overload]
    if kind == "move":
        src = core.mon_at(player, int(target[1]))  # type: ignore[call-overload]
        dst = core.mon_at(player, int(target[2]))  # type: ignore[call-overload]
        return f"{src.card.name if src else '?'} → {dst.card.name if dst else '?'}"
    if kind == "candy":
        mon = core.mon_at(player, int(target[2]))  # type: ignore[call-overload]
        return f"{mon.card.name if mon else '?'} → {target[1]}"
    if kind == "mode":
        if isinstance(action, UseAttack) and player.active is not None:
            spec = attacks.spec_for(player.active.card.attacks[action.attack_index])
            if spec is not None and spec.mode_label is not None:
                return spec.mode_label(int(target[1]))  # type: ignore[call-overload]
        if isinstance(action, PlayTrainer) and player.hand[action.hand_index].name == "Kieran":
            return "+30 de dano contra ex/V neste turno"
        return f"opção {target[1]}"
    return str(target)
