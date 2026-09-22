"""Habilidades ativadas ("uma vez durante o seu turno...") e gatilhos de
entrada em jogo ("quando você jogar este Pokémon da mão...").

Habilidades passivas (Skyliner, Fairy Zone, Mysterious Rock Inn...) ficam em
`passives.py`; as de Checkup (Freezing Shroud, Toxic Subjugation) nas regras.
Gatilhos de entrada viram uma `UseAbility` disponível no turno em que o
Pokémon foi jogado/evoluído, para o jogador poder recusar ("você pode").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pokemon_companion.engine.effects import core, passives
from pokemon_companion.engine.effects.cardinfo import (
    energy_type_of,
    has_ability,
    in_group,
    is_basic_energy,
    is_evolution,
    pokemon_type,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import PokemonInPlay, StatusCondition

Target = tuple[object, ...]
AbilityFn = Callable[[Ctx], None]
CheckFn = Callable[[Ctx], bool]
OptionsFn = Callable[[Ctx], list[Target | None]]


@dataclass(frozen=True)
class AbilitySpec:
    fn: AbilityFn
    can_use: CheckFn
    #: "turn" (1x por turno), "bench" (ao jogar no banco), "evolve" (ao evoluir)
    trigger: str = "turn"
    options: OptionsFn | None = None
    #: limite "não pode usar mais de 1 Habilidade X por turno" (por jogador)
    shared_limit: str | None = None


ABILITIES: dict[str, AbilitySpec] = {}


def ability(
    name: str,
    can_use: CheckFn = lambda ctx: True,
    *,
    trigger: str = "turn",
    options: OptionsFn | None = None,
    shared_limit: str | None = None,
) -> Callable[[AbilityFn], AbilityFn]:
    def decorator(fn: AbilityFn) -> AbilityFn:
        ABILITIES[name] = AbilitySpec(fn, can_use, trigger, options, shared_limit)
        return fn

    return decorator


def usable_abilities(ctx: Ctx, mon: PokemonInPlay) -> list[tuple[str, AbilitySpec]]:
    """Habilidades de `mon` que podem ser usadas agora."""
    result: list[tuple[str, AbilitySpec]] = []
    for ab in mon.card.abilities:
        spec = ABILITIES.get(ab.name)
        if spec is None or ab.name in mon.abilities_used:
            continue
        if not passives.ability_active(ctx.state, mon, ab.name):
            continue
        if spec.shared_limit and spec.shared_limit in ctx.me.used_ability_names:
            continue
        if spec.trigger == "bench" and mon.played_from_hand_turn != ctx.turn:
            continue
        if spec.trigger == "evolve" and not mon.evolved_this_turn:
            continue
        ctx.source = mon
        if spec.can_use(ctx):
            result.append((ab.name, spec))
    return result


def ability_options(ctx: Ctx, spec: AbilitySpec) -> list[Target | None]:
    if spec.options is None:
        return [None]
    return spec.options(ctx) or []


def use_ability(ctx: Ctx, name: str) -> None:
    spec = ABILITIES[name]
    assert ctx.source is not None
    ctx.source.abilities_used.add(name)
    if spec.shared_limit:
        ctx.me.used_ability_names.add(spec.shared_limit)
    ctx.log(f"{ctx.who()} usou a Habilidade {name} de {ctx.source.card.name}.")
    spec.fn(ctx)


def is_active(ctx: Ctx) -> bool:
    return ctx.me.active is ctx.source


def has_in_hand(ctx: Ctx, predicate: Callable[..., bool]) -> bool:
    return any(predicate(card) for card in ctx.me.hand)


def basic_of(energy_type: str) -> Callable[..., bool]:
    return lambda card: is_basic_energy(card) and energy_type_of(card) == energy_type


def opp_any(ctx: Ctx) -> list[Target | None]:
    return [("opp", p) for p in core.positions(ctx.opp)]


# ---------------------------------------------------------------------------
# compra


@ability("Run Errand", is_active, shared_limit="Run Errand")
def _run_errand(ctx: Ctx) -> None:
    core.draw(ctx.me, 2)


@ability(
    "Flip the Script",
    lambda ctx: ctx.me.knocked_out_turn == ctx.turn - 1,
    shared_limit="Flip the Script",
)
def _flip_the_script(ctx: Ctx) -> None:
    core.draw(ctx.me, 3)


@ability("Trade", lambda ctx: bool(ctx.me.hand) and bool(ctx.me.deck))
def _trade(ctx: Ctx) -> None:
    core.discard_from_hand(ctx, 1)
    core.draw(ctx.me, 2)


@ability("Run Away Draw", lambda ctx: bool(ctx.me.deck))
def _run_away_draw(ctx: Ctx) -> None:
    core.draw(ctx.me, 3)
    mon = ctx.source
    assert mon is not None
    ctx.me.deck.extend(mon.all_cards())
    ctx.me.deck.extend(
        core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
    )
    if ctx.me.active is mon:
        ctx.me.active = None
    else:
        del ctx.me.bench[core.index_of(ctx.me.bench, mon)]
    core.shuffle_deck(ctx.me)


@ability("Teleporter", is_active)
def _teleporter(ctx: Ctx) -> None:
    mon = ctx.source
    assert mon is not None
    ctx.me.deck.extend(mon.all_cards())
    ctx.me.deck.extend(
        core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
    )
    ctx.me.active = None
    core.shuffle_deck(ctx.me)


@ability(
    "Lunar Cycle",
    lambda ctx: any(m.card.name == "Solrock" for m in ctx.me.all_pokemon_in_play())
    and has_in_hand(ctx, basic_of("Fighting")),
    shared_limit="Lunar Cycle",
)
def _lunar_cycle(ctx: Ctx) -> None:
    card = next(c for c in ctx.me.hand if basic_of("Fighting")(c))
    ctx.me.hand.remove(card)
    ctx.me.discard.append(card)
    core.draw(ctx.me, 3)


@ability("Alluring Light", lambda ctx: bool(ctx.me.deck))
def _alluring_light(ctx: Ctx) -> None:
    core.draw(ctx.me, 1)
    core.draw(ctx.opp, 1)


@ability("Psychic Draw", trigger="evolve")
def _psychic_draw(ctx: Ctx) -> None:
    assert ctx.source is not None
    core.draw(ctx.me, 3 if ctx.source.card.name == "Alakazam" else 2)


# ---------------------------------------------------------------------------
# busca


@ability("Last-Ditch Catch", trigger="bench", shared_limit="Last-Ditch")
def _last_ditch_catch(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: "Supporter" in c.subtypes, 1)


@ability("Recon Directive", lambda ctx: bool(ctx.me.deck))
def _recon_directive(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 2, lambda c: True, 1, rest="bottom")


@ability(
    "Boom Boom Groove",
    lambda ctx: ctx.me.active is not None and has_ability(ctx.me.active.card, "Festival Lead"),
)
def _boom_boom_groove(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: True, 1)


@ability("Champion's Call")
def _champions_call(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon and in_group(c, "Cynthia's"), 1)


@ability("Attract Customers", is_active)
def _attract_customers(ctx: Ctx) -> None:
    core.look_top_and_take(ctx, 6, lambda c: "Supporter" in c.subtypes, 1)


@ability("Metallic Signal")
def _metallic_signal(ctx: Ctx) -> None:
    core.search_deck(ctx, lambda c: is_evolution(c) and pokemon_type(c) == "Metal", 2)


# ---------------------------------------------------------------------------
# energia


@ability("Teal Dance", lambda ctx: has_in_hand(ctx, basic_of("Grass")))
def _teal_dance(ctx: Ctx) -> None:
    card = next(c for c in ctx.me.hand if basic_of("Grass")(c))
    ctx.me.hand.remove(card)
    assert ctx.source is not None
    core.attach_energy_card(ctx.source, card)
    core.draw(ctx.me, 1)


@ability("Ripening Charge", lambda ctx: has_in_hand(ctx, basic_of("Grass")))
def _ripening_charge(ctx: Ctx) -> None:
    card = next(c for c in ctx.me.hand if basic_of("Grass")(c))
    target = core.best_energy_target(ctx.state, ctx.player_id, "Grass")
    if target is None:
        return
    ctx.me.hand.remove(card)
    core.attach_energy_card(target, card)
    core.heal(target, 30)
    ctx.log(f"{target.card.name} recebeu {card.name} e curou 30.")


def _dark_bench(ctx: Ctx) -> list[PokemonInPlay]:
    return [m for m in ctx.me.bench if pokemon_type(m.card) == "Darkness"]


@ability(
    "Sinister Surge",
    lambda ctx: bool(_dark_bench(ctx)) and any(basic_of("Darkness")(c) for c in ctx.me.deck),
)
def _sinister_surge(ctx: Ctx) -> None:
    bench = _dark_bench(ctx)
    card = next(c for c in ctx.me.deck if basic_of("Darkness")(c))
    target = core.best_energy_target(
        ctx.state, ctx.player_id, "Darkness", lambda m: any(m is b for b in bench)
    )
    if target is None:
        return
    ctx.me.deck.remove(card)
    core.attach_energy_card(target, card)
    target.damage_counters += 20
    core.shuffle_deck(ctx.me)
    ctx.log(f"{target.card.name} recebeu {card.name} e 2 contadores de dano.")


@ability("Charging Up", lambda ctx: any(is_basic_energy(c) for c in ctx.me.discard))
def _charging_up(ctx: Ctx) -> None:
    source = ctx.source
    core.attach_from(ctx, ctx.me.discard, is_basic_energy, 1, allowed=lambda m: m is source)


@ability("Metal Maker", lambda ctx: bool(ctx.me.deck))
def _metal_maker(ctx: Ctx) -> None:
    top = ctx.me.deck[:4]
    del ctx.me.deck[:4]
    metals = [c for c in top if basic_of("Metal")(c)]
    others = [c for c in top if c not in metals]
    core.attach_from(ctx, metals, lambda c: True, len(metals))
    import random

    random.shuffle(others)
    ctx.me.deck.extend(others)


@ability("Punk Up", trigger="evolve")
def _punk_up(ctx: Ctx) -> None:
    core.attach_from(
        ctx,
        ctx.me.deck,
        basic_of("Darkness"),
        5,
        allowed=lambda m: in_group(m.card, "Marnie's"),
    )
    core.shuffle_deck(ctx.me)


# ---------------------------------------------------------------------------
# dano, trocas e Estádio


def _can_move_counters(ctx: Ctx) -> bool:
    assert ctx.source is not None
    has_dark = any(e in ("Darkness", "Team Rocket's Energy") for e in ctx.source.attached_energies)
    return has_dark and any(m.damage_counters for m in ctx.me.all_pokemon_in_play())


@ability(
    "Adrena-Brain",
    lambda ctx: _can_move_counters(ctx) and not passives.counters_locked(ctx.state),
    options=opp_any,
)
def _adrena_brain(ctx: Ctx) -> None:
    donor = max(ctx.me.all_pokemon_in_play(), key=lambda m: m.damage_counters)
    moved = min(3, core.damage_counters_on(donor))
    target = core.mon_at(ctx.opp, int(ctx.target[1]) if ctx.target else -1)  # type: ignore[call-overload]
    if target is None or not moved:
        return
    donor.damage_counters -= 10 * moved
    core.place_counters(ctx, ctx.opp_id, target, moved, from_ability=True)


@ability("Cursed Blast", options=opp_any)
def _cursed_blast(ctx: Ctx) -> None:
    assert ctx.source is not None
    counters = 13 if ctx.source.card.name == "Dusknoir" else 5
    target = core.mon_at(ctx.opp, int(ctx.target[1]) if ctx.target else -1)  # type: ignore[call-overload]
    if target is not None:
        core.place_counters(ctx, ctx.opp_id, target, counters, from_ability=True)
    ctx.source.damage_counters = ctx.source.max_hp


def _chains_options(ctx: Ctx) -> list[Target | None]:
    return [
        ("own", i)
        for i, m in enumerate(ctx.me.bench)
        if pokemon_type(m.card) == "Darkness" and m.card.name != "Pecharunt ex"
    ]


@ability(
    "Subjugating Chains",
    lambda ctx: bool(_chains_options(ctx)) and ctx.me.active is not None,
    options=_chains_options,
    shared_limit="Subjugating Chains",
)
def _subjugating_chains(ctx: Ctx) -> None:
    index = int(ctx.target[1]) if ctx.target else 0  # type: ignore[call-overload]
    core.switch_active(ctx.state, ctx.me, index)
    active = ctx.me.active
    if active is not None and not passives.immune_to_special_conditions(ctx.state, active):
        active.status = StatusCondition.POISONED
    ctx.log(f"{active.card.name if active else '?'} foi para o Ativo, Envenenado.")


def _gust_options(ctx: Ctx) -> list[Target | None]:
    return [("opp", i) for i in range(len(ctx.opp.bench))]


@ability(
    "Heave-Ho Catcher", lambda ctx: bool(ctx.opp.bench), trigger="evolve", options=_gust_options
)
def _heave_ho(ctx: Ctx) -> None:
    index = int(ctx.target[1]) if ctx.target else 0  # type: ignore[call-overload]
    core.switch_active(ctx.state, ctx.opp, index)


@ability("Snow Sink", lambda ctx: ctx.state.stadium is not None, trigger="bench")
def _snow_sink(ctx: Ctx) -> None:
    from pokemon_companion.engine.effects.trainers import discard_stadium

    discard_stadium(ctx.state)


@ability(
    "Rapid Vernier",
    lambda ctx: ctx.me.active is not None and any(m is ctx.source for m in ctx.me.bench),
    trigger="bench",
)
def _rapid_vernier(ctx: Ctx) -> None:
    mon = ctx.source
    assert mon is not None
    core.switch_active(ctx.state, ctx.me, core.index_of(ctx.me.bench, mon))
    attack = max(mon.card.attacks, key=lambda a: a.base_damage, default=None)
    if attack is None:
        return
    for donor in ctx.me.all_pokemon_in_play():
        if donor is mon:
            continue
        for energy in list(donor.attached_energies):
            if passives.can_pay(ctx.state, ctx.player_id, mon, attack):
                return
            core.attach_energy_card(mon, core.detach_energy(donor, energy))
