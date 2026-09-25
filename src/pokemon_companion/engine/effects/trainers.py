"""Cartas de Treinador: Itens, Apoiadores, Estádios (efeito de "uma vez por
turno") e Ferramentas, registrados pelo nome da carta.

As restrições gerais (1 Apoiador por turno, nada de Apoiador no 1º turno de
quem começa, Itens bloqueados, 1 Estádio por turno) ficam em `rules.py`;
aqui cada carta diz se pode ser jogada agora (`can_play`), quais alvos
oferece (`options`) e o que faz. Ferramentas não têm efeito ao jogar — só
enquanto anexadas (ver `passives.py`) — e são tratadas de forma genérica.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine.effects import core, passives
from pokemon_companion.engine.effects.cardinfo import (
    energy_type_of,
    has_rule_box,
    in_group,
    is_basic_energy,
    is_evolution,
    is_mega,
    is_tera,
    pokemon_type,
    stage_of,
    trainer_kind,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import (
    GameState,
    PlayerId,
    PokemonInPlay,
    StatusCondition,
)

Target = tuple[object, ...]
TrainerFn = Callable[[Ctx], None]
CheckFn = Callable[[Ctx], bool]
OptionsFn = Callable[[Ctx], list[Target | None]]


@dataclass(frozen=True)
class TrainerSpec:
    fn: TrainerFn
    can_play: CheckFn
    options: OptionsFn | None = None


TRAINERS: dict[str, TrainerSpec] = {}
STADIUMS: dict[str, TrainerSpec] = {}


def trainer(
    name: str, can_play: CheckFn = lambda ctx: True, options: OptionsFn | None = None
) -> Callable[[TrainerFn], TrainerFn]:
    def decorator(fn: TrainerFn) -> TrainerFn:
        TRAINERS[name] = TrainerSpec(fn, can_play, options)
        return fn

    return decorator


def stadium(name: str, can_use: CheckFn = lambda ctx: True) -> Callable[[TrainerFn], TrainerFn]:
    def decorator(fn: TrainerFn) -> TrainerFn:
        STADIUMS[name] = TrainerSpec(fn, can_use)
        return fn

    return decorator


def spec_for(card: Card) -> TrainerSpec | None:
    """Registro à mão pelo nome; senão, o texto compilado (`text_effects`)."""
    spec = TRAINERS.get(card.name)
    if spec is None and card.rules and trainer_kind(card) in ("Item", "Supporter"):
        from pokemon_companion.engine.effects.text_effects import compiled_trainer

        spec = compiled_trainer(" ".join(card.rules))
    return spec


def stadium_spec_for(card: Card) -> TrainerSpec | None:
    """Efeito "uma vez por turno" do Estádio: à mão ou compilado."""
    spec = STADIUMS.get(card.name)
    if spec is None and card.rules:
        from pokemon_companion.engine.effects.text_effects import compiled_stadium

        spec = compiled_stadium(" ".join(card.rules))
    return spec


def is_implemented(card: Card) -> bool:
    from pokemon_companion.engine.effects.cardinfo import is_fossil_item

    return (
        spec_for(card) is not None
        or trainer_kind(card) in ("Tool", "Stadium")
        or is_fossil_item(card)
    )


def discard_stadium(state: GameState) -> None:
    if state.stadium is None:
        return
    owner = state.stadium_owner or state.active_player
    state.state_of(owner).discard.append(state.stadium)
    state.stadium = None
    state.stadium_owner = None


# ---------------------------------------------------------------------------
# helpers


def others_in_hand(ctx: Ctx, count: int) -> bool:
    """ "Você só pode usar esta carta se descartar N outras cartas da mão"."""
    return len(ctx.me.hand) - 1 >= count


def opp_bench_options(ctx: Ctx) -> list[Target | None]:
    return [
        ("opp", i)
        for i, mon in enumerate(ctx.opp.bench)
        if not passives.trainer_shielded(ctx.state, ctx.opp_id, mon, ctx.playing)
    ]


def own_bench_options(ctx: Ctx) -> list[Target | None]:
    return [("own", i) for i in range(len(ctx.me.bench))]


def target_index(ctx: Ctx, default: int = 0) -> int:
    if ctx.target and len(ctx.target) >= 2 and isinstance(ctx.target[1], int):
        return ctx.target[1]
    return default


def gust(ctx: Ctx) -> None:
    index = target_index(ctx, default=-2)
    allowed = [i for (_, i) in opp_bench_options(ctx)]  # type: ignore[misc]
    if index < 0 or index not in allowed:
        index = core.gust_target(ctx.state, ctx.opp_id) or 0
    if index not in allowed:
        return
    if ctx.opp.bench:
        core.switch_active(ctx.state, ctx.opp, index)
        ctx.log(f"{ctx.opp.active.card.name} foi puxado para o Ativo.")  # type: ignore[union-attr]


def own_switch(ctx: Ctx, index: int | None = None) -> None:
    if index is None:
        index = core.best_bench_index(ctx.state, ctx.player_id)
    if index is not None and ctx.me.bench:
        core.switch_active(ctx.state, ctx.me, index)
        ctx.log(f"{ctx.who()} trocou o Ativo por {ctx.me.active.card.name}.")  # type: ignore[union-attr]


def _is_stage(stage: str) -> core.CardFilter:
    return lambda card: card.is_pokemon and stage_of(card) == stage


def _is_kind(kind: str) -> core.CardFilter:
    return lambda card: trainer_kind(card) == kind


def _is_mon(target: PokemonInPlay) -> Callable[[PokemonInPlay], bool]:
    return lambda mon: mon is target


def my_ko_last_turn(ctx: Ctx) -> bool:
    return ctx.me.knocked_out_turn == ctx.turn - 1


def team_rocket_supporter(card: Card) -> bool:
    return trainer_kind(card) == "Supporter" and "Team Rocket" in card.name


# ---------------------------------------------------------------------------
# Apoiadores


@trainer("Lillie's Determination")
def _lillies_determination(ctx: Ctx) -> None:
    core.shuffle_hand_into_deck(ctx.me)
    core.draw(ctx.me, 8 if len(ctx.me.prizes) == 6 else 6)


@trainer("Boss's Orders", lambda ctx: bool(ctx.opp.bench), opp_bench_options)
def _boss(ctx: Ctx) -> None:
    gust(ctx)


@trainer("Team Rocket's Petrel")
def _petrel(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.supertype.value == "Trainer", 1)


@trainer("Crispin", lambda ctx: any(is_basic_energy(c) for c in ctx.me.deck))
def _crispin(ctx: Ctx) -> None:
    found: list[Card] = []
    for card in ctx.me.deck:
        if is_basic_energy(card) and all(energy_type_of(card) != energy_type_of(f) for f in found):
            found.append(card)
        if len(found) == 2:
            break
    for card in found:
        ctx.me.deck.remove(card)
    if found:
        target = core.best_energy_target(ctx.state, ctx.player_id, energy_type_of(found[0]))
        if target is not None:
            core.attach_energy_card(target, found[0])
            ctx.log(f"{ctx.who()} anexou {found[0].name} em {target.card.name}.")
        else:
            ctx.me.hand.append(found[0])
        ctx.me.hand.extend(found[1:])
    core.shuffle_deck(ctx.me)


@trainer("Judge")
def _judge(ctx: Ctx) -> None:
    for player in (ctx.me, ctx.opp):
        core.shuffle_hand_into_deck(player)
        core.draw(player, 4)


@trainer("Hilda")
def _hilda(ctx: Ctx) -> None:
    core.search_deck(ctx, is_evolution, 1)
    core.search_deck(ctx, lambda c: c.supertype.value == "Energy", 1)


@trainer("Team Rocket's Ariana")
def _ariana(ctx: Ctx) -> None:
    all_team = all(in_group(m.card, "Team Rocket's") for m in ctx.me.all_pokemon_in_play())
    core.draw_until(ctx.me, 8 if all_team else 5)


@trainer("Cyrano")
def _cyrano(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon and "ex" in c.subtypes, 3)


@trainer("Ciphermaniac's Codebreaking")
def _ciphermaniac(ctx: Ctx) -> None:
    chosen = core.choose_cards(ctx.state, ctx.player_id, ctx.me.deck, 2)
    for card in chosen:
        ctx.me.deck.remove(card)
    core.shuffle_deck(ctx.me)
    ctx.me.deck[:0] = chosen


@trainer(
    "Team Rocket's Giovanni",
    lambda ctx: bool(ctx.opp.bench)
    and ctx.me.active is not None
    and in_group(ctx.me.active.card, "Team Rocket's")
    and any(in_group(m.card, "Team Rocket's") for m in ctx.me.bench),
    opp_bench_options,
)
def _giovanni(ctx: Ctx) -> None:
    candidates = [i for i, m in enumerate(ctx.me.bench) if in_group(m.card, "Team Rocket's")]
    own_switch(ctx, max(candidates, key=lambda i: len(ctx.me.bench[i].attached_energies)))
    gust(ctx)


@trainer(
    "Gwynn",
    lambda ctx: any(c.is_pokemon and not has_rule_box(c) for c in ctx.me.hand),
)
def _gwynn(ctx: Ctx) -> None:
    pool = [c for c in ctx.me.hand if c.is_pokemon and not has_rule_box(c)]
    worst = sorted(pool, key=lambda c: core.card_priority(ctx.state, ctx.player_id, c))[:2]
    for card in worst:
        ctx.me.hand.remove(card)
        ctx.me.discard.append(card)
    core.draw(ctx.me, 3 * len(worst))


@trainer("Team Rocket's Proton")
def _proton(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_basic and in_group(c, "Team Rocket's"), 3)


@trainer("Dawn")
def _dawn(ctx: Ctx) -> None:
    for stage in ("Basic", "Stage 1", "Stage 2"):
        core.search_deck(ctx, _is_stage(stage), 1)


@trainer("Brock's Scouting")
def _brocks_scouting(ctx: Ctx) -> None:
    evolutions = [c for c in ctx.me.deck if is_evolution(c)]
    best_evo = core.choose_cards(ctx.state, ctx.player_id, evolutions, 1)
    basics = [c for c in ctx.me.deck if c.is_basic]
    if best_evo and (
        not basics
        or core.card_priority(ctx.state, ctx.player_id, best_evo[0])
        > max(core.card_priority(ctx.state, ctx.player_id, c) for c in basics) + 15
    ):
        core.search_deck(ctx, is_evolution, 1)
    else:
        core.search_deck(ctx, lambda c: c.is_basic, 2)


@trainer("Explorer's Guidance", lambda ctx: bool(ctx.me.deck))
def _explorers_guidance(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 6, lambda c: True, 2, rest="discard")


@trainer("Team Rocket's Archer", lambda ctx: my_ko_last_turn(ctx))
def _archer(ctx: Ctx) -> None:
    for player in (ctx.me, ctx.opp):
        core.shuffle_hand_into_deck(player)
    core.draw(ctx.me, 5)
    core.draw(ctx.opp, 3)


@trainer("Xerosic's Machinations", lambda ctx: len(ctx.opp.hand) > 3)
def _xerosic(ctx: Ctx) -> None:
    core.discard_from_hand(ctx, len(ctx.opp.hand) - 3, player_id=ctx.opp_id)


@trainer("Eri", lambda ctx: bool(ctx.opp.hand))
def _eri(ctx: Ctx) -> None:
    items = [c for c in ctx.opp.hand if trainer_kind(c) in ("Item", "Tool")][:2]
    for card in items:
        ctx.opp.hand.remove(card)
        ctx.opp.discard.append(card)
    if items:
        ctx.log(f"{ctx.who(ctx.opp_id)} descartou {', '.join(c.name for c in items)}.")


def _lanas_filter(card: Card) -> bool:
    return (card.is_pokemon and not has_rule_box(card)) or is_basic_energy(card)


@trainer("Lana's Aid", lambda ctx: any(_lanas_filter(c) for c in ctx.me.discard))
def _lanas_aid(ctx: Ctx) -> None:
    core.recover_from_discard(ctx, _lanas_filter, 3)


def _kieran_options(ctx: Ctx) -> list[Target | None]:
    return [*own_bench_options(ctx), ("mode", 1)]


@trainer("Kieran", options=_kieran_options)
def _kieran(ctx: Ctx) -> None:
    if ctx.target and ctx.target[0] == "own":
        own_switch(ctx, target_index(ctx))
    else:
        ctx.me.damage_bonus_this_turn.append((30, "ex_v"))


@trainer("Gladion's Final Battle", lambda ctx: len(ctx.me.hand) == 1)
def _gladion(ctx: Ctx) -> None:
    ctx.me.damage_bonus_this_turn.append((80, "no_rule_box"))


@trainer("Black Belt's Training")
def _black_belt(ctx: Ctx) -> None:
    ctx.me.damage_bonus_this_turn.append((40, "ex"))


def _stage2s(ctx: Ctx) -> list[int]:
    return [p for p in core.positions(ctx.me) if stage_of(core.mon_at(ctx.me, p).card) == "Stage 2"]  # type: ignore[union-attr]


@trainer(
    "Rosa's Encouragement",
    lambda ctx: len(ctx.me.prizes) > len(ctx.opp.prizes)
    and bool(_stage2s(ctx))
    and any(is_basic_energy(c) for c in ctx.me.discard),
)
def _rosa(ctx: Ctx) -> None:
    targets = [core.mon_at(ctx.me, p) for p in _stage2s(ctx)]
    target = targets[0]
    core.attach_from(ctx, ctx.me.discard, is_basic_energy, 2, allowed=lambda m: m is target)


@trainer("Surfer", lambda ctx: bool(ctx.me.bench), own_bench_options)
def _surfer(ctx: Ctx) -> None:
    own_switch(ctx, target_index(ctx))
    core.draw_until(ctx.me, 5)


def _damaged(ctx: Ctx) -> bool:
    return any(
        m.damage_counters or m.status != StatusCondition.NONE for m in ctx.me.all_pokemon_in_play()
    )


@trainer("Pokémon Center Lady", _damaged)
def _center_lady(ctx: Ctx) -> None:
    mon = max(ctx.me.all_pokemon_in_play(), key=lambda m: m.damage_counters)
    core.heal(mon, 60)
    mon.status = StatusCondition.NONE


@trainer(
    "Bianca's Devotion",
    lambda ctx: any(0 < m.current_hp <= 30 for m in ctx.me.all_pokemon_in_play()),
)
def _bianca(ctx: Ctx) -> None:
    mon = max(
        (m for m in ctx.me.all_pokemon_in_play() if m.current_hp <= 30),
        key=lambda m: m.damage_counters,
    )
    mon.damage_counters = 0


@trainer(
    "Wally's Compassion",
    lambda ctx: any(is_mega(m.card) and m.damage_counters for m in ctx.me.all_pokemon_in_play()),
)
def _wally(ctx: Ctx) -> None:
    mon = max(
        (m for m in ctx.me.all_pokemon_in_play() if is_mega(m.card)),
        key=lambda m: m.damage_counters,
    )
    mon.damage_counters = 0
    for energy in list(mon.attached_energies):
        ctx.me.hand.append(core.detach_energy(mon, energy))


@trainer("Colress's Tenacity")
def _colress_tenacity(ctx: Ctx) -> None:
    core.search_deck(ctx, _is_kind("Stadium"), 1)
    core.search_deck(ctx, lambda c: c.supertype.value == "Energy", 1)


@trainer("AZ's Tranquility", lambda ctx: bool(ctx.me.bench), own_bench_options)
def _az_tranquility(ctx: Ctx) -> None:
    leaving = ctx.me.active
    own_switch(ctx, target_index(ctx))
    if leaving is not None and "ex" in leaving.card.subtypes and core.heal(leaving, 80):
        ctx.log(f"{leaving.card.name} curou 80 ao ir para o Banco.")


def _evolutions_in_deck(ctx: Ctx, mon: PokemonInPlay) -> list[Card]:
    return [
        c
        for c in ctx.me.deck
        if c.is_pokemon and not c.abilities and c.evolves_from == mon.card.name
    ]


@trainer(
    "Salvatore",
    lambda ctx: any(_evolutions_in_deck(ctx, m) for m in ctx.me.all_pokemon_in_play()),
)
def _salvatore(ctx: Ctx) -> None:
    """Evolui direto do deck, inclusive Pokémon que entraram neste turno."""
    options = [(m, c) for m in ctx.me.all_pokemon_in_play() for c in _evolutions_in_deck(ctx, m)]
    mon, card = max(options, key=lambda o: (o[0] is ctx.me.active, o[1].hp or 0))
    ctx.me.deck.remove(card)
    core.evolve_into(ctx.state, ctx.me, mon, card)
    core.shuffle_deck(ctx.me)
    ctx.log(f"{mon.card.name} evoluiu para {card.name} (Salvatore).")


@trainer("Briar", lambda ctx: len(ctx.opp.prizes) == 2)
def _briar(ctx: Ctx) -> None:
    ctx.me.prize_bonus = (1, ctx.turn, "Tera Pokémon")


@trainer("Hassel", lambda ctx: my_ko_last_turn(ctx))
def _hassel(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 8, lambda c: True, 3)


# ---------------------------------------------------------------------------
# Itens


@trainer("Poké Pad")
def _poke_pad(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon and not has_rule_box(c), 1)


@trainer("Ultra Ball", lambda ctx: others_in_hand(ctx, 2))
def _ultra_ball(ctx: Ctx) -> None:
    core.discard_from_hand(ctx, 2)
    core.search_deck(ctx, lambda c: c.is_pokemon, 1)


def _stretcher_filter(card: Card) -> bool:
    return card.is_pokemon or is_basic_energy(card)


@trainer("Night Stretcher", lambda ctx: any(_stretcher_filter(c) for c in ctx.me.discard))
def _night_stretcher(ctx: Ctx) -> None:
    core.recover_from_discard(ctx, _stretcher_filter, 1)


def _poffin_filter(card: Card) -> bool:
    return card.is_basic and (card.hp or 0) <= 70


@trainer("Buddy-Buddy Poffin", lambda ctx: core.bench_space(ctx.state, ctx.me) > 0)
def _poffin(ctx: Ctx) -> None:
    core.search_deck(ctx, _poffin_filter, 2, destination="bench")


@trainer("Precious Trolley", lambda ctx: core.bench_space(ctx.state, ctx.me) > 0)
def _trolley(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_basic, 8, destination="bench")


def _basic_energy_holders(ctx: Ctx) -> list[int]:
    return [
        p
        for p in core.positions(ctx.me)
        if any(e in core.BASIC_ENERGIES for e in core.mon_at(ctx.me, p).attached_energies)  # type: ignore[union-attr]
    ]


def _energy_switch_options(ctx: Ctx) -> list[Target | None]:
    """("move", origem, destino): mover 1 energia básica entre seus Pokémon."""
    return [
        ("move", src, dst)
        for src in _basic_energy_holders(ctx)
        for dst in core.positions(ctx.me)
        if dst != src
    ]


@trainer("Energy Switch", lambda ctx: bool(_energy_switch_options(ctx)), _energy_switch_options)
def _energy_switch(ctx: Ctx) -> None:
    assert ctx.target is not None
    src = core.mon_at(ctx.me, int(ctx.target[1]))  # type: ignore[call-overload]
    dst = core.mon_at(ctx.me, int(ctx.target[2]))  # type: ignore[call-overload]
    if src is None or dst is None:
        return
    basics = [e for e in src.attached_energies if e in core.BASIC_ENERGIES]
    needed = {c for a in dst.card.attacks for c in a.cost}
    energy = next((e for e in basics if e in needed), basics[0])
    core.attach_energy_card(dst, core.detach_energy(src, energy))
    ctx.log(f"{ctx.who()} moveu {energy} de {src.card.name} para {dst.card.name}.")


@trainer("Special Red Card", lambda ctx: len(ctx.opp.prizes) <= 3 and bool(ctx.opp.hand))
def _special_red_card(ctx: Ctx) -> None:
    ctx.opp.deck.extend(ctx.opp.hand)
    ctx.opp.hand.clear()
    core.draw(ctx.opp, 3)


@trainer(
    "Team Rocket's Transceiver",
    lambda ctx: any(team_rocket_supporter(c) for c in ctx.me.deck),
)
def _transceiver(ctx: Ctx) -> None:
    core.search_deck(ctx, team_rocket_supporter, 1)


@trainer("Switch", lambda ctx: bool(ctx.me.bench), own_bench_options)
def _switch(ctx: Ctx) -> None:
    own_switch(ctx, target_index(ctx))


@trainer("Pokégear 3.0", lambda ctx: bool(ctx.me.deck))
def _pokegear(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 7, lambda c: "Supporter" in c.subtypes, 1)


@trainer("Roto-Stick", lambda ctx: bool(ctx.me.deck))
def _roto_stick(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 4, lambda c: "Supporter" in c.subtypes, 4)


@trainer("Secret Box", lambda ctx: others_in_hand(ctx, 3))
def _secret_box(ctx: Ctx) -> None:
    core.discard_from_hand(ctx, 3)
    for kind in ("Item", "Tool", "Supporter", "Stadium"):
        core.search_deck(ctx, _is_kind(kind), 1)


def _candy_options(ctx: Ctx) -> list[Target | None]:
    """("candy", nome do Estágio 2 na mão, posição do Básico)."""
    if ctx.turn <= 2:
        return []
    options: list[Target | None] = []
    seen: set[str] = set()
    for card in ctx.me.hand:
        if not card.is_pokemon or stage_of(card) != "Stage 2" or card.name in seen:
            continue
        seen.add(card.name)
        for position in core.positions(ctx.me):
            mon = core.mon_at(ctx.me, position)
            assert mon is not None
            if (
                stage_of(mon.card) == "Basic"
                and mon.turn_played < ctx.turn
                and not mon.evolved_this_turn
                and _stage1_name_evolves_from(ctx, card) == mon.card.name
            ):
                options.append(("candy", card.name, position))
    return options


def _stage1_name_evolves_from(ctx: Ctx, stage2: Card) -> str | None:
    """Nome do Básico da linha evolutiva do Estágio 2 (procura o Estágio 1 nas
    cartas conhecidas do jogador; se não achar, usa a tabela de nomes)."""
    for zone in (ctx.me.deck, ctx.me.hand, ctx.me.discard, ctx.me.prizes):
        for card in zone:
            if card.name == stage2.evolves_from and card.evolves_from:
                return card.evolves_from
    for mon in ctx.me.all_pokemon_in_play():
        for card in [mon.card, *mon.prior_cards]:
            if card.name == stage2.evolves_from and card.evolves_from:
                return card.evolves_from
    return None


@trainer("Rare Candy", lambda ctx: bool(_candy_options(ctx)), _candy_options)
def _rare_candy(ctx: Ctx) -> None:
    assert ctx.target is not None
    card = next(c for c in ctx.me.hand if c.name == ctx.target[1])
    mon = core.mon_at(ctx.me, int(ctx.target[2]))  # type: ignore[call-overload]
    assert mon is not None
    ctx.me.hand.remove(card)
    core.evolve_into(ctx.state, ctx.me, mon, card)
    ctx.log(f"{mon.card.name} evoluiu direto para {card.name} (Rare Candy).")


@trainer("Bug Catching Set", lambda ctx: bool(ctx.me.deck))
def _bug_catching(ctx: Ctx) -> None:
    core.look_top_and_take(
        ctx,
        7,
        lambda c: (c.is_pokemon and pokemon_type(c) == "Grass")
        or (is_basic_energy(c) and energy_type_of(c) == "Grass"),
        2,
    )


@trainer("Fighting Gong")
def _fighting_gong(ctx: Ctx) -> None:
    core.search_deck(
        ctx,
        lambda c: (is_basic_energy(c) and energy_type_of(c) == "Fighting")
        or (c.is_basic and pokemon_type(c) == "Fighting"),
        1,
    )


@trainer(
    "Jumbo Ice Cream",
    lambda ctx: ctx.me.active is not None
    and len(ctx.me.active.attached_energies) >= 3
    and ctx.me.active.damage_counters > 0,
)
def _jumbo_ice_cream(ctx: Ctx) -> None:
    assert ctx.me.active is not None
    core.heal(ctx.me.active, 80)


def _psychic_bench(ctx: Ctx) -> list[int]:
    return [i for i, m in enumerate(ctx.me.bench) if pokemon_type(m.card) == "Psychic"]


@trainer(
    "Wondrous Patch",
    lambda ctx: bool(_psychic_bench(ctx))
    and any(core.type_filter("Psychic")(c) for c in ctx.me.discard),
)
def _wondrous_patch(ctx: Ctx) -> None:
    bench = [ctx.me.bench[i] for i in _psychic_bench(ctx)]
    core.attach_from(
        ctx,
        ctx.me.discard,
        core.type_filter("Psychic"),
        1,
        allowed=lambda m: any(m is b for b in bench),
    )


@trainer(
    "Crushing Hammer", lambda ctx: any(m.attached_energies for m in ctx.opp.all_pokemon_in_play())
)
def _crushing_hammer(ctx: Ctx) -> None:
    if core.coin():
        mon = max(ctx.opp.all_pokemon_in_play(), key=lambda m: len(m.attached_energies))
        card = core.discard_energy(ctx.opp, mon)
        if card is not None:
            ctx.log(f"Cara: {card.name} de {mon.card.name} foi descartada.")
    else:
        ctx.log("Coroa: nada acontece.")


@trainer("Unfair Stamp", lambda ctx: my_ko_last_turn(ctx))
def _unfair_stamp(ctx: Ctx) -> None:
    for player in (ctx.me, ctx.opp):
        core.shuffle_hand_into_deck(player)
    core.draw(ctx.me, 5)
    core.draw(ctx.opp, 2)


@trainer("Premium Power Pro")
def _premium_power_pro(ctx: Ctx) -> None:
    ctx.me.damage_bonus_this_turn.append((30, "fighting"))


def _glass_trumpet_targets(ctx: Ctx) -> list[int]:
    return [i for i, m in enumerate(ctx.me.bench) if pokemon_type(m.card) == "Colorless"]


@trainer(
    "Glass Trumpet",
    lambda ctx: any(is_tera(m.card) for m in ctx.me.all_pokemon_in_play())
    and bool(_glass_trumpet_targets(ctx))
    and any(is_basic_energy(c) for c in ctx.me.discard),
)
def _glass_trumpet(ctx: Ctx) -> None:
    for i in _glass_trumpet_targets(ctx)[:2]:
        core.attach_from(ctx, ctx.me.discard, is_basic_energy, 1, allowed=_is_mon(ctx.me.bench[i]))


def _ns_bench(ctx: Ctx) -> list[int]:
    return [i for i, m in enumerate(ctx.me.bench) if in_group(m.card, "N's")]


@trainer(
    "N's PP Up",
    lambda ctx: bool(_ns_bench(ctx)) and any(is_basic_energy(c) for c in ctx.me.discard),
)
def _ns_pp_up(ctx: Ctx) -> None:
    bench = [ctx.me.bench[i] for i in _ns_bench(ctx)]
    core.attach_from(
        ctx, ctx.me.discard, is_basic_energy, 1, allowed=lambda m: any(m is b for b in bench)
    )


@trainer("Sacred Ash", lambda ctx: any(c.is_pokemon for c in ctx.me.discard))
def _sacred_ash(ctx: Ctx) -> None:
    core.recover_from_discard(ctx, lambda c: c.is_pokemon, 5, destination="deck")


@trainer("Energy Recycler", lambda ctx: any(is_basic_energy(c) for c in ctx.me.discard))
def _energy_recycler(ctx: Ctx) -> None:
    core.recover_from_discard(ctx, is_basic_energy, 5, destination="deck")


@trainer(
    "Tool Scrapper",
    lambda ctx: any(m.tool is not None for m in ctx.opp.all_pokemon_in_play()),
)
def _tool_scrapper(ctx: Ctx) -> None:
    for mon in [m for m in ctx.opp.all_pokemon_in_play() if m.tool is not None][:2]:
        assert mon.tool is not None
        ctx.opp.discard.append(mon.tool)
        ctx.log(f"{mon.tool.name} de {mon.card.name} foi descartada.")
        mon.tool = None


@trainer("Prime Catcher", lambda ctx: bool(ctx.opp.bench), opp_bench_options)
def _prime_catcher(ctx: Ctx) -> None:
    gust(ctx)
    own_switch(ctx)


@trainer("Miracle Headset", lambda ctx: any("Supporter" in c.subtypes for c in ctx.me.discard))
def _miracle_headset(ctx: Ctx) -> None:
    core.recover_from_discard(ctx, lambda c: "Supporter" in c.subtypes, 2)


def _special_holders(ctx: Ctx) -> list[PokemonInPlay]:
    return [m for m in ctx.opp.all_pokemon_in_play() if m.special_energy_cards]


@trainer("Enhanced Hammer", lambda ctx: bool(_special_holders(ctx)))
def _enhanced_hammer(ctx: Ctx) -> None:
    holders = _special_holders(ctx)
    mon = ctx.opp.active if ctx.opp.active in holders else holders[0]
    assert mon is not None
    card = mon.special_energy_cards[0]
    ctx.opp.discard.append(core.detach_energy(mon, card.name))
    ctx.log(f"{card.name} de {mon.card.name} foi descartada.")


def _timepiece_options(ctx: Ctx) -> list[Target | None]:
    if passives.hand_return_blocked(ctx.state, ctx.player_id):
        return []
    return [
        ("own", p)
        for p in core.positions(ctx.me)
        if (mon := core.mon_at(ctx.me, p)) is not None
        and mon.prior_cards
        and pokemon_type(mon.card) == "Psychic"
    ]


@trainer("Strange Timepiece", lambda ctx: bool(_timepiece_options(ctx)), _timepiece_options)
def _strange_timepiece(ctx: Ctx) -> None:
    """Desevolui um estágio (o uso comum: jogar a evolução de novo e repetir
    Habilidades como Psychic Draw). O Pokémon não pode evoluir neste turno."""
    mon = core.mon_at(ctx.me, target_index(ctx, -1))
    assert mon is not None and mon.prior_cards
    ctx.me.hand.append(mon.card)
    ctx.log(f"{mon.card.name} voltou para a mão; {mon.prior_cards[0].name} fica em jogo.")
    mon.card = mon.prior_cards[0]
    mon.prior_cards = mon.prior_cards[1:]
    mon.turn_played = ctx.turn
    mon.abilities_used = set()


@trainer("Energy Search")
def _energy_search(ctx: Ctx) -> None:
    core.search_deck(ctx, is_basic_energy, 1)


@trainer("Mega Signal", lambda ctx: any(c.is_pokemon and is_mega(c) for c in ctx.me.deck))
def _mega_signal(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon and is_mega(c), 1)


def _actives_hit_by_dark_bell(ctx: Ctx) -> list[tuple[PlayerId, PokemonInPlay]]:
    return [
        (pid, mon)
        for pid in (ctx.player_id, ctx.opp_id)
        if (mon := ctx.state.state_of(pid).active) is not None
        and pokemon_type(mon.card) != "Darkness"
    ]


@trainer("Dark Bell", lambda ctx: bool(_actives_hit_by_dark_bell(ctx)))
def _dark_bell(ctx: Ctx) -> None:
    for pid, mon in _actives_hit_by_dark_bell(ctx):
        core.set_status(ctx, pid, mon, StatusCondition.CONFUSED)


def _damaged_options(ctx: Ctx) -> list[Target | None]:
    return [("own", p) for p in core.positions(ctx.me) if core.mon_at(ctx.me, p).damage_counters]  # type: ignore[union-attr]


@trainer("Super Potion", lambda ctx: bool(_damaged_options(ctx)), _damaged_options)
def _super_potion(ctx: Ctx) -> None:
    mon = core.mon_at(ctx.me, target_index(ctx, -1))
    if mon is not None and core.heal(mon, 60):
        core.discard_energy(ctx.me, mon)


def _blowtorch_options(ctx: Ctx) -> list[Target | None]:
    """("opp", posição) para Ferramenta/Energia Especial, ou ("stadium",)."""
    options: list[Target | None] = [
        ("opp", p)
        for p in core.positions(ctx.opp)
        if (mon := core.mon_at(ctx.opp, p)) is not None
        and (mon.tool is not None or mon.special_energy_cards)
    ]
    if ctx.state.stadium is not None:
        options.append(("stadium",))
    return options


@trainer(
    "Blowtorch",
    lambda ctx: any(core.type_filter("Fire")(c) for c in ctx.me.hand)
    and bool(_blowtorch_options(ctx)),
    _blowtorch_options,
)
def _blowtorch(ctx: Ctx) -> None:
    cost = next(c for c in ctx.me.hand if core.type_filter("Fire")(c))
    ctx.me.hand.remove(cost)
    ctx.me.discard.append(cost)
    if ctx.target and ctx.target[0] == "stadium":
        discard_stadium(ctx.state)
        return
    mon = core.mon_at(ctx.opp, target_index(ctx, -1))
    if mon is None:
        return
    if mon.tool is not None:
        ctx.opp.discard.append(mon.tool)
        ctx.log(f"{mon.tool.name} de {mon.card.name} foi descartada.")
        mon.tool = None
    elif mon.special_energy_cards:
        card = mon.special_energy_cards[0]
        ctx.opp.discard.append(core.detach_energy(mon, card.name))
        ctx.log(f"{card.name} de {mon.card.name} foi descartada.")


# ---------------------------------------------------------------------------
# Estádios: efeito "uma vez durante o turno de cada jogador"


@stadium("Prism Tower", lambda ctx: len(ctx.me.hand) >= 2 and bool(ctx.me.deck))
def _prism_tower(ctx: Ctx) -> None:
    core.discard_from_hand(ctx, 2)
    core.draw(ctx.me, 1)


@stadium(
    "Academy at Night",
    # Só para o humano: a heurística da IA "ganhava" deck devolvendo uma carta
    # ao topo todo turno e os dois lados travavam sem nunca esvaziar o deck.
    lambda ctx: bool(ctx.me.hand) and ctx.player_id in ctx.state.manual_choices,
)
def _academy_at_night(ctx: Ctx) -> None:
    worst = sorted(ctx.me.hand, key=lambda c: core.card_priority(ctx.state, ctx.player_id, c))[0]
    ctx.me.hand.remove(worst)
    ctx.me.deck.insert(0, worst)


@stadium(
    "Spikemuth Gym", lambda ctx: any(c.is_pokemon and in_group(c, "Marnie's") for c in ctx.me.deck)
)
def _spikemuth_gym(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon and in_group(c, "Marnie's"), 1)


@stadium(
    "Team Rocket's Factory",
    lambda ctx: ctx.me.played_team_rocket_supporter_turn == ctx.turn,
)
def _rocket_factory(ctx: Ctx) -> None:
    core.draw(ctx.me, 2)


@stadium(
    "Lumiose City",
    lambda ctx: core.bench_space(ctx.state, ctx.me) > 0 and any(c.is_basic for c in ctx.me.deck),
)
def _lumiose_city(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_basic, 1, destination="bench")
    ctx.ends_turn = True


def _next_stage(ctx: Ctx, mon: PokemonInPlay, stage: str) -> Card | None:
    options = [
        c
        for c in ctx.me.deck
        if c.is_pokemon and stage_of(c) == stage and c.evolves_from == mon.card.name
    ]
    return max(options, key=lambda c: c.hp or 0, default=None)


def _grand_tree_basics(ctx: Ctx) -> list[PokemonInPlay]:
    if ctx.turn <= 2:
        return []
    return [
        m
        for m in ctx.me.all_pokemon_in_play()
        if stage_of(m.card) == "Basic"
        and m.turn_played < ctx.turn
        and _next_stage(ctx, m, "Stage 1") is not None
    ]


@stadium("Grand Tree", lambda ctx: bool(_grand_tree_basics(ctx)))
def _grand_tree(ctx: Ctx) -> None:
    """Evolui um Básico para o Estágio 1 e, se der, para o Estágio 2."""
    mon = max(_grand_tree_basics(ctx), key=lambda m: m is ctx.me.active)
    for stage in ("Stage 1", "Stage 2"):
        card = _next_stage(ctx, mon, stage)
        if card is None:
            break
        ctx.me.deck.remove(card)
        ctx.log(f"{mon.card.name} evoluiu para {card.name} (Grand Tree).")
        mon = core.evolve_into(ctx.state, ctx.me, mon, card)
    core.shuffle_deck(ctx.me)


def ace_spec_blocked(state: GameState, player_id: PlayerId) -> bool:
    """ACE Nullifier: Genesect com Ferramenta impede o oponente de jogar ACE SPEC."""
    return any(
        passives.ability_active(state, mon, "ACE Nullifier") and mon.tool is not None
        for mon in state.state_of(player_id.other).all_pokemon_in_play()
    )


def tool_targets(ctx: Ctx) -> list[Target | None]:
    return [
        ("own", p)
        for p in core.positions(ctx.me)
        if (mon := core.mon_at(ctx.me, p)) is not None
        and len(mon.tools) < passives.tool_slots(ctx.state, ctx.player_id, mon)
    ]
