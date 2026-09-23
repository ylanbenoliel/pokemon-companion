"""Primitivas de efeito (comprar, buscar, trocar, curar, contadores, dano...)
usadas pelas implementações de Treinadores, Habilidades e ataques.

Escolhas internas de um efeito ("procure no deck um Pokémon", "descarte 2
cartas", "coloque contadores em qualquer Pokémon") são resolvidas por
heurísticas simples e determinísticas, iguais para os dois lados. Escolhas
que mudam muito a jogada (qual Pokémon puxar com Boss's Orders, onde anexar
uma Ferramenta) são expostas como alvos da ação, para a IA e o humano
escolherem de fato.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.models import Card, Supertype
from pokemon_companion.engine.effects import passives
from pokemon_companion.engine.effects.cardinfo import (
    energy_type_of,
    has_rule_box,
    is_basic_energy,
    is_special_energy,
    is_tera,
    pokemon_type,
    stage_of,
    trainer_kind,
)
from pokemon_companion.engine.game_state import (
    MAX_BENCH_SIZE,
    GameState,
    PlayerId,
    PlayerState,
    PokemonInPlay,
    StatusCondition,
)

CardFilter = Callable[[Card], bool]


def coin() -> bool:
    return random.random() < 0.5


@dataclass
class Ctx:
    state: GameState
    player_id: PlayerId
    source: PokemonInPlay | None = None
    target: tuple[object, ...] | None = None
    messages: list[str] = field(default_factory=list)
    #: dano base do ataque (antes de modificadores do próprio texto)
    base_damage: int = 0
    #: marcado por efeitos de ataque que cancelam o ataque ("não faz nada")
    cancelled: bool = False
    is_ability: bool = False
    #: efeito que encerra o turno (ex: Lumiose City)
    ends_turn: bool = False
    #: ataque em resolução (bônus do tipo "o ataque X deste Pokémon faz +N")
    attack_name: str = ""
    #: tipo do Treinador sendo jogado ("Item", "Supporter"), para proteções
    playing: str = ""
    #: o Treinador jogado volta para o deck em vez do descarte (Caretaker)
    card_to_deck: bool = False

    @property
    def me(self) -> PlayerState:
        return self.state.state_of(self.player_id)

    @property
    def opp_id(self) -> PlayerId:
        return self.player_id.other

    @property
    def opp(self) -> PlayerState:
        return self.state.state_of(self.player_id.other)

    @property
    def turn(self) -> int:
        return self.state.turn_number

    def log(self, text: str) -> None:
        self.messages.append(text)

    def who(self, pid: PlayerId | None = None) -> str:
        return (pid or self.player_id).value


# ---------------------------------------------------------------------------
# posições


def mon_at(player: PlayerState, position: int) -> PokemonInPlay | None:
    if position == -1:
        return player.active
    return player.bench[position] if 0 <= position < len(player.bench) else None


def index_of(mons: list[PokemonInPlay], mon: PokemonInPlay) -> int:
    """Índice por identidade (dataclasses iguais não são o mesmo Pokémon)."""
    return next(i for i, other in enumerate(mons) if other is mon)


def position_of(player: PlayerState, mon: PokemonInPlay) -> int:
    return -1 if player.active is mon else index_of(player.bench, mon)


def positions(player: PlayerState) -> list[int]:
    return ([-1] if player.active is not None else []) + list(range(len(player.bench)))


def bench_limit(state: GameState, player: PlayerState) -> int:
    """5, ou 8 com Area Zero Underdepths e algum Pokémon Tera em jogo."""
    if passives.stadium_is(state, "Area Zero Underdepths") and any(
        is_tera(mon.card) for mon in player.all_pokemon_in_play()
    ):
        return 8
    return MAX_BENCH_SIZE


def bench_space(state: GameState, player: PlayerState) -> int:
    return max(bench_limit(state, player) - len(player.bench), 0)


# ---------------------------------------------------------------------------
# entrada em jogo e evolução


def put_on_bench(state: GameState, player: PlayerState, card: Card) -> PokemonInPlay:
    mon = PokemonInPlay(card=card, turn_played=state.turn_number)
    player.bench.append(mon)
    if passives.stadium_is(state, "Risky Ruins") and pokemon_type(card) != "Darkness":
        mon.damage_counters += 20
    return mon


def evolve_into(
    state: GameState, player: PlayerState, target: PokemonInPlay, card: Card
) -> PokemonInPlay:
    """Coloca `card` sobre `target`: mantém energias, dano, Ferramenta e
    marcas; remove condições especiais (livro de regras)."""
    evolved = target.clone()
    evolved.card = card
    evolved.prior_cards = [target.card, *target.prior_cards]
    keep_confusion = (
        passives.stadium_is(state, "Dizzying Valley") and target.status == StatusCondition.CONFUSED
    )
    evolved.status = StatusCondition.CONFUSED if keep_confusion else StatusCondition.NONE
    evolved.evolved_this_turn = True
    evolved.abilities_used = set()
    if player.active is target:
        player.active = evolved
    else:
        player.bench[index_of(player.bench, target)] = evolved
    evolved.hp_bonus = passives.hp_bonus(state, evolved)
    return evolved


# ---------------------------------------------------------------------------
# cartas entre zonas


def draw(player: PlayerState, count: int) -> int:
    drawn = 0
    for _ in range(count):
        if not player.deck:
            break
        player.hand.append(player.deck.pop(0))
        drawn += 1
    return drawn


def shuffle_deck(player: PlayerState) -> None:
    random.shuffle(player.deck)


def shuffle_hand_into_deck(player: PlayerState) -> None:
    player.deck.extend(player.hand)
    player.hand.clear()
    shuffle_deck(player)


def draw_until(player: PlayerState, size: int) -> int:
    return draw(player, max(size - len(player.hand), 0))


def discard_pokemon(player: PlayerState, mon: PokemonInPlay) -> None:
    from pokemon_companion.engine.effects.cardinfo import FOSSIL_ORIGINALS

    player.discard.extend(FOSSIL_ORIGINALS.get(c.id, c) for c in mon.all_cards())
    player.discard.extend(BASIC_ENERGIES[e] for e in mon.attached_energies if e in BASIC_ENERGIES)


def energy_card_from(mon: PokemonInPlay, energy: str) -> Card:
    if energy in BASIC_ENERGIES:
        return BASIC_ENERGIES[energy]
    for card in mon.special_energy_cards:
        if card.name == energy:
            return card
    return Card(
        id=f"special-{energy}", name=energy, supertype=Supertype.ENERGY, subtypes=["Special"]
    )


def attach_energy_card(mon: PokemonInPlay, card: Card) -> None:
    mon.attached_energies.append(energy_type_of(card))
    if is_special_energy(card):
        mon.special_energy_cards.append(card)
    if card.name == "Bubbly Water Energy" and pokemon_type(mon.card) == "Water":
        mon.status = StatusCondition.NONE


def detach_energy(mon: PokemonInPlay, energy: str) -> Card:
    """Tira a energia do Pokémon e devolve a carta (quem chama decide o destino)."""
    mon.attached_energies.remove(energy)
    card = energy_card_from(mon, energy)
    if card in mon.special_energy_cards:
        mon.special_energy_cards.remove(card)
    return card


def discard_energy(
    player: PlayerState, mon: PokemonInPlay, energy: str | None = None
) -> Card | None:
    if not mon.attached_energies:
        return None
    choice = energy if energy in mon.attached_energies else least_useful_energy(mon)
    card = detach_energy(mon, choice)
    player.discard.append(card)
    return card


def least_useful_energy(mon: PokemonInPlay) -> str:
    """Energia que menos atrapalha perder: a que não é exigida por ataques."""
    needed = {c for attack in mon.card.attacks for c in attack.cost if c != "Colorless"}
    spare = [e for e in mon.attached_energies if e not in needed]
    return (spare or mon.attached_energies)[-1]


# ---------------------------------------------------------------------------
# prioridade de cartas (heurística de busca/descarte)


def card_priority(state: GameState, pid: PlayerId, card: Card) -> float:
    player = state.state_of(pid)
    in_play = player.all_pokemon_in_play()
    names_in_play = {mon.card.name for mon in in_play}
    names_in_hand = [c.name for c in player.hand]
    if card.is_pokemon:
        if card.evolves_from and card.evolves_from in names_in_play:
            return 100 - 10 * names_in_hand.count(card.name)
        if card.evolves_from:
            # estágio 2 com o básico em jogo (Rare Candy) ou o estágio anterior na mão
            base = card.evolves_from
            if base in names_in_hand:
                return 70
            return 45
        basics = sum(1 for mon in in_play if stage_of(mon.card) == "Basic")
        return (
            80 - basics * 8 + (card.hp or 0) / 20 - (15 if has_rule_box(card) and basics < 2 else 0)
        )
    if card.supertype == Supertype.ENERGY:
        active = player.active
        if active is not None:
            needed = [c for attack in active.card.attacks for c in attack.cost if c != "Colorless"]
            if (
                energy_type_of(card) in needed
                and energy_type_of(card) not in active.attached_energies
            ):
                return 85
        return 55 - 5 * sum(1 for c in player.hand if c.supertype == Supertype.ENERGY)
    kind = trainer_kind(card)
    if kind == "Supporter":
        return 60 - 12 * sum(1 for c in player.hand if trainer_kind(c) == "Supporter")
    if kind == "Stadium":
        return 35
    return 50 - 4 * names_in_hand.count(card.name)


def choose_cards(state: GameState, pid: PlayerId, cards: list[Card], count: int) -> list[Card]:
    ranked = sorted(cards, key=lambda c: card_priority(state, pid, c), reverse=True)
    return ranked[:count]


def search_deck(
    ctx: Ctx,
    predicate: CardFilter,
    count: int = 1,
    destination: str = "hand",
    player_id: PlayerId | None = None,
) -> list[Card]:
    """Procura até `count` cartas que satisfazem `predicate` e as leva para a
    mão ou para o banco (Pokémon Básicos). Embaralha o deck no fim."""
    pid = player_id or ctx.player_id
    player = ctx.state.state_of(pid)
    candidates = [card for card in player.deck if predicate(card)]
    if destination == "bench":
        count = min(count, bench_space(ctx.state, player))
        candidates = [card for card in candidates if card.is_basic]
    chosen = choose_cards(ctx.state, pid, candidates, count)
    for card in chosen:
        player.deck.remove(card)
        if destination == "bench":
            put_on_bench(ctx.state, player, card)
        else:
            player.hand.append(card)
    shuffle_deck(player)
    if chosen:
        where = "no banco" if destination == "bench" else "na mão"
        ctx.log(f"{ctx.who(pid)} buscou {', '.join(c.name for c in chosen)} ({where}).")
    return chosen


def look_top_and_take(
    ctx: Ctx, look: int, predicate: CardFilter, take: int, rest: str = "shuffle"
) -> list[Card]:
    top = ctx.me.deck[:look]
    chosen = choose_cards(ctx.state, ctx.player_id, [c for c in top if predicate(c)], take)
    for card in chosen:
        ctx.me.deck.remove(card)
        ctx.me.hand.append(card)
    if rest == "discard":
        for card in [c for c in top if c not in chosen]:
            ctx.me.deck.remove(card)
            ctx.me.discard.append(card)
    elif rest == "bottom":
        others = [c for c in top if c not in chosen]
        for card in others:
            ctx.me.deck.remove(card)
        ctx.me.deck.extend(others)
    else:
        shuffle_deck(ctx.me)
    if chosen:
        ctx.log(f"{ctx.who()} pegou {', '.join(c.name for c in chosen)} do topo do deck.")
    return chosen


def recover_from_discard(
    ctx: Ctx, predicate: CardFilter, count: int, destination: str = "hand"
) -> list[Card]:
    candidates = [card for card in ctx.me.discard if predicate(card)]
    chosen = choose_cards(ctx.state, ctx.player_id, candidates, count)
    for card in chosen:
        ctx.me.discard.remove(card)
        if destination == "deck":
            ctx.me.deck.append(card)
        else:
            ctx.me.hand.append(card)
    if destination == "deck":
        shuffle_deck(ctx.me)
    if chosen:
        ctx.log(f"{ctx.who()} recuperou {', '.join(c.name for c in chosen)} do descarte.")
    return chosen


def discard_from_hand(
    ctx: Ctx, count: int, exclude: Card | None = None, player_id: PlayerId | None = None
) -> list[Card]:
    pid = player_id or ctx.player_id
    player = ctx.state.state_of(pid)
    pool = list(player.hand)
    if exclude is not None and exclude in pool:
        pool.remove(exclude)
    ranked = sorted(pool, key=lambda c: card_priority(ctx.state, pid, c))
    chosen = ranked[:count]
    for card in chosen:
        player.hand.remove(card)
        player.discard.append(card)
    return chosen


# ---------------------------------------------------------------------------
# energia vinda de fora da mão


def best_energy_target(
    state: GameState,
    pid: PlayerId,
    energy: str,
    allowed: Callable[[PokemonInPlay], bool] | None = None,
) -> PokemonInPlay | None:
    """Pokémon que mais se beneficia de receber a energia: quem fica mais
    perto de pagar um ataque, com preferência pelo Ativo."""
    player = state.state_of(pid)
    candidates = [m for m in player.all_pokemon_in_play() if allowed is None or allowed(m)]
    if not candidates:
        return None

    def score(mon: PokemonInPlay) -> tuple[int, int, int]:
        before = sum(passives.can_pay(state, pid, mon, a) for a in mon.card.attacks)
        mon.attached_energies.append(energy)
        after = sum(passives.can_pay(state, pid, mon, a) for a in mon.card.attacks)
        mon.attached_energies.pop()
        needed = any(energy in a.cost for a in mon.card.attacks)
        return (after - before, int(needed), int(mon is player.active))

    return max(candidates, key=score)


def attach_from(
    ctx: Ctx,
    source: list[Card],
    predicate: CardFilter,
    count: int,
    allowed: Callable[[PokemonInPlay], bool] | None = None,
) -> int:
    attached = 0
    for _ in range(count):
        options = [card for card in source if predicate(card)]
        if not options:
            break
        card = options[0]
        target = best_energy_target(ctx.state, ctx.player_id, energy_type_of(card), allowed)
        if target is None:
            break
        source.remove(card)
        attach_energy_card(target, card)
        attached += 1
        ctx.log(f"{ctx.who()} anexou {card.name} em {target.card.name}.")
    return attached


# ---------------------------------------------------------------------------
# trocas de Ativo


def switch_active(state: GameState, player: PlayerState, bench_index: int) -> None:
    """Troca o Ativo pelo Pokémon do banco (quem vai ao banco perde as
    condições especiais)."""
    if player.active is None or not (0 <= bench_index < len(player.bench)):
        return
    incoming = player.bench[bench_index]
    player.active.status = StatusCondition.NONE
    player.active.moved_to_bench_turn = state.turn_number
    benched = player.active
    player.bench[bench_index] = benched
    player.active = incoming
    incoming.moved_to_active_turn = state.turn_number
    owner = PlayerId.PLAYER if player is state.player else PlayerId.OPPONENT
    passives.after_switch_to_bench(state, owner, benched, incoming)


def best_bench_index(state: GameState, pid: PlayerId) -> int | None:
    from pokemon_companion.engine.rules import choose_promotion  # evita import circular

    bench = state.state_of(pid).bench
    return choose_promotion(bench, state, pid) if bench else None


def gust_target(state: GameState, opp_id: PlayerId) -> int | None:
    """Pokémon do banco adversário mais vantajoso de puxar: o que dá para
    nocautear agora (mais prêmios primeiro), senão o mais frágil."""
    bench = state.state_of(opp_id).bench
    if not bench:
        return None
    return min(
        range(len(bench)),
        key=lambda i: (-_prize_value(bench[i].card), bench[i].current_hp),
    )


def _prize_value(card: Card) -> int:
    from pokemon_companion.engine.rules import prize_count_for

    return prize_count_for(card)


def opponent_switches_out(state: GameState, opp_id: PlayerId) -> None:
    """ "Troque o Ativo do oponente para o banco (o oponente escolhe o novo)"."""
    index = best_bench_index(state, opp_id)
    if index is not None:
        switch_active(state, state.state_of(opp_id), index)


# ---------------------------------------------------------------------------
# condições, contadores, cura e dano


def set_status(ctx: Ctx, owner: PlayerId, mon: PokemonInPlay, status: StatusCondition) -> bool:
    is_active = mon is ctx.state.state_of(owner).active
    if owner != ctx.player_id and passives.prevents_attack_effects(
        ctx.state, owner, mon, is_active
    ):
        return False
    if passives.immune_to_special_conditions(ctx.state, mon):
        return False
    if passives.immune_to(ctx.state, mon, status):
        return False
    mon.status = status
    if status == StatusCondition.POISONED:
        mon.poison_damage = 10
    if status == StatusCondition.CONFUSED:
        mon.confusion_damage = 30
    ctx.log(f"{mon.card.name} agora está {status.name}.")
    return True


def place_counters(
    ctx: Ctx, owner: PlayerId, mon: PokemonInPlay, counters: int, from_ability: bool = False
) -> int:
    if counters <= 0:
        return 0
    owner_state = ctx.state.state_of(owner)
    is_active = mon is owner_state.active
    if owner != ctx.player_id:
        if from_ability and passives.prevents_ability_effects(ctx.state, mon):
            return 0
        if not from_ability and passives.prevents_attack_effects(ctx.state, owner, mon, is_active):
            return 0
        if not is_active and passives.stadium_is(ctx.state, "Battle Cage"):
            return 0
    mon.damage_counters += 10 * counters
    ctx.log(f"{counters} contador(es) de dano em {mon.card.name}.")
    return counters


def heal(mon: PokemonInPlay, amount: int) -> int:
    healed = min(amount, mon.damage_counters)
    mon.damage_counters -= healed
    if healed:
        mon.healed_this_turn = True
    return healed


def deal_damage(
    ctx: Ctx,
    owner: PlayerId,
    defender: PokemonInPlay,
    amount: int,
    *,
    is_active: bool,
    apply_weakness: bool = True,
    apply_resistance: bool = True,
    ignore_defender_effects: bool = False,
) -> int:
    """Dano de ataque: bônus do atacante (só contra o Ativo), Fraqueza e
    Resistência (só no Ativo), reduções e prevenções do defensor."""
    attacker = ctx.source
    state = ctx.state
    if attacker is not None and passives.pierces(state, ctx.player_id, attacker):
        ignore_defender_effects = True
    if attacker is not None and is_active and amount > 0:
        amount += passives.attacker_bonus(state, ctx.player_id, attacker, defender)
        bonus = attacker.attack_bonus
        if bonus and bonus[2] == state.turn_number and bonus[0] in ("*", ctx.attack_name):
            amount += bonus[1]
    if attacker is not None and is_active and amount > 0:
        attacker_types = set(attacker.card.types)
        if apply_weakness and attacker_types & passives.weakness_types(state, owner, defender):
            amount *= 2
        resistances = {r.energy_type for r in defender.card.resistances}
        if apply_resistance and attacker_types & resistances:
            amount -= 30
    if not ignore_defender_effects and attacker is not None:
        if passives.damage_prevented(state, owner, defender, attacker, is_active):
            amount = 0
        if passives.shield_blocks(state, defender, attacker, amount):
            amount = 0
        if passives.prevents_big_hit(state, owner, defender, amount):
            amount = 0
        if defender.damage_reduction and defender.damage_reduction[1] == state.turn_number:
            amount -= defender.damage_reduction[0]
        amount -= passives.static_damage_reduction(state, owner, defender)
        amount -= passives.compiled_reduction(state, owner, defender, attacker)
        amount -= passives.shield_reduction(state, defender, attacker)
    amount = max(amount, 0)
    if amount and attacker is not None:
        amount = _survival(ctx, owner, defender, amount)
    if amount:
        defender.damage_counters += amount
        defender.last_attacked = (amount, state.turn_number)
        ctx.log(f"{defender.card.name} sofreu {amount} de dano.")
        retaliation = defender.retaliation
        if attacker is not None and retaliation and retaliation[1] == state.turn_number:
            # contadores < 0: "iguais ao dano causado a este Pokémon"
            attacker.damage_counters += amount if retaliation[0] < 0 else 10 * retaliation[0]
            ctx.log(
                f"{defender.card.name} revidou: {retaliation[0]} contador(es) em {attacker.card.name}."
            )
        if attacker is not None:
            _counterattack(ctx, owner, defender, attacker)
            _owner_triggers(ctx, owner, defender)
    return amount


def _owner_triggers(ctx: Ctx, owner: PlayerId, defender: PokemonInPlay) -> None:
    """Habilidades do Pokémon atingido que agem a favor do dono (Smog
    Signals ao sofrer dano no Ativo; Final Chain e Photon Cord no nocaute)."""
    state = ctx.state
    side = state.state_of(owner)
    own = Ctx(state, owner, defender, messages=ctx.messages)
    active = side.active is defender
    if active and passives.ability_active(state, defender, "Smog Signals"):
        search_deck(own, lambda c: c.is_basic and "Koffing" in c.name, 2, destination="bench")
    if not defender.is_knocked_out:
        return
    if passives.ability_active(state, defender, "Final Chain"):
        search_deck(own, lambda c: True, 1)
    if active and passives.ability_active(state, defender, "Photon Cord") and side.bench:
        receiver = max(side.bench, key=lambda m: len(m.attached_energies))
        for _ in range(2):
            if "Lightning" not in defender.attached_energies:
                break
            attach_energy_card(receiver, detach_energy(defender, "Lightning"))


def _survival(ctx: Ctx, owner: PlayerId, defender: PokemonInPlay, amount: int) -> int:
    """Prevenção por moeda e "não é nocauteado, fica com 10 de HP"."""
    kinds = passives.survival(ctx.state, owner, defender)
    if not kinds:
        return amount
    if "coin_prevent" in kinds and coin():
        ctx.log(f"Cara: {defender.card.name} preveniu o dano.")
        return 0
    if amount >= defender.current_hp:
        full = defender.damage_counters == 0 and "survive_full" in kinds
        if full or ("survive_coin" in kinds and coin()):
            ctx.log(f"{defender.card.name} resistiu com 10 de HP.")
            return max(defender.current_hp - 10, 0)
    return amount


def _counterattack(
    ctx: Ctx, owner: PlayerId, defender: PokemonInPlay, attacker: PokemonInPlay
) -> None:
    """Contra-ataques de Habilidades passivas do Pokémon atingido."""
    state = ctx.state
    for passive, holder in passives.damage_reactions(
        state, owner, defender, defender.is_knocked_out
    ):
        counters = passive.counters * (  # type: ignore[attr-defined]
            passive.per(state, owner, holder) if passive.per else 1  # type: ignore[attr-defined]
        )
        if counters:
            attacker.damage_counters += 10 * counters
            ctx.log(f"{holder.card.name} contra-atacou: {counters} contador(es).")
        status = passive.status  # type: ignore[attr-defined]
        if status is not None and not passives.immune_to_special_conditions(state, attacker):
            attacker.status = status
        if passive.discard_energy and attacker.attached_energies:  # type: ignore[attr-defined]
            discard_energy(ctx.state.state_of(ctx.player_id), attacker)


def damage_counters_on(mon: PokemonInPlay) -> int:
    return mon.damage_counters // 10


def all_pokemon(state: GameState, pid: PlayerId) -> list[PokemonInPlay]:
    return state.state_of(pid).all_pokemon_in_play()


def best_counter_target(state: GameState, opp_id: PlayerId, counters: int) -> PokemonInPlay | None:
    """Onde colocar contadores: um nocaute (mais prêmios) se possível, senão o
    Ativo."""
    mons = all_pokemon(state, opp_id)
    if not mons:
        return None
    kills = [m for m in mons if m.current_hp <= counters * 10]
    if kills:
        return max(kills, key=lambda m: (_prize_value(m.card), -m.current_hp))
    return state.state_of(opp_id).active or mons[0]


def spread_counters(ctx: Ctx, opp_id: PlayerId, counters: int, bench_only: bool) -> None:
    """Distribui contadores tentando nocautear o máximo de Pokémon."""
    player = ctx.state.state_of(opp_id)
    targets = list(player.bench) if bench_only else player.all_pokemon_in_play()
    remaining = counters
    survivors = []
    for mon in sorted(targets, key=lambda m: (m.current_hp, -_prize_value(m.card))):
        needed = -(-mon.current_hp // 10)
        if needed <= remaining:
            remaining -= place_counters(ctx, opp_id, mon, needed)
        else:
            survivors.append(mon)
    if remaining and survivors:
        place_counters(ctx, opp_id, min(survivors, key=lambda m: m.current_hp), remaining)


def type_filter(energy_type: str) -> CardFilter:
    return lambda card: is_basic_energy(card) and energy_type_of(card) == energy_type


def is_pokemon_of_type(energy_type: str) -> CardFilter:
    return lambda card: card.is_pokemon and pokemon_type(card) == energy_type
