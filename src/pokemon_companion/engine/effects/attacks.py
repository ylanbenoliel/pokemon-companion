"""Efeitos de texto dos ataques, registrados pelo nome do ataque.

Um ataque sem registro causa apenas o dano base no Ativo do oponente. Um
registro substitui esse comportamento por inteiro: a função decide quanto
dano causar (via `hit_active`/`hit`) e aplica o restante do texto.

Ataques com escolhas estratégicas declaram `options`: a lista de alvos vira
uma ação `UseAttack(i, target)` por opção, para a IA avaliar cada uma e o
humano escolher. As demais escolhas do texto são resolvidas por heurística.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pokemon_companion.cards_db.models import Attack, Card
from pokemon_companion.engine.effects import core, passives
from pokemon_companion.engine.effects.cardinfo import (
    energy_type_of,
    has_ability,
    has_rule_box,
    in_group,
    is_basic_energy,
    is_tera,
    pokemon_type,
    stage_of,
    trainer_kind,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import PokemonInPlay, StatusCondition

Target = tuple[object, ...]
AttackFn = Callable[[Ctx, Attack], None]
OptionsFn = Callable[[Ctx, Attack], list[Target | None]]


EstimateFn = Callable[["Ctx", Attack], int]


@dataclass(frozen=True)
class AttackSpec:
    fn: AttackFn
    options: OptionsFn | None = None
    #: rótulo de uma opção ("mode", k) para a UI
    mode_label: Callable[[int], str] | None = None
    #: dano provável, para a IA avaliar ataques cujo número depende do estado
    estimate: EstimateFn | None = None


ATTACKS: dict[str, AttackSpec] = {}


def attack(
    *names: str,
    options: OptionsFn | None = None,
    mode_label: Callable[[int], str] | None = None,
    estimate: EstimateFn | None = None,
) -> Callable[[AttackFn], AttackFn]:
    def decorator(fn: AttackFn) -> AttackFn:
        for name in names:
            ATTACKS[name] = AttackSpec(fn, options, mode_label, estimate)
        return fn

    return decorator


#: dano presumido de um ataque sem número no texto (efeitos, cópia, status)
EFFECT_ATTACK_VALUE = 60


def estimated_damage(state: object, side: object, mon: PokemonInPlay, attack_: Attack) -> int:
    """Quanto este ataque provavelmente causa agora — usado pela avaliação da
    IA, que senão trata "20× o número de X" como um ataque fraco qualquer."""
    spec = spec_for(attack_)
    if spec is not None and spec.estimate is not None:
        ctx = Ctx(state, side, mon)  # type: ignore[arg-type]
        return spec.estimate(ctx, attack_)
    if attack_.base_damage:
        return attack_.base_damage
    return EFFECT_ATTACK_VALUE if attack_.text else 0


def spec_for(attack_: Attack) -> AttackSpec | None:
    """Registro à mão pelo nome; senão, o texto compilado (`text_effects`)."""
    spec = ATTACKS.get(attack_.name)
    if spec is None and attack_.text:
        from pokemon_companion.engine.effects.text_effects import compiled_spec

        spec = compiled_spec(attack_.text)
    return spec


def attack_options(ctx: Ctx, attack_: Attack) -> list[Target | None]:
    spec = spec_for(attack_)
    if spec is None or spec.options is None:
        return [None]
    return spec.options(ctx, attack_) or [None]


def resolve_attack(ctx: Ctx, attack_: Attack) -> None:
    ctx.base_damage = attack_.base_damage
    ctx.attack_name = attack_.name
    spec = spec_for(attack_)
    if spec is None:
        hit_active(ctx, attack_.base_damage)
    else:
        spec.fn(ctx, attack_)


# ---------------------------------------------------------------------------
# dano


def hit_active(
    ctx: Ctx,
    amount: int,
    *,
    weakness: bool = True,
    resistance: bool = True,
    ignore_effects: bool = False,
) -> int:
    defender = ctx.opp.active
    if defender is None or amount <= 0:
        return 0
    dealt = core.deal_damage(
        ctx,
        ctx.opp_id,
        defender,
        amount,
        is_active=True,
        apply_weakness=weakness,
        apply_resistance=resistance,
        ignore_defender_effects=ignore_effects,
    )
    if dealt > 0:
        _on_active_damaged(ctx, defender)
    return dealt


def hit(ctx: Ctx, position: int, amount: int, **flags: bool) -> int:
    """Dano a um Pokémon do oponente (-1 = Ativo; banco sem Fraqueza/Resistência)."""
    if position == -1:
        return hit_active(ctx, amount, **flags)
    mon = core.mon_at(ctx.opp, position)
    if mon is None or amount <= 0:
        return 0
    return core.deal_damage(
        ctx,
        ctx.opp_id,
        mon,
        amount,
        is_active=False,
        ignore_defender_effects=flags.get("ignore_effects", False),
    )


def _on_active_damaged(ctx: Ctx, defender: PokemonInPlay) -> None:
    """Ferramentas/Energias que reagem ao Ativo receber dano de um ataque."""
    attacker = ctx.source
    state = ctx.state
    if attacker is None:
        return
    spiky = defender.attached_energies.count("Spiky Energy")
    if spiky:
        attacker.damage_counters += 20 * spiky
        ctx.log(f"Spiky Energy colocou {2 * spiky} contador(es) em {attacker.card.name}.")
    if passives.tool_active(state, defender, "Lucky Helmet"):
        core.draw(ctx.opp, 2)
        ctx.log(f"{ctx.who(ctx.opp_id)} comprou 2 cartas (Lucky Helmet).")
    if passives.tool_active(state, defender, "Handheld Fan") and attacker.attached_energies:
        receivers = ctx.me.bench
        if receivers:
            energy = attacker.attached_energies[-1]
            card = core.detach_energy(attacker, energy)
            receiver = min(receivers, key=lambda m: len(m.attached_energies))
            core.attach_energy_card(receiver, card)
            ctx.log(f"Handheld Fan moveu {card.name} para {receiver.card.name}.")


def best_damage_targets(ctx: Ctx, amount: int, count: int, bench_only: bool = False) -> list[int]:
    """Posições do oponente onde `amount` rende mais: nocautes com mais
    prêmios primeiro, depois quem fica mais perto do nocaute."""
    candidates = [p for p in core.positions(ctx.opp) if not (bench_only and p == -1)]

    def score(position: int) -> tuple[int, int, int]:
        mon = core.mon_at(ctx.opp, position)
        assert mon is not None
        kills = mon.current_hp <= amount
        return (int(kills), core._prize_value(mon.card) if kills else 0, -mon.current_hp)

    return sorted(candidates, key=score, reverse=True)[:count]


# ---------------------------------------------------------------------------
# opções


def opp_any(ctx: Ctx, _attack: Attack) -> list[Target | None]:
    return [("opp", p) for p in core.positions(ctx.opp)]


def opp_bench(ctx: Ctx, _attack: Attack) -> list[Target | None]:
    return [("opp", i) for i in range(len(ctx.opp.bench))]


def own_bench(ctx: Ctx, _attack: Attack) -> list[Target | None]:
    return [("own", i) for i in range(len(ctx.me.bench))]


def target_index(ctx: Ctx, default: int = -1) -> int:
    if ctx.target and len(ctx.target) >= 2 and isinstance(ctx.target[1], int):
        return ctx.target[1]
    return default


def mode(ctx: Ctx, default: int = 0) -> int:
    if ctx.target and ctx.target[0] == "mode":
        return int(ctx.target[1])  # type: ignore[call-overload]
    return default


# ---------------------------------------------------------------------------
# marcas de duração


def cant_attack_next_turn(ctx: Ctx) -> None:
    if ctx.source is not None:
        ctx.source.cannot_attack_turn = ctx.turn + 2


def cant_use_next_turn(ctx: Ctx, attack_: Attack) -> None:
    if ctx.source is not None:
        ctx.source.blocked_attack = (attack_.name, ctx.turn + 2)


def defending_cant_retreat(ctx: Ctx) -> None:
    defender = ctx.exposed_defender
    if defender is not None:
        defender.cannot_retreat_turn = ctx.turn + 1


def retaliate_next_turn(ctx: Ctx, counters: int) -> None:
    """ "No próximo turno do oponente, se este Pokémon sofrer dano de um
    ataque, coloque N contadores no Pokémon atacante"."""
    if ctx.source is not None:
        ctx.source.retaliation = (counters, ctx.turn + 1)


