"""Compilador de texto de ataque: o texto impresso na carta vira passos.

O texto dos ataques segue poucas formas de frase ("Flip a coin. If heads,
...", "This attack does N more damage for each ...", "Discard an Energy from
this Pokémon."). Cada forma tem uma regra aqui (`@phrase`), com os valores
lidos da própria frase. Um ataque é compilado quando **todas** as frases do
texto são reconhecidas — e então funciona sem código próprio, inclusive em
cartas de sets futuros com a mesma redação. Ataques registrados à mão em
`attacks.ATTACKS` têm prioridade (casos singulares).

Execução: moedas, portões ("If tails, this attack does nothing") e
modificadores de dano primeiro; depois os efeitos "antes do dano"; o dano no
Ativo; e os demais efeitos, na ordem do texto. Escolhas internas são
heurísticas; um único alvo estratégico (Pokémon do oponente, banco próprio)
vira opção da ação, como nos ataques registrados à mão.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from pokemon_companion.cards_db.models import Attack, Card
from pokemon_companion.engine.effects import attacks, core, passives
from pokemon_companion.engine.effects.attacks import AttackSpec, Target
from pokemon_companion.engine.effects.cardinfo import (
    energy_type_of,
    has_rule_box,
    is_basic_energy,
    is_evolution,
    is_ex,
    is_tera,
    pokemon_type,
    stage_of,
    trainer_kind,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import PlayerState, PokemonInPlay, StatusCondition

# ---------------------------------------------------------------------------
# execução


@dataclass
class Run:
    ctx: Ctx
    attack: Attack
    damage: int
    estimate: bool = False
    heads: int = 0
    tails: int = 0
    cancelled: bool = False
    #: o dano principal vai no Ativo do oponente (False: foi para outro alvo)
    main_hit: bool = True
    weakness: bool = True
    resistance: bool = True
    ignore_effects: bool = False
    dealt: int = 0
    #: "for each card you discarded in this way"
    counted: int = 0

    @property
    def me(self) -> PlayerState:
        return self.ctx.me

    @property
    def opp(self) -> PlayerState:
        return self.ctx.opp

    @property
    def source(self) -> PokemonInPlay | None:
        return self.ctx.source

    @property
    def defender(self) -> PokemonInPlay | None:
        return self.ctx.opp.active

    def coin(self) -> bool:
        return core.coin()


#: o valor devolvido é ignorado
Act = Callable[[Run], object]


@dataclass
class Step:
    #: "pre" (moedas e dano), "before" (antes do dano), "after"
    phase: str
    act: Act
    #: alvo estratégico que vira opção da ação ("opp_any", "opp_bench", "own_bench")
    option: str | None = None
    #: regra e valores que geraram o passo (para auditar a compilação)
    desc: str = ""


@dataclass
class Program:
    steps: list[Step] = field(default_factory=list)

    @property
    def option(self) -> str | None:
        options = {s.option for s in self.steps if s.option}
        return options.pop() if len(options) == 1 else None

    def _pre(self, run: Run) -> None:
        for step in self.steps:
            if step.phase == "pre":
                step.act(run)

    def execute(self, ctx: Ctx, attack_: Attack) -> None:
        run = Run(ctx, attack_, attack_.base_damage)
        self._pre(run)
        if run.cancelled:
            ctx.log("O ataque não faz nada.")
            return
        for step in self.steps:
            if step.phase == "before":
                step.act(run)
        if run.main_hit:
            run.dealt = attacks.hit_active(
                ctx,
                max(run.damage, 0),
                weakness=run.weakness,
                resistance=run.resistance,
                ignore_effects=run.ignore_effects,
            )
        for step in self.steps:
            if step.phase == "after":
                step.act(run)

    def estimate(self, ctx: Ctx, attack_: Attack) -> int:
        run = Run(ctx, attack_, attack_.base_damage, estimate=True)
        self._pre(run)
        if run.cancelled and not run.heads:
            return attack_.base_damage // 2
        return max(run.damage, 0) if run.damage else attacks.EFFECT_ATTACK_VALUE

    def options(self, ctx: Ctx, attack_: Attack) -> list[Target | None]:
        kind = self.option
        if kind == "opp_any":
            return attacks.opp_any(ctx, attack_)
        if kind == "opp_bench":
            return attacks.opp_bench(ctx, attack_)
        if kind == "own_bench":
            return attacks.own_bench(ctx, attack_)
        return [None]


# ---------------------------------------------------------------------------
# vocabulário

WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
TOKENS = {
    "{N}": r"(\d+|an?|one|two|three|four|five|six)",
    "{E}": r"[\[{](\w)[\]}]",
    "{S}": r"(Asleep|Burned|Confused|Paralyzed|Poisoned)",
    "{X}": r"(.+?)",
}
STATUSES = {s.name.capitalize(): s for s in StatusCondition if s != StatusCondition.NONE}


def num(word: str) -> int:
    return WORDS[word.lower()] if word.lower() in WORDS else int(word)


def energy(symbol: str) -> str:
    return attacks.ENERGY_SYMBOLS.get(symbol.upper(), "Colorless")


def expand(pattern: str) -> str:
    for token, regex in TOKENS.items():
        pattern = pattern.replace(token, regex)
    return pattern


# ---------------------------------------------------------------------------
# registros: frases, condições, contagens e filtros de carta

Builder = Callable[..., list[Step] | Step | None]
PHRASES: list[tuple[re.Pattern[str], Builder]] = []


def phrase(*patterns: str) -> Callable[[Builder], Builder]:
    def decorator(fn: Builder) -> Builder:
        for pattern in patterns:
            PHRASES.append((re.compile(expand(pattern), re.IGNORECASE), fn))
        return fn

    return decorator


Predicate = Callable[[Run], bool]
CONDITIONS: list[tuple[re.Pattern[str], Callable[..., Predicate | None]]] = []


def condition(
    *patterns: str,
) -> Callable[[Callable[..., Predicate | None]], Callable[..., Predicate | None]]:
    def decorator(fn: Callable[..., Predicate | None]) -> Callable[..., Predicate | None]:
        for pattern in patterns:
            CONDITIONS.append((re.compile(expand(pattern), re.IGNORECASE), fn))
        return fn

    return decorator


Counter_ = Callable[[Run], int]
COUNTS: list[tuple[re.Pattern[str], Callable[..., Counter_ | None]]] = []


def count(
    *patterns: str,
) -> Callable[[Callable[..., Counter_ | None]], Callable[..., Counter_ | None]]:
    def decorator(fn: Callable[..., Counter_ | None]) -> Callable[..., Counter_ | None]:
        for pattern in patterns:
            COUNTS.append((re.compile(expand(pattern), re.IGNORECASE), fn))
        return fn

    return decorator


CardFilter = Callable[[Card], bool]
KINDS: list[tuple[re.Pattern[str], Callable[..., CardFilter | None]]] = []


def kind(
    *patterns: str,
) -> Callable[[Callable[..., CardFilter | None]], Callable[..., CardFilter | None]]:
    def decorator(fn: Callable[..., CardFilter | None]) -> Callable[..., CardFilter | None]:
        for pattern in patterns:
            KINDS.append((re.compile(expand(pattern), re.IGNORECASE), fn))
        return fn

    return decorator


def _lookup(
    table: Sequence[tuple[re.Pattern[str], Callable[..., object]]], text: str
) -> object | None:
    for pattern, build in table:
        match = pattern.fullmatch(text.strip())
        if match:
            result = build(*match.groups())
            if result is not None:
                return result
    return None


def parse_condition(text: str) -> Predicate | None:
    return _lookup(CONDITIONS, text)  # type: ignore[return-value]


def parse_count(text: str) -> Counter_ | None:
    return _lookup(COUNTS, text)  # type: ignore[return-value]


def parse_kind(text: str) -> CardFilter | None:
    text = re.sub(r"\bcards\b", "card", text.strip())
    return _lookup(KINDS, text)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# compilação


def _clean(text: str) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", text)  # lembretes entre parênteses
    return text.replace("’", "'").strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _clean(text))
    return [p.rstrip(".!").strip() for p in parts if p.rstrip(".!").strip()]


def _as_steps(result: list[Step] | Step | None) -> list[Step] | None:
    if result is None:
        return None
    return result if isinstance(result, list) else [result]


#: frase sendo compilada (regras que dependem do destino citado no fim dela,
#: como "... to this Pokémon", consultam aqui)
_current: list[str] = [""]


def parse_clause(text: str) -> list[Step] | None:
    previous = _current[0]
    _current[0] = text.strip().rstrip(",")
    try:
        return _parse_clause(_current[0])
    finally:
        _current[0] = previous


def _parse_clause(text: str) -> list[Step] | None:
    for pattern, build in PHRASES:
        match = pattern.fullmatch(text)
        if match:
            steps = _as_steps(build(*match.groups()))
            if steps is not None:
                values = ", ".join(g for g in match.groups() if g)
                for step in steps:
                    step.desc = step.desc or f"{build.__name__.strip('_')}({values})"
                return steps
    return _parse_compound(text)


def _wrap(steps: list[Step], guard: Callable[[Run], int], label: str) -> list[Step]:
    """Repete cada passo `guard(run)` vezes (0 = não faz)."""

    def make(step: Step) -> Step:
        def act(run: Run) -> None:
            for _ in range(guard(run)):
                step.act(run)

        return Step(step.phase, act, step.option, f"{label} → {step.desc}")

    return [make(step) for step in steps]


def _parse_compound(text: str) -> list[Step] | None:
    prefixes: list[tuple[str, Callable[[str, list[Step]], list[Step] | None]]] = [
        (r"If heads, (.+)", lambda _, s: _wrap(s, lambda r: int(r.heads > 0), "se cara")),
        (r"If tails, (.+)", lambda _, s: _wrap(s, lambda r: int(r.tails > 0), "se coroa")),
        (
            r"If both of them are heads, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.tails == 0), "se todas cara"),
        ),
        (
            r"If either of them is tails, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.tails > 0), "se alguma coroa"),
        ),
        (r"For each heads, (.+)", lambda _, s: _wrap(s, lambda r: r.heads, "por cara")),
        (
            r"Before doing damage, (.+)",
            lambda _, s: [Step("before", x.act, x.option, x.desc) for x in s],
        ),
        (r"Then, (.+)", lambda _, s: s),
        (r"You may (.+)", lambda _, s: s),
        (r"Also, (.+)", lambda _, s: s),
    ]
    for prefix, combine in prefixes:
        match = re.fullmatch(prefix, text, re.IGNORECASE)
        if match:
            inner = match.group(1)
            inner = inner[0].upper() + inner[1:]
            steps = parse_clause(inner)
            return combine(inner, steps) if steps is not None else None
    match = re.fullmatch(r"If (.+?), (.+)", text, re.IGNORECASE)
    if match:
        predicate = parse_condition(match.group(1))
        inner = match.group(2)
        steps = parse_clause(inner[0].upper() + inner[1:])
        if predicate is not None and steps is not None:
            pred = predicate
            return _wrap(steps, lambda r: int(pred(r)), f"se [{match.group(1)}]")
    for joiner in (", and ", " and "):
        if joiner in text:
            left, right = text.split(joiner, 1)
            first = parse_clause(left)
            second = parse_clause(right[0].upper() + right[1:]) if right else None
            if first is not None and second is not None:
                return first + second
    return None


@lru_cache(maxsize=4096)
def compile_text(text: str) -> Program | None:
    """Programa do texto, ou None se alguma frase não for reconhecida."""
    program = Program()
    for sentence in sentences(text):
        steps = parse_clause(sentence)
        if steps is None:
            return None
        program.steps.extend(steps)
    return program if program.steps else None


def explain(text: str) -> list[str]:
    """Passos compilados, em ordem de execução por fase (auditoria)."""
    program = compile_text(text)
    if program is None:
        return []
    order = {"pre": 0, "before": 1, "after": 2}
    return [f"{s.phase}: {s.desc}" for s in sorted(program.steps, key=lambda s: order[s.phase])]


def unknown_sentences(text: str) -> list[str]:
    return [s for s in sentences(text) if parse_clause(s) is None]


@lru_cache(maxsize=4096)
def compiled_spec(text: str) -> AttackSpec | None:
    program = compile_text(text)
    if program is None:
        return None
    options = program.options if program.option else None
    return AttackSpec(program.execute, options, estimate=program.estimate)


# ---------------------------------------------------------------------------
# frases: moedas e dano


def pre(act: Act) -> Step:
    return Step("pre", act)


def after(act: Act, option: str | None = None) -> Step:
    return Step("after", act, option)


def _flip(n: Callable[[Run], int]) -> Act:
    def act(run: Run) -> None:
        total = n(run)
        if run.estimate:
            run.heads, run.tails = total // 2 + total % 2, total // 2
            return
        run.heads = sum(run.coin() for _ in range(total))
        run.tails = total - run.heads
        run.ctx.log(f"{run.heads} cara(s) em {total} moeda(s).")

    return act


@phrase("Flip a coin")
def _flip_one() -> Step:
    return pre(_flip(lambda r: 1))


@phrase("Flip {N} coins")
def _flip_n(n: str) -> Step:
    return pre(_flip(lambda r: num(n)))


@phrase("Flip a coin for each {X}")
def _flip_each(what: str) -> Step | None:
    counter = parse_count(what)
    return pre(_flip(counter)) if counter else None


@phrase("Flip a coin until you get tails")
def _flip_until() -> Step:
    def act(run: Run) -> None:
        if run.estimate:
            run.heads, run.tails = 1, 1
            return
        run.heads = 0
        while run.coin():
            run.heads += 1
        run.tails = 1
        run.ctx.log(f"{run.heads} cara(s) antes da coroa.")

    return pre(act)


@phrase("This attack does nothing")
def _nothing() -> Step:
    def act(run: Run) -> None:
        run.cancelled = True

    return pre(act)


@phrase("This attack does {N} more damage", "You may do {N} more damage")
def _more(n: str) -> Step:
    def act(run: Run) -> None:
        run.damage += num(n)

    return pre(act)


@phrase("This attack does {N} less damage")
def _less(n: str) -> Step:
    def act(run: Run) -> None:
        run.damage -= num(n)

    return pre(act)


@phrase("This attack does {N} more damage for each {X}")
def _more_each(n: str, what: str) -> Step | None:
    counter = parse_count(what)
    if counter is None:
        return None

    def act(run: Run) -> None:
        run.damage += num(n) * counter(run)

    return pre(act)


@phrase("This attack does {N} less damage for each {X}")
def _less_each(n: str, what: str) -> Step | None:
    counter = parse_count(what)
    if counter is None:
        return None

    def act(run: Run) -> None:
        run.damage -= num(n) * counter(run)

    return pre(act)


@phrase("This attack does {N} damage for each {X}")
def _each(n: str, what: str) -> Step | None:
    counter = parse_count(what)
    if counter is None:
        return None

    def act(run: Run) -> None:
        run.damage = num(n) * counter(run)

    return pre(act)


@phrase("This attack does {N} damage to the new Active Pokémon")
def _damage_new_active(n: str) -> Step:
    def act(run: Run) -> None:
        run.damage = num(n)

    return pre(act)


@phrase(r"This attack's damage isn't affected by (.+)")
def _unaffected_by(what: str) -> Step | None:
    what = what.lower()
    known = {
        "weakness",
        "resistance",
        "weakness or resistance",
        "any effects on your opponent's active pokémon",
    }
    parts = [p.strip() for p in re.split(r",? or by |, or ", what)]
    if not all(p in known for p in parts):
        return None

    def act(run: Run) -> None:
        for part in parts:
            if "weakness" in part:
                run.weakness = False
            if "resistance" in part:
                run.resistance = False
            if "effects" in part:
                run.ignore_effects = True

    return pre(act)


def _hit_targets(amount: int, how_many: int, bench_only: bool) -> Act:
    def act(run: Run) -> None:
        ctx = run.ctx
        chosen = attacks.target_index(ctx, -2) if ctx.target else -2
        targets = attacks.best_damage_targets(ctx, amount, how_many, bench_only=bench_only)
        if how_many == 1 and chosen >= -1 and (chosen >= 0 or not bench_only):
            targets = [chosen]
        for position in targets:
            attacks.hit(ctx, position, amount)

    return act


@phrase("This attack does {N} damage to {N} of your opponent's Pokémon")
def _damage_any(n: str, k: str) -> list[Step]:
    amount, how_many = num(n), num(k)

    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = amount * how_many

    option = "opp_any" if how_many == 1 else None
    return [pre(replace), after(_hit_targets(amount, how_many, False), option)]


@phrase("This attack does {N} damage to {N} of your opponent's Benched Pokémon")
def _damage_bench(n: str, k: str) -> list[Step]:
    amount, how_many = num(n), num(k)

    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = amount * how_many

    option = "opp_bench" if how_many == 1 else None
    return [pre(replace), after(_hit_targets(amount, how_many, True), option)]


@phrase("This attack also does {N} damage to {N} of your opponent's Benched Pokémon")
def _also_bench(n: str, k: str) -> Step:
    amount, how_many = num(n), num(k)
    return after(_hit_targets(amount, how_many, True), "opp_bench" if how_many == 1 else None)


@phrase("This attack also does {N} damage to each of your opponent's Benched Pokémon")
def _also_each_opp_bench(n: str) -> Step:
    def act(run: Run) -> None:
        for i in range(len(run.opp.bench)):
            attacks.hit(run.ctx, i, num(n))

    return after(act)


def _self_damage(mons: Callable[[Run], list[PokemonInPlay]], amount: int) -> Act:
    def act(run: Run) -> None:
        for mon in mons(run):
            mon.damage_counters += amount
        run.ctx.log(f"{amount} de dano nos próprios Pokémon.")

    return act


@phrase("This attack also does {N} damage to each of your Benched Pokémon")
def _also_own_bench(n: str) -> Step:
    return after(_self_damage(lambda r: list(r.me.bench), num(n)))


@phrase("This attack also does {N} damage to {N} of your Benched Pokémon")
def _also_some_own_bench(n: str, k: str) -> Step:
    def pick(run: Run) -> list[PokemonInPlay]:
        return sorted(run.me.bench, key=lambda m: -m.current_hp)[: num(k)]

    return after(_self_damage(pick, num(n)))


@phrase("This attack also does {N} damage to each Benched Pokémon")
def _also_all_bench(n: str) -> Step:
    def act(run: Run) -> None:
        for i in range(len(run.opp.bench)):
            attacks.hit(run.ctx, i, num(n))
        _self_damage(lambda r: list(r.me.bench), num(n))(run)

    return after(act)


@phrase("This Pokémon also does {N} damage to itself", "This Pokémon does {N} damage to itself")
def _recoil(n: str) -> Step:
    return after(lambda run: attacks.recoil(run.ctx, num(n)))


# ---------------------------------------------------------------------------
# frases: condições especiais, travas e proteções


def _status_list(text: str) -> list[StatusCondition] | None:
    names = [p.strip() for p in re.split(r",\s*|\s+and\s+", text) if p.strip()]
    if not names or any(n.capitalize() not in STATUSES for n in names):
        return None
    chosen = [STATUSES[n.capitalize()] for n in names]
    # o motor guarda uma condição por Pokémon: a que trava o Pokémon vale mais
    return sorted(chosen, key=lambda s: s in (StatusCondition.POISONED, StatusCondition.BURNED))


@phrase(
    "Your opponent's Active Pokémon is now {X}",
    "The Defending Pokémon is now {X}",
    "Make your opponent's Active Pokémon {X}",
)
def _status(text: str) -> Step | None:
    statuses = _status_list(text)
    if statuses is None:
        return None
    return after(lambda run: attacks.status_on_defender(run.ctx, statuses[0]))


@phrase("This Pokémon is now {X}")
def _self_status(text: str) -> Step | None:
    statuses = _status_list(text)
    if statuses is None:
        return None

    def act(run: Run) -> None:
        mon = run.source
        if mon is not None and not passives.immune_to_special_conditions(run.ctx.state, mon):
            mon.status = statuses[0]

    return after(act)


@phrase(
    "During your opponent's next turn, the Defending Pokémon can't retreat",
    "During your opponent's next turn, that Pokémon can't retreat",
    "The Defending Pokémon can't retreat during your opponent's next turn",
)
def _no_retreat() -> Step:
    return after(lambda run: attacks.defending_cant_retreat(run.ctx))


def _defender_mark(mark: Callable[[PokemonInPlay, int], None]) -> Act:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is not None and not passives.prevents_attack_effects(
            run.ctx.state, run.ctx.opp_id, defender, True
        ):
            mark(defender, run.ctx.turn + 1)

    return act


@phrase(
    "During your opponent's next turn, the Defending Pokémon can't attack",
    "During your opponent's next turn, the Defending Pokémon can't use attacks",
    "During your opponent's next turn, that Pokémon can't attack",
)
def _defender_cant_attack() -> Step:
    def mark(mon: PokemonInPlay, turn: int) -> None:
        mon.cannot_attack_turn = turn

    return after(_defender_mark(mark))


@phrase(
    "During your opponent's next turn, attacks used by the Defending Pokémon do {N} less damage",
    "During your opponent's next turn, the Defending Pokémon's attacks do {N} less damage",
)
def _defender_weaker(n: str) -> Step:
    def mark(mon: PokemonInPlay, turn: int) -> None:
        mon.attack_debuff = (num(n), turn)

    return after(_defender_mark(mark))


@phrase(
    "During your next turn, this Pokémon can't attack",
    "During your next turn, this Pokémon can't use attacks",
)
def _cant_attack() -> Step:
    return after(lambda run: attacks.cant_attack_next_turn(run.ctx))


@phrase("During your next turn, this Pokémon can't use {X}")
def _cant_use(name: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.blocked_attack = (name.strip(), run.ctx.turn + 2)

    return after(act)


@phrase("This Pokémon can't use {X} again until it leaves the Active Spot")
def _cant_use_again(name: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.blocked_attack = (name.strip(), run.ctx.turn + 2)

    return after(act)


@phrase("During your opponent's next turn, this Pokémon takes {N} less damage from attacks")
def _reduce(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.damage_reduction = (num(n), run.ctx.turn + 1)

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage from and effects of attacks done to "
    "this Pokémon",
    "Prevent all damage from and effects of attacks done to this Pokémon during your opponent's "
    "next turn",
)
def _protect() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.protected_turn = run.ctx.turn + 1

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks",
    "Prevent all damage done to this Pokémon by attacks during your opponent's next turn",
)
def _protect_damage() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.damage_reduction = (10_000, run.ctx.turn + 1)

    return after(act)


@phrase(
    "During your opponent's next turn, if this Pokémon is damaged by an attack, put {N} damage "
    "counters on the Attacking Pokémon"
)
def _retaliate(n: str) -> Step:
    return after(lambda run: attacks.retaliate_next_turn(run.ctx, num(n)))


@phrase(
    "During your opponent's next turn, they can't play any Item cards from their hand",
    "Your opponent can't play any Item cards from their hand during their next turn",
)
def _item_lock() -> Step:
    def act(run: Run) -> None:
        run.opp.items_blocked_turn = run.ctx.turn + 1

    return after(act)


# ---------------------------------------------------------------------------
# frases: cura, energia e cartas


@phrase("Heal {N} damage from this Pokémon")
def _heal_self(n: str) -> Step:
    return after(lambda run: run.source and core.heal(run.source, num(n)) and None)


@phrase("Heal all damage from this Pokémon")
def _heal_all_self() -> Step:
    return after(
        lambda run: run.source and core.heal(run.source, run.source.damage_counters) and None
    )


@phrase("Heal {N} damage from each of your Pokémon")
def _heal_each(n: str) -> Step:
    def act(run: Run) -> None:
        for mon in run.me.all_pokemon_in_play():
            core.heal(mon, num(n))

    return after(act)


@phrase(
    "Heal {N} damage from {N} of your Pokémon", "Heal {N} damage from {N} of your Benched Pokémon"
)
def _heal_some(n: str, k: str) -> Step:
    def act(run: Run) -> None:
        for mon in sorted(run.me.all_pokemon_in_play(), key=lambda m: -m.damage_counters)[: num(k)]:
            core.heal(mon, num(n))

    return after(act)


@phrase(
    "Heal from this Pokémon the same amount of damage you did to your opponent's Active Pokémon"
)
def _drain() -> Step:
    return after(lambda run: run.source and core.heal(run.source, run.dealt) and None)


@phrase("Discard {N} Energy from this Pokémon", "Discard {N} {E} Energy from this Pokémon")
def _discard_own(n: str, symbol: str | None = None) -> Step:
    kind_ = energy(symbol) if symbol else None

    def act(run: Run) -> None:
        run.counted = attacks.discard_own_energy(run.ctx, num(n), kind_)

    return after(act)


@phrase("Discard all Energy from this Pokémon", "Discard all {E} Energy from this Pokémon")
def _discard_all_own(symbol: str | None = None) -> Step:
    kind_ = energy(symbol) if symbol else None

    def act(run: Run) -> None:
        mon = run.source
        if mon is not None:
            run.counted = attacks.discard_own_energy(run.ctx, len(mon.attached_energies), kind_)

    return after(act)


def _discard_from_defender(amount: int, special_only: bool) -> Act:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is None or passives.prevents_attack_effects(
            run.ctx.state, run.ctx.opp_id, defender, True
        ):
            return
        for _ in range(amount):
            pool = (
                [c.name for c in defender.special_energy_cards]
                if special_only
                else defender.attached_energies
            )
            if not pool:
                return
            card = core.discard_energy(run.opp, defender, pool[0])
            if card is not None:
                run.ctx.log(f"{card.name} de {defender.card.name} foi descartada.")

    return act


@phrase("Discard {N} Energy from your opponent's Active Pokémon")
def _discard_opp(n: str) -> Step:
    return after(_discard_from_defender(num(n), False))


@phrase("Discard {N} Special Energy from your opponent's Active Pokémon")
def _discard_opp_special(n: str) -> Step:
    return after(_discard_from_defender(num(n), True))


@phrase("Discard all Energy from your opponent's Active Pokémon")
def _discard_opp_all() -> Step:
    return after(_discard_from_defender(99, False))


def _on_bench(player: PlayerState) -> Callable[[PokemonInPlay], bool]:
    bench = list(player.bench)
    return lambda m: any(m is b for b in bench)


@phrase("Move {N} Energy from this Pokémon to {N} of your Benched Pokémon")
def _move_to_bench(n: str, _k: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        for _ in range(num(n)):
            if mon is None or not mon.attached_energies or not run.me.bench:
                return
            choice = core.least_useful_energy(mon)
            target = core.best_energy_target(
                run.ctx.state, run.ctx.player_id, choice, _on_bench(run.me)
            )
            if target is None:
                return
            core.attach_energy_card(target, core.detach_energy(mon, choice))

    return after(act)


@phrase("Put {N} Energy attached to this Pokémon into your hand")
def _energy_to_hand(n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        for _ in range(num(n)):
            if mon is None or not mon.attached_energies:
                return
            run.me.hand.append(core.detach_energy(mon, core.least_useful_energy(mon)))

    return after(act)


def _destination(sentence: str) -> str:
    """Onde a energia vai, pelo fim da frase: "self", "bench" ou "any"."""
    tail = sentence.rsplit(" to ", 1)[-1]
    if "this Pokémon" in tail:
        return "self"
    return "bench" if "Benched" in tail else "any"


def _attach(source: Callable[[Run], list[Card]], what: CardFilter, amount: int, where: str) -> Act:
    def act(run: Run) -> None:
        me, bench = run.source, run.me.bench
        allowed: Callable[[PokemonInPlay], bool] | None = None
        if where == "self":
            allowed = lambda m: m is me  # noqa: E731
        elif where == "bench":
            allowed = lambda m: any(m is b for b in bench)  # noqa: E731
        pile = source(run)
        core.attach_from(run.ctx, pile, what, amount, allowed=allowed)
        if pile is run.me.deck:
            core.shuffle_deck(run.me)

    return act


@phrase(
    "Attach {X} from your discard pile to this Pokémon",
    "Attach {X} from your discard pile to your Pokémon in any way you like",
    "Attach {X} from your discard pile to your Benched Pokémon in any way you like",
    "Attach {X} from your discard pile to {N} of your Pokémon",
    "Attach {X} from your discard pile to {N} of your Benched Pokémon",
)
def _attach_discard(what: str, *_: str) -> Step | None:
    return _energy_search(what, lambda r: r.me.discard, _destination(_current[0]))


@phrase(
    "Search your deck for {X} and attach {X} to this Pokémon",
    "Search your deck for {X} and attach {X} to your Pokémon in any way you like",
    "Search your deck for {X} and attach {X} to your Benched Pokémon in any way you like",
    "Search your deck for {X} and attach {X} to {N} of your Pokémon",
    "Search your deck for {X} and attach {X} to {N} of your Benched Pokémon",
)
def _attach_deck(what: str, *_: str) -> Step | None:
    return _energy_search(what, lambda r: r.me.deck, _destination(_current[0]))


def _energy_search(what: str, source: Callable[[Run], list[Card]], where: str) -> Step | None:
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    if not match:
        return None
    card_filter = parse_kind(match.group(2))
    if card_filter is None:
        return None
    return after(_attach(source, card_filter, num(match.group(1)), where))


@phrase("Draw {N} cards", "Draw {N} card")
def _draw(n: str) -> Step:
    return after(lambda run: core.draw(run.me, num(n)) and None)


@phrase("Draw cards until you have {N} cards in your hand")
def _draw_until(n: str) -> Step:
    return after(lambda run: core.draw_until(run.me, num(n)) and None)


@phrase("Each player draws {N} cards", "Each player draws {N} card")
def _both_draw(n: str) -> Step:
    def act(run: Run) -> None:
        core.draw(run.me, num(n))
        core.draw(run.opp, num(n))

    return after(act)


@phrase("Shuffle your hand into your deck")
def _shuffle_hand() -> Step:
    return after(lambda run: core.shuffle_hand_into_deck(run.me))


@phrase("Shuffle your deck", "Shuffle the other cards back into your deck")
def _shuffle() -> Step:
    return after(lambda run: core.shuffle_deck(run.me))


@phrase(
    "Your opponent reveals their hand",
    "Look at your opponent's hand",
    "Your opponent reveals their hand to you",
    "If you go first, you can use this attack during your first turn",
)
def _no_op() -> Step:
    return after(lambda run: None)


def _mill(player: Callable[[Run], PlayerState], amount: int) -> Act:
    def act(run: Run) -> None:
        target = player(run)
        for _ in range(min(amount, len(target.deck))):
            target.discard.append(target.deck.pop(0))

    return act


@phrase(
    "Discard the top card of your opponent's deck",
    "Discard the top {N} cards of your opponent's deck",
)
def _mill_opp(n: str = "1") -> Step:
    return after(_mill(lambda r: r.opp, num(n)))


@phrase("Discard the top card of your deck", "Discard the top {N} cards of your deck")
def _mill_self(n: str = "1") -> Step:
    return after(_mill(lambda r: r.me, num(n)))


@phrase(
    "Discard a random card from your opponent's hand",
    "Discard {N} random cards from your opponent's hand",
)
def _random_discard(n: str = "1") -> Step:
    def act(run: Run) -> None:
        import random

        for _ in range(min(num(n), len(run.opp.hand))):
            card = random.choice(run.opp.hand)
            run.opp.hand.remove(card)
            run.opp.discard.append(card)
            run.ctx.log(f"{run.ctx.who(run.ctx.opp_id)} descartou {card.name}.")

    return after(act)


@phrase(
    "Your opponent discards {N} cards from their hand",
    "Your opponent discards {N} card from their hand",
)
def _opp_discards(n: str) -> Step:
    return after(
        lambda run: core.discard_from_hand(run.ctx, num(n), player_id=run.ctx.opp_id) and None
    )


@phrase(
    "Search your deck for {X}, reveal {X}, and put {X} into your hand",
    "Search your deck for {X} and put {X} into your hand",
)
def _search_hand(what: str, *_: str) -> Step | None:
    return _search(what, "hand")


@phrase("Search your deck for {X} and put {X} onto your Bench")
def _search_bench(what: str, *_: str) -> Step | None:
    return _search(what, "bench")


def _search(what: str, destination: str) -> Step | None:
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    if not match:
        return None
    card_filter = parse_kind(match.group(2))
    if card_filter is None:
        return None
    amount = num(match.group(1))
    return after(lambda run: core.search_deck(run.ctx, card_filter, amount, destination) and None)


@phrase(
    "Put {X} from your discard pile into your hand",
    "Shuffle {X} from your discard pile into your deck",
)
def _recover(what: str) -> Step | None:
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    if not match:
        return None
    card_filter = parse_kind(match.group(2))
    if card_filter is None:
        return None
    amount = num(match.group(1))
    destination = "deck" if _current[0].lower().startswith("shuffle") else "hand"
    return after(
        lambda run: core.recover_from_discard(run.ctx, card_filter, amount, destination) and None
    )


# ---------------------------------------------------------------------------
# frases: trocas, Estádio, contadores e o próprio Pokémon


@phrase("Switch this Pokémon with {N} of your Benched Pokémon")
def _switch_self(_n: str) -> Step:
    return after(lambda run: attacks.switch_self(run.ctx), "own_bench")


@phrase("Switch in {N} of your opponent's Benched Pokémon to the Active Spot")
def _gust(_n: str) -> Step:
    return Step("before", lambda run: attacks.gust_opponent(run.ctx), "opp_bench")


@phrase(
    "Switch out your opponent's Active Pokémon to the Bench",
    "Your opponent switches their Active Pokémon with {N} of their Benched Pokémon",
)
def _push_out(*_: str) -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is not None and not passives.prevents_attack_effects(
            run.ctx.state, run.ctx.opp_id, defender, True
        ):
            core.opponent_switches_out(run.ctx.state, run.ctx.opp_id)

    return after(act)


@phrase("Discard a Stadium in play", "Discard the Stadium in play", "Discard that Stadium")
def _discard_stadium() -> Step:
    def act(run: Run) -> None:
        from pokemon_companion.engine.effects.trainers import discard_stadium

        discard_stadium(run.ctx.state)

    return after(act)


@phrase(
    "Discard all Pokémon Tools from your opponent's Active Pokémon",
    "Discard a Pokémon Tool from your opponent's Active Pokémon",
)
def _discard_tool() -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is not None and defender.tool is not None:
            run.opp.discard.append(defender.tool)
            run.ctx.log(f"{defender.tool.name} de {defender.card.name} foi descartada.")
            defender.tool = None

    return after(act)


def _counters_on(targets: Callable[[Run], list[PokemonInPlay]], amount: int) -> Act:
    def act(run: Run) -> None:
        for mon in targets(run):
            core.place_counters(run.ctx, run.ctx.opp_id, mon, amount)

    return act


@phrase("Put {N} damage counters on your opponent's Active Pokémon")
def _counters_active(n: str) -> Step:
    return after(_counters_on(lambda r: [r.defender] if r.defender else [], num(n)))


@phrase("Put {N} damage counters on each of your opponent's Pokémon")
def _counters_each(n: str) -> Step:
    return after(_counters_on(lambda r: r.opp.all_pokemon_in_play(), num(n)))


@phrase("Put {N} damage counters on each of your opponent's Benched Pokémon")
def _counters_each_bench(n: str) -> Step:
    return after(_counters_on(lambda r: list(r.opp.bench), num(n)))


@phrase("Put {N} damage counters on {N} of your opponent's Pokémon")
def _counters_one(n: str, k: str) -> Step:
    def act(run: Run) -> None:
        for _ in range(num(k)):
            target = core.best_counter_target(run.ctx.state, run.ctx.opp_id, num(n))
            if target is not None:
                core.place_counters(run.ctx, run.ctx.opp_id, target, num(n))

    return after(act)


@phrase(
    "Put {N} damage counters on your opponent's Pokémon in any way you like",
    "Put {N} damage counters on your opponent's Benched Pokémon in any way you like",
)
def _counters_spread(n: str) -> Step:
    bench_only = "Benched" in _current[0]
    return after(lambda run: core.spread_counters(run.ctx, run.ctx.opp_id, num(n), bench_only))


def _leave_play(destination: str) -> Act:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or run.me.active is not mon:
            return
        cards = mon.all_cards() + [
            core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
        ]
        run.me.active = None
        if destination == "hand":
            run.me.hand.extend(cards)
        else:
            run.me.deck.extend(cards)
            core.shuffle_deck(run.me)
        run.ctx.log(f"{mon.card.name} saiu de jogo.")

    return act


@phrase("Put this Pokémon and all attached cards into your hand")
def _to_hand() -> Step:
    return after(_leave_play("hand"))


@phrase("Shuffle this Pokémon and all attached cards into your deck")
def _to_deck() -> Step:
    return after(_leave_play("deck"))


@phrase(
    "Search your deck for a card that evolves from this Pokémon and put it onto this Pokémon to evolve it"
)
def _evolve_self() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        card = next((c for c in run.me.deck if c.evolves_from == mon.card.name), None)
        if card is not None:
            run.me.deck.remove(card)
            core.evolve_into(run.ctx.state, run.me, mon, card)
            run.ctx.log(f"{mon.card.name} evoluiu para {card.name}.")
        core.shuffle_deck(run.me)

    return after(act)


# ---------------------------------------------------------------------------
# condições ("If ..., ...")


def _defender_is(test: Callable[[PokemonInPlay], bool]) -> Predicate:
    return lambda run: run.defender is not None and test(run.defender)


@condition(r"your opponent's Active Pokémon is a Pokémon ex")
def _c_ex() -> Predicate:
    return _defender_is(lambda m: is_ex(m.card))


@condition(r"your opponent's Active Pokémon is a Pokémon ex or Pokémon V")
def _c_ex_v() -> Predicate:
    return _defender_is(lambda m: is_ex(m.card) or "V" in m.card.subtypes)


@condition(r"your opponent's Active Pokémon has a Rule Box")
def _c_rule_box() -> Predicate:
    return _defender_is(lambda m: has_rule_box(m.card))


@condition(r"your opponent's Active Pokémon is an Evolution Pokémon")
def _c_evolution() -> Predicate:
    return _defender_is(lambda m: is_evolution(m.card))


@condition(r"your opponent's Active Pokémon is a Basic Pokémon")
def _c_basic() -> Predicate:
    return _defender_is(lambda m: stage_of(m.card) == "Basic")


@condition(r"your opponent's Active Pokémon is a Stage 2 Pokémon")
def _c_stage2() -> Predicate:
    return _defender_is(lambda m: stage_of(m.card) == "Stage 2")


@condition(r"your opponent's Active Pokémon is a {E} Pokémon")
def _c_type(symbol: str) -> Predicate:
    return _defender_is(lambda m: pokemon_type(m.card) == energy(symbol))


@condition(r"your opponent's Active Pokémon is {S}", r"the Defending Pokémon is {S}")
def _c_status(name: str) -> Predicate:
    return _defender_is(lambda m: m.status == STATUSES[name.capitalize()])


@condition(r"your opponent's Active Pokémon is affected by a Special Condition")
def _c_any_status() -> Predicate:
    return _defender_is(lambda m: m.status != StatusCondition.NONE)


@condition(r"your opponent's Active Pokémon already has any damage counters on it")
def _c_damaged() -> Predicate:
    return _defender_is(lambda m: m.damage_counters > 0)


@condition(r"your opponent's Active Pokémon has an Ability")
def _c_ability() -> Predicate:
    return _defender_is(lambda m: bool(m.card.abilities))


@condition(r"your opponent's Active Pokémon has any Special Energy attached")
def _c_special() -> Predicate:
    return _defender_is(lambda m: bool(m.special_energy_cards))


@condition(r"your opponent's Active Pokémon has a Pokémon Tool attached")
def _c_opp_tool() -> Predicate:
    return _defender_is(lambda m: m.tool is not None)


@condition(r"a Stadium is in play")
def _c_stadium() -> Predicate:
    return lambda run: run.ctx.state.stadium is not None


@condition(
    r"any of your Pokémon were Knocked Out by damage from an attack during your opponent's last turn",
    r"any of your Pokémon were Knocked Out during your opponent's last turn",
)
def _c_revenge() -> Predicate:
    return lambda run: run.me.knocked_out_turn == run.ctx.turn - 1


@condition(r"this Pokémon has a Pokémon Tool attached")
def _c_tool() -> Predicate:
    return lambda run: run.source is not None and run.source.tool is not None


@condition(r"this Pokémon has any damage counters on it")
def _c_self_damaged() -> Predicate:
    return lambda run: run.source is not None and run.source.damage_counters > 0


@condition(r"this Pokémon has no damage counters on it")
def _c_self_healthy() -> Predicate:
    return lambda run: run.source is not None and run.source.damage_counters == 0


@condition(r"this Pokémon has any {E} Energy attached")
def _c_self_energy(symbol: str) -> Predicate:
    return lambda run: run.source is not None and energy(symbol) in run.source.attached_energies


@condition(
    r"this Pokémon has at least {N} extra Energy attached",
    r"this Pokémon has at least {N} extra {E} Energy attached",
)
def _c_extra(n: str, symbol: str | None = None) -> Predicate:
    def test(run: Run) -> bool:
        mon = run.source
        if mon is None:
            return False
        units = passives.provided_energy(run.ctx.state, run.ctx.player_id, mon)
        cost = passives.attack_cost(run.ctx.state, run.ctx.player_id, mon, run.attack)
        if symbol:
            units = [u for u in units if u in (energy(symbol), "Any")]
            cost = [c for c in cost if c == energy(symbol)]
        return len(units) - len(cost) >= num(n)

    return test


@condition(r"this Pokémon moved from your Bench to the Active Spot this turn")
def _c_moved() -> Predicate:
    return lambda run: run.source is not None and run.source.moved_to_active_turn == run.ctx.turn


@condition(r"your Benched Pokémon have any damage counters on them")
def _c_bench_damaged() -> Predicate:
    return lambda run: any(m.damage_counters for m in run.me.bench)


@condition(r"your opponent has {N} or fewer Prize cards remaining")
def _c_opp_prizes(n: str) -> Predicate:
    return lambda run: len(run.opp.prizes) <= num(n)


@condition(r"you have {N} or fewer Prize cards remaining")
def _c_my_prizes(n: str) -> Predicate:
    return lambda run: len(run.me.prizes) <= num(n)


@condition(r"you have more Prize cards remaining than your opponent")
def _c_behind() -> Predicate:
    return lambda run: len(run.me.prizes) > len(run.opp.prizes)


@condition(r"you have at least {N} {E} Energy in play")
def _c_energy_in_play(n: str, symbol: str) -> Predicate:
    return lambda run: (
        sum(m.attached_energies.count(energy(symbol)) for m in run.me.all_pokemon_in_play())
        >= num(n)
    )


@condition(r"you played a Supporter card from your hand during this turn")
def _c_supporter() -> Predicate:
    return lambda run: run.me.supporter_played_this_turn


@condition(r"you have no cards in your hand", r"you have no other cards in your hand")
def _c_empty_hand() -> Predicate:
    return lambda run: not run.me.hand


@condition(r"your opponent's Active Pokémon has more remaining HP than this Pokémon")
def _c_more_hp() -> Predicate:
    return lambda run: (
        run.source is not None
        and run.defender is not None
        and run.defender.current_hp > run.source.current_hp
    )


@condition(r"your opponent has {N} or more cards in their hand")
def _c_opp_hand(n: str) -> Predicate:
    return lambda run: len(run.opp.hand) >= num(n)


@condition(r"you have {N} or more cards in your hand")
def _c_my_hand(n: str) -> Predicate:
    return lambda run: len(run.me.hand) >= num(n)


@condition(r"you have {N} or more Energy in play")
def _c_energy_count(n: str) -> Predicate:
    return lambda run: sum(len(m.attached_energies) for m in run.me.all_pokemon_in_play()) >= num(n)


@condition(r"you have a Stadium in play")
def _c_own_stadium() -> Predicate:
    return lambda run: (
        run.ctx.state.stadium is not None and run.ctx.state.stadium_owner == run.ctx.player_id
    )


def _bench_has(test: Callable[[Card], bool]) -> Predicate:
    return lambda run: any(test(m.card) for m in run.me.bench)


@condition(r"you have any {E} Pokémon on your Bench")
def _c_bench_type(symbol: str) -> Predicate:
    return _bench_has(lambda c: pokemon_type(c) == energy(symbol))


@condition(r"you have any Stage 2 {E} Pokémon on your Bench")
def _c_bench_stage2_type(symbol: str) -> Predicate:
    return _bench_has(lambda c: stage_of(c) == "Stage 2" and pokemon_type(c) == energy(symbol))


@condition(r"you have any Tera Pokémon on your Bench")
def _c_bench_tera() -> Predicate:
    return _bench_has(is_tera)


@condition(r"you have (?:an?|any) {X} on your Bench", r"you have (?:an?|any) {X} in play")
def _c_named(text: str) -> Predicate | None:
    name = card_name(text)
    if name is None:
        return None
    on_bench = "Bench" in _current[0]

    def test(run: Run) -> bool:
        mons = run.me.bench if on_bench else run.me.all_pokemon_in_play()
        return any(m.card.name == name for m in mons)

    return test


# ---------------------------------------------------------------------------
# contagens ("for each ...")


@count(r"heads")
def _n_heads() -> Counter_:
    return lambda run: run.heads


@count(r"Energy attached to this Pokémon")
def _n_own_energy() -> Counter_:
    return lambda run: len(run.source.attached_energies) if run.source else 0


@count(r"{E} Energy attached to this Pokémon")
def _n_own_typed(symbol: str) -> Counter_:
    return lambda run: run.source.attached_energies.count(energy(symbol)) if run.source else 0


@count(
    r"Energy attached to your opponent's Active Pokémon",
    r"Energy attached to the Defending Pokémon",
)
def _n_opp_energy() -> Counter_:
    return lambda run: len(run.defender.attached_energies) if run.defender else 0


@count(r"Energy attached to all of your Pokémon")
def _n_team_energy() -> Counter_:
    return lambda run: sum(len(m.attached_energies) for m in run.me.all_pokemon_in_play())


@count(r"{E} Energy attached to all of your Pokémon")
def _n_team_typed(symbol: str) -> Counter_:
    return lambda run: sum(
        m.attached_energies.count(energy(symbol)) for m in run.me.all_pokemon_in_play()
    )


@count(r"Energy attached to all of your opponent's Pokémon")
def _n_opp_team_energy() -> Counter_:
    return lambda run: sum(len(m.attached_energies) for m in run.opp.all_pokemon_in_play())


@count(r"damage counter on this Pokémon")
def _n_own_counters() -> Counter_:
    return lambda run: core.damage_counters_on(run.source) if run.source else 0


@count(
    r"damage counter on your opponent's Active Pokémon", r"damage counter on the Defending Pokémon"
)
def _n_opp_counters() -> Counter_:
    return lambda run: core.damage_counters_on(run.defender) if run.defender else 0


@count(r"damage counter on all of your Benched Pokémon", r"damage counter on your Benched Pokémon")
def _n_bench_counters() -> Counter_:
    return lambda run: sum(core.damage_counters_on(m) for m in run.me.bench)


@count(r"Prize card your opponent has taken")
def _n_opp_taken() -> Counter_:
    return lambda run: run.ctx.state.prize_count - len(run.opp.prizes)


@count(r"Prize card you have taken")
def _n_my_taken() -> Counter_:
    return lambda run: run.ctx.state.prize_count - len(run.me.prizes)


@count(r"of your Pokémon in play", r"Pokémon you have in play")
def _n_my_pokemon() -> Counter_:
    return lambda run: len(run.me.all_pokemon_in_play())


@count(r"of your Benched Pokémon", r"Pokémon on your Bench")
def _n_my_bench() -> Counter_:
    return lambda run: len(run.me.bench)


@count(r"of your opponent's Benched Pokémon", r"Pokémon on your opponent's Bench")
def _n_opp_bench() -> Counter_:
    return lambda run: len(run.opp.bench)


@count(r"Benched Pokémon")
def _n_all_bench() -> Counter_:
    return lambda run: len(run.me.bench) + len(run.opp.bench)


@count(r"of your {E} Pokémon in play", r"{E} Pokémon you have in play")
def _n_typed_pokemon(symbol: str) -> Counter_:
    return lambda run: sum(
        1 for m in run.me.all_pokemon_in_play() if pokemon_type(m.card) == energy(symbol)
    )


@count(r"card in your hand")
def _n_hand() -> Counter_:
    return lambda run: len(run.me.hand)


@count(r"card in your opponent's hand")
def _n_opp_hand() -> Counter_:
    return lambda run: len(run.opp.hand)


@count(r"Special Condition affecting your opponent's Active Pokémon")
def _n_conditions() -> Counter_:
    return lambda run: int(run.defender is not None and run.defender.status != StatusCondition.NONE)


@count(r"card you discarded in this way", r"Energy card you discarded in this way")
def _n_discarded() -> Counter_:
    return lambda run: run.counted


@count(r"Pokémon Tool attached to all of your Pokémon")
def _n_tools() -> Counter_:
    return lambda run: sum(1 for m in run.me.all_pokemon_in_play() if m.tool is not None)


@count(r"{X} in your discard pile")
def _n_discard(what: str) -> Counter_ | None:
    card_filter = parse_kind(re.sub(r"^(?:an?|each) ", "", what))
    if card_filter is None:
        return None
    return lambda run: sum(1 for c in run.me.discard if card_filter(c))


# ---------------------------------------------------------------------------
# tipos de carta ("Basic {W} Energy card", "Supporter card", "Pokémon"...)


def _is_pokemon(card: Card) -> bool:
    return card.is_pokemon


@kind(r"card")
def _k_any() -> CardFilter:
    return lambda card: True


@kind(r"Pokémon")
def _k_pokemon() -> CardFilter:
    return _is_pokemon


@kind(r"Basic Pokémon")
def _k_basic() -> CardFilter:
    return lambda card: card.is_basic


@kind(r"Evolution Pokémon")
def _k_evolution() -> CardFilter:
    return is_evolution


@kind(r"{E} Pokémon")
def _k_typed(symbol: str) -> CardFilter:
    return lambda card: card.is_pokemon and pokemon_type(card) == energy(symbol)


@kind(r"Basic {E} Pokémon")
def _k_basic_typed(symbol: str) -> CardFilter:
    return lambda card: card.is_basic and pokemon_type(card) == energy(symbol)


@kind(r"Pokémon ex")
def _k_ex() -> CardFilter:
    return lambda card: card.is_pokemon and is_ex(card)


@kind(r"Pokémon that doesn't have a Rule Box", r"Pokémon without a Rule Box")
def _k_no_rule_box() -> CardFilter:
    return lambda card: card.is_pokemon and not has_rule_box(card)


@kind(r"Basic Energy card", r"Basic Energy")
def _k_basic_energy() -> CardFilter:
    return is_basic_energy


@kind(r"Basic {E} Energy card", r"Basic {E} Energy", r"{E} Energy card")
def _k_basic_energy_typed(symbol: str) -> CardFilter:
    return lambda card: is_basic_energy(card) and energy_type_of(card) == energy(symbol)


@kind(r"Energy card", r"Energy")
def _k_energy() -> CardFilter:
    return lambda card: card.supertype.value == "Energy"


@kind(r"Trainer card")
def _k_trainer() -> CardFilter:
    return lambda card: card.supertype.value == "Trainer"


@kind(r"(Supporter|Item|Stadium|Tool) card", r"Pokémon (Tool) card")
def _k_trainer_kind(kind_name: str) -> CardFilter:
    wanted = kind_name.capitalize()
    return lambda card: trainer_kind(card) == wanted


@kind(r"Pokémon and Basic Energy card", r"Pokémon or Basic Energy card")
def _k_pokemon_or_energy() -> CardFilter:
    return lambda card: card.is_pokemon or is_basic_energy(card)


GENERIC_WORDS = {
    "Pokémon",
    "Energy",
    "Stadium",
    "Basic",
    "Evolution",
    "Supporter",
    "Item",
    "Trainer",
}


def card_name(text: str) -> str | None:
    """`text` se parecer nome de carta ("Froakie", "Iono's Bellibolt ex",
    "Mr. Mime"): palavras com maiúscula (ou "ex"), sem números nem ícones, e
    que não seja um termo genérico do jogo."""
    words = text.strip().split()
    if not words or any(w in GENERIC_WORDS for w in words):
        return None
    if any(not (w[0].isupper() or w == "ex") or re.search(r"[\d{}\[\]]", w) for w in words):
        return None
    return text.strip()


@kind(r"(.+)")
def _k_named(text: str) -> CardFilter | None:
    name = card_name(text)
    return None if name is None else (lambda card: card.name == name)
