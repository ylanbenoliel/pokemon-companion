"""Motor de regras: gera ações legais e aplica seus efeitos sobre o GameState.

Cobre as regras essenciais do livro de regras oficial (ver README para o
que fica de fora): energia (1 por turno), ataques com fraqueza/resistência,
evolução, recuo, condições especiais com Checkup entre turnos, nocaute,
prêmios por tipo de Pokémon e as 3 condições de vitória. Inclui as
restrições de primeiro turno: quem começa não ataca no turno 1, e nenhum
jogador evolui no próprio primeiro turno.

Simplificações documentadas:
- Após um nocaute, o novo ativo é escolhido automaticamente (heurística em
  `choose_promotion`), não pelo jogador.
- Cartas de Treinador, Habilidades e efeitos de texto de ataques (fora os
  cadastrados em `effects/`) não são aplicados.
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Attack, Card, Supertype
from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    Retreat,
    UseAttack,
)
from pokemon_companion.engine.effects.registry import get_effect
from pokemon_companion.engine.game_state import (
    MAX_BENCH_SIZE,
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
    pool = list(attached)
    specific = [c for c in cost if c != "Colorless"]
    colorless_needed = cost.count("Colorless")
    for energy_type in specific:
        if energy_type in pool:
            pool.remove(energy_type)
        else:
            return False
    return len(pool) >= colorless_needed


def calculate_damage(attacker: PokemonInPlay, attack: Attack, defender: PokemonInPlay) -> int:
    damage = attack.base_damage
    attacker_types = set(attacker.card.types)
    weak_types = {w.energy_type for w in defender.card.weaknesses}
    if attacker_types & weak_types:
        damage *= 2
    resist_types = {r.energy_type for r in defender.card.resistances}
    if attacker_types & resist_types:
        damage = max(damage - 30, 0)
    return damage


def legal_actions(state: GameState) -> list[Action]:
    if state.winner is not None:
        return []

    player_id = state.active_player
    player = state.state_of(player_id)
    actions: list[Action] = []

    for i, card in enumerate(player.hand):
        if card.is_basic:
            if player.active is None:
                actions.append(PlayBasicToActive(hand_index=i))
            elif len(player.bench) < MAX_BENCH_SIZE:
                actions.append(PlayBasicToBench(hand_index=i))

    # Regras de 1º turno: turno 1 é o primeiro turno de quem começa, turno 2 o
    # do outro jogador — ninguém evolui no próprio primeiro turno.
    can_evolve = state.turn_number > 2
    for i, card in enumerate(player.hand):
        if can_evolve and card.is_pokemon and card.evolves_from:
            if (
                player.active
                and player.active.card.name == card.evolves_from
                and player.active.turn_played < state.turn_number
                and not player.active.evolved_this_turn
            ):
                actions.append(Evolve(hand_index=i, target_is_active=True))
            for bi, mon in enumerate(player.bench):
                if (
                    mon.card.name == card.evolves_from
                    and mon.turn_played < state.turn_number
                    and not mon.evolved_this_turn
                ):
                    actions.append(Evolve(hand_index=i, target_is_active=False, bench_index=bi))

    if not player.has_attached_energy_this_turn:
        for i, card in enumerate(player.hand):
            if card.supertype == Supertype.ENERGY:
                if player.active:
                    actions.append(AttachEnergy(hand_index=i, target_is_active=True))
                for bi in range(len(player.bench)):
                    actions.append(
                        AttachEnergy(hand_index=i, target_is_active=False, bench_index=bi)
                    )

    # Quem começa pula a etapa de ataque no primeiro turno da partida.
    if state.turn_number > 1 and player.active and can_attack(player.active):
        for ai, attack in enumerate(player.active.card.attacks):
            if energy_satisfies_cost(player.active.attached_energies, attack.cost):
                actions.append(UseAttack(attack_index=ai))

    if player.active and not player.has_retreated_this_turn and can_retreat(player.active):
        cost = len(player.active.card.retreat_cost)
        if len(player.active.attached_energies) >= cost:
            for bi in range(len(player.bench)):
                actions.append(Retreat(bench_index=bi))

    actions.append(EndTurn())
    return actions


def _energy_type_of(card: Card) -> str:
    return card.types[0] if card.types else card.name.replace(" Energy", "")


_TRIPLE_PRIZE_SUBTYPES = {"VMAX", "VSTAR"}
_DOUBLE_PRIZE_SUBTYPES = {"ex", "EX", "GX", "V", "BREAK"}


def prize_count_for(card: Card) -> int:
    """Quantos prêmios o oponente leva ao nocautear esta carta.

    Simplificação do MVP: cobre os casos mais comuns (ex/GX/V = 2,
    VMAX/VSTAR/Mega Evolução ex = 3); outras raridades especiais (ex: TAG
    TEAM) contam como 1.
    """
    subtypes = set(card.subtypes)
    if subtypes & _TRIPLE_PRIZE_SUBTYPES:
        return 3
    if "Mega" in subtypes and "ex" in subtypes:
        return 3
    if subtypes & _DOUBLE_PRIZE_SUBTYPES:
        return 2
    return 1


def choose_promotion(bench: list[PokemonInPlay]) -> int:
    """Índice do Pokémon do banco que assume o ativo após um nocaute.

    Pelas regras, o dono escolhe; aqui a escolha é automática (para os dois
    lados): primeiro quem já consegue atacar com a energia anexada, depois
    quem tem mais energia, depois mais HP restante.
    """

    def score(mon: PokemonInPlay) -> tuple[bool, int, int]:
        ready = any(energy_satisfies_cost(mon.attached_energies, a.cost) for a in mon.card.attacks)
        return (ready, len(mon.attached_energies), mon.current_hp)

    return max(range(len(bench)), key=lambda index: score(bench[index]))


def _check_and_process_knockout(state: GameState, owner_id: PlayerId) -> list[str]:
    messages: list[str] = []
    owner = state.state_of(owner_id)
    opponent_id = owner_id.other
    opponent = state.state_of(opponent_id)

    if owner.active and owner.active.is_knocked_out:
        knocked_out_card = owner.active.card
        messages.append(f"{knocked_out_card.name} ({owner_id.value}) foi nocauteado!")
        owner.discard.append(knocked_out_card)
        owner.active = None

        prize_count = min(prize_count_for(knocked_out_card), len(opponent.prizes))
        for _ in range(prize_count):
            opponent.hand.append(opponent.prizes.pop())
        if prize_count:
            messages.append(
                f"{opponent_id.value} pegou {prize_count} prêmio(s) "
                f"({len(opponent.prizes)} restantes)."
            )

        if owner.bench:
            owner.active = owner.bench.pop(choose_promotion(owner.bench))
        if not opponent.prizes or not owner.has_pokemon_in_play():
            state.winner = opponent_id
            messages.append(f"{opponent_id.value} venceu a partida!")

    return messages


def _end_turn(state: GameState) -> list[str]:
    messages: list[str] = []

    # Pokémon Checkup (livro de regras): Envenenado, Queimado, Adormecido,
    # Paralisado — para os ativos dos dois jogadores, em toda troca de turno.
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        ps = state.state_of(pid)
        if ps.active:
            messages.extend(apply_between_turns_effects(ps.active))
    current_active = state.state_of(state.active_player).active
    if current_active is not None:
        # Paralisado se recupera no checkup logo após o turno do próprio dono.
        messages.extend(recover_from_paralysis(current_active))

    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        messages.extend(_check_and_process_knockout(state, pid))
        if state.winner is not None:
            return messages

    current = state.state_of(state.active_player)
    current.has_attached_energy_this_turn = False
    current.has_retreated_this_turn = False
    for mon in current.all_pokemon_in_play():
        mon.evolved_this_turn = False

    state.active_player = state.active_player.other
    state.turn_number += 1
    new_player = state.state_of(state.active_player)

    if not new_player.deck:
        state.winner = state.active_player.other
        messages.append(
            f"{state.active_player.value} não tem cartas para comprar e perde a partida!"
        )
        return messages

    new_player.hand.append(new_player.deck.pop(0))
    return messages


def apply_action(state: GameState, action: Action) -> list[str]:
    if state.winner is not None:
        return [f"A partida já terminou. Vencedor: {state.winner.value}."]

    player_id = state.active_player
    player = state.state_of(player_id)
    opponent = state.state_of(player_id.other)
    messages: list[str] = []

    if isinstance(action, PlayBasicToActive):
        card = player.hand.pop(action.hand_index)
        player.active = PokemonInPlay(card=card, turn_played=state.turn_number)
        messages.append(f"{player_id.value} colocou {card.name} como Pokémon ativo.")

    elif isinstance(action, PlayBasicToBench):
        card = player.hand.pop(action.hand_index)
        player.bench.append(PokemonInPlay(card=card, turn_played=state.turn_number))
        messages.append(f"{player_id.value} colocou {card.name} no banco.")

    elif isinstance(action, Evolve):
        card = player.hand.pop(action.hand_index)
        target = player.active if action.target_is_active else player.bench[action.bench_index]  # type: ignore[index]
        assert target is not None
        evolved = PokemonInPlay(
            card=card,
            attached_energies=target.attached_energies,
            damage_counters=target.damage_counters,
            # Evoluir remove todas as condições especiais (livro de regras).
            status=StatusCondition.NONE,
            turn_played=target.turn_played,
            evolved_this_turn=True,
        )
        if action.target_is_active:
            player.active = evolved
        else:
            player.bench[action.bench_index] = evolved  # type: ignore[index]
        messages.append(f"{target.card.name} evoluiu para {card.name}.")

    elif isinstance(action, AttachEnergy):
        card = player.hand.pop(action.hand_index)
        target = player.active if action.target_is_active else player.bench[action.bench_index]  # type: ignore[index]
        assert target is not None
        energy_type = _energy_type_of(card)
        target.attached_energies.append(energy_type)
        player.has_attached_energy_this_turn = True
        messages.append(f"{player_id.value} anexou {card.name} em {target.card.name}.")

    elif isinstance(action, UseAttack):
        attacker = player.active
        assert attacker is not None
        attack = attacker.card.attacks[action.attack_index]
        can_proceed, confusion_messages = check_confusion_self_damage(attacker)
        messages.extend(confusion_messages)
        if can_proceed:
            defender = opponent.active
            if defender is not None:
                effect = get_effect(attacker.card.id, attack.name)
                if effect:
                    effect(state, player_id, attacker, defender, attack.name)
                damage = calculate_damage(attacker, attack, defender)
                defender.damage_counters += damage
                messages.append(
                    f"{attacker.card.name} usou {attack.name} e causou {damage} de dano "
                    f"em {defender.card.name}."
                )
                messages.extend(_check_and_process_knockout(state, player_id.other))
        if state.winner is None:
            messages.extend(_end_turn(state))

    elif isinstance(action, Retreat):
        new_active = player.bench[action.bench_index]
        assert player.active is not None
        cost = len(player.active.card.retreat_cost)
        for _ in range(cost):
            if player.active.attached_energies:
                player.active.attached_energies.pop()
        # Ir para o banco remove todas as condições especiais.
        player.active.status = StatusCondition.NONE
        player.bench[action.bench_index] = player.active
        player.active = new_active
        player.has_retreated_this_turn = True
        messages.append(f"{player_id.value} recuou para {new_active.card.name}.")

    elif isinstance(action, EndTurn):
        messages.extend(_end_turn(state))

    return messages


def is_game_over(state: GameState) -> bool:
    return state.winner is not None