def _to_discard_or_hand(ctx: Ctx, card: Card) -> None:
    """Nitro Fire Energy volta para a mão quando descartada pelo ataque do
    Pokémon {R} em que está."""
    mon = ctx.source
    nitro = card.name == "Nitro Fire Energy"
    if nitro and mon is not None and pokemon_type(mon.card) == "Fire":
        ctx.me.hand.append(card)
    else:
        ctx.me.discard.append(card)


def discard_own_energy(ctx: Ctx, count: int, energy: str | None = None) -> int:
    """Descarta até `count` energias do atacante (só do tipo `energy`, se dado)."""
    mon = ctx.source
    discarded = 0
    while mon is not None and discarded < count:
        if energy is not None:
            if energy not in mon.attached_energies:
                break
            choice = energy
        elif mon.attached_energies:
            choice = core.least_useful_energy(mon)
        else:
            break
        _to_discard_or_hand(ctx, core.detach_energy(mon, choice))
        discarded += 1
    return discarded


def attach_from_deck(ctx: Ctx, predicate: core.CardFilter, count: int) -> int:
    """ "Procure no deck até N energias e anexe aos seus Pokémon como quiser"."""
    attached = core.attach_from(ctx, ctx.me.deck, predicate, count)
    core.shuffle_deck(ctx.me)
    return attached


def opp_hand_damage(ctx: Ctx, a: Attack) -> int:
    """ "N de dano para cada carta na mão do oponente"."""
    return a.base_damage * len(ctx.opp.hand)


def recoil(ctx: Ctx, amount: int) -> None:
    if ctx.source is not None:
        ctx.source.damage_counters += amount
        ctx.log(f"{ctx.source.card.name} sofreu {amount} de dano de recuo.")


def status_on_defender(ctx: Ctx, status: StatusCondition) -> None:
    if ctx.opp.active is not None:
        core.set_status(ctx, ctx.opp_id, ctx.opp.active, status)


def switch_self(ctx: Ctx) -> None:
    index = target_index(ctx, default=-2)
    if index < 0:
        index = core.best_bench_index(ctx.state, ctx.player_id) or 0
    if ctx.me.bench:
        core.switch_active(ctx.state, ctx.me, index)
        ctx.log(f"{ctx.who()} trocou o Ativo por {ctx.me.active.card.name}.")  # type: ignore[union-attr]


def hns_in_discard(ctx: Ctx) -> int:
    return sum(
        1 for card in ctx.me.discard if card.is_pokemon and has_ability(card, "Hide 'n' Sneak")
    )


def prizes_taken_by(ctx: Ctx) -> int:
    return ctx.state.prize_count - len(ctx.me.prizes)


def discard_all_energy(ctx: Ctx) -> None:
    mon = ctx.source
    if mon is None:
        return
    for energy in list(mon.attached_energies):
        _to_discard_or_hand(ctx, core.detach_energy(mon, energy))


# ---------------------------------------------------------------------------
# dano variável


@attack("Rapid-Fire Combo", estimate=lambda ctx, a: a.base_damage + 50)
def _rapid_fire(ctx: Ctx, a: Attack) -> None:
    heads = 0
    while core.coin():
        heads += 1
    ctx.log(f"{heads} cara(s).")
    hit_active(ctx, a.base_damage + 50 * heads)


@attack("Comet Punch", estimate=lambda ctx, a: 60)
def _comet_punch(ctx: Ctx, a: Attack) -> None:
    heads = sum(core.coin() for _ in range(4))
    ctx.log(f"{heads} cara(s).")
    hit_active(ctx, 30 * heads)


@attack("Tumbling Attack", "Play Rough", "Quick Attack", "Ambush")
def _coin_bonus(ctx: Ctx, a: Attack) -> None:
    bonus = number_in_text(a, r"this attack does (\d+) more damage", 20)
    hit_active(ctx, a.base_damage + (bonus if core.coin() else 0))


@attack("Surprise Attack", "Best Punch")
def _tails_does_nothing(ctx: Ctx, a: Attack) -> None:
    if core.coin():
        hit_active(ctx, a.base_damage)
    else:
        ctx.log("Coroa: o ataque não faz nada.")


@attack(
    "Fury Swipes",
    "Double Scratch",
    "Double Smash",
    estimate=lambda ctx, a: a.base_damage * number_in_text(a, r"Flip (\d+) coins", 2) // 2,
)
def _per_heads(ctx: Ctx, a: Attack) -> None:
    heads = sum(core.coin() for _ in range(number_in_text(a, r"Flip (\d+) coins", 2)))
    ctx.log(f"{heads} cara(s).")
    hit_active(ctx, a.base_damage * heads)


@attack(
    "Full Moon Rondo",
    estimate=lambda ctx, a: a.base_damage + 20 * (len(ctx.me.bench) + len(ctx.opp.bench)),
)
def _full_moon(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage + 20 * (len(ctx.me.bench) + len(ctx.opp.bench)))


@attack("Do the Wave", estimate=lambda ctx, a: 20 * len(ctx.me.bench))
def _do_the_wave(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, 20 * len(ctx.me.bench))


@attack(
    "Myriad Leaf Shower",
    estimate=lambda ctx, a: a.base_damage
    + 30 * sum(len(m.attached_energies) for m in (ctx.me.active, ctx.opp.active) if m),
)
def _myriad(ctx: Ctx, a: Attack) -> None:
    count = sum(len(m.attached_energies) for m in (ctx.me.active, ctx.opp.active) if m)
    hit_active(ctx, a.base_damage + 30 * count)


@attack(
    "Syrup Storm",
    estimate=lambda ctx, a: a.base_damage
    + 30 * sum(m.attached_energies.count("Grass") for m in ctx.me.all_pokemon_in_play()),
)
def _syrup(ctx: Ctx, a: Attack) -> None:
    grass = sum(m.attached_energies.count("Grass") for m in ctx.me.all_pokemon_in_play())
    hit_active(ctx, a.base_damage + 30 * grass)


@attack(
    "Rocket Rush",
    estimate=lambda ctx, a: 30
    * sum(1 for m in ctx.me.all_pokemon_in_play() if in_group(m.card, "Team Rocket's")),
)
def _rocket_rush(ctx: Ctx, a: Attack) -> None:
    team = sum(1 for m in ctx.me.all_pokemon_in_play() if in_group(m.card, "Team Rocket's"))
    hit_active(ctx, 30 * team)


@attack(
    "R Command",
    estimate=lambda ctx, a: 20
    * sum(1 for c in ctx.me.discard if trainer_kind(c) == "Supporter" and "Team Rocket" in c.name),
)
def _r_command(ctx: Ctx, a: Attack) -> None:
    supporters = sum(
        1 for c in ctx.me.discard if trainer_kind(c) == "Supporter" and "Team Rocket" in c.name
    )
    hit_active(ctx, 20 * supporters)


@attack(
    "Raging Curse",
    estimate=lambda ctx, a: 10
    * sum(core.damage_counters_on(m) for m in ctx.me.bench if in_group(m.card, "Cynthia's")),
)
def _raging_curse(ctx: Ctx, a: Attack) -> None:
    counters = sum(
        core.damage_counters_on(m) for m in ctx.me.bench if in_group(m.card, "Cynthia's")
    )
    hit_active(ctx, 10 * counters, weakness=False)


@attack(
    "Relentless Punches",
    estimate=lambda ctx, a: a.base_damage
    + 50 * (core.damage_counters_on(ctx.opp.active) if ctx.opp.active else 0),
)
def _relentless(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    counters = core.damage_counters_on(defender) if defender else 0
    hit_active(ctx, a.base_damage + 50 * counters)


@attack("Whirling Envy")
def _whirling_envy(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    bonus = 90 if core.damage_counters_on(ctx.source) >= 2 else 0
    hit_active(ctx, a.base_damage + bonus, weakness=False)


def _per_own_counter(ctx: Ctx, a: Attack) -> int:
    counters = core.damage_counters_on(ctx.source) if ctx.source else 0
    return (a.base_damage or 20) * counters


@attack("Powerful Rage", "Wrathful Hearth", estimate=_per_own_counter)
def _powerful_rage(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _per_own_counter(ctx, a))


@attack("Spiky Wheel")
def _spiky_wheel(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    hit_active(ctx, a.base_damage + 40 * ctx.source.attached_energies.count("Darkness"))


@attack("Dark Frost")
def _dark_frost(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    bonus = 60 if "Team Rocket's Energy" in ctx.source.attached_energies else 0
    hit_active(ctx, a.base_damage + bonus)


@attack("Wicked Impact")
def _wicked_impact(ctx: Ctx, a: Attack) -> None:
    bonus = 100 if ctx.me.played_team_rocket_supporter_turn == ctx.turn else 0
    hit_active(ctx, a.base_damage + bonus)


@attack("Spirited Tackle")
def _spirited_tackle(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    bonus = 90 if defender and stage_of(defender.card) == "Stage 1" else 0
    hit_active(ctx, a.base_damage + bonus)


@attack("Mirror Attack")
def _mirror_attack(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    bonus = 30 if defender and pokemon_type(defender.card) == "Psychic" else 0
    hit_active(ctx, a.base_damage + bonus)


def number_in_text(attack_: Attack, pattern: str, default: int) -> int:
    """Número do texto do ataque (versões da mesma carta mudam só o valor).
    Aceita "a"/"an" como 1 ("Draw a card", "Discard an Energy")."""
    match = re.search(pattern, attack_.text)
    if not match:
        return default
    word = match.group(1)
    return 1 if word in ("a", "an") else int(word)


#: símbolos de energia no texto das cartas: "{W}" ou "[W]"
ENERGY_SYMBOLS = {
    "G": "Grass",
    "R": "Fire",
    "W": "Water",
    "L": "Lightning",
    "P": "Psychic",
    "F": "Fighting",
    "D": "Darkness",
    "M": "Metal",
    "N": "Dragon",
    "C": "Colorless",
}
SYMBOL = r"[\[{](\w)[\]}]"


def energy_in_text(attack_: Attack, pattern: str) -> str | None:
    """Tipo de energia citado no texto; `pattern` usa `{SYMBOL}` no lugar do ícone."""
    match = re.search(pattern.replace("{SYMBOL}", SYMBOL), attack_.text)
    return ENERGY_SYMBOLS.get(match.group(1)) if match else None


def effect_happens(attack_: Attack, clause: str) -> bool:
    """A frase `clause` acontece sempre, ou só com cara se vier logo depois de
    "Flip a coin. If heads," no texto."""
    index = attack_.text.lower().find(clause.lower())
    if index < 0:
        return False
    gated = attack_.text[:index].rstrip().endswith("If heads,")
    return core.coin() if gated else True


@attack("Psychic", "Ear Force")
def _per_defender_energy(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    per_energy = number_in_text(a, r"(\d+) more damage for each Energy", 30)
    hit_active(
        ctx, a.base_damage + per_energy * (len(defender.attached_energies) if defender else 0)
    )


@attack("Fighting Wings")
def _fighting_wings(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    hit_active(ctx, a.base_damage + (90 if defender and "ex" in defender.card.subtypes else 0))


@attack("Electromagnetic Sonar")
def _electromagnetic_sonar(ctx: Ctx, a: Attack) -> None:
    core.recover_from_discard(ctx, lambda c: c.supertype.value == "Trainer", 1)


@attack("Ghostly Blow")
def _ghostly_blow(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    targets = best_damage_targets(ctx, 50, 1, bench_only=True)
    if targets:
        mon = core.mon_at(ctx.opp, targets[0])
        assert mon is not None
        core.place_counters(ctx, ctx.opp_id, mon, 5)


@attack("Strange Hacking")
def _strange_hacking(ctx: Ctx, a: Attack) -> None:
    """Confunde o Ativo e move contadores entre os Pokémon do oponente para
    nocautear o alvo que vale mais prêmios, quando dá."""
    status_on_defender(ctx, StatusCondition.CONFUSED)
    if passives.counters_locked(ctx.state):
        return
    mons = ctx.opp.all_pokemon_in_play()
    for target in sorted(mons, key=lambda m: -core._prize_value(m.card)):
        donors = [m for m in mons if m is not target and m.damage_counters]
        needed = target.current_hp
        if needed > sum(m.damage_counters for m in donors):
            continue
        for donor in sorted(donors, key=lambda m: -m.damage_counters):
            moved = min(donor.damage_counters, needed)
            donor.damage_counters -= moved
            target.damage_counters += moved
            needed -= moved
            if needed <= 0:
                break
        ctx.log(f"Contadores movidos: {target.card.name} fica sem HP.")
        return


@attack(
    "Irritated Outburst",
    estimate=lambda ctx, a: 60 * (ctx.state.prize_count - len(ctx.me.prizes)),
)
def _irritated(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, 60 * prizes_taken_by(ctx))


@attack("Hungry Jaws")
def _hungry_jaws(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    hit_active(ctx, a.base_damage + (150 if ctx.source.damage_counters else 0))


@attack("Gale Thrust")
def _gale_thrust(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    bonus = 170 if ctx.source.moved_to_active_turn == ctx.turn else 0
    hit_active(ctx, a.base_damage + bonus)


@attack("Love Resonance")
def _love_resonance(ctx: Ctx, a: Attack) -> None:
    mine = {pokemon_type(m.card) for m in ctx.me.all_pokemon_in_play()}
    theirs = {pokemon_type(m.card) for m in ctx.opp.all_pokemon_in_play()}
    hit_active(ctx, a.base_damage + (120 if mine & theirs else 0))


@attack("Maximum Drilling")
def _max_drilling(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    units = passives.provided_energy(ctx.state, ctx.player_id, ctx.source)
    extra = len(units) - len(passives.attack_cost(ctx.state, ctx.player_id, ctx.source, a))
    hit_active(ctx, a.base_damage + (130 if extra >= 2 else 0))


@attack("Vengeful Anchor")
def _vengeful_anchor(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage + (140 if hns_in_discard(ctx) >= 4 else 0))


@attack("Cosmic Beam")
def _cosmic_beam(ctx: Ctx, a: Attack) -> None:
    if not any(m.card.name == "Lunatone" for m in ctx.me.bench):
        ctx.log("Sem Lunatone no banco: o ataque não faz nada.")
        return
    hit_active(ctx, a.base_damage, weakness=False, resistance=False)


@attack("Rock Hurl")
def _rock_hurl(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage, resistance=False)


@attack("Superb Scissors", "Spiky Hopper", "Shred", "Sonic Edge")
def _ignore_defender_effects(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage, ignore_effects=True)


@attack("Nebula Beam", "Demolish")
def _ignore_everything(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage, weakness=False, resistance=False, ignore_effects=True)


@attack("Mind Ruler", "Resentful Refrain", estimate=opp_hand_damage)
def _opp_hand(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, opp_hand_damage(ctx, a))


def _retreat_bonus(ctx: Ctx, a: Attack) -> int:
    defender = ctx.opp.active
    cost = passives.retreat_cost(ctx.state, ctx.opp_id, defender) if defender else 0
    return a.base_damage + number_in_text(a, r"(\d+) more damage for each", 50) * cost


@attack("Phantom Maze", estimate=_retreat_bonus)
def _phantom_maze(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _retreat_bonus(ctx, a))


@attack("Shocking Web")
def _shocking_web(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    bonus = 80 if "Lightning" in ctx.source.attached_energies else 0
    hit_active(ctx, a.base_damage + bonus)


# ---------------------------------------------------------------------------
# escolhas com custo: "você pode descartar..."


def _bellowing_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    total = sum(
        1
        for m in ctx.me.all_pokemon_in_play()
        for e in m.attached_energies
        if e in core.BASIC_ENERGIES
    )
    return [("mode", k) for k in range(1, total + 1)]


@attack(
    "Bellowing Thunder",
    options=_bellowing_options,
    mode_label=lambda k: f"Descartar {k} energia(s)",
)
def _bellowing_thunder(ctx: Ctx, a: Attack) -> None:
    wanted = mode(ctx)
    discarded = 0
    # descarta primeiro do banco, preservando o Ativo para o próximo turno
    for mon in [*ctx.me.bench, *([ctx.me.active] if ctx.me.active else [])]:
        for energy in list(mon.attached_energies):
            if discarded >= wanted:
                break
            if energy in core.BASIC_ENERGIES:
                ctx.me.discard.append(core.detach_energy(mon, energy))
                discarded += 1
    hit_active(ctx, 70 * discarded)


@attack(
    "Metallic Hammer",
    options=lambda ctx, a: [("mode", 0), ("mode", 1)],
    mode_label=lambda k: "Descartar 3 {M} (+150)" if k else "Sem descartar",
)
def _metallic_hammer(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    bonus = 0
    if mode(ctx) and ctx.source.attached_energies.count("Metal") >= 3:
        for _ in range(3):
            ctx.me.discard.append(core.detach_energy(ctx.source, "Metal"))
        bonus = 150
    hit_active(ctx, a.base_damage + bonus)


def _erasure_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    available = sum(len(m.attached_energies) for m in ctx.me.bench)
    return [("mode", k) for k in range(min(2, available) + 1)]


@attack("Erasure Ball", options=_erasure_options, mode_label=lambda k: f"Descartar {k} energia(s)")
def _erasure_ball(ctx: Ctx, a: Attack) -> None:
    discarded = 0
    for _ in range(mode(ctx)):
        donors = [m for m in ctx.me.bench if m.attached_energies]
        if not donors:
            break
        donor = max(donors, key=lambda m: len(m.attached_energies))
        core.discard_energy(ctx.me, donor)
        discarded += 1
    hit_active(ctx, a.base_damage + 60 * discarded)


def _feathers_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    count = sum(
        1 for c in ctx.me.hand if trainer_kind(c) == "Supporter" and "Team Rocket" in c.name
    )
    return [("mode", k) for k in range(count + 1)]


@attack(
    "Rocket Feathers", options=_feathers_options, mode_label=lambda k: f"Descartar {k} Apoiador(es)"
)
def _rocket_feathers(ctx: Ctx, a: Attack) -> None:
    discarded = 0
    for card in [
        c for c in ctx.me.hand if trainer_kind(c) == "Supporter" and "Team Rocket" in c.name
    ]:
        if discarded >= mode(ctx):
            break
        ctx.me.hand.remove(card)
        ctx.me.discard.append(card)
        discarded += 1
    hit_active(ctx, 60 * discarded)


def _pump_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    options: list[Target | None] = [("mode", 0)]
    if ctx.source is not None and len(ctx.source.attached_energies) >= 3:
        options += [("opp", i) for i in range(len(ctx.opp.bench))]
    return options


@attack("Torrential Pump", options=_pump_options, mode_label=lambda k: "Sem embaralhar energias")
def _torrential_pump(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.target and ctx.target[0] == "opp" and ctx.source is not None:
        for energy in ctx.source.attached_energies[:3]:
            ctx.me.deck.append(core.detach_energy(ctx.source, energy))
        core.shuffle_deck(ctx.me)
        hit(ctx, target_index(ctx), 120)


# ---------------------------------------------------------------------------
# alvo livre / banco


def _any_target_amount(ctx: Ctx, a: Attack) -> int:
    return number_in_text(a, r"does (\d+) damage to 1 of your opponent's Pokémon", 100)


@attack("Cruel Arrow", "Garnet Volley", options=opp_any, estimate=_any_target_amount)
def _any_target(ctx: Ctx, a: Attack) -> None:
    hit(ctx, target_index(ctx), _any_target_amount(ctx, a))


@attack("Sonic Ripper", options=opp_any, estimate=lambda ctx, a: 220)
def _sonic_ripper(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    for energy in list(ctx.source.attached_energies):
        ctx.me.deck.append(core.detach_energy(ctx.source, energy))
    core.shuffle_deck(ctx.me)
    hit(ctx, target_index(ctx), 220)


@attack(
    "Strike the Sleeper",
    options=opp_bench,
    estimate=lambda ctx, a: 20
    * max((core.damage_counters_on(m) for m in ctx.opp.bench), default=0),
)
def _strike_sleeper(ctx: Ctx, a: Attack) -> None:
    mon = core.mon_at(ctx.opp, target_index(ctx, 0))
    if mon is not None:
        hit(ctx, target_index(ctx, 0), 20 * core.damage_counters_on(mon))


@attack("Twin Shotels", estimate=lambda ctx, a: 100)
def _twin_shotels(ctx: Ctx, a: Attack) -> None:
    for position in best_damage_targets(ctx, 50, 2):
        hit(ctx, position, 50, weakness=False, resistance=False, ignore_effects=True)


@attack("Trifrost", estimate=lambda ctx, a: 220)
def _trifrost(ctx: Ctx, a: Attack) -> None:
    discard_all_energy(ctx)
    for position in best_damage_targets(ctx, 110, 3):
        hit(ctx, position, 110)


@attack("Phantom Dive", estimate=lambda ctx, a: a.base_damage + 60)
def _phantom_dive(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    core.spread_counters(ctx, ctx.opp_id, 6, bench_only=True)


@attack("Shadow Bullet", "Jetting Blow")
def _also_bench(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    amount = number_in_text(a, r"also does (\d+) damage to 1 of your opponent's Benched", 30)
    targets = best_damage_targets(ctx, amount, 1, bench_only=True)
    if targets:
        hit(ctx, targets[0], amount)


@attack("Mirage Barrage", estimate=lambda ctx, a: 240)
def _mirage_barrage(ctx: Ctx, a: Attack) -> None:
    discard_own_energy(ctx, 2)
    for position in best_damage_targets(ctx, 120, 2):
        hit(ctx, position, 120)


@attack("Matcha Spin")
def _matcha_spin(ctx: Ctx, a: Attack) -> None:
    if hns_in_discard(ctx) >= 6:
        for mon in ctx.opp.all_pokemon_in_play():
            core.place_counters(ctx, ctx.opp_id, mon, 4)


@attack("Spiritual End")
def _spiritual_end(ctx: Ctx, a: Attack) -> None:
    if hns_in_discard(ctx) < 13:
        return
    mons = sorted(ctx.opp.all_pokemon_in_play(), key=lambda m: -m.damage_counters)[:2]
    for mon in mons:
        core.place_counters(ctx, ctx.opp_id, mon, 3 * core.damage_counters_on(mon))


@attack("Powerful Hand", estimate=lambda ctx, a: 20 * len(ctx.me.hand))
def _powerful_hand(ctx: Ctx, a: Attack) -> None:
    if ctx.opp.active is not None:
        core.place_counters(ctx, ctx.opp_id, ctx.opp.active, 2 * len(ctx.me.hand))


@attack("Furtive Drop")
def _furtive_drop(ctx: Ctx, a: Attack) -> None:
    if ctx.opp.active is not None:
        core.place_counters(ctx, ctx.opp_id, ctx.opp.active, 1)


# ---------------------------------------------------------------------------
# recuo, "não pode atacar", "não pode recuar"


def _recoil_attack(amount: int) -> AttackFn:
    def fn(ctx: Ctx, a: Attack) -> None:
        hit_active(ctx, a.base_damage)
        recoil(ctx, amount)

    return fn


for _name, _amount in (
    ("Iron Tackle", 10),
    ("Take Down", 10),
    ("Reckless Charge", 10),
    ("Slight Intrusion", 10),
    ("Wood Hammer", 30),
    ("Wild Press", 70),
):
    ATTACKS[_name] = AttackSpec(_recoil_attack(_amount))


@attack(
    "Eon Blade",
    "Prism Edge",
    "Blood Moon",
    "Rampaging Thunder",
    "Boundless Power",
    "Thunderous Bolt",
    "Giga Impact",
)
def _cant_attack(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    cant_attack_next_turn(ctx)


@attack(
    "Smashing Headbutt",
    "Topaz Bolt",
    "Flamethrower",
    "Scorching Fire",
    "Power Stomp",
    "Air Slash",
    "Ember",
    "Strong Volt",
    "Thunderbolt",
    "Illusory Impulse",
)
def _discard_own_after(ctx: Ctx, a: Attack) -> None:
    """ "Discard N/a/an/all [{X}] Energy from this Pokémon" depois do dano.
    Versões sem texto do mesmo ataque só causam dano."""
    hit_active(ctx, a.base_damage)
    if "Discard all Energy from this Pokémon" in a.text:
        discard_all_energy(ctx)
        return
    pattern = r"Discard (\d+|an?) (?:{SYMBOL} )?Energy from this"
    count = number_in_text(a, pattern.replace("{SYMBOL}", SYMBOL), 0)
    discard_own_energy(ctx, count, energy_in_text(a, r"Discard an? {SYMBOL} Energy"))


@attack("Ready to Ram", "Repulsor Axe")
def _retaliate(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    retaliate_next_turn(ctx, number_in_text(a, r"put (\d+) damage counters", 6))


@attack("Accelerating Stab", "Mega Brave", "Flare Strike", "Flashing Bolt")
def _cant_repeat(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    cant_use_next_turn(ctx, a)


@attack("Shadow Bind", "Sob", "Clutch", "Corner", "Big Bite", "Bind Down")
def _no_retreat(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    defending_cant_retreat(ctx)


@attack("Protect Charge", "Guard Press", "Gaia Wave", "Steel Wing", "Ramming Shell")
def _reduce_damage_next_turn(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.source is not None:
        amount = number_in_text(a, r"takes (\d+) less damage", 30)
        ctx.source.damage_reduction = (amount, ctx.turn + 1)


@attack("Hide")
def _hide(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.source is not None and effect_happens(a, "during your opponent's next turn, prevent"):
        ctx.source.protected_turn = ctx.turn + 1
        ctx.log(f"{ctx.source.card.name} fica protegido no próximo turno.")


@attack("Growl")
def _growl(ctx: Ctx, a: Attack) -> None:
    defender = ctx.exposed_defender
    if defender:
        defender.attack_debuff = (20, ctx.turn + 1)


@attack("Itchy Pollen")
def _itchy_pollen(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    ctx.opp.items_blocked_turn = ctx.turn + 1


@attack("Evolution Jammer")
def _evolution_jammer(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    ctx.opp.evolution_blocked_turn = ctx.turn + 1


@attack("Torment")
def _torment(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    defender = ctx.exposed_defender
    if defender and defender.card.attacks:
        strongest = max(defender.card.attacks, key=lambda x: x.base_damage)
        defender.blocked_attack = (strongest.name, ctx.turn + 1)


# ---------------------------------------------------------------------------
# condições especiais


@attack("Mind Bend")
def _mind_bend(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    status_on_defender(ctx, StatusCondition.CONFUSED)


@attack("Absolute Snow")
def _absolute_snow(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    status_on_defender(ctx, StatusCondition.ASLEEP)


@attack("Poison Spray")
def _poison_spray(ctx: Ctx, a: Attack) -> None:
    status_on_defender(ctx, StatusCondition.POISONED)


@attack("Poison Chain")
def _poison_chain(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    status_on_defender(ctx, StatusCondition.POISONED)
    defending_cant_retreat(ctx)


@attack("Tantrum")
def _tantrum(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.source is not None and not passives.immune_to_special_conditions(ctx.state, ctx.source):
        ctx.source.status = StatusCondition.CONFUSED


# ---------------------------------------------------------------------------
# trocas


@attack("Trading Places", "Teleportation Attack", "Run Around", "Strafe", options=own_bench)
def _switch_self_attack(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    switch_self(ctx)


def gust_opponent(ctx: Ctx) -> None:
    """ "Switch in 1 of your opponent's Benched Pokémon to the Active Spot"."""
    if not ctx.opp.bench:
        return
    index = target_index(ctx, -2)
    if index < 0:
        index = core.gust_target(ctx.state, ctx.opp_id) or 0
    core.switch_active(ctx.state, ctx.opp, index)
    ctx.log(f"{ctx.opp.active.card.name} foi puxado para o Ativo.")  # type: ignore[union-attr]


@attack("Follow Me", options=opp_bench)
def _gust_attack(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    gust_opponent(ctx)


@attack("Drag Off", options=opp_bench)
def _gust_then_hit(ctx: Ctx, a: Attack) -> None:
    gust_opponent(ctx)
    hit_active(ctx, number_in_text(a, r"does (\d+) damage to the new Active", 20))


@attack("Bounce Back", "Push Down")
def _push_out(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    defender = ctx.exposed_defender
    if defender:
        core.opponent_switches_out(ctx.state, ctx.opp_id)


@attack("Tuck Tail")
def _tuck_tail(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    mon = ctx.source
    if mon is not None and ctx.me.active is mon:
        ctx.me.hand.extend(mon.all_cards())
        ctx.me.hand.extend(
            core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
        )
        ctx.me.active = None
        ctx.log(f"{mon.card.name} voltou para a mão.")


# ---------------------------------------------------------------------------
# compra e busca


@attack("Greedy Fang", "Rapid Draw")
def _draw_two(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    core.draw(ctx.me, 2)


@attack("Filch", "Collect", "Add On")
def _draw_from_text(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    core.draw(ctx.me, number_in_text(a, r"Draw (\d+|a) cards?", 1))


@attack("Gather Strength", "Minor Errand-Running")
def _search_basic_energy(ctx: Ctx, a: Attack) -> None:
    core.search_deck(ctx, is_basic_energy, number_in_text(a, r"up to (\d+)", 1))


@attack("Flower Shower")
def _both_draw(ctx: Ctx, a: Attack) -> None:
    count = number_in_text(a, r"Each player draws (\d+)", 3)
    core.draw(ctx.me, count)
    core.draw(ctx.opp, count)


@attack("Spreading Light")
def _same_name_to_bench(ctx: Ctx, a: Attack) -> None:
    assert ctx.source is not None
    name = ctx.source.card.name
    count = number_in_text(a, r"up to (\d+)", 3)
    core.search_deck(ctx, lambda c: c.name == name, count, destination="bench")


@attack("Summoning Jutsu", "Find a Friend")
def _search_pokemon(ctx: Ctx, a: Attack) -> None:
    core.search_deck(ctx, lambda c: c.is_pokemon, number_in_text(a, r"up to (\d+)", 1))


@attack("Burst Roar")
def _burst_roar(ctx: Ctx, a: Attack) -> None:
    ctx.me.discard.extend(ctx.me.hand)
    ctx.me.hand.clear()
    core.draw(ctx.me, 6)


@attack("Corkscrew Dive", "Return")
def _corkscrew_dive(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    core.draw_until(ctx.me, 6)


@attack("Call for Family")
def _call_for_family(ctx: Ctx, a: Attack) -> None:
    core.search_deck(ctx, lambda c: c.is_basic, 2, destination="bench")


@attack("Deceit")
def _deceit(ctx: Ctx, a: Attack) -> None:
    core.search_deck(ctx, lambda c: trainer_kind(c) == "Supporter", 1)


@attack("Puppet Pull", "Shinobi Blade")
def _puppet_pull(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    core.search_deck(ctx, lambda c: True, 1)


@attack("Traverse Time")
def _traverse_time(ctx: Ctx, a: Attack) -> None:
    core.search_deck(
        ctx,
        lambda c: (c.is_pokemon and pokemon_type(c) == "Grass") or trainer_kind(c) == "Stadium",
        3,
    )


@attack("Dangle Tail")
def _dangle_tail(ctx: Ctx, a: Attack) -> None:
    core.recover_from_discard(ctx, lambda c: c.is_pokemon, 1)


@attack("Come and Get You")
def _come_and_get_you(ctx: Ctx, a: Attack) -> None:
    for _ in range(min(3, core.bench_space(ctx.state, ctx.me))):
        card = next((c for c in ctx.me.discard if c.name == "Duskull"), None)
        if card is None:
            break
        ctx.me.discard.remove(card)
        core.put_on_bench(ctx.state, ctx.me, card)
        ctx.log(f"{ctx.who()} colocou Duskull do descarte no banco.")


@attack("Ascension")
def _ascension(ctx: Ctx, a: Attack) -> None:
    mon = ctx.source
    if mon is None:
        return
    card = next((c for c in ctx.me.deck if c.evolves_from == mon.card.name), None)
    if card is not None:
        ctx.me.deck.remove(card)
        core.evolve_into(ctx.state, ctx.me, mon, card)
        ctx.log(f"{mon.card.name} evoluiu para {card.name}.")
    core.shuffle_deck(ctx.me)


# ---------------------------------------------------------------------------
# energia


@attack("Aura Jab")
def _aura_jab(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    bench = ctx.me.bench
    core.attach_from(
        ctx,
        ctx.me.discard,
        core.type_filter("Fighting"),
        3,
        allowed=lambda m: any(m is b for b in bench),
    )


@attack("Kaleidowaltz")
def _kaleidowaltz(ctx: Ctx, a: Attack) -> None:
    heads = sum(core.coin() for _ in range(3))
    ctx.log(f"{heads} cara(s).")
    attach_from_deck(ctx, is_basic_energy, 2 * heads)


@attack("Jolting Charge")
def _jolting_charge(ctx: Ctx, a: Attack) -> None:
    for energy_type in ("Grass", "Lightning"):
        core.attach_from(ctx, ctx.me.deck, core.type_filter(energy_type), 2)
    core.shuffle_deck(ctx.me)


@attack("Draconic Buster")
def _draconic_buster(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    discard_all_energy(ctx)


@attack("Icicle Loop")
def _icicle_loop(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.source is not None and ctx.source.attached_energies:
        energy = core.least_useful_energy(ctx.source)
        ctx.me.hand.append(core.detach_energy(ctx.source, energy))


@attack("Whirlpool")
def _whirlpool(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    defender = ctx.exposed_defender
    if (
        defender
        and defender.attached_energies
        and core.coin()
    ):
        core.discard_energy(ctx.opp, defender, defender.attached_energies[0])
        ctx.log(f"Uma energia de {defender.card.name} foi descartada.")


@attack("Slight Shift")
def _slight_shift(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    if defender and defender.attached_energies and ctx.opp.bench:
        receiver = min(ctx.opp.bench, key=lambda m: len(m.card.attacks))
        card = core.detach_energy(defender, defender.attached_energies[0])
        core.attach_energy_card(receiver, card)


# ---------------------------------------------------------------------------
# cópia de ataques


def _copyable(card: Card) -> list[int]:
    return [j for j, x in enumerate(card.attacks) if x.name not in COPY_ATTACKS]


def _night_joker_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    return [
        ("copy", i, j)
        for i, mon in enumerate(ctx.me.bench)
        if in_group(mon.card, "N's")
        for j in _copyable(mon.card)
    ]


def _mimicry_options(ctx: Ctx, _a: Attack) -> list[Target | None]:
    defender = ctx.opp.active
    if defender is None or not is_tera(defender.card):
        return []
    return [("copy", -1, j) for j in _copyable(defender.card)]


def use_copied(ctx: Ctx, copied: Attack) -> None:
    ctx.log(f"Usou {copied.name} como este ataque.")
    sub = Ctx(ctx.state, ctx.player_id, ctx.source, None, ctx.messages)
    options = attack_options(sub, copied)
    sub.target = options[0]
    resolve_attack(sub, copied)


@attack("Night Joker", options=_night_joker_options)
def _night_joker(ctx: Ctx, a: Attack) -> None:
    if not ctx.target or ctx.target[0] != "copy":
        return
    mon = core.mon_at(ctx.me, int(ctx.target[1]))  # type: ignore[call-overload]
    if mon is not None:
        use_copied(ctx, mon.card.attacks[int(ctx.target[2])])  # type: ignore[call-overload]


@attack("Gemstone Mimicry", options=_mimicry_options)
def _gemstone_mimicry(ctx: Ctx, a: Attack) -> None:
    if not ctx.target or ctx.target[0] != "copy" or ctx.opp.active is None:
        return
    use_copied(ctx, ctx.opp.active.card.attacks[int(ctx.target[2])])  # type: ignore[call-overload]


@attack("Seek Inspiration")
def _seek_inspiration(ctx: Ctx, a: Attack) -> None:
    if not ctx.me.deck:
        return
    card = ctx.me.deck.pop(0)
    ctx.me.discard.append(card)
    ctx.log(f"Descartou {card.name} do topo do deck.")
    if card.is_pokemon and not has_rule_box(card) and _copyable(card):
        best = max(_copyable(card), key=lambda j: card.attacks[j].base_damage)
        use_copied(ctx, card.attacks[best])


COPY_ATTACKS = {"Night Joker", "Gemstone Mimicry", "Seek Inspiration"}


# ---------------------------------------------------------------------------
# diversos


@attack("Undermine")
def _undermine(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    for _ in range(min(2, len(ctx.opp.deck))):
        ctx.opp.discard.append(ctx.opp.deck.pop(0))


@attack("Hacking")
def _hacking(ctx: Ctx, a: Attack) -> None:
    if core.discard_from_hand(ctx, 1):
        core.discard_from_hand(ctx, 1, player_id=ctx.opp_id)


@attack("Claw of Darkness")
def _claw_of_darkness(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.opp.hand:
        best = core.choose_cards(ctx.state, ctx.opp_id, ctx.opp.hand, 1)[0]
        ctx.opp.hand.remove(best)
        ctx.opp.discard.append(best)
        ctx.log(f"{ctx.who(ctx.opp_id)} descartou {best.name}.")


@attack("Terminal Period")
def _terminal_period(ctx: Ctx, a: Attack) -> None:
    defender = ctx.opp.active
    if defender and defender.damage_counters == 60:
        defender.damage_counters = defender.max_hp
        ctx.log(f"{defender.card.name} foi nocauteado por Terminal Period.")


@attack("Destined Fight")
def _destined_fight(ctx: Ctx, a: Attack) -> None:
    for mon in (ctx.me.active, ctx.opp.active):
        if mon is not None:
            mon.damage_counters = mon.max_hp


def energy_cards_in(cards: list[Card], energy_type: str | None = None) -> list[Card]:
    return [
        c
        for c in cards
        if is_basic_energy(c) and (energy_type is None or energy_type_of(c) == energy_type)
    ]


# ---------------------------------------------------------------------------
# modelos de texto: uma função por forma de frase, valores lidos da carta

STATUSES = {
    "Asleep": StatusCondition.ASLEEP,
    "Burned": StatusCondition.BURNED,
    "Confused": StatusCondition.CONFUSED,
    "Paralyzed": StatusCondition.PARALYZED,
    "Poisoned": StatusCondition.POISONED,
}


@attack(
    "Singe",
    "Searing Flame",
    "Thunder Shock",
    "Perplex",
    "Body Slam",
    "Disarming Voice",
    "Poison Ring",
)
def _status_from_text(ctx: Ctx, a: Attack) -> None:
    """ "[Flip a coin. If heads,] your opponent's Active Pokémon is now X"
    (e "can't retreat", se o texto disser)."""
    hit_active(ctx, a.base_damage)
    match = re.search(r"(?i)your opponent's Active Pokémon is now (\w+)", a.text)
    if match and match.group(1) in STATUSES and effect_happens(a, match.group(0)):
        status_on_defender(ctx, STATUSES[match.group(1)])
    if "can't retreat" in a.text:
        defending_cant_retreat(ctx)


@attack("Mega Drain", "Hold Still", "Absorb", "Jungle Dump")
def _heal_self(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    if ctx.source is not None:
        core.heal(ctx.source, number_in_text(a, r"Heal (\d+) damage from this Pokémon", 30))


@attack("Wild Tackle", "Inferno Onrush")
def _recoil_from_text(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    recoil(ctx, number_in_text(a, r"also does (\d+) damage to itself", 10))


@attack("Tighten Up")
def _opponent_discards(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    count = number_in_text(a, r"Your opponent discards (\d+|a) cards?", 1)
    discarded = core.discard_from_hand(ctx, count, player_id=ctx.opp_id)
    if discarded:
        ctx.log(f"{ctx.who(ctx.opp_id)} descartou {', '.join(c.name for c in discarded)}.")


def _status_bonus(ctx: Ctx, a: Attack) -> int:
    """ "If your opponent's Active Pokémon is X, this attack does N more damage"."""
    match = re.search(r"If your opponent's Active Pokémon is (\w+)", a.text)
    defender = ctx.opp.active
    status = STATUSES.get(match.group(1)) if match else None
    if defender is None or status is None or defender.status != status:
        return a.base_damage
    return a.base_damage + number_in_text(a, r"this attack does (\d+) more damage", 0)


@attack("Venoshock", estimate=_status_bonus)
def _venoshock(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _status_bonus(ctx, a))


def _round_damage(ctx: Ctx, a: Attack) -> int:
    """N para cada Pokémon seu em jogo com um ataque de mesmo nome."""
    same = sum(
        1 for m in ctx.me.all_pokemon_in_play() if any(x.name == a.name for x in m.card.attacks)
    )
    return a.base_damage * same


@attack("Round", estimate=_round_damage)
def _round(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _round_damage(ctx, a))


@attack("Unified Beatdown", estimate=lambda ctx, a: a.base_damage * len(ctx.me.bench))
def _per_benched(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage * len(ctx.me.bench))


def _per_own_energy(ctx: Ctx, a: Attack) -> int:
    """ "N more damage for each {X} Energy attached to this Pokémon"."""
    energy = energy_in_text(a, r"for each {SYMBOL} Energy attached to this Pokémon")
    if ctx.source is None or energy is None:
        return a.base_damage
    per = number_in_text(a, r"(\d+) more damage for each", 0)
    return a.base_damage + per * ctx.source.attached_energies.count(energy)


@attack("Hydro Pump", estimate=_per_own_energy)
def _hydro_pump(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _per_own_energy(ctx, a))


def _per_team_energy(ctx: Ctx, a: Attack) -> int:
    """ "N damage for each {X} Energy attached to all of your Pokémon"."""
    energy = energy_in_text(a, r"for each {SYMBOL} Energy attached to all of your Pokémon")
    count = sum(m.attached_energies.count(energy or "") for m in ctx.me.all_pokemon_in_play())
    return a.base_damage * count


@attack("Mega Symphonia", estimate=_per_team_energy)
def _mega_symphonia(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _per_team_energy(ctx, a))


def _per_tool(ctx: Ctx, a: Attack) -> int:
    return a.base_damage * sum(1 for m in ctx.me.all_pokemon_in_play() if m.tool is not None)


@attack("Gadget Show", estimate=_per_tool)
def _gadget_show(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, _per_tool(ctx, a))


@attack("Dragon Pulse")
def _mill_self(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    for _ in range(min(number_in_text(a, r"top (\d+) cards", 1), len(ctx.me.deck))):
        ctx.me.discard.append(ctx.me.deck.pop(0))


@attack("Regi Charge")
def _charge_from_discard(ctx: Ctx, a: Attack) -> None:
    """ "Attach up to N Basic {X} Energy cards from your discard pile to this Pokémon"."""
    hit_active(ctx, a.base_damage)
    energy = energy_in_text(a, r"Basic {SYMBOL} Energy")
    source = ctx.source
    if energy is not None and source is not None:
        count = number_in_text(a, r"up to (\d+)", 1)
        core.attach_from(
            ctx, ctx.me.discard, core.type_filter(energy), count, allowed=lambda m: m is source
        )


@attack("Crunch")
def _discard_defender_energy(ctx: Ctx, a: Attack) -> None:
    hit_active(ctx, a.base_damage)
    defender = ctx.exposed_defender
    if (
        defender is not None
        and defender.attached_energies
        and effect_happens(a, "discard an Energy from your opponent's Active")
    ):
        card = core.discard_energy(ctx.opp, defender, defender.attached_energies[0])
        if card is not None:
            ctx.log(f"{card.name} de {defender.card.name} foi descartada.")
