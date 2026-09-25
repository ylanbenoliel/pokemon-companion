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

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import NamedTuple

from pokemon_companion.cards_db.models import Attack, Card
from pokemon_companion.engine.effects import attacks, core, pack, passives
from pokemon_companion.engine.effects.abilities import AbilitySpec
from pokemon_companion.engine.effects.attacks import AttackSpec, Target
from pokemon_companion.engine.effects.cardinfo import (
    TYPED_SPECIAL_ENERGIES,
    energy_type_of,
    fossil_pokemon,
    has_rule_box,
    is_basic_energy,
    is_evolution,
    is_ex,
    is_fossil_item,
    is_tera,
    pokemon_type,
    stage_of,
    trainer_kind,
)
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.effects.trainers import TrainerSpec
from pokemon_companion.engine.game_state import (
    PlayerId,
    PlayerState,
    PokemonInPlay,
    StatusCondition,
)

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
    #: a última ação opcional aconteceu ("If you do, ...")
    did: bool = False
    #: Pokémon citado depois como "that Pokémon"
    last_target: PokemonInPlay | None = None
    #: Pokémon escolhidos por "Choose N of ..." (usados pela frase seguinte)
    chosen: list[PokemonInPlay] = field(default_factory=list)
    #: carta escolhida por "Choose a random card ..." / filtro citado como "those cards"
    chosen_card: Card | None = None
    memo: Callable[[Card], bool] | None = None
    #: cartas compradas pelo efeito ("draw N cards instead")
    drawn: int = 0
    #: cartas escolhidas/vistas ("those cards", "you find there")
    picked: list[Card] = field(default_factory=list)
    #: condição especial escolhida ("that Special Condition")
    chosen_status: StatusCondition | None = None
    #: moeda de cada jogador ("each player flips a coin")
    coins_by_player: dict[PlayerId, bool] = field(default_factory=dict)

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

    @property
    def exposed_defender(self) -> PokemonInPlay | None:
        return self.ctx.exposed_defender

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
    # "Choose 1 or both:" + tópicos: a IA faz os dois (nunca é pior)
    text = re.sub(r"Choose (?:1|one) or both:\s*", "", text)
    text = re.sub(r"\s*•\s*", " ", text)
    text = _RULE_REMINDERS.sub("", text)
    # "Basic Fire Energy" (tipo por extenso, em algumas impressões) = "Basic {R} Energy"
    text = re.sub(
        r"\bBasic (Grass|Fire|Water|Lightning|Psychic|Fighting|Darkness|Metal) Energy",
        lambda m: f"Basic {{{_TYPE_SYMBOL[m.group(1)]}}} Energy",
        text,
    )
    return text.replace("’", "'").strip()


#: lembretes de regra do jogo impressos nas cartas (a pokemontcg.io os traz
#: em `rules`); não são efeitos da carta
_RULE_REMINDERS = re.compile(
    r"\s*(?:You may play only 1 Supporter card during your turn"
    r"|You may play any number of Item cards during your turn"
    r"|You may play only 1 Stadium card during your turn"
    r"|You can't have more than 1 ACE SPEC card in your deck"
    r"|Attach a Pokémon Tool to 1 of your Pokémon that doesn't already have a Pokémon Tool "
    r"attached"
    r"|This card stays in play when you play it"
    r"|Discard this card if another Stadium card comes into play)\.?"
)

_TYPE_SYMBOL = {
    "Grass": "G",
    "Fire": "R",
    "Water": "W",
    "Lightning": "L",
    "Psychic": "P",
    "Fighting": "F",
    "Darkness": "D",
    "Metal": "M",
}


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
#: texto inteiro do ataque em compilação (regras que olham outra frase)
_whole: list[str] = [""]


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
            r"If (?:either of them is|any of them are) heads, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.heads > 0), "se alguma cara"),
        ),
        (
            r"If either of them is tails, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.tails > 0), "se alguma coroa"),
        ),
        (r"For each heads, (.+)", lambda _, s: _wrap(s, lambda r: r.heads, "por cara")),
        (r"For each tails, (.+)", lambda _, s: _wrap(s, lambda r: r.tails, "por coroa")),
        (
            r"If all of them are heads, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.tails == 0 and r.heads > 0), "se todas cara"),
        ),
        (
            r"If both of them are tails, (.+)",
            lambda _, s: _wrap(s, lambda r: int(r.heads == 0), "se todas coroa"),
        ),
        (
            r"Before doing damage, (.+)",
            lambda _, s: [Step("before", x.act, x.option, x.desc) for x in s],
        ),
        (r"If you do, (.+)", lambda _, s: _wrap(s, lambda r: int(r.did), "se fez")),
        (r"If you did, (.+)", lambda _, s: _wrap(s, lambda r: int(r.did), "se fez")),
        (r"If they do, (.+)", lambda _, s: _wrap(s, lambda r: int(r.did), "se fez")),
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
    text = pack.rewrite(text)
    program = Program()
    _whole[0] = _clean(text)
    try:
        for sentence in sentences(text):
            steps = parse_clause(sentence)
            if steps is None:
                return None
            program.steps.extend(steps)
    finally:
        _whole[0] = ""
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

    return Step("before", act)


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
    names = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", text) if p.strip()]
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
        defender = run.exposed_defender
        if defender is not None:
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
        healed = sum(core.heal(mon, num(n)) for mon in run.me.all_pokemon_in_play())
        run.did = healed > 0

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
        defender = run.exposed_defender
        if defender is None:
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


MonTest = Callable[[Run, PokemonInPlay], bool]


def _qualifier(text: str) -> Callable[[PokemonInPlay], bool] | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    return pokemon_filter(text)


def _destination(sentence: str) -> MonTest | None:
    """Quem pode receber a energia, pelo fim da frase ("to this Pokémon",
    "to 1 of your Benched {L} Pokémon"...). None se não reconhecer."""
    tail = sentence.rsplit(" to ", 1)[-1].strip()
    if tail == "this Pokémon":
        return lambda run, m: m is run.source
    active = re.fullmatch(r"your Active (.*?)Pokémon", tail)
    if active:
        qualifier = _qualifier(active.group(1))
        if qualifier is None:
            return None
        only = qualifier
        return lambda run, m: m is run.me.active and only(m)
    match = re.fullmatch(
        expand(r"(?:{N} of )?your (Benched )?(.*?)Pokémon(?: in any way you like)?"), tail
    )
    if not match:
        return None
    qualifier = _qualifier(match.group(3))
    if qualifier is None:
        return None
    test = qualifier
    if match.group(2):
        return lambda run, m: any(m is b for b in run.me.bench) and test(m)
    return lambda run, m: test(m)


def _attach(
    source: Callable[[Run], list[Card]], what: CardFilter, amount: int, where: MonTest
) -> Act:
    def act(run: Run) -> None:
        pile = source(run)
        core.attach_from(run.ctx, pile, what, amount, allowed=lambda m: where(run, m))
        if pile is run.me.deck:
            core.shuffle_deck(run.me)

    return act


@phrase("Attach {X} from your discard pile to {X}")
def _attach_discard(what: str, *_: str) -> Step | None:
    return _energy_search(what, lambda r: r.me.discard, _destination(_current[0]))


@phrase("Search your deck for {X} and attach {X} to {X}")
def _attach_deck(what: str, *_: str) -> Step | None:
    return _energy_search(what, lambda r: r.me.deck, _destination(_current[0]))


def _energy_search(
    what: str, source: Callable[[Run], list[Card]], where: MonTest | None
) -> Step | None:
    if where is None:
        return None
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    if not match:
        return None
    card_filter = parse_kind(match.group(2))
    if card_filter is None:
        return None
    return after(_attach(source, card_filter, num(match.group(1)), where))


@phrase("Draw {N} cards", "Draw {N} card")
def _draw(n: str) -> Step:
    def act(run: Run) -> None:
        run.drawn += core.draw(run.me, num(n))

    return after(act)


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
    def act(run: Run) -> None:
        run.did = bool(run.opp.bench)
        attacks.gust_opponent(run.ctx)

    return Step("before", act, "opp_bench")


@phrase(
    "Switch out your opponent's Active Pokémon to the Bench",
    "Your opponent switches their Active Pokémon with {N} of their Benched Pokémon",
)
def _push_out(*_: str) -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
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


@kind(r"{E} Energy")
def _k_typed_energy(symbol: str) -> CardFilter:
    return lambda card: card.supertype.value == "Energy" and energy_type_of(card) == energy(symbol)


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


# ---------------------------------------------------------------------------
# lote 2: marcadores com duração, custos, escolhas e o resto da cauda longa


def _nocaute(run: Run, mon: PokemonInPlay | None) -> None:
    if mon is None:
        return
    mon.damage_counters = mon.max_hp
    run.ctx.log(f"{mon.card.name} foi nocauteado.")


def _defender_mark_now(mark: Callable[[PokemonInPlay], None]) -> Act:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            mark(defender)

    return act


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "Basic Pokémon",
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "Basic non-{E} Pokémon",
)
def _shield_basic(symbol: str | None = None) -> Step:
    kind_ = f"basic:{energy(symbol)}" if symbol else "basic"

    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = (kind_, run.ctx.turn + 1)

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "Pokémon ex"
)
def _shield_ex() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = ("ex", run.ctx.turn + 1)

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks if that "
    "damage is {N} or less"
)
def _shield_small(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = (f"le:{num(n)}", run.ctx.turn + 1)

    return after(act)


@phrase("During your opponent's next turn, this Pokémon has no Weakness")
def _no_weakness() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.no_weakness_turn = run.ctx.turn + 1

    return after(act)


@phrase(
    "During your next turn, attacks used by this Pokémon do {N} more damage to your opponent's "
    "Active Pokémon",
    "During your next turn, this Pokémon's {X} attack does {N} more damage",
    "During your next turn, this Pokémon's {X} attack does {N} more damage to your opponent's "
    "Active Pokémon",
)
def _next_turn_bonus(*groups: str) -> Step:
    name, amount = ("*", groups[0]) if len(groups) == 1 else (groups[0].strip(), groups[1])

    def act(run: Run) -> None:
        if run.source is not None:
            run.source.attack_bonus = (name, num(amount), run.ctx.turn + 2)

    return after(act)


@phrase(
    "During Pokémon Checkup, put {N} damage counters on that Pokémon instead of {N}",
    "During Pokémon Checkup, place {N} damage counters on that Pokémon instead of {N}",
)
def _strong_poison(n: str, _base: str) -> Step:
    def mark(mon: PokemonInPlay) -> None:
        if mon.status == StatusCondition.POISONED:
            mon.poison_damage = 10 * num(n)

    return after(_defender_mark_now(mark))


def _doom(kind_: str) -> Act:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.doom = (kind_, run.ctx.turn + 1)

    return act


@phrase("At the end of your opponent's next turn, the Defending Pokémon will be Knocked Out")
def _doom_ko() -> Step:
    return after(_doom("ko"))


@phrase(
    "At the end of your opponent's next turn, discard the Defending Pokémon and all attached cards"
)
def _doom_discard() -> Step:
    return after(_doom("discard"))


@phrase("At the end of your opponent's next turn, put {N} damage counters on the Defending Pokémon")
def _doom_counters(n: str) -> Step:
    return after(_doom(f"counters:{num(n)}"))


# nocautes e remoções


@phrase("It is Knocked Out", "Knock Out your opponent's Active Pokémon")
def _ko_defender() -> Step:
    return after(lambda run: _nocaute(run, run.defender))


@phrase("Knock Out your opponent's Active Basic Pokémon")
def _ko_basic_defender() -> Step:
    def act(run: Run) -> None:
        if run.defender is not None and stage_of(run.defender.card) == "Basic":
            _nocaute(run, run.defender)

    return after(act)


@phrase("Knock Out {N} of your opponent's Benched Basic Pokémon")
def _ko_benched_basic(n: str) -> Step:
    def act(run: Run) -> None:
        basics = [m for m in run.opp.bench if stage_of(m.card) == "Basic"]
        best = sorted(basics, key=lambda m: -core._prize_value(m.card))[: num(n)]
        for mon in best:
            _nocaute(run, mon)

    return after(act)


@phrase(
    "Choose a Pokémon in play that has the least HP remaining, except for this Pokémon, and it "
    "is Knocked Out"
)
def _ko_weakest() -> Step:
    def act(run: Run) -> None:
        mons = [
            m
            for m in run.me.all_pokemon_in_play() + run.opp.all_pokemon_in_play()
            if m is not run.source
        ]
        if not mons:
            return
        least = min(m.current_hp for m in mons)
        # empate: escolhe um do oponente, o que mais vale em prêmios
        tied = [m for m in mons if m.current_hp == least]
        theirs = [m for m in tied if any(m is o for o in run.opp.all_pokemon_in_play())]
        _nocaute(run, max(theirs or tied, key=lambda m: core._prize_value(m.card)))

    return after(act)


def _remove_from_play(player: PlayerState, mon: PokemonInPlay, to_deck: bool) -> None:
    cards = mon.all_cards() + [
        core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
    ]
    if player.active is mon:
        player.active = None
    else:
        del player.bench[core.index_of(player.bench, mon)]
    if to_deck:
        player.deck.extend(cards)
        core.shuffle_deck(player)
    else:
        player.discard.extend(cards)


@phrase("Discard your opponent's Active Pokémon and all attached cards")
def _discard_defender() -> Step:
    def act(run: Run) -> None:
        if run.defender is not None:
            run.ctx.log(f"{run.defender.card.name} foi descartado.")
            _remove_from_play(run.opp, run.defender, to_deck=False)

    return after(act)


@phrase(
    "Shuffle your opponent's Active Pokémon and all attached cards into their deck",
)
def _shuffle_defender() -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            run.ctx.log(f"{defender.card.name} voltou para o deck.")
            _remove_from_play(run.opp, defender, to_deck=True)

    return after(act)


@phrase("Choose {N} of your opponent's Benched Pokémon")
def _choose_opp_bench(n: str) -> Step:
    def act(run: Run) -> None:
        ranked = sorted(
            run.opp.bench,
            key=lambda m: (-core._prize_value(m.card), -len(m.attached_energies), m.current_hp),
        )
        run.chosen = ranked[: num(n)]

    return Step("before", act)


@phrase(
    "Shuffle those Pokémon and all attached cards into your opponent's deck",
    "Shuffle those Pokémon and all attached cards into their deck",
)
def _shuffle_chosen() -> Step:
    def act(run: Run) -> None:
        for mon in list(run.chosen):
            if any(mon is b for b in run.opp.bench):
                _remove_from_play(run.opp, mon, to_deck=True)
        run.chosen = []

    return after(act)


@phrase(
    "If you do, shuffle all of your opponent's Benched Pokémon that you didn't choose, and all "
    "cards attached to those Pokémon, into their deck"
)
def _shuffle_unchosen() -> Step:
    def act(run: Run) -> None:
        for mon in [m for m in run.opp.bench if not any(m is c for c in run.chosen)]:
            _remove_from_play(run.opp, mon, to_deck=True)

    return after(act)


@phrase("Take {N} Prize cards?", "Take {N} Prize card", "take {N} Prize card")
def _take_prize(n: str) -> Step:
    def act(run: Run) -> None:
        for _ in range(min(num(n), len(run.me.prizes))):
            run.me.hand.append(run.me.prizes.pop())
        run.ctx.log(f"{run.ctx.who()} pegou {num(n)} prêmio(s).")

    return after(act)


# custos e descartes ligados ao dano


def _per_discarded() -> int:
    """Dano por carta descartada; negativo quando multiplica ("does 90
    damage for each...", sem "more": o número impresso não soma)."""
    match = re.search(r"(\d+) (more )?damage for each (?:Energy )?card you discarded", _whole[0])
    if not match:
        return 0
    return int(match.group(1)) if match.group(2) else -int(match.group(1))


def _discards_needed(run: Run, per: int, available: int) -> int:
    """Quantas descartar para nocautear o Ativo (Fraqueza incluída); se não
    der para nocautear, todas."""
    defender = run.defender
    if not per or defender is None:
        return available
    remaining = defender.current_hp if per < 0 else max(defender.current_hp - run.damage, 0)
    attacker = run.source
    if attacker is not None and pokemon_type(attacker.card) in passives.weakness_types(
        run.ctx.state, run.ctx.opp_id, defender
    ):
        remaining = -(-remaining // 2)
    return min(available, max(-(-remaining // abs(per)), 1))


def _discard_for_damage(
    pile: Callable[[Run], list[Card] | None], what: CardFilter, limit: int
) -> Act:
    """Descarta até `limit` cartas para o dano da frase seguinte: só o
    necessário para nocautear o Ativo, senão o máximo."""
    per = _per_discarded()

    def act(run: Run) -> None:
        cards = pile(run)
        available = [c for c in (cards or []) if what(c)][:limit]
        wanted = len(available)
        wanted = _discards_needed(run, per, wanted)
        run.counted = wanted
        run.did = wanted > 0
        if run.estimate or cards is None:
            return
        for card in available[:wanted]:
            cards.remove(card)
            run.me.discard.append(card)

    return act


def _energy_cards_of(mon: PokemonInPlay | None) -> list[Card] | None:
    if mon is None:
        return None
    return [core.energy_card_from(mon, e) for e in mon.attached_energies]


def _discard_attached_for_damage(what: CardFilter, limit: int) -> Act:
    per = _per_discarded()

    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        pairs = [(e, core.energy_card_from(mon, e)) for e in mon.attached_energies]
        chosen = [e for e, card in pairs if what(card)][:limit]
        wanted = len(chosen)
        wanted = _discards_needed(run, per, wanted)
        run.counted = wanted
        run.did = wanted > 0
        if run.estimate:
            return
        for energy_name in chosen[:wanted]:
            run.me.discard.append(core.detach_energy(mon, energy_name))

    return act


def _among_your_pokemon(what: CardFilter, limit: int) -> Act:
    per = _per_discarded()

    def act(run: Run) -> None:
        donors = [
            (m, e)
            for m in [*run.me.bench, *([run.me.active] if run.me.active else [])]
            for e in m.attached_energies
            if what(core.energy_card_from(m, e))
        ][:limit]
        wanted = len(donors)
        wanted = _discards_needed(run, per, wanted)
        run.counted = wanted
        run.did = wanted > 0
        if run.estimate:
            return
        for mon, energy_name in donors[:wanted]:
            run.me.discard.append(core.detach_energy(mon, energy_name))

    return act


@phrase(
    "Discard up to {N} {X} from this Pokémon",
    "Discard any amount of {X} from this Pokémon",
)
def _discard_up_to_self(*groups: str) -> Step | None:
    limit, what = (num(groups[0]), groups[1]) if len(groups) == 2 else (99, groups[0])
    card_filter = parse_kind(what)
    return pre(_discard_attached_for_damage(card_filter, limit)) if card_filter else None


@phrase("Discard any amount of {X} from among your Pokémon")
def _discard_among(what: str) -> Step | None:
    card_filter = parse_kind(what)
    return pre(_among_your_pokemon(card_filter, 99)) if card_filter else None


@phrase("Discard up to {N} {X} from your hand", "Discard any number of {X} from your hand")
def _discard_hand_up_to(*groups: str) -> Step | None:
    limit, what = (num(groups[0]), groups[1]) if len(groups) == 2 else (99, groups[0])
    card_filter = parse_kind(what)
    return (
        pre(_discard_for_damage(lambda r: r.me.hand, card_filter, limit)) if card_filter else None
    )


@phrase("Discard {N} {X} from your hand")
def _discard_hand_cost(n: str, what: str) -> Step | None:
    """Custo: descarta exatamente N (se não tiver, `counted` fica menor)."""
    card_filter = parse_kind(what)
    if card_filter is None:
        return None
    amount = num(n)

    def act(run: Run) -> None:
        found = [c for c in run.me.hand if card_filter(c)]
        run.counted = len(found) if len(found) < amount else amount
        run.did = run.counted == amount
        if run.estimate or not run.did:
            return
        for card in found[:amount]:
            run.me.hand.remove(card)
            run.me.discard.append(card)

    return pre(act)


@phrase("If you can't discard {N} cards in this way, this attack does nothing")
def _cost_not_paid(n: str) -> Step:
    def act(run: Run) -> None:
        if run.counted < num(n):
            run.cancelled = True

    return pre(act)


@phrase("If you can't, this attack does nothing")
def _cost_not_done() -> Step:
    def act(run: Run) -> None:
        if not run.did:
            run.cancelled = True

    return pre(act)


@phrase("Discard {N} {X} Energy from this Pokémon")
def _discard_named_energy(n: str, name: str) -> Step | None:
    wanted = f"{name.strip()} Energy"
    if name.strip().startswith(("{", "[")) or card_name(wanted) is None:
        return None

    def act(run: Run) -> None:
        mon = run.source
        run.did = False
        for _ in range(num(n)):
            if mon is None or wanted not in mon.attached_energies:
                return
            run.me.discard.append(core.detach_energy(mon, wanted))
            run.did = True

    return after(act)


@phrase(
    "You may put {N} {E} Energy attached to this Pokémon into your hand and have this attack do "
    "{N} more damage"
)
def _energy_back_for_damage(n: str, symbol: str, bonus: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mon = run.source
        if mon is None or mon.attached_energies.count(kind_) < num(n):
            return
        run.damage += num(bonus)
        if run.estimate:
            return
        for _ in range(num(n)):
            run.me.hand.append(core.detach_energy(mon, kind_))

    return pre(act)


# energia de outros lugares


@phrase(
    "For each of your Benched Pokémon, search your deck for a Basic {E} Energy card and attach it "
    "to that Pokémon"
)
def _energy_each_bench(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        for mon in list(run.me.bench):
            card = next(
                (c for c in run.me.deck if is_basic_energy(c) and energy_type_of(c) == kind_),
                None,
            )
            if card is None:
                break
            run.me.deck.remove(card)
            core.attach_energy_card(mon, card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase("Attach any number of {X} from your hand to {X}", "Attach {X} from your hand to {X}")
def _attach_from_hand(what: str, *_: str) -> Step | None:
    many = _current[0].lower().startswith("attach any number")
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    amount, text = (99, what) if many or not match else (num(match.group(1)), match.group(2))
    card_filter = parse_kind(text)
    if card_filter is None:
        return None
    where = _destination(_current[0])
    if where is None:
        return None

    def act(run: Run) -> None:
        before = {id(m): len(m.attached_energies) for m in run.me.all_pokemon_in_play()}
        _attach(lambda r: r.me.hand, card_filter, amount, where)(run)
        changed = [
            m
            for m in run.me.all_pokemon_in_play()
            if len(m.attached_energies) > before.get(id(m), 0)
        ]
        run.did = bool(changed)
        run.last_target = changed[0] if changed else None

    return after(act)


@phrase("Heal all damage from that Pokémon")
def _heal_that() -> Step:
    def act(run: Run) -> None:
        if run.last_target is not None:
            core.heal(run.last_target, run.last_target.damage_counters)

    return after(act)


@phrase(
    "Look at the top {N} cards of your deck and attach any number of Energy cards you find there "
    "to your Pokémon in any way you like"
)
def _energy_from_top(n: str) -> Step:
    def act(run: Run) -> None:
        top = run.me.deck[: num(n)]
        energies = [c for c in top if c.supertype.value == "Energy"]
        for card in energies:
            run.me.deck.remove(card)
        core.attach_from(run.ctx, energies, lambda c: True, len(energies))
        run.me.deck.extend(energies)  # sem alvo: volta para o deck
        core.shuffle_deck(run.me)

    return after(act)


@phrase("If you attached Energy to a Pokémon in this way, {X}")
def _if_attached(rest: str) -> list[Step] | None:
    steps = parse_clause(rest[0].upper() + rest[1:])
    return None if steps is None else _wrap(steps, lambda r: int(r.did), "se anexou")


# cura em grupos


@phrase("Heal {N} damage from each Pokémon")
def _heal_everyone(n: str) -> Step:
    def act(run: Run) -> None:
        for mon in run.me.all_pokemon_in_play() + run.opp.all_pokemon_in_play():
            core.heal(mon, num(n))

    return after(act)


@phrase("Heal {N} damage from each of your {X}")
def _heal_each_kind(n: str, what: str) -> Step | None:
    what = what.strip()
    tests: dict[str, Callable[[PokemonInPlay, Run], bool]] = {
        "Basic Pokémon": lambda m, r: stage_of(m.card) == "Basic",
        "Benched Pokémon": lambda m, r: any(m is b for b in r.me.bench),
        "Evolution Pokémon": lambda m, r: is_evolution(m.card),
    }
    typed = re.fullmatch(expand("{E} Pokémon"), what)
    if typed:
        kind_ = energy(typed.group(1))
        tests[what] = lambda m, r: pokemon_type(m.card) == kind_
    test = tests.get(what)
    if test is None:
        return None

    def act(run: Run) -> None:
        for mon in run.me.all_pokemon_in_play():
            if test(mon, run):
                core.heal(mon, num(n))

    return after(act)


# dano em vários alvos


@phrase("This attack does {N} damage to each of your opponent's Benched Pokémon")
def _only_bench_each(n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n) * len(run.opp.bench)

    def act(run: Run) -> None:
        for i in range(len(run.opp.bench)):
            attacks.hit(run.ctx, i, num(n))

    return [pre(replace), after(act)]


@phrase("This attack does {N} damage to each of your opponent's Pokémon")
def _each_opp(n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.damage = num(n)

    def act(run: Run) -> None:
        for i in range(len(run.opp.bench)):
            attacks.hit(run.ctx, i, num(n))

    return [pre(replace), after(act)]


@phrase("This attack does {N} damage to each of {N} of your opponent's Pokémon")
def _each_of_n(n: str, k: str) -> list[Step] | Step | None:
    return _damage_any(n, k)


@phrase("This attack also does {N} damage to each of your opponent's Benched Pokémon for each {X}")
def _bench_each_scaled(n: str, what: str) -> Step | None:
    counter = parse_count(what)
    if counter is None:
        return None

    def act(run: Run) -> None:
        amount = num(n) * counter(run)
        for i in range(len(run.opp.bench)):
            attacks.hit(run.ctx, i, amount)

    return after(act)


@phrase(
    "This attack does {N} damage to {N} of your opponent's Pokémon for each {X}",
)
def _any_scaled(n: str, k: str, what: str) -> list[Step] | None:
    counter = parse_count(what)
    if counter is None:
        return None
    how_many = num(k)

    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n) * counter(run)

    def act(run: Run) -> None:
        amount = num(n) * counter(run)
        _hit_targets(amount, how_many, False)(run)

    return [pre(replace), after(act, "opp_any" if how_many == 1 else None)]


@phrase(
    "This attack also does {N} damage to {N} of your opponent's Benched Pokémon that has any "
    "damage counters on it"
)
def _bench_damaged(n: str, k: str) -> Step:
    def act(run: Run) -> None:
        damaged = [i for i, m in enumerate(run.opp.bench) if m.damage_counters]
        ranked = sorted(damaged, key=lambda i: run.opp.bench[i].current_hp)[: num(k)]
        for i in ranked:
            attacks.hit(run.ctx, i, num(n))

    return after(act)


@phrase("For each of your opponent's Pokémon, flip a coin")
def _flip_per_opp() -> Step:
    def act(run: Run) -> None:
        positions = core.positions(run.opp)
        run.chosen = [
            core.mon_at(run.opp, p)  # type: ignore[misc]
            for p in positions
            if (run.estimate and p == -1) or (not run.estimate and run.coin())
        ]
        run.heads = len(run.chosen)
        run.main_hit = False
        run.damage = 0

    return pre(act)


@phrase("If heads, this attack does {N} damage to that Pokémon")
def _hit_heads_targets(n: str) -> list[Step]:
    def estimate(run: Run) -> None:
        run.damage = num(n) * run.heads

    def act(run: Run) -> None:
        for mon in list(run.chosen):
            if mon is run.opp.active:
                attacks.hit_active(run.ctx, num(n))
            elif any(mon is b for b in run.opp.bench):
                attacks.hit(run.ctx, core.index_of(run.opp.bench, mon), num(n))

    return [pre(estimate), after(act)]


# mão do oponente


@phrase("Choose a random card from your opponent's hand")
def _pick_random() -> Step:
    def act(run: Run) -> None:
        import random

        left = [c for c in run.opp.hand if not any(c is p for p in run.picked)]
        if left:
            run.picked.append(random.choice(left))

    return after(act)


@phrase(
    "Your opponent reveals that card and shuffles it into their deck",
    "Your opponent shuffles that card into their deck",
    "Your opponent reveals those cards and shuffles them into their deck",
)
def _shuffle_picked() -> Step:
    def act(run: Run) -> None:
        for card in run.picked:
            if card in run.opp.hand:
                run.opp.hand.remove(card)
                run.opp.deck.append(card)
        run.picked = []
        core.shuffle_deck(run.opp)

    return after(act)


@phrase("Your opponent chooses {N} cards from their hand and shuffles those cards into their deck")
def _opp_shuffles_some(n: str) -> Step:
    def act(run: Run) -> None:
        pid = run.ctx.opp_id
        worst = sorted(run.opp.hand, key=lambda c: core.card_priority(run.ctx.state, pid, c))
        for card in worst[: num(n)]:
            run.opp.hand.remove(card)
            run.opp.deck.append(card)
        core.shuffle_deck(run.opp)

    return after(act)


@phrase("Your opponent discards {N} more cards", "Your opponent discards {N} more card")
def _opp_discards_more(n: str) -> Step:
    return after(lambda run: core.discard_from_hand(run.ctx, num(n), player_id=run.ctx.opp_id))


# ferramentas e energias especiais do oponente


@phrase("Discard all Pokémon Tools and Special Energy from all of your opponent's Pokémon")
def _strip_all() -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            if mon.tool is not None:
                run.opp.discard.append(mon.tool)
                mon.tool = None
            for card in list(mon.special_energy_cards):
                run.opp.discard.append(core.detach_energy(mon, card.name))

    return after(act)


# evolução e devolução


@phrase(
    "For each of your Benched Pokémon, search your deck for a card that evolves from that Pokémon "
    "and put it onto that Pokémon to evolve it"
)
def _evolve_bench() -> Step:
    def act(run: Run) -> None:
        for mon in list(run.me.bench):
            card = next((c for c in run.me.deck if c.evolves_from == mon.card.name), None)
            if card is not None:
                run.me.deck.remove(card)
                core.evolve_into(run.ctx.state, run.me, mon, card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase(
    "Devolve each of your opponent's evolved Pokémon by shuffling the highest Stage Evolution "
    "card on it into your opponent's deck"
)
def _devolve_all() -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            if not mon.prior_cards:
                continue
            run.opp.deck.append(mon.card)
            mon.card = mon.prior_cards[0]
            mon.prior_cards = mon.prior_cards[1:]
            mon.hp_bonus = passives.hp_bonus(run.ctx.state, mon)
        core.shuffle_deck(run.opp)

    return after(act)


@phrase("Put up to {N} {X} from your discard pile onto your Bench")
def _discard_to_bench(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None

    def act(run: Run) -> None:
        room = core.bench_space(run.ctx.state, run.me)
        found = [c for c in run.me.discard if card_filter(c) and c.is_basic][: min(num(n), room)]
        for card in found:
            run.me.discard.remove(card)
            core.put_on_bench(run.ctx.state, run.me, card)

    return after(act)


@phrase("Put this Pokémon into your hand")
def _self_to_hand() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or run.me.active is not mon or not run.me.bench:
            return  # sem banco, sair do Ativo perderia o jogo
        run.me.discard.extend(mon.special_energy_cards)
        run.me.discard.extend(
            core.BASIC_ENERGIES[e] for e in mon.attached_energies if e in core.BASIC_ENERGIES
        )
        if mon.tool is not None:
            run.me.discard.append(mon.tool)
        run.me.hand.extend([mon.card, *mon.prior_cards])
        run.me.active = None
        run.ctx.log(f"{mon.card.name} voltou para a mão.")

    return after(act)


@phrase("Switch this Pokémon with {N} of your Benched {E} Pokémon")
def _switch_typed(_n: str, symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        typed = [i for i, m in enumerate(run.me.bench) if pokemon_type(m.card) == kind_]
        if typed:
            best = max(typed, key=lambda i: len(run.me.bench[i].attached_energies))
            core.switch_active(run.ctx.state, run.me, best)

    return after(act)


@phrase("Put {N} damage counters on {N} of your opponent's Pokémon for each {X}")
def _counters_scaled(n: str, _k: str, what: str) -> Step | None:
    counter = parse_count(what)
    if counter is None:
        return None
    kind_match = re.fullmatch(r"(.+) in your discard pile", what.strip())
    memo = parse_kind(re.sub(r"^(?:an?|each) ", "", kind_match.group(1))) if kind_match else None

    def act(run: Run) -> None:
        total = num(n) * counter(run)
        run.memo = memo
        target = core.best_counter_target(run.ctx.state, run.ctx.opp_id, total)
        if target is not None:
            core.place_counters(run.ctx, run.ctx.opp_id, target, total)

    return after(act)


@phrase("Shuffle those Energy cards into your deck", "Shuffle those cards into your deck")
def _shuffle_memo() -> Step:
    def act(run: Run) -> None:
        if run.memo is None:
            return
        found = [c for c in run.me.discard if run.memo(c)]
        for card in found:
            run.me.discard.remove(card)
        run.me.deck.extend(found)
        core.shuffle_deck(run.me)

    return after(act)


# condições do lote 2


@condition(r"you have {N} or fewer Benched Pokémon")
def _c_few_benched(n: str) -> Predicate:
    return lambda run: len(run.me.bench) <= num(n)


@condition(r"there is no Stadium in play")
def _c_no_stadium() -> Predicate:
    return lambda run: run.ctx.state.stadium is None


@condition(r"this Pokémon is {S}")
def _c_self_status(name: str) -> Predicate:
    return lambda run: run.source is not None and run.source.status == STATUSES[name.capitalize()]


@condition(r"your opponent's Active Pokémon isn't {S}", r"the Defending Pokémon isn't {S}")
def _c_not_status(name: str) -> Predicate:
    return _defender_is(lambda m: m.status != STATUSES[name.capitalize()])


@condition(r"this Pokémon evolved from {X} during this turn")
def _c_evolved_from(name: str) -> Predicate:
    return lambda run: (
        run.source is not None
        and run.source.evolved_this_turn
        and bool(run.source.prior_cards)
        and run.source.prior_cards[0].name == name.strip()
    )


@condition(r"{X} is in your discard pile")
def _c_in_discard(name: str) -> Predicate | None:
    found = card_name(name)
    if found is None:
        return None
    return lambda run: any(c.name == found for c in run.me.discard)


@condition(r"this Pokémon has any Special Energy attached")
def _c_self_special() -> Predicate:
    return lambda run: run.source is not None and bool(run.source.special_energy_cards)


@condition(r"your opponent has {N} or fewer cards in their hand")
def _c_opp_small_hand(n: str) -> Predicate:
    return lambda run: len(run.opp.hand) <= num(n)


@condition(r"your opponent's Active Pokémon has no Retreat Cost")
def _c_free_retreat() -> Predicate:
    return _defender_is(lambda m: not m.card.retreat_cost)


# contagens do lote 2


@count(r"{X} card you find there")
def _n_found(what: str) -> Counter_ | None:
    card_filter = parse_kind(f"{what} card")
    return (
        None
        if card_filter is None
        else (lambda run: sum(1 for c in run.opp.hand if card_filter(c)))
    )


@count(r"{X} in your opponent's discard pile")
def _n_opp_discard(what: str) -> Counter_ | None:
    card_filter = parse_kind(re.sub(r"^(?:an?|each) ", "", what))
    return (
        None
        if card_filter is None
        else (lambda run: sum(1 for c in run.opp.discard if card_filter(c)))
    )


@count(r"{E} Energy attached to all of your opponent's Pokémon")
def _n_opp_typed(symbol: str) -> Counter_:
    return lambda run: sum(
        m.attached_energies.count(energy(symbol)) for m in run.opp.all_pokemon_in_play()
    )


@count(r"Energy attached to all Pokémon")
def _n_all_energy() -> Counter_:
    return lambda run: sum(
        len(m.attached_energies)
        for m in run.me.all_pokemon_in_play() + run.opp.all_pokemon_in_play()
    )


@count(r"of your Benched Pokémon that has any {E} Energy attached")
def _n_bench_with(symbol: str) -> Counter_:
    return lambda run: sum(1 for m in run.me.bench if energy(symbol) in m.attached_energies)


@count(r"of your {X} in play")
def _n_named_in_play(names: str) -> Counter_ | None:
    wanted = [card_name(n) for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip())]
    if not wanted or any(n is None for n in wanted):
        return None
    return lambda run: sum(1 for m in run.me.all_pokemon_in_play() if m.card.name in wanted)


@count(r"{E} in your opponent's Active Pokémon's Retreat Cost")
def _n_retreat(_symbol: str) -> Counter_:
    return lambda run: (
        passives.retreat_cost(run.ctx.state, run.ctx.opp_id, run.defender) if run.defender else 0
    )


@count(r"damage counter on all of your opponent's Pokémon")
def _n_all_opp_counters() -> Counter_:
    return lambda run: sum(core.damage_counters_on(m) for m in run.opp.all_pokemon_in_play())


@count(r"card you revealed in this way")
def _n_revealed() -> Counter_:
    return lambda run: run.counted


@phrase(
    "Reveal any number of {X} from your hand",
)
def _reveal_named(names: str) -> Step | None:
    wanted = [card_name(n) for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip())]
    if not wanted or any(n is None for n in wanted):
        return None

    def act(run: Run) -> None:
        run.counted = sum(1 for c in run.me.hand if c.name in wanted)

    return pre(act)


@phrase(
    "If this Pokémon was damaged by an attack during your opponent's last turn, this attack does "
    "that much more damage"
)
def _revenge_amount() -> Step:
    def act(run: Run) -> None:
        mark = run.source.last_attacked if run.source else None
        if mark and mark[1] == run.ctx.turn - 1:
            run.damage += mark[0]

    return pre(act)


# ---------------------------------------------------------------------------
# Treinadores e Estádios: o mesmo compilador, sem o dano do ataque

_NO_ATTACK = Attack(name="", cost=[], damage="", text="")


def _run_effect(program: Program, ctx: Ctx) -> None:
    run = Run(ctx, _NO_ATTACK, 0, main_hit=False)
    program._pre(run)
    if run.cancelled:
        return
    for phase in ("before", "after"):
        for step in program.steps:
            if step.phase == phase:
                step.act(run)


def _check(predicate: Predicate) -> Callable[[Ctx], bool]:
    return lambda ctx: bool(predicate(Run(ctx, _NO_ATTACK, 0, estimate=True)))


#: frases de requisito: (padrão, construtor → (checagem, custo pago ao jogar))
Requirement = tuple[Callable[[Ctx], bool], Act | None]


def _requirement(sentence: str) -> Requirement | None:
    text = sentence.strip()
    match = re.fullmatch(
        expand(r"You can use this card only if you discard {N} other cards? from your hand"),
        text,
        re.IGNORECASE,
    ) or re.fullmatch(
        r"You can use this card only if you discard (another) card from your hand", text, re.I
    )
    if match:
        amount = 1 if match.group(1).lower() == "another" else num(match.group(1))

        def pay(run: Run) -> None:
            core.discard_from_hand(run.ctx, amount)

        return (lambda ctx: len(ctx.me.hand) - 1 >= amount), pay
    if re.fullmatch(
        r"You can use this card only when it is the last card in your hand", text, re.I
    ):
        return (lambda ctx: len(ctx.me.hand) == 1), None
    if re.fullmatch(r"You can't use this card during your first turn", text, re.I):
        return (lambda ctx: ctx.turn > 2), None
    if re.fullmatch(
        r"You can use this card only if you go second, and only during your first turn", text, re.I
    ):
        return (lambda ctx: ctx.turn == 2), None
    match = re.fullmatch(r"You can use this card only if (.+)", text, re.IGNORECASE)
    if match:
        predicate = parse_condition(match.group(1))
        if predicate is not None:
            return _check(predicate), None
    if re.fullmatch(r"If you go first, you may use this card during your first turn", text, re.I):
        return (lambda ctx: True), None  # não libera: a regra geral continua valendo
    if re.fullmatch(
        r"This card can't be put into your hand or deck from the discard pile", text, re.I
    ):
        return (lambda ctx: True), None
    return None


def compile_card_text(text: str) -> tuple[Program, list[Callable[[Ctx], bool]]] | None:
    program, checks = Program(), []
    _whole[0] = _clean(text)
    try:
        for sentence in sentences(text):
            requirement = _requirement(sentence)
            if requirement is not None:
                check, pay = requirement
                checks.append(check)
                if pay is not None:
                    program.steps.insert(0, Step("pre", pay, desc="custo de uso"))
                continue
            steps = parse_clause(sentence)
            if steps is None:
                return None
            program.steps.extend(steps)
    finally:
        _whole[0] = ""
    return (program, checks) if program.steps else None


_TRAINER_OPTIONS = {"opp_bench": "opp_bench_options", "own_bench": "own_bench_options"}


@lru_cache(maxsize=2048)
def compiled_trainer(text: str) -> TrainerSpec | None:
    from pokemon_companion.engine.effects import trainers

    compiled = compile_card_text(text)
    if compiled is None:
        return None
    program, checks = compiled
    kind_ = program.option
    options = getattr(trainers, _TRAINER_OPTIONS[kind_]) if kind_ in _TRAINER_OPTIONS else None
    return trainers.TrainerSpec(
        lambda ctx: _run_effect(program, ctx),
        lambda ctx: all(check(ctx) for check in checks),
        options,
    )


_THIRD_PERSON = (
    (r"\bthat player may\b ", ""),
    (r"\bthat player shuffles\b", "shuffle"),
    (r"\bin their name\b", "in its name"),
    (r"\ba player\b", "you"),
    (r"\bthat player's\b", "your"),
    (r"\bthat player\b", "you"),
    (r"\btheir\b", "your"),
    (r"\bthey have\b", "you have"),
    (r"\bthey\b", "you"),
)


@lru_cache(maxsize=512)
def compiled_stadium(text: str) -> TrainerSpec | None:
    """ "Once during each player's turn, that player may X" → X na 2ª pessoa."""
    from pokemon_companion.engine.effects import trainers

    match = re.search(r"Once during each player's turn, (.+)", _clean(text), re.IGNORECASE)
    if not match:
        return None
    effect = match.group(1)
    for pattern, repl in _THIRD_PERSON:
        effect = re.sub(pattern, repl, effect, flags=re.IGNORECASE)
    effect = effect[0].upper() + effect[1:]
    compiled = compile_card_text(effect)
    if compiled is None:
        return None
    program, checks = compiled
    return trainers.TrainerSpec(
        lambda ctx: _run_effect(program, ctx), lambda ctx: all(c(ctx) for c in checks)
    )


# frases que aparecem sobretudo em Treinadores


@phrase("Draw {N} cards instead", "draw {N} cards instead")
def _draw_instead(n: str) -> Step:
    def act(run: Run) -> None:
        run.drawn += core.draw(run.me, max(num(n) - run.drawn, 0))

    return after(act)


@phrase("Draw {N} more cards", "draw {N} more cards")
def _draw_more(n: str) -> Step:
    return after(lambda run: core.draw(run.me, num(n)))


@phrase("Discard your hand")
def _discard_hand() -> Step:
    def act(run: Run) -> None:
        run.did = bool(run.me.hand)
        if run.estimate:
            return
        run.me.discard.extend(run.me.hand)
        run.me.hand.clear()

    return pre(act)


@phrase("Draw a card for each {X}", "you draw a card for each {X}", "Draw {N} cards for each {X}")
def _draw_per(*groups: str) -> Step | None:
    per, what = (1, groups[0]) if len(groups) == 1 else (num(groups[0]), groups[1])
    counter = parse_count(what)
    if counter is None:
        return None
    return after(lambda run: core.draw(run.me, per * counter(run)))


@phrase("Heal {N} damage from {N} of your {E} Pokémon")
def _heal_typed(n: str, k: str, symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mons = [m for m in run.me.all_pokemon_in_play() if pokemon_type(m.card) == kind_]
        for mon in sorted(mons, key=lambda m: -m.damage_counters)[: num(k)]:
            core.heal(mon, num(n))

    return after(act)


@phrase("Heal {N} damage and remove a Special Condition from your Active Pokémon")
def _heal_and_cure(n: str) -> Step:
    def act(run: Run) -> None:
        if run.me.active is not None:
            core.heal(run.me.active, num(n))
            run.me.active.status = StatusCondition.NONE

    return after(act)


@phrase("Move up to {N} Energy from your Benched Pokémon to your Active Pokémon")
def _energy_to_active(n: str) -> Step:
    def act(run: Run) -> None:
        active = run.me.active
        for _ in range(num(n)):
            donors = [m for m in run.me.bench if m.attached_energies]
            if active is None or not donors:
                return
            donor = max(donors, key=lambda m: len(m.attached_energies))
            energy_name = core.least_useful_energy(donor)
            core.attach_energy_card(active, core.detach_energy(donor, energy_name))

    return after(act)


@phrase("Switch in {N} of your opponent's Benched Basic Pokémon to the Active Spot")
def _gust_basic(_n: str) -> Step:
    def act(run: Run) -> None:
        basics = [i for i, m in enumerate(run.opp.bench) if stage_of(m.card) == "Basic"]
        run.did = bool(basics)
        if basics:
            best = max(basics, key=lambda i: core._prize_value(run.opp.bench[i].card))
            core.switch_active(run.ctx.state, run.opp, best)

    return Step("before", act)


@phrase("The new Active Pokémon is now {X}")
def _new_active_status(text: str) -> Step | None:
    statuses = _status_list(text)
    if statuses is None:
        return None
    return after(lambda run: attacks.status_on_defender(run.ctx, statuses[0]))


@phrase("Your turn ends")
def _turn_ends() -> Step:
    def act(run: Run) -> None:
        run.ctx.ends_turn = True

    return after(act)


@phrase("Put {N} of your Pokémon and all attached cards into your hand")
def _scoop(n: str) -> Step:
    def act(run: Run) -> None:
        mons = run.me.all_pokemon_in_play()
        if len(mons) < 2:
            return  # tirar o único Pokémon perderia o jogo
        target = max(mons, key=lambda m: (m.damage_counters, m is not run.me.active))
        cards = target.all_cards() + [
            core.BASIC_ENERGIES[e] for e in target.attached_energies if e in core.BASIC_ENERGIES
        ]
        if run.me.active is target:
            run.me.active = None
        else:
            del run.me.bench[core.index_of(run.me.bench, target)]
        run.me.hand.extend(cards)

    return after(act)


@phrase(
    "Discard an Energy from {N} of your opponent's Pokémon",
    "Discard a Special Energy from {N} of your opponent's Pokémon",
)
def _discard_opp_any(_n: str) -> Step:
    special = "Special" in _current[0]

    def act(run: Run) -> None:
        holders = [
            m
            for m in run.opp.all_pokemon_in_play()
            if (m.special_energy_cards if special else m.attached_energies)
        ]
        if not holders:
            return
        mon = max(holders, key=lambda m: (m is run.opp.active, len(m.attached_energies)))
        choice = mon.special_energy_cards[0].name if special else mon.attached_energies[0]
        card = core.discard_energy(run.opp, mon, choice)
        if card is not None:
            run.ctx.log(f"{card.name} de {mon.card.name} foi descartada.")

    return after(act)


@phrase("Discard a Pokémon Tool and a Special Energy from {N} of your opponent's Pokémon")
def _discard_tool_and_special(_n: str) -> Step:
    def act(run: Run) -> None:
        mons = run.opp.all_pokemon_in_play()
        both = [m for m in mons if m.tool is not None and m.special_energy_cards]
        candidates = both or [m for m in mons if m.tool or m.special_energy_cards]
        if not candidates:
            return
        mon = candidates[0]
        if mon.tool is not None:
            run.opp.discard.append(mon.tool)
            mon.tool = None
        if mon.special_energy_cards:
            run.opp.discard.append(core.detach_energy(mon, mon.special_energy_cards[0].name))

    return after(act)


@phrase("Each player shuffles their hand into their deck")
def _both_shuffle_hands() -> Step:
    def act(run: Run) -> None:
        core.shuffle_hand_into_deck(run.me)
        core.shuffle_hand_into_deck(run.opp)

    return after(act)


@phrase("You draw {N} cards, and your opponent draws {N} cards")
def _split_draw(mine: str, theirs: str) -> Step:
    def act(run: Run) -> None:
        core.draw(run.me, num(mine))
        core.draw(run.opp, num(theirs))

    return after(act)


def _combo_filter(what: str) -> CardFilter | None:
    """ "A and B" / "A, B, and C": carta que é de qualquer um dos tipos."""
    parts = [p for p in re.split(r",\s*(?:and\s+)?|\s+and\s+|\s+or\s+", what.strip()) if p]
    filters = [parse_kind(re.sub(r"^(?:an?|any) ", "", p)) for p in parts]
    if not filters or any(f is None for f in filters):
        return None
    return lambda card: any(f(card) for f in filters if f is not None)


@kind(r"in any combination of {X}")
def _k_combination(what: str) -> CardFilter | None:
    return _combo_filter(what)


@phrase("Search your deck for {X}, reveal them, and put them into your hand")
def _search_several(what: str) -> Step | None:
    """ "a Pokémon, a Supporter card, and a Basic Energy card": uma de cada."""
    parts = [p for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", what.strip()) if p]
    if len(parts) < 2 or any(not re.match(r"an? ", p) for p in parts):
        return None
    filters = [parse_kind(p.split(" ", 1)[1]) for p in parts]
    if any(f is None for f in filters):
        return None

    def act(run: Run) -> None:
        for card_filter in filters:
            assert card_filter is not None
            core.search_deck(run.ctx, card_filter, 1)

    return after(act)


@kind(r"{X} that has \"?{X}\"? in its name", r"{X} that have \"?{X}\"? in their names")
def _k_named_part(what: str, part: str) -> CardFilter | None:
    base = parse_kind(what)
    return None if base is None else (lambda card: base(card) and part in card.name)


@kind(r"{X} Team Rocket's Pokémon")
def _k_rocket_stage(stage: str) -> CardFilter | None:
    tests = {"Basic": lambda c: c.is_basic, "Evolution": is_evolution}
    test = tests.get(stage.strip())
    if test is None:
        return None
    return lambda card: card.is_pokemon and test(card) and card.name.startswith("Team Rocket's")


@kind(r"([A-Z][\w.']+(?:'s)) Pokémon")
def _k_owner_group(owner: str) -> CardFilter | None:
    if not owner.endswith("'s"):
        return None
    return lambda card: card.is_pokemon and card.name.startswith(owner)


# ---------------------------------------------------------------------------
# Habilidades ativadas ("Once during your turn, ...") e gatilhos de entrada

_ABILITY_HEADS: list[tuple[str, str | None, Callable[[Ctx], bool] | None]] = [
    # (prefixo, gatilho, checagem)
    (
        r"when you play this Pokémon from your hand to evolve 1 of your Pokémon"
        r"(?: during your turn)?,? ",
        "evolve",
        None,
    ),
    (
        r"when you play this Pokémon from your hand onto your Bench(?: during your turn)?,? ",
        "bench",
        None,
    ),
    (
        r"when this Pokémon moves from your Bench to the Active Spot,? ",
        None,
        lambda ctx: ctx.source is not None and ctx.source.moved_to_active_turn == ctx.turn,
    ),
    (r"if this Pokémon is in the Active Spot,? ", None, lambda ctx: ctx.me.active is ctx.source),
    (
        r"when this Pokémon moves from the Active Spot to (?:your|the) Bench,? ",
        None,
        lambda ctx: ctx.source is not None and ctx.source.moved_to_bench_turn == ctx.turn,
    ),
    (
        r"if this Pokémon is on your Bench,? ",
        None,
        lambda ctx: any(ctx.source is b for b in ctx.me.bench),
    ),
]


class AbilityParts(NamedTuple):
    trigger: str
    checks: list[Callable[[Ctx], bool]]
    effect: str
    cost: Act | None
    repeatable: bool
    shared_limit: str | None


def _cost_before_head(text: str) -> tuple[Act, Callable[[Ctx], bool], str] | None:
    """ "You must discard/put X ... in order to use this Ability." no começo."""
    match = re.match(r"You must (.+?) in order to use this Ability\. ", text)
    if not match:
        return None
    what = match.group(1)
    rest = text[match.end() :]
    energy_self = re.fullmatch(expand(r"discard an? Basic {E} Energy from this Pokémon"), what)
    if energy_self:
        kind_ = energy(energy_self.group(1))

        def pay_energy(run: Run) -> None:
            if run.source is not None and kind_ in run.source.attached_energies:
                run.me.discard.append(core.detach_energy(run.source, kind_))

        return (
            pay_energy,
            lambda ctx: ctx.source is not None and kind_ in ctx.source.attached_energies,
            rest,
        )
    from_hand = re.fullmatch(expand(r"discard {N} (.+?) from your hand"), what)
    if from_hand:
        amount = num(from_hand.group(1))
        card_filter = parse_kind(from_hand.group(2))
        if card_filter is None:
            return None
        found = card_filter

        def pay_hand(run: Run) -> None:
            for card in [c for c in run.me.hand if found(c)][:amount]:
                run.me.hand.remove(card)
                run.me.discard.append(card)

        return pay_hand, lambda ctx: sum(1 for c in ctx.me.hand if found(c)) >= amount, rest
    if what == "put a card from your hand on the bottom of your deck":

        def pay_bottom(run: Run) -> None:
            worst = min(
                run.me.hand, key=lambda c: core.card_priority(run.ctx.state, run.ctx.player_id, c)
            )
            run.me.hand.remove(worst)
            run.me.deck.append(worst)

        return pay_bottom, lambda ctx: bool(ctx.me.hand), rest
    return None


def _ability_parts(text: str) -> AbilityParts | None:
    """Gatilho, checagens, texto do efeito, custo e repetição de uma
    Habilidade ativada ou de entrada em jogo."""
    text = _clean(text)
    checks: list[Callable[[Ctx], bool]] = []
    cost: Act | None = None
    early = _cost_before_head(text)
    if early is not None:
        cost, early_check, text = early
        checks.append(early_check)
    shared: str | None = None
    limit = re.search(r"\s*You can't use more than 1 (.+?) Ability during your turn\.?$", text)
    if limit:
        shared, text = limit.group(1), text[: limit.start()]
    trigger, repeatable = "turn", False
    head = re.match(
        r"(Once during your turn, |Once during your first turn, |As often as you like during "
        r"your turn, |(?=When you play this Pokémon))",
        text,
    )
    if head is None:
        return None
    if head.group(1).startswith("As often"):
        repeatable = True
    if head.group(1).startswith("Once during your first turn"):
        checks.append(lambda ctx: ctx.turn <= 2)
    rest = text[head.end() :]
    rest = rest[0].lower() + rest[1:]
    changed = True
    while changed:
        changed = False
        if rest.startswith("and "):
            rest, changed = rest[4:], True
        for prefix, new_trigger, check in _ABILITY_HEADS:
            match = re.match(prefix, rest, re.IGNORECASE)
            if match:
                rest, changed = rest[match.end() :], True
                trigger = new_trigger or trigger
                if check is not None:
                    checks.append(check)
        match = re.match(r"if (.+?), (?=(?:and if |you may ))", rest, re.IGNORECASE)
        if match:
            predicate = parse_condition(match.group(1))
            if predicate is None:
                return None
            checks.append(_check(predicate))
            rest, changed = rest[match.end() :], True
    match = re.match(r"you may discard (.+?) from your hand in order to use this Ability\. ", rest)
    if match:
        wanted = re.sub(r"^(?:an?) ", "", match.group(1))
        card_filter = parse_kind(wanted)
        if card_filter is None:
            return None
        found = card_filter
        checks.append(lambda ctx: any(found(c) for c in ctx.me.hand))

        def pay(run: Run) -> None:
            card = next(c for c in run.me.hand if found(c))
            run.me.hand.remove(card)
            run.me.discard.append(card)

        cost, rest = pay, rest[match.end() :]
    elif re.match(r"you may use this Ability\. ", rest):
        rest = rest[len("you may use this Ability. ") :]
    elif rest.startswith("you may "):
        rest = rest[len("you may ") :]
    else:
        return None
    rest = rest[0].upper() + rest[1:]
    return AbilityParts(trigger, checks, rest, cost, repeatable, shared)


def _opp_any_targets(ctx: Ctx) -> list[Target | None]:
    return [("opp", p) for p in core.positions(ctx.opp)]


def _opp_bench_targets(ctx: Ctx) -> list[Target | None]:
    return [("opp", i) for i in range(len(ctx.opp.bench))]


def _own_bench_targets(ctx: Ctx) -> list[Target | None]:
    return [("own", i) for i in range(len(ctx.me.bench))]


_ABILITY_OPTIONS: dict[str, Callable[[Ctx], list[Target | None]]] = {
    "opp_any": _opp_any_targets,
    "opp_bench": _opp_bench_targets,
    "own_bench": _own_bench_targets,
}


@lru_cache(maxsize=1024)
def compiled_ability(text: str) -> AbilitySpec | None:
    parts = _ability_parts(text)
    if parts is None:
        return None
    trigger, checks, effect, cost = parts.trigger, parts.checks, parts.effect, parts.cost
    compiled = compile_card_text(effect)
    if compiled is None:
        return None
    program, more_checks = compiled
    if cost is not None:
        program.steps.insert(0, Step("pre", cost, desc="custo da Habilidade"))
    all_checks = checks + more_checks
    options = _ABILITY_OPTIONS.get(program.option or "")

    def can_use(ctx: Ctx) -> bool:
        if not all(check(ctx) for check in all_checks):
            return False
        return not parts.repeatable or _changes_something(ctx, program)

    return AbilitySpec(
        lambda ctx: _run_effect(program, ctx),
        can_use,
        trigger,
        options,
        shared_limit=parts.shared_limit,
        repeatable=parts.repeatable,
    )


def _signature(ctx: Ctx) -> tuple[object, ...]:
    def side(player: PlayerState) -> tuple[object, ...]:
        mons = tuple(
            (m.card.name, tuple(m.attached_energies), m.damage_counters, m.status)
            for m in player.all_pokemon_in_play()
        )
        return len(player.hand), len(player.deck), len(player.discard), mons

    return side(ctx.me), side(ctx.opp)


def _changes_something(ctx: Ctx, program: Program) -> bool:
    """Habilidade repetível só é oferecida se usá-la muda o jogo (senão a IA
    a repetiria à toa)."""
    twin = ctx.state.clone()
    source = None
    if ctx.source is not None:
        position = core.position_of(ctx.me, ctx.source)
        source = core.mon_at(twin.state_of(ctx.player_id), position)
    twin_ctx = Ctx(twin, ctx.player_id, source)
    before = _signature(twin_ctx)
    _run_effect(program, twin_ctx)
    return _signature(twin_ctx) != before


@phrase("If you use this Ability, this Pokémon is Knocked Out")
def _self_ko() -> Step:
    return after(lambda run: _nocaute(run, run.source))


@phrase("If you use this Ability, your turn ends")
def _ability_ends_turn() -> list[Step] | Step | None:
    return _turn_ends()


@phrase(
    "Place {N} damage counters on {N} of your opponent's Pokémon",
    "Place {N} damage counters on your opponent's Active Pokémon",
    "Place {N} damage counters on each of your opponent's Pokémon",
)
def _place_counters(*groups: str) -> list[Step] | Step | None:
    text = _current[0].replace("Place ", "Put ", 1)
    return parse_clause(text)


@phrase("Switch your Active Pokémon with {N} of your Benched Pokémon")
def _switch_active(_n: str) -> Step:
    def act(run: Run) -> None:
        run.last_target = run.me.active
        index = attacks.target_index(run.ctx, -2)
        if index < 0:
            index = core.best_bench_index(run.ctx.state, run.ctx.player_id) or 0
        if run.me.bench:
            core.switch_active(run.ctx.state, run.me, index)
            run.did = True

    return after(act, "own_bench")


@phrase("Heal {N} damage from your Active Pokémon")
def _heal_active(n: str) -> Step:
    def act(run: Run) -> None:
        if run.me.active is not None:
            run.did = core.heal(run.me.active, num(n)) > 0

    return after(act)


@phrase(
    "Heal all damage from {N} of your Pokémon", "Heal all damage from {N} of your Benched Pokémon"
)
def _heal_all_some(k: str) -> Step:
    bench_only = "Benched" in _current[0]

    def act(run: Run) -> None:
        pool = run.me.bench if bench_only else run.me.all_pokemon_in_play()
        for mon in sorted(pool, key=lambda m: -m.damage_counters)[: num(k)]:
            core.heal(mon, mon.damage_counters)

    return after(act)


@phrase("Put {N} Energy attached to your opponent's Active Pokémon into their hand")
def _bounce_energy(n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        for _ in range(num(n)):
            if defender is None or not defender.attached_energies:
                return
            run.opp.hand.append(core.detach_energy(defender, defender.attached_energies[0]))

    return after(act)


@phrase("Put {X} from your discard pile onto your Bench")
def _discard_onto_bench(what: str) -> Step | None:
    match = re.fullmatch(expand(r"(?:up to )?{N} (.+)"), what.strip(), re.IGNORECASE)
    if not match:
        return None
    card_filter = parse_kind(match.group(2))
    if card_filter is None:
        return None
    amount = num(match.group(1))

    def act(run: Run) -> None:
        room = core.bench_space(run.ctx.state, run.me)
        found = [c for c in run.me.discard if card_filter(c) and c.is_basic][: min(amount, room)]
        for card in found:
            run.me.discard.remove(card)
            core.put_on_bench(run.ctx.state, run.me, card)

    return after(act)


@kind(r"Basic Pokémon with {N} HP or less")
def _k_small_basic(n: str) -> CardFilter:
    return lambda card: card.is_basic and (card.hp or 0) <= num(n)


@phrase("Put {N} damage counters on this Pokémon")
def _counters_self(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.damage_counters += 10 * num(n)
            run.did = True

    return after(act)


@phrase(
    "During this turn, attacks used by this Pokémon do {N} more damage to your opponent's Active "
    "Pokémon"
)
def _bonus_this_turn(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.attack_bonus = ("*", num(n), run.ctx.turn)

    return after(act)


@phrase("Have your opponent shuffle their hand into their deck and draw {N} cards")
def _opp_reshuffle(n: str) -> Step:
    def act(run: Run) -> None:
        core.shuffle_hand_into_deck(run.opp)
        core.draw(run.opp, num(n))

    return after(act)


@phrase("Move any amount of {E} Energy from your other Pokémon to this Pokémon")
def _gather_energy(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        for donor in run.me.all_pokemon_in_play():
            while donor is not mon and kind_ in donor.attached_energies:
                core.attach_energy_card(mon, core.detach_energy(donor, kind_))

    return after(act)


@condition(
    r"you played {X} from your hand this turn", r"you played {X} from your hand during this turn"
)
def _c_played(name: str) -> Predicate | None:
    found = card_name(name)
    return None if found is None else (lambda run: found in run.me.played_this_turn)


@condition(r"you have any {X} in play", r"you have any {X} on your Bench")
def _c_any_kind(what: str) -> Predicate | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None
    on_bench = "Bench" in _current[0]

    def test(run: Run) -> bool:
        mons = run.me.bench if on_bench else run.me.all_pokemon_in_play()
        return any(card_filter(m.card) for m in mons)

    return test


@kind(r"Mega Evolution Pokémon ex")
def _k_mega() -> CardFilter:
    return lambda card: card.is_pokemon and "Mega" in card.subtypes


@kind(r"{E} Mega Evolution Pokémon ex")
def _k_mega_typed(symbol: str) -> CardFilter:
    return lambda card: (
        card.is_pokemon and "Mega" in card.subtypes and pokemon_type(card) == energy(symbol)
    )


@kind(r"Tera Pokémon")
def _k_tera() -> CardFilter:
    return is_tera


@kind(r"(.+?) card")
def _k_named_card(text: str) -> CardFilter | None:
    name = card_name(text)
    return None if name is None else (lambda card: card.name == name)


@kind(r"(.+ or .+)")
def _k_either(text: str) -> CardFilter | None:
    return _combo_filter(text)


@phrase("Switch it with your Active Pokémon")
def _switch_in_self() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is not None and any(mon is b for b in run.me.bench):
            core.switch_active(run.ctx.state, run.me, core.index_of(run.me.bench, mon))

    return after(act)


@phrase(
    "Attach a Basic {E} Energy card, a Basic {E} Energy card, or 1 of each from your hand to your "
    "Pokémon in any way you like"
)
def _attach_one_of_each(first: str, second: str) -> Step:
    kinds = [energy(first), energy(second)]

    def act(run: Run) -> None:
        for kind_ in kinds:
            core.attach_from(run.ctx, run.me.hand, _basic_energy_of(kind_), 1)

    return after(act)


def _basic_energy_of(kind_: str) -> CardFilter:
    return lambda card: is_basic_energy(card) and energy_type_of(card) == kind_


# ---------------------------------------------------------------------------
# lote 3: cauda longa dos ataques


def _pokemon_count(qualifier: str, where: str) -> Counter_ | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier)
    if test is None:
        return None

    def count_(run: Run) -> int:
        mons = run.me.bench if where == "bench" else run.me.all_pokemon_in_play()
        return sum(1 for m in mons if test(m))

    return count_


@count(r"of your (.+?)Pokémon in play")
def _n_kind_in_play(qualifier: str) -> Counter_ | None:
    return _pokemon_count(qualifier, "play")


@count(r"(.+?)Pokémon on your Bench")
def _n_kind_on_bench(qualifier: str) -> Counter_ | None:
    return _pokemon_count(qualifier, "bench")


@count(r"of your Benched Pokémon that has any damage counters on it")
def _n_damaged_bench() -> Counter_:
    return lambda run: sum(1 for m in run.me.bench if m.damage_counters)


@count(r"damage counter on all of your Benched (.+)")
def _n_counters_bench_kind(what: str) -> Counter_ | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    what = what.strip()
    name = card_name(what)
    if name is not None:
        test: Callable[[PokemonInPlay], bool] | None = lambda m: m.card.name == name  # noqa: E731
    else:
        test = pokemon_filter(what[: -len("Pokémon")] if what.endswith("Pokémon") else "")
        if not what.endswith("Pokémon"):
            test = None
    if test is None:
        return None
    return lambda run: sum(core.damage_counters_on(m) for m in run.me.bench if test(m))


@count(r"{X} discarded in this way", r"{X} that you discarded in this way")
def _n_discarded_kind(what: str) -> Counter_ | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None
    return lambda run: sum(1 for c in run.picked if card_filter(c))


# condições


@condition(r"you have {N} or more {X} in your discard pile")
def _c_discard_count(n: str, what: str) -> Predicate | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None
    return lambda run: sum(1 for c in run.me.discard if card_filter(c)) >= num(n)


@condition(r"you have the same number of cards in your hand as your opponent")
def _c_same_hand() -> Predicate:
    return lambda run: len(run.me.hand) == len(run.opp.hand)


@condition(r"you don't have the same number of cards in your hand as your opponent")
def _c_diff_hand() -> Predicate:
    return lambda run: len(run.me.hand) != len(run.opp.hand)


@condition(r"your opponent's Active Pokémon isn't {X}")
def _c_isnt(what: str) -> Predicate | None:
    positive = parse_condition(f"your opponent's Active Pokémon is {what}")
    return None if positive is None else (lambda run: not positive(run))


@condition(
    r"your opponent's Active Pokémon has no damage counters on it",
    r"your opponent's Active Pokémon has no damage counters on it before this attack does damage",
)
def _c_undamaged() -> Predicate:
    return _defender_is(lambda m: m.damage_counters == 0)


@condition(r"your opponent's Active Pokémon has {E} Resistance")
def _c_resistance(symbol: str) -> Predicate:
    kind_ = energy(symbol)
    return _defender_is(lambda m: any(r.energy_type == kind_ for r in m.card.resistances))


@condition(r"your opponent's Active Pokémon is an evolved Pokémon")
def _c_evolved() -> Predicate:
    return _defender_is(lambda m: bool(m.prior_cards))


@condition(r"{X} is on your Bench")
def _c_named_bench(name: str) -> Predicate | None:
    found = card_name(name)
    return None if found is None else (lambda run: any(m.card.name == found for m in run.me.bench))


@condition(r"you don't have {X} on your Bench")
def _c_missing_bench(names: str) -> Predicate | None:
    wanted = [card_name(n) for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip())]
    if not wanted or any(n is None for n in wanted):
        return None
    return lambda run: not all(any(m.card.name == n for m in run.me.bench) for n in wanted)


@condition(r"your opponent's Pokémon is Knocked Out by damage from this attack")
def _c_knocked_out_now() -> Predicate:
    return lambda run: run.dealt > 0 and run.defender is not None and run.defender.is_knocked_out


# efeitos


@phrase("Discard {N} (.+?) Energy from this Pokémon")
def _discard_named_energy_2(n: str, name: str) -> Step | None:
    if card_name(name) is None:
        return None
    wanted = f"{name.strip()} Energy"

    def act(run: Run) -> None:
        mon = run.source
        run.did = False
        for _ in range(num(n)):
            if mon is None or wanted not in mon.attached_energies:
                return
            run.me.discard.append(core.detach_energy(mon, wanted))
            run.did = True

    return after(act)


@phrase("Discard this Pokémon and all attached cards")
def _discard_self() -> Step:
    def act(run: Run) -> None:
        if run.source is not None and run.source in run.me.all_pokemon_in_play():
            _remove_from_play(run.me, run.source, to_deck=False)

    return after(act)


@phrase("Look at the top card of your deck")
def _look_top() -> Step:
    def act(run: Run) -> None:
        run.picked = run.me.deck[:1]

    return after(act)


@phrase("You may discard that card", "Discard that card")
def _maybe_discard_top() -> Step:
    def act(run: Run) -> None:
        for card in run.picked:
            if (
                card in run.me.deck
                and core.card_priority(run.ctx.state, run.ctx.player_id, card) < 50
            ):
                run.me.deck.remove(card)
                run.me.discard.append(card)

    return after(act)


@phrase("Move all Energy from this Pokémon to {N} of your Benched Pokémon")
def _move_all_to_bench(_n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or not run.me.bench:
            return
        receiver = max(
            run.me.bench,
            key=lambda m: len(m.card.attacks)
            and max((a.base_damage for a in m.card.attacks), default=0),
        )
        for energy_name in list(mon.attached_energies):
            core.attach_energy_card(receiver, core.detach_energy(mon, energy_name))

    return after(act)


@phrase("Move a Basic Energy from this Pokémon to {N} of your Benched Pokémon")
def _move_basic_to_bench(n: str) -> list[Step] | Step | None:
    return _move_to_bench("1", n)


@phrase("This attack does {N} damage to each of your opponent's {X}")
def _hit_each_kind(n: str, what: str) -> list[Step] | None:
    tests: dict[str, Callable[[PokemonInPlay], bool]] = {
        "Pokémon ex": lambda m: is_ex(m.card),
        "Pokémon ex and Pokémon V": lambda m: is_ex(m.card) or "V" in m.card.subtypes,
        "Benched Pokémon ex": lambda m: is_ex(m.card),
        "Pokémon that has an Ability": lambda m: bool(m.card.abilities),
    }
    test = tests.get(what.strip())
    if test is None:
        return None
    bench_only = what.startswith("Benched")

    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n)

    def act(run: Run) -> None:
        for position in core.positions(run.opp):
            if bench_only and position == -1:
                continue
            mon = core.mon_at(run.opp, position)
            if mon is not None and test(mon):
                attacks.hit(
                    run.ctx, position, num(n), weakness=run.weakness, resistance=run.resistance
                )

    return [pre(replace), after(act)]


@phrase("This Pokémon recovers from all Special Conditions")
def _recover_self() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.status = StatusCondition.NONE

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "Evolution Pokémon"
)
def _shield_evolution() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = ("evolution", run.ctx.turn + 1)

    return after(act)


@phrase("Discard a {E} Energy from your opponent's Active Pokémon")
def _discard_opp_typed(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None and kind_ in defender.attached_energies:
            core.discard_energy(run.opp, defender, kind_)

    return after(act)


@phrase(
    "Discard an Energy from that Pokémon",
    "discard an Energy from that Pokémon",
    "Discard an Energy from it",
)
def _discard_that(*_: str) -> Step:
    return after(_discard_from_defender(1, False))


@phrase("Heal {N} damage from {N} of your Benched {E} Pokémon")
def _heal_bench_typed(n: str, k: str, symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mons = [m for m in run.me.bench if pokemon_type(m.card) == kind_]
        for mon in sorted(mons, key=lambda m: -m.damage_counters)[: num(k)]:
            core.heal(mon, num(n))

    return after(act)


@phrase("Heal {N} damage from it")
def _heal_it(n: str) -> list[Step] | Step | None:
    return _heal_self(n)


@phrase("It is now {X}", "it is now {X}")
def _it_status(text: str) -> list[Step] | Step | None:
    return _status(text)


@phrase("Knock Out {N} of your opponent's Pokémon that has exactly {N} damage counters on it")
def _ko_exact(_k: str, n: str) -> Step:
    def act(run: Run) -> None:
        exact = [m for m in run.opp.all_pokemon_in_play() if core.damage_counters_on(m) == num(n)]
        if exact:
            _nocaute(run, max(exact, key=lambda m: core._prize_value(m.card)))

    return after(act)


@phrase("Knock Out each of your opponent's Pokémon that has {N} HP or less remaining")
def _ko_low(n: str) -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            if mon.current_hp <= num(n):
                _nocaute(run, mon)

    return after(act)


def _mill_counting(players: Callable[[Run], list[PlayerState]], amount: int) -> Act:
    def act(run: Run) -> None:
        run.picked = []
        for player in players(run):
            top = player.deck[:amount]
            if not run.estimate:
                del player.deck[: len(top)]
                player.discard.extend(top)
            run.picked.extend(top)

    return act


@phrase("Discard the top card of each player's deck")
def _mill_both() -> Step:
    return pre(_mill_counting(lambda r: [r.me, r.opp], 1))


@phrase("Discard the top {N} cards of your deck, and this attack does {N} damage for each {X}")
def _mill_for_damage(n: str, per: str, what: str) -> list[Step] | None:
    counter = parse_count(what)
    if counter is None:
        return None

    def damage(run: Run) -> None:
        run.damage = num(per) * counter(run)

    return [pre(_mill_counting(lambda r: [r.me], num(n))), pre(damage)]


@phrase("Search your deck for any number of {X}, reveal them, and put them into your hand")
def _search_any_number(what: str) -> Step | None:
    return _search(f"up to 60 {what}", "hand")


@phrase("Put {N} of your Benched Pokémon and all attached cards into your hand")
def _scoop_bench(_n: str) -> Step:
    def act(run: Run) -> None:
        if not run.me.bench:
            return
        target = max(run.me.bench, key=lambda m: m.damage_counters)
        cards = target.all_cards() + [
            core.BASIC_ENERGIES[e] for e in target.attached_energies if e in core.BASIC_ENERGIES
        ]
        del run.me.bench[core.index_of(run.me.bench, target)]
        run.me.hand.extend(cards)

    return after(act)


@phrase("Shuffle {N} of your Benched Pokémon and all attached cards into your deck")
def _bench_to_deck(_n: str) -> Step:
    def act(run: Run) -> None:
        if run.me.bench:
            target = max(run.me.bench, key=lambda m: m.damage_counters)
            _remove_from_play(run.me, target, to_deck=True)

    return after(act)


@phrase(
    "Search your deck for a card that evolves from {N} of your Pokémon and put it onto that "
    "Pokémon to evolve it"
)
def _evolve_one(_n: str) -> Step:
    def act(run: Run) -> None:
        options = [
            (mon, card)
            for mon in run.me.all_pokemon_in_play()
            for card in run.me.deck
            if card.evolves_from == mon.card.name
        ]
        if options:
            mon, card = max(options, key=lambda o: (o[1].hp or 0, o[0] is run.me.active))
            run.me.deck.remove(card)
            core.evolve_into(run.ctx.state, run.me, mon, card)
        core.shuffle_deck(run.me)

    return after(act)


def _move_counters(source_kind: str, to_active: bool) -> Act:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(source_kind) or (lambda m: True)

    def act(run: Run) -> None:
        if passives.counters_locked(run.ctx.state):
            return
        donors = [m for m in run.me.bench if m.damage_counters and test(m)]
        if not donors:
            return
        donor = max(donors, key=lambda m: m.damage_counters)
        counters = core.damage_counters_on(donor)
        target = (
            run.defender
            if to_active
            else core.best_counter_target(run.ctx.state, run.ctx.opp_id, counters)
        )
        if target is None:
            return
        donor.damage_counters = 0
        core.place_counters(run.ctx, run.ctx.opp_id, target, counters)

    return act


@phrase(
    "Move all damage counters from {N} of your Benched (.*?)Pokémon to your opponent's Active "
    "Pokémon",
    "Move all damage counters from {N} of your Benched (.*?)Pokémon to {N} of your opponent's "
    "Pokémon",
)
def _move_counters_phrase(_n: str, kind_: str, *rest: str) -> Step:
    return after(_move_counters(kind_, to_active=not rest))


@phrase(
    "Look at the top {N} cards of your opponent's deck and put them back in any order",
    "Look at the top {N} cards of your deck and put them back in any order",
)
def _look_only(_n: str) -> Step:
    return after(lambda run: None)


@phrase(
    "Devolve it by putting the highest Stage Evolution card on it into your opponent's hand",
    "devolve it by putting the highest Stage Evolution card on it into your opponent's hand",
)
def _devolve_defender() -> Step:
    def act(run: Run) -> None:
        mon = run.defender
        if mon is None or not mon.prior_cards:
            return
        run.opp.hand.append(mon.card)
        mon.card = mon.prior_cards[0]
        mon.prior_cards = mon.prior_cards[1:]
        mon.hp_bonus = passives.hp_bonus(run.ctx.state, mon)

    return after(act)


@phrase("You may discard all Energy from this Pokémon and have this attack do {N} more damage")
def _all_energy_for_damage(n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or not mon.attached_energies:
            return
        run.damage += num(n)
        if not run.estimate:
            for energy_name in list(mon.attached_energies):
                run.me.discard.append(core.detach_energy(mon, energy_name))

    return pre(act)


def _copy_best(pool: Callable[[Run], list[PokemonInPlay]]) -> Act:
    def act(run: Run) -> None:
        candidates = [
            atk
            for mon in pool(run)
            for atk in mon.card.attacks
            if atk.name not in attacks.COPY_ATTACKS
        ]
        if not candidates:
            return
        best = max(candidates, key=lambda a: a.base_damage)
        attacks.use_copied(run.ctx, best)

    return act


@phrase("Choose {N} of your opponent's Active Pokémon's attacks and use it as this attack")
def _copy_defender(_n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False

    return [pre(replace), after(_copy_best(lambda r: [r.defender] if r.defender else []))]


@phrase(
    "Choose an attack from {N} of your opponent's Pokémon in play and use it as this attack",
    "Choose an attack from 1 of your opponent's Pokémon in play and use it as this attack",
)
def _copy_any(*_: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False

    return [pre(replace), after(_copy_best(lambda r: r.opp.all_pokemon_in_play()))]


@phrase("This attack also does {N} damage to each of your {X}")
def _self_named_damage(n: str, names: str) -> Step | None:
    wanted = [card_name(x) for x in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip())]
    if not wanted or any(w is None for w in wanted):
        return None
    return after(
        _self_damage(
            lambda r: [m for m in r.me.all_pokemon_in_play() if m.card.name in wanted], num(n)
        )
    )


@phrase("Discard a card you find there")
def _discard_found() -> Step:
    def act(run: Run) -> None:
        if run.opp.hand:
            best = core.choose_cards(run.ctx.state, run.ctx.opp_id, run.opp.hand, 1)[0]
            run.opp.hand.remove(best)
            run.opp.discard.append(best)

    return after(act)


@phrase("For each heads, choose a card you find there and shuffle it into your opponent's deck")
def _shuffle_found_per_heads() -> Step:
    def act(run: Run) -> None:
        chosen = core.choose_cards(run.ctx.state, run.ctx.opp_id, run.opp.hand, run.heads)
        for card in chosen:
            run.opp.hand.remove(card)
            run.opp.deck.append(card)
        core.shuffle_deck(run.opp)

    return after(act)


@phrase("Look at the top {N} cards of your deck")
def _look_top_n(n: str) -> Step:
    def act(run: Run) -> None:
        run.picked = run.me.deck[: num(n)]

    return after(act)


@phrase("You may put any number of Pokémon you find there onto your Bench")
def _bench_found() -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.picked if c.is_basic]:
            if core.bench_space(run.ctx.state, run.me) == 0:
                break
            if card in run.me.deck:
                run.me.deck.remove(card)
                core.put_on_bench(run.ctx.state, run.me, card)

    return after(act)


@phrase("Put a number of cards up to the number of heads from your discard pile into your hand")
def _recover_per_heads() -> Step:
    return after(lambda run: core.recover_from_discard(run.ctx, lambda c: True, run.heads))


@phrase("Put damage counters on your opponent's Active Pokémon until its remaining HP is {N}")
def _counters_until(n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.defender
        if mon is not None and mon.current_hp > num(n):
            core.place_counters(run.ctx, run.ctx.opp_id, mon, (mon.current_hp - num(n)) // 10)

    return after(act)


@phrase(
    "Put damage counters on each of your opponent's Benched Pokémon until its remaining HP is {N}"
)
def _bench_counters_until(n: str) -> Step:
    def act(run: Run) -> None:
        for mon in list(run.opp.bench):
            if mon.current_hp > num(n):
                core.place_counters(run.ctx, run.ctx.opp_id, mon, (mon.current_hp - num(n)) // 10)

    return after(act)


@phrase(
    "During your opponent's next turn, if the Defending Pokémon tries to use an attack, your "
    "opponent flips a coin"
)
def _smokescreen() -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.attack_coin_turn = run.ctx.turn + 1

    return after(act)


@phrase("If tails, that attack doesn't happen")
def _smokescreen_rest() -> Step:
    return after(lambda run: None)  # a moeda é tirada pelas regras (`attack_coin_turn`)


@phrase("During your next turn, this Pokémon's {X} attack's base damage is {N}")
def _next_base_damage(name: str, n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        attack_ = next((a for a in mon.card.attacks if a.name == name.strip()), None)
        if attack_ is not None:
            mon.attack_bonus = (attack_.name, num(n) - attack_.base_damage, run.ctx.turn + 2)

    return after(act)


@phrase(
    "If 1 of your Pokémon used {X} during your last turn, this attack can't be used",
)
def _no_repeat_team(name: str) -> Step:
    def act(run: Run) -> None:
        last = run.me.last_attack
        if last is not None and last == (name.strip(), run.ctx.turn - 2):
            run.cancelled = True

    return pre(act)


@phrase(
    "You may discard up to {N} {X} from your Benched Pokémon",
    "Discard up to {N} {X} from your Benched Pokémon",
)
def _discard_bench_for_damage(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None
    per = _per_discarded()
    limit = num(n)

    def act(run: Run) -> None:
        donors = [
            (m, e)
            for m in run.me.bench
            for e in m.attached_energies
            if card_filter(core.energy_card_from(m, e))
        ][:limit]
        wanted = len(donors)
        wanted = _discards_needed(run, per, wanted)
        run.counted = wanted
        if run.estimate:
            return
        for mon, energy_name in donors[:wanted]:
            run.me.discard.append(core.detach_energy(mon, energy_name))

    return pre(act)


@phrase(
    "Reveal any number of {X} from your hand, and this attack does {N} damage for each card you "
    "revealed in this way"
)
def _reveal_for_damage(names: str, n: str) -> list[Step] | None:
    wanted = [card_name(x) for x in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip())]
    if not wanted or any(w is None for w in wanted):
        return None

    def act(run: Run) -> None:
        run.damage = num(n) * sum(1 for c in run.me.hand if c.name in wanted)

    return [pre(act)]


# ---------------------------------------------------------------------------
# lote 4


def _at_least_heads(n: int) -> Callable[[str, list[Step]], list[Step]]:
    return lambda _, steps: _wrap(steps, lambda r: int(r.heads >= n), f"se {n}+ caras")


@phrase(r"If at least {N} of them are heads, {X}")
def _if_at_least(n: str, rest: str) -> list[Step] | None:
    steps = parse_clause(rest[0].upper() + rest[1:])
    return None if steps is None else _at_least_heads(num(n))(rest, steps)


# condições


@condition(r"you have exactly {N} Prize cards? remaining")
def _c_my_exact_prizes(n: str) -> Predicate:
    return lambda run: len(run.me.prizes) == num(n)


@condition(r"your opponent has exactly {N} Prize cards? remaining")
def _c_opp_exact_prizes(n: str) -> Predicate:
    return lambda run: len(run.opp.prizes) == num(n)


@condition(r"your opponent doesn't have exactly {N} or {N} Prize cards remaining")
def _c_opp_not_prizes(a: str, b: str) -> Predicate:
    return lambda run: len(run.opp.prizes) not in (num(a), num(b))


@condition(r"you don't have exactly {N} cards in your hand")
def _c_not_exact_hand(n: str) -> Predicate:
    return lambda run: len(run.me.hand) != num(n)


@condition(
    r"any of your (.+?) Pokémon were Knocked Out by damage from an attack during your "
    r"opponent's last turn"
)
def _c_group_revenge(group: str) -> Predicate:
    return lambda run: run.me.knocked_out_turn == run.ctx.turn - 1 and any(
        name.startswith(group) for name in run.me.knocked_out_names
    )


@condition(r"this Pokémon is {S} or {S}")
def _c_self_either(a: str, b: str) -> Predicate:
    wanted = {STATUSES[a.capitalize()], STATUSES[b.capitalize()]}
    return lambda run: run.source is not None and run.source.status in wanted


@condition(r"this Pokémon's remaining HP is {N} or less")
def _c_low_hp(n: str) -> Predicate:
    return lambda run: run.source is not None and run.source.current_hp <= num(n)


@condition(r"there are {N} or fewer cards in your deck")
def _c_thin_deck(n: str) -> Predicate:
    return lambda run: len(run.me.deck) <= num(n)


@condition(r"your opponent has any (.*?)Pokémon in play")
def _c_opp_has(qualifier: str) -> Predicate | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier)
    return (
        None if test is None else (lambda run: any(test(m) for m in run.opp.all_pokemon_in_play()))
    )


@condition(r"all of your Benched Pokémon have at least 1 damage counter on them")
def _c_all_bench_hurt() -> Predicate:
    return lambda run: bool(run.me.bench) and all(m.damage_counters for m in run.me.bench)


@condition(r"you didn't play {X} from your hand during this turn")
def _c_not_played(name: str) -> Predicate | None:
    found = card_name(name)
    return None if found is None else (lambda run: found not in run.me.played_this_turn)


@condition(r"your opponent has a Stadium in play")
def _c_opp_stadium() -> Predicate:
    return lambda run: run.ctx.state.stadium is not None and (
        run.ctx.state.stadium_owner == run.ctx.opp_id
    )


@condition(
    r"you discarded a Pokémon Tool in this way",
    r"you discarded any cards in this way",
    r"you put any Pokémon onto your Bench in this way",
)
def _c_did() -> Predicate:
    return lambda run: run.did


# contagens


@count(r"of your opponent's Pokémon ex in play")
def _n_opp_ex() -> Counter_:
    return lambda run: sum(1 for m in run.opp.all_pokemon_in_play() if is_ex(m.card))


@count(r"of your opponent's Pokémon ex and Pokémon V in play")
def _n_opp_ex_v() -> Counter_:
    return lambda run: sum(
        1 for m in run.opp.all_pokemon_in_play() if is_ex(m.card) or "V" in m.card.subtypes
    )


@count(r"Special Energy card attached to this Pokémon")
def _n_own_special() -> Counter_:
    return lambda run: len(run.source.special_energy_cards) if run.source else 0


def _names_in_quotes(text: str) -> list[str]:
    return re.findall(r'"([^"]+)"', text)


@count(r"Pokémon in play that has {X} in its name")
def _n_named_anywhere(text: str) -> Counter_ | None:
    parts = _names_in_quotes(text)
    if not parts:
        return None
    return lambda run: sum(
        1
        for m in run.me.all_pokemon_in_play() + run.opp.all_pokemon_in_play()
        if any(p in m.card.name for p in parts)
    )


@count(r"of your Pokémon that has {X} in its name that has any damage counters on it")
def _n_named_hurt(text: str) -> Counter_ | None:
    parts = _names_in_quotes(text)
    if not parts:
        return None
    return lambda run: sum(
        1
        for m in run.me.all_pokemon_in_play()
        if any(p in m.card.name for p in parts) and m.damage_counters
    )


@count(r"of your Benched Pokémon that has {X} in its name")
def _n_bench_named_part(text: str) -> Counter_ | None:
    parts = _names_in_quotes(text)
    if not parts:
        return None
    return lambda run: sum(1 for m in run.me.bench if any(p in m.card.name for p in parts))


@count(r"of your Benched {X}")
def _n_bench_named(name: str) -> Counter_ | None:
    found = card_name(name)
    return (
        None
        if found is None
        else (lambda run: sum(1 for m in run.me.bench if m.card.name == found))
    )


@count(r"{E} Energy attached to all of your (.+?)Pokémon")
def _n_group_energy(symbol: str, qualifier: str) -> Counter_ | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier)
    kind_ = energy(symbol)
    if test is None:
        return None
    return lambda run: sum(
        m.attached_energies.count(kind_) for m in run.me.all_pokemon_in_play() if test(m)
    )


@count(r"Pokémon in your discard pile that has the {X} attack")
def _n_discard_with_attack(name: str) -> Counter_:
    return lambda run: sum(
        1 for c in run.me.discard if c.is_pokemon and any(a.name == name for a in c.attacks)
    )


@count(r"Energy attached to both Active Pokémon")
def _n_both_active_energy() -> Counter_:
    return lambda run: sum(
        len(m.attached_energies) for m in (run.me.active, run.opp.active) if m is not None
    )


# efeitos


@phrase("Discard all Special Energy from all of your opponent's Pokémon")
def _strip_special() -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            for card in list(mon.special_energy_cards):
                run.opp.discard.append(core.detach_energy(mon, card.name))

    return after(act)


@phrase("Attach a Basic {E} Energy card from your discard pile to each of your Benched Pokémon")
def _energy_each_bench_discard(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        for mon in list(run.me.bench):
            card = next(
                (c for c in run.me.discard if is_basic_energy(c) and energy_type_of(c) == kind_),
                None,
            )
            if card is None:
                return
            run.me.discard.remove(card)
            core.attach_energy_card(mon, card)

    return after(act)


@phrase("Move an Energy from your opponent's Active Pokémon to {N} of their Benched Pokémon")
def _move_opp_energy(_n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is None or not defender.attached_energies or not run.opp.bench:
            return
        receiver = min(
            run.opp.bench, key=lambda m: max((a.base_damage for a in m.card.attacks), default=0)
        )
        energy_name = defender.attached_energies[0]
        core.attach_energy_card(receiver, core.detach_energy(defender, energy_name))

    return after(act)


@phrase("During your next turn, the Defending Pokémon takes {N} more damage from attacks")
def _defender_vulnerable(n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.damage_reduction = (-num(n), run.ctx.turn + 2)

    return after(act)


@phrase("During your opponent's next turn, this Pokémon takes {N} more damage from attacks")
def _self_vulnerable(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.damage_reduction = (-num(n), run.ctx.turn + 1)

    return after(act)


@phrase("Discard up to {N} Pokémon Tools from your opponent's Pokémon")
def _discard_tools(n: str) -> Step:
    def act(run: Run) -> None:
        tooled = [m for m in run.opp.all_pokemon_in_play() if m.tool is not None][: num(n)]
        for mon in tooled:
            assert mon.tool is not None
            run.opp.discard.append(mon.tool)
            mon.tool = None

    return after(act)


@phrase("During your next turn, this Pokémon can't retreat")
def _self_no_retreat() -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.cannot_retreat_turn = run.ctx.turn + 2

    return after(act)


@phrase(
    "During your opponent's next turn, if this Pokémon is damaged by an attack, put damage "
    "counters on the Attacking Pokémon equal to the damage done to this Pokémon"
)
def _mirror_damage() -> Step:
    return after(lambda run: attacks.retaliate_next_turn(run.ctx, -1))


@phrase(
    "During your opponent's next turn, if this Pokémon is damaged by an attack, place {N} damage "
    "counters on the Attacking Pokémon"
)
def _retaliate_place(n: str) -> Step:
    return after(lambda run: attacks.retaliate_next_turn(run.ctx, num(n)))


def _different_types(pile: list[Card], limit: int) -> list[Card]:
    chosen: list[Card] = []
    for card in pile:
        if is_basic_energy(card) and all(energy_type_of(card) != energy_type_of(c) for c in chosen):
            chosen.append(card)
        if len(chosen) == limit:
            break
    return chosen


@phrase(
    "Search your deck for up to {N} Basic Energy cards of different types, reveal them, and put "
    "them into your hand"
)
def _search_rainbow(n: str) -> Step:
    def act(run: Run) -> None:
        for card in _different_types(run.me.deck, num(n)):
            run.me.deck.remove(card)
            run.me.hand.append(card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase(
    "Search your deck for up to {N} Basic Energy cards of different types and attach them to {X}"
)
def _attach_rainbow(n: str, tail: str) -> Step | None:
    where = _destination(f" to {tail}")
    if where is None:
        return None

    def act(run: Run) -> None:
        found = _different_types(run.me.deck, num(n))
        for card in found:
            run.me.deck.remove(card)
        core.attach_from(run.ctx, found, lambda c: True, len(found), lambda m: where(run, m))
        run.me.deck.extend(found)  # o que não coube volta
        core.shuffle_deck(run.me)

    return after(act)


@phrase(
    "Look at the top {N} cards of your deck, and you may reveal any number of Pokémon you find "
    "there and put them into your hand"
)
def _top_pokemon_to_hand(n: str) -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.me.deck[: num(n)] if c.is_pokemon]:
            run.me.deck.remove(card)
            run.me.hand.append(card)

    return after(act)


@phrase("Switch {N} of your opponent's Benched Pokémon with their Active Pokémon")
def _gust_switch(n: str) -> list[Step] | Step | None:
    return _gust(n)


@phrase(
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "(Burned|Ancient) Pokémon",
    "During your opponent's next turn, prevent all damage done to this Pokémon by attacks from "
    "Pokémon that have an (Ability)",
)
def _shield_kind(which: str) -> Step:
    kind_ = {"Burned": "burned", "Ancient": "ancient", "Ability": "ability"}[which]

    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = (kind_, run.ctx.turn + 1)

    return after(act)


@phrase("Discard all Pokémon Tools and Special Energy from your opponent's Active Pokémon")
def _strip_defender() -> Step:
    def act(run: Run) -> None:
        mon = run.defender
        if mon is None:
            return
        if mon.tool is not None:
            run.opp.discard.append(mon.tool)
            mon.tool = None
        for card in list(mon.special_energy_cards):
            run.opp.discard.append(core.detach_energy(mon, card.name))

    return after(act)


@phrase("Shuffle {N} of your opponent's Benched Pokémon and all attached cards into their deck")
def _bounce_benched(_n: str) -> Step:
    def act(run: Run) -> None:
        if run.opp.bench:
            target = max(
                run.opp.bench, key=lambda m: (core._prize_value(m.card), len(m.attached_energies))
            )
            _remove_from_play(run.opp, target, to_deck=True)

    return after(act)


@phrase("Put this Pokémon and all attached cards into your deck")
def _self_to_deck() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        run.did = False
        if mon is not None and run.me.active is mon and run.me.bench:
            _remove_from_play(run.me, mon, to_deck=True)
            run.did = True

    return after(act)


@phrase(
    "You may put all Energy attached to this Pokémon into your hand to have this attack do {N} "
    "more damage"
)
def _energy_home_for_damage(n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or not mon.attached_energies:
            return
        run.damage += num(n)
        if not run.estimate:
            for energy_name in list(mon.attached_energies):
                run.me.hand.append(core.detach_energy(mon, energy_name))

    return pre(act)


@phrase(
    "This attack does {N} damage to 1 of your opponent's Benched Pokémon ex or Benched Pokémon V"
)
def _snipe_ex_v(n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n)

    def act(run: Run) -> None:
        targets = [
            i for i, m in enumerate(run.opp.bench) if is_ex(m.card) or "V" in m.card.subtypes
        ]
        if targets:
            attacks.hit(run.ctx, min(targets, key=lambda i: run.opp.bench[i].current_hp), num(n))

    return [pre(replace), after(act)]


@phrase(
    "This attack does {N} damage to 1 of your opponent's Pokémon that has any Special Energy attached"
)
def _snipe_special(n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n)

    def act(run: Run) -> None:
        targets = [
            p
            for p in core.positions(run.opp)
            if (mon := core.mon_at(run.opp, p)) is not None and mon.special_energy_cards
        ]
        if targets:
            attacks.hit(run.ctx, targets[0], num(n))

    return [pre(replace), after(act)]


@phrase("Choose {N} of your opponent's Pokémon")
def _choose_any_opp(n: str) -> Step:
    def act(run: Run) -> None:
        ranked = sorted(
            run.opp.all_pokemon_in_play(),
            key=lambda m: (-core._prize_value(m.card), -len(m.attached_energies)),
        )
        run.chosen = ranked[: num(n)]

    return Step("before", act)


@phrase("Shuffle that Pokémon and all attached cards into their deck")
def _shuffle_that() -> Step:
    chose = "choose" in _whole[0].lower()

    def act(run: Run) -> None:
        if chose and not run.chosen:
            return  # a escolha não aconteceu (ex.: coroa)
        target = run.chosen[0] if chose else run.defender
        if target is not None and target in run.opp.all_pokemon_in_play():
            _remove_from_play(run.opp, target, to_deck=True)

    return after(act)


@phrase(
    "Look at {N} of your opponent's face-down Prize cards",
    "Look at the top card of your opponent's deck",
)
def _peek(*_: str) -> Step:
    return after(lambda run: None)


@phrase("You may have your opponent shuffle their deck", "Have your opponent shuffle their deck")
def _opp_shuffle() -> Step:
    def act(run: Run) -> None:
        top = run.opp.deck[:1]
        if top and core.card_priority(run.ctx.state, run.ctx.opp_id, top[0]) >= 60:
            core.shuffle_deck(run.opp)

    return after(act)


@phrase("Put {N} {E} Energy attached to this Pokémon into your hand")
def _typed_energy_home(n: str, symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mon = run.source
        for _ in range(num(n)):
            if mon is None or kind_ not in mon.attached_energies:
                return
            run.me.hand.append(core.detach_energy(mon, kind_))

    return after(act)


@phrase("Draw {N} cards from the bottom of your deck")
def _draw_bottom(n: str) -> Step:
    def act(run: Run) -> None:
        for _ in range(min(num(n), len(run.me.deck))):
            run.me.hand.append(run.me.deck.pop())

    return after(act)


@phrase(
    "If your opponent has a Stadium in play, discard it",
    "If a Stadium is in play, discard it",
)
def _discard_their_stadium() -> Step:
    mine_too = "a Stadium is in play" in _current[0]

    def act(run: Run) -> None:
        from pokemon_companion.engine.effects.trainers import discard_stadium

        state = run.ctx.state
        if state.stadium is not None and (mine_too or state.stadium_owner == run.ctx.opp_id):
            discard_stadium(state)
            run.did = True

    return after(act)


@phrase(
    "Your opponent can't play any Supporter cards from their hand during their next turn",
    "your opponent can't play any Supporter cards from their hand during their next turn",
)
def _supporter_lock() -> Step:
    def act(run: Run) -> None:
        run.opp.supporters_blocked_turn = run.ctx.turn + 1

    return after(act)


@phrase(
    "Your opponent can't play any Stadium cards from their hand during their next turn",
    "your opponent can't play any Stadium cards from their hand during their next turn",
)
def _stadium_lock() -> Step:
    def act(run: Run) -> None:
        run.opp.stadiums_blocked_turn = run.ctx.turn + 1

    return after(act)


@phrase(
    "You may have this Pokémon also do {N} damage to itself and make your opponent's Active "
    "Pokémon {X}"
)
def _recoil_for_status(n: str, status_text: str) -> list[Step] | None:
    statuses = _status_list(status_text)
    if statuses is None:
        return None

    def act(run: Run) -> None:
        attacks.recoil(run.ctx, num(n))
        attacks.status_on_defender(run.ctx, statuses[0])

    return [after(act)]


@phrase(
    "Put {N} damage counters on each of your opponent's Pokémon that has any damage counters on it"
)
def _counters_on_hurt(n: str) -> Step:
    return after(
        _counters_on(
            lambda r: [m for m in r.opp.all_pokemon_in_play() if m.damage_counters], num(n)
        )
    )


@phrase("During your next turn, your Pokémon can't attack")
def _team_cant_attack() -> Step:
    def act(run: Run) -> None:
        for mon in run.me.all_pokemon_in_play():
            mon.cannot_attack_turn = run.ctx.turn + 2

    return after(act)


@phrase("Your opponent's Active Pokémon is Knocked Out")
def _ko_active() -> Step:
    return after(lambda run: _nocaute(run, run.defender))


@phrase("This Pokémon also does {N} damage to itself for each damage counter on it")
def _recoil_scaled(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            attacks.recoil(run.ctx, num(n) * core.damage_counters_on(run.source))

    return after(act)


@phrase("Double the number of damage counters on each of your opponent's Pokémon")
def _double_counters() -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            core.place_counters(run.ctx, run.ctx.opp_id, mon, core.damage_counters_on(mon))

    return after(act)


@phrase(
    "This attack does {N} damage to each Pokémon that has any damage counters on it, except for this Pokémon"
)
def _hit_all_hurt(n: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n)

    def act(run: Run) -> None:
        for position in core.positions(run.opp):
            mon = core.mon_at(run.opp, position)
            if mon is not None and mon.damage_counters:
                attacks.hit(run.ctx, position, num(n))
        for mon in run.me.all_pokemon_in_play():
            if mon is not run.source and mon.damage_counters:
                mon.damage_counters += num(n)

    return [pre(replace), after(act)]


@phrase("Move a {E} Energy from this Pokémon to {N} of your Benched Pokémon")
def _move_typed_to_bench(symbol: str, _n: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        mon = run.source
        if mon is None or kind_ not in mon.attached_energies or not run.me.bench:
            return
        target = core.best_energy_target(run.ctx.state, run.ctx.player_id, kind_, _on_bench(run.me))
        if target is not None:
            core.attach_energy_card(target, core.detach_energy(mon, kind_))

    return after(act)


@phrase("You can use this attack only if you go second, and only during your first turn")
def _only_going_second() -> Step:
    def act(run: Run) -> None:
        if run.ctx.turn != 2:
            run.cancelled = True

    return pre(act)


@phrase("This attack's base damage is {N}", "this attack's base damage is {N}")
def _base_damage_is(n: str) -> Step:
    def act(run: Run) -> None:
        run.damage = num(n)

    return pre(act)


@phrase("Put up to {N} Basic Pokémon you find there onto your opponent's Bench")
def _fill_opp_bench(n: str) -> Step:
    def act(run: Run) -> None:
        room = core.bench_space(run.ctx.state, run.opp)
        basics = [c for c in run.opp.hand if c.is_basic][: min(num(n), room)]
        for card in basics:
            run.opp.hand.remove(card)
            core.put_on_bench(run.ctx.state, run.opp, card)

    return after(act)


@phrase(
    "During your opponent's next turn, attacks used by the Defending Pokémon cost {E} more, and "
    "its Retreat Cost is {E} more"
)
def _tax(*_: str) -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.taxed_turn = run.ctx.turn + 1

    return after(act)


@phrase(
    "If you put any Pokémon onto your Bench in this way, move an Energy from this Pokémon to the "
    "new Benched Pokémon"
)
def _energy_to_newcomer() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        newcomer = run.me.bench[-1] if run.me.bench else None
        if mon is None or newcomer is None or not mon.attached_energies:
            return
        if newcomer.turn_played == run.ctx.turn:
            energy_name = core.least_useful_energy(mon)
            core.attach_energy_card(newcomer, core.detach_energy(mon, energy_name))

    return after(act)


# ---------------------------------------------------------------------------
# lote 5: Treinadores e Estádios restantes


def _choose_own(qualifier: str, limit: int) -> Act:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier) or (lambda m: False)

    def act(run: Run) -> None:
        mons = [m for m in run.me.all_pokemon_in_play() if test(m)]
        ranked = sorted(mons, key=lambda m: (m is not run.me.active, -len(m.attached_energies)))
        run.chosen = ranked[:limit]

    return act


@phrase("Choose up to {N} of your (.*?)Pokémon", "Choose {N} of your (.*?)Pokémon in play")
def _choose_own_phrase(n: str, qualifier: str) -> Step | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    if pokemon_filter(qualifier) is None:
        return None
    return Step("before", _choose_own(qualifier, num(n)))


@phrase(
    "For each of those Pokémon, search your deck for a Basic {E} Energy card and attach it to "
    "that Pokémon"
)
def _energy_each_chosen(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        run.picked = []
        for mon in run.chosen:
            card = next(
                (c for c in run.me.deck if is_basic_energy(c) and energy_type_of(c) == kind_),
                None,
            )
            if card is None:
                break
            run.me.deck.remove(card)
            core.attach_energy_card(mon, card)
            run.last_target = mon if mon is run.me.active else run.last_target
        core.shuffle_deck(run.me)

    return after(act)


@phrase("If you attached Energy to your Active Pokémon in this way, it is now {S}")
def _poison_own_active(name: str) -> Step:
    status = STATUSES[name.capitalize()]

    def act(run: Run) -> None:
        active = run.me.active
        immune = active is not None and passives.immune_to_special_conditions(run.ctx.state, active)
        if active is not None and run.last_target is active and not immune:
            active.status = status

    return after(act)


@phrase(
    "During your opponent's next turn, prevent all damage from and effects of attacks done to "
    "that Pokémon by your opponent's Pokémon ex"
)
def _shield_chosen_ex() -> Step:
    def act(run: Run) -> None:
        for mon in run.chosen:
            mon.shield = ("ex", run.ctx.turn + 1)

    return after(act)


@phrase("You may reveal a Pokémon and a Trainer card you find there and put them into your hand")
def _take_pokemon_and_trainer() -> Step:
    def act(run: Run) -> None:
        for test in (lambda c: c.is_pokemon, lambda c: c.supertype.value == "Trainer"):
            found = [c for c in run.picked if test(c) and c in run.me.deck]
            if found:
                best = core.choose_cards(run.ctx.state, run.ctx.player_id, found, 1)[0]
                run.me.deck.remove(best)
                run.me.hand.append(best)

    return after(act)


@phrase("You may reveal a Pokémon you find there and put it into your hand")
def _take_pokemon() -> Step:
    def act(run: Run) -> None:
        found = [c for c in run.picked if c.is_pokemon and c in run.me.deck]
        if found:
            best = core.choose_cards(run.ctx.state, run.ctx.player_id, found, 1)[0]
            run.me.deck.remove(best)
            run.me.hand.append(best)

    return after(act)


@phrase("Look at the bottom {N} cards of your deck")
def _look_bottom(n: str) -> Step:
    def act(run: Run) -> None:
        run.picked = run.me.deck[-num(n) :]

    return after(act)


@phrase("During your opponent's next turn, their Poisoned Pokémon can't retreat")
def _poisoned_stay() -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            if mon.status == StatusCondition.POISONED:
                mon.cannot_retreat_turn = run.ctx.turn + 1

    return after(act)


@phrase("At the end of this turn, if you have {N} or more cards in your hand, discard your hand")
def _discard_hand_later(n: str) -> Step:
    def act(run: Run) -> None:
        run.me.discard_hand_at_end = (num(n), run.ctx.turn)

    return after(act)


@phrase(
    "Discard an Energy card from your hand in order to draw cards until you have as many cards "
    "in your hand as you have {E} Pokémon in play"
)
def _garden(symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        card = next((c for c in run.me.hand if c.supertype.value == "Energy"), None)
        if card is None:
            return
        run.me.hand.remove(card)
        run.me.discard.append(card)
        wanted = sum(1 for m in run.me.all_pokemon_in_play() if pokemon_type(m.card) == kind_)
        core.draw_until(run.me, wanted)

    return after(act)


@phrase("Reveal up to {N} Pokémon in your hand and put them into your deck")
def _pokemon_back(n: str) -> Step:
    def act(run: Run) -> None:
        mons = sorted(
            (c for c in run.me.hand if c.is_pokemon),
            key=lambda c: core.card_priority(run.ctx.state, run.ctx.player_id, c),
        )[: num(n)]
        for card in mons:
            run.me.hand.remove(card)
            run.me.deck.append(card)
        run.counted = len(mons)
        run.did = bool(mons)

    return after(act)


@phrase(
    "Search your deck for up to that many Pokémon, reveal them, and put them into your hand",
    "search your deck for up to that many Pokémon, reveal them, and put them into your hand",
)
def _search_that_many() -> Step:
    return after(lambda run: core.search_deck(run.ctx, lambda c: c.is_pokemon, run.counted))


@phrase(
    "During your opponent's next turn, all of your (.*?)Pokémon take {N} less damage from attacks "
    "from your opponent's Pokémon"
)
def _team_guard(qualifier: str, n: str) -> Step | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier)
    if test is None:
        return None

    def act(run: Run) -> None:
        for mon in run.me.all_pokemon_in_play():
            if test(mon):
                mon.damage_reduction = (num(n), run.ctx.turn + 1)

    return after(act)


@kind(r"Pokémon card")
def _k_pokemon_card() -> CardFilter:
    return lambda card: card.is_pokemon


@kind(r"Basic (.+?'s) Pokémon")
def _k_basic_group(group: str) -> CardFilter:
    return lambda card: card.is_basic and card.name.startswith(group)


@kind(r"Stage 1 Pokémon", r"Stage 2 Pokémon")
def _k_stage() -> CardFilter:
    stage = "Stage 2" if "Stage 2" in _current[0] else "Stage 1"
    return lambda card: card.is_pokemon and stage_of(card) == stage


@kind(r'{X} that have "?{X}"? in their name')
def _k_named_part_plural(what: str, part: str) -> CardFilter | None:
    base = parse_kind(what)
    return None if base is None else (lambda card: base(card) and part in card.name)


@phrase("Before drawing cards, you may discard any number of cards from your hand")
def _trim_before_draw() -> Step:
    def act(run: Run) -> None:
        for card in [
            c for c in run.me.hand if core.card_priority(run.ctx.state, run.ctx.player_id, c) < 40
        ]:
            run.me.hand.remove(card)
            run.me.discard.append(card)

    return pre(act)


@phrase(
    "Look at the top {N} cards of your deck and put a {X} you find there onto your Bench",
)
def _top_to_bench(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None

    def act(run: Run) -> None:
        found = [c for c in run.me.deck[: num(n)] if card_filter(c) and c.is_basic]
        if found and core.bench_space(run.ctx.state, run.me):
            run.me.deck.remove(found[0])
            core.put_on_bench(run.ctx.state, run.me, found[0])

    return after(act)


@phrase("Shuffle the other cards and put them on the bottom of your deck")
def _others_to_bottom() -> Step:
    return after(lambda run: None)  # ordem do fundo do deck não é modelada


@phrase("Switch your Active {E} Pokémon with {N} of your Benched {E} Pokémon")
def _switch_typed_active(a: str, _n: str, b: str) -> Step:
    kind_ = energy(a)

    def act(run: Run) -> None:
        active = run.me.active
        typed = [i for i, m in enumerate(run.me.bench) if pokemon_type(m.card) == energy(b)]
        if active is None or pokemon_type(active.card) != kind_ or not typed:
            return
        core.switch_active(
            run.ctx.state, run.me, max(typed, key=lambda i: len(run.me.bench[i].attached_energies))
        )

    return after(act)


@phrase("Put {N} cards from your hand on the bottom of your deck in any order")
def _hand_to_bottom(n: str) -> Step:
    def act(run: Run) -> None:
        run.did = len(run.me.hand) >= num(n)
        if not run.did:
            return
        worst = sorted(
            run.me.hand, key=lambda c: core.card_priority(run.ctx.state, run.ctx.player_id, c)
        )[: num(n)]
        for card in worst:
            run.me.hand.remove(card)
            run.me.deck.append(card)

    return after(act)


@condition(r"you put {N} cards on the bottom of your deck in this way")
def _c_put_bottom(_n: str) -> Predicate:
    return lambda run: run.did


@condition(
    r"you played a Supporter card from your hand this turn",
    r"you healed any damage in this way",
)
def _c_played_supporter_or_did() -> Predicate:
    healed = "healed" in _current[0]
    return (lambda run: run.did) if healed else (lambda run: run.me.supporter_played_this_turn)


@phrase(
    "Your opponent reveals their hand, and you choose an Energy card you find there and put it on "
    "the bottom of their deck"
)
def _energy_to_their_bottom() -> Step:
    def act(run: Run) -> None:
        card = next((c for c in run.opp.hand if c.supertype.value == "Energy"), None)
        if card is not None:
            run.opp.hand.remove(card)
            run.opp.deck.append(card)

    return after(act)


@count(r"of your opponent's Mega Evolution Pokémon ex in play")
def _n_opp_megas() -> Counter_:
    return lambda run: sum(1 for m in run.opp.all_pokemon_in_play() if "Mega" in m.card.subtypes)


@phrase(
    "Reveal the top {N} cards of your opponent's deck",
)
def _reveal_opp_top(n: str) -> Step:
    def act(run: Run) -> None:
        run.picked = run.opp.deck[: num(n)]

    return after(act)


@phrase(
    "You may choose any number of Basic Pokémon you find there and put those Pokémon onto their "
    "Bench"
)
def _fill_their_bench_from_deck() -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.picked if c.is_basic and c in run.opp.deck]:
            if not core.bench_space(run.ctx.state, run.opp):
                break
            run.opp.deck.remove(card)
            core.put_on_bench(run.ctx.state, run.opp, card)

    return after(act)


@phrase("Your opponent shuffles the other cards back into their deck")
def _opp_shuffles() -> Step:
    return after(lambda run: core.shuffle_deck(run.opp))


@phrase(
    "Each player discards cards from their hand until they have {N} cards in their hand",
)
def _hand_trimmer(n: str) -> Step:
    def act(run: Run) -> None:
        for pid in (run.ctx.opp_id, run.ctx.player_id):
            player = run.ctx.state.state_of(pid)
            extra = len(player.hand) - num(n)
            if extra > 0:
                core.discard_from_hand(run.ctx, extra, player_id=pid)

    return after(act)


@phrase("Your opponent discards first")
def _they_first() -> Step:
    return after(lambda run: None)  # a ordem já é essa em `_hand_trimmer`


@phrase(
    "Search your deck for a Pokémon with the same name as {N} of your opponent's Pokémon in play, "
    "reveal it, and put it into your hand"
)
def _love_ball(_n: str) -> Step:
    def act(run: Run) -> None:
        names = {m.card.name for m in run.opp.all_pokemon_in_play()}
        core.search_deck(run.ctx, lambda c: c.is_pokemon and c.name in names, 1)

    return after(act)


@phrase(
    "Count your Prize cards, shuffle them, and put them on the bottom of your deck",
)
def _prizes_to_bottom() -> Step:
    def act(run: Run) -> None:
        run.counted = len(run.me.prizes)
        run.me.deck.extend(run.me.prizes)
        run.me.prizes = []

    return after(act)


@phrase(
    "Take that many cards from the top of your deck and put them face down as your Prize cards",
    "take that many cards from the top of your deck and put them face down as your Prize cards",
)
def _new_prizes() -> Step:
    def act(run: Run) -> None:
        run.me.prizes = run.me.deck[: run.counted]
        del run.me.deck[: run.counted]

    return after(act)


@phrase("Attach a Basic Energy card from your discard pile to each of your (.*?)Pokémon")
def _energy_each_kind_discard(qualifier: str) -> Step | None:
    from pokemon_companion.engine.effects.passive_text import pokemon_filter

    test = pokemon_filter(qualifier)
    if test is None:
        return None

    def act(run: Run) -> None:
        for mon in [m for m in run.me.all_pokemon_in_play() if test(m)]:
            card = next((c for c in run.me.discard if is_basic_energy(c)), None)
            if card is None:
                return
            run.me.discard.remove(card)
            core.attach_energy_card(mon, card)

    return after(act)


@phrase("If that Pokémon is an {X} Pokémon, heal {N} damage from it instead")
def _heal_more_if_group(group: str, n: str) -> Step:
    earlier = re.search(r"Heal (\d+) damage from your Active Pokémon", _whole[0])
    first = int(earlier.group(1)) if earlier else 0

    def act(run: Run) -> None:
        active = run.me.active
        if active is not None and active.card.name.startswith(group):
            core.heal(active, max(num(n) - first, 0))  # a cura menor já aconteceu

    return after(act)


@phrase("Heal {N} damage from your Active {E} Pokémon")
def _heal_active_typed(n: str, symbol: str) -> Step:
    kind_ = energy(symbol)

    def act(run: Run) -> None:
        active = run.me.active
        if active is not None and pokemon_type(active.card) == kind_:
            core.heal(active, num(n))

    return after(act)


@phrase("Search your deck for up to {N} cards and discard them")
def _search_and_discard(n: str) -> Step:
    def act(run: Run) -> None:
        energies = [c for c in run.me.deck if c.supertype.value == "Energy"][: num(n)]
        for card in energies:
            run.me.deck.remove(card)
            run.me.discard.append(card)

    return after(act)


@phrase("Put an Energy attached to {N} of your opponent's Pokémon into their hand")
def _bounce_any_energy(_n: str) -> Step:
    def act(run: Run) -> None:
        holders = [m for m in run.opp.all_pokemon_in_play() if m.attached_energies]
        if holders:
            mon = max(holders, key=lambda m: len(m.attached_energies))
            run.opp.hand.append(core.detach_energy(mon, mon.attached_energies[0]))

    return after(act)


@phrase(
    "Look at the top {N} cards of your deck and put them back in any order, or shuffle them and "
    "put them on the bottom of your deck"
)
def _deduction(n: str) -> Step:
    def act(run: Run) -> None:
        top = run.me.deck[: num(n)]
        weak = [c for c in top if core.card_priority(run.ctx.state, run.ctx.player_id, c) < 45]
        if len(weak) * 2 > len(top):
            del run.me.deck[: len(top)]
            run.me.deck.extend(top)

    return after(act)


@phrase(
    "Search your deck for any number of Basic Energy cards of different types, reveal them, and "
    "put them into your hand"
)
def _rainbow_all() -> list[Step] | Step | None:
    return _search_rainbow("9")


@phrase("Put {N} damage counters on your Active Pokémon")
def _counters_own_active(n: str) -> Step:
    def act(run: Run) -> None:
        if run.me.active is not None:
            run.me.active.damage_counters += 10 * num(n)

    return after(act)


@phrase(
    "Your opponent counts the cards in their hand, shuffles those cards, and puts them on the "
    "bottom of their deck"
)
def _memo() -> Step:
    def act(run: Run) -> None:
        run.counted = len(run.opp.hand)
        run.opp.deck.extend(run.opp.hand)
        run.opp.hand.clear()
        run.did = run.counted > 0

    return after(act)


@phrase("They draw that many cards")
def _they_draw_that_many() -> Step:
    return after(lambda run: core.draw(run.opp, run.counted))


@phrase(
    "You may move any amount of Energy from the Pokémon you moved to your Bench to the new Active "
    "Pokémon",
    "Move any amount of Energy from the Pokémon you moved to your Bench to the new Active Pokémon",
)
def _carry_energy() -> Step:
    def act(run: Run) -> None:
        active, donor = run.me.active, run.last_target
        if active is None or donor is None or donor is active:
            return
        for energy_name in list(donor.attached_energies):
            core.attach_energy_card(active, core.detach_energy(donor, energy_name))

    return after(act)


@phrase(
    "Look at the top {N} cards of your deck and attach a Basic Energy card you find there to {N} "
    "of your Pokémon"
)
def _waitress(n: str, _k: str) -> Step:
    def act(run: Run) -> None:
        top = [c for c in run.me.deck[: num(n)] if is_basic_energy(c)]
        if top:
            run.me.deck.remove(top[0])
            core.attach_from(run.ctx, [top[0]], lambda c: True, 1)

    return after(act)


@phrase("Ask your opponent if each player may take a Prize card")
def _bargain_ask() -> Step:
    def act(run: Run) -> None:
        # o oponente aceita quando não está atrás na corrida de prêmios
        run.did = len(run.opp.prizes) <= len(run.me.prizes)

    return after(act)


@phrase("If yes, each player takes a Prize card")
def _bargain_yes() -> Step:
    def act(run: Run) -> None:
        if not run.did:
            return
        for player in (run.me, run.opp):
            if player.prizes:
                player.hand.append(player.prizes.pop())

    return after(act)


@phrase("If no, you draw {N} cards")
def _bargain_no(n: str) -> Step:
    return after(lambda run: None if run.did else core.draw(run.me, num(n)))


@count(r"Pokémon you find there")
def _n_found_pokemon() -> Counter_:
    return lambda run: sum(1 for c in run.opp.hand if c.is_pokemon)


# ---------------------------------------------------------------------------
# lote 6: efeitos de Habilidades ativadas e de entrada em jogo


@phrase("Put {N} damage counters on each of them", "put {N} damage counters on each of them")
def _counters_on_chosen(n: str) -> Step:
    return after(_counters_on(lambda r: list(r.chosen), num(n)))


@phrase(
    "Switch in {N} of your opponent's Benched Pokémon that has {N} HP or less remaining to the "
    "Active Spot"
)
def _gust_weak(_n: str, hp: str) -> Step:
    def act(run: Run) -> None:
        weak = [i for i, m in enumerate(run.opp.bench) if m.current_hp <= num(hp)]
        if weak:
            best = max(weak, key=lambda i: core._prize_value(run.opp.bench[i].card))
            core.switch_active(run.ctx.state, run.opp, best)

    return Step("before", act)


@phrase(
    "Have your opponent reveal their hand and you put any number of Basic Pokémon you find there "
    "onto their Bench"
)
def _fill_their_bench_all() -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.opp.hand if c.is_basic]:
            if not core.bench_space(run.ctx.state, run.opp):
                break
            run.opp.hand.remove(card)
            core.put_on_bench(run.ctx.state, run.opp, card)

    return after(act)


@phrase(
    "Your opponent reveals their hand, and you put a Basic Pokémon with {N} HP or less that you "
    "find there onto your opponent's Bench"
)
def _fill_their_bench_one(hp: str) -> Step:
    def act(run: Run) -> None:
        small = [c for c in run.opp.hand if c.is_basic and (c.hp or 0) <= num(hp)]
        if small and core.bench_space(run.ctx.state, run.opp):
            run.opp.hand.remove(small[0])
            core.put_on_bench(run.ctx.state, run.opp, small[0])

    return after(act)


def _heal_all_of(test: Callable[[Run, PokemonInPlay], bool]) -> Act:
    def act(run: Run) -> None:
        run.chosen = []
        for mon in run.me.all_pokemon_in_play():
            if test(run, mon) and core.heal(mon, mon.damage_counters):
                run.chosen.append(mon)
        run.did = bool(run.chosen)

    return act


@phrase("Heal all damage from each of your (.*?)Pokémon")
def _heal_all_each(qualifier: str) -> Step | None:
    test = _qualifier(qualifier)
    if test is None:
        return None
    only = test
    return after(_heal_all_of(lambda run, m: only(m)))


@phrase("Heal all damage from your Active (.*?)Pokémon")
def _heal_all_active(qualifier: str) -> Step | None:
    test = _qualifier(qualifier)
    if test is None:
        return None
    only = test
    return after(_heal_all_of(lambda run, m: m is run.me.active and only(m)))


@phrase(
    "If you healed any damage in this way, discard all Energy from those Pokémon",
    "If you healed any damage in this way, discard all Energy from that Pokémon",
)
def _discard_energy_of_healed() -> Step:
    def act(run: Run) -> None:
        for mon in run.chosen:
            for energy_name in list(mon.attached_energies):
                run.me.discard.append(core.detach_energy(mon, energy_name))

    return after(act)


@kind(r"(.+ Energy) card")
def _k_special_energy_named(name: str) -> CardFilter | None:
    words = name.split()
    if len(words) < 2 or words[0] == "Basic" or any(not w[0].isupper() for w in words):
        return None
    return lambda card: card.name == name


@phrase("Heal {N} damage from your Active Pokémon and have it recover from a Special Condition")
def _heal_and_recover(n: str) -> Step:
    def act(run: Run) -> None:
        active = run.me.active
        if active is not None:
            core.heal(active, num(n))
            active.status = StatusCondition.NONE

    return after(act)


@phrase("Search your deck for a card")
def _search_card_for_top() -> Step:
    def act(run: Run) -> None:
        if run.me.deck:
            run.picked = core.choose_cards(run.ctx.state, run.ctx.player_id, run.me.deck, 1)

    return after(act)


@phrase("Shuffle your deck, then put that card on top of it")
def _put_on_top() -> Step:
    def act(run: Run) -> None:
        for card in run.picked:
            if card in run.me.deck:
                run.me.deck.remove(card)
        core.shuffle_deck(run.me)
        run.me.deck[:0] = [c for c in run.picked]

    return after(act)


@phrase(
    "Devolve {N} of your opponent's evolved Pokémon by putting the highest Stage Evolution card "
    "on it into your opponent's hand"
)
def _devolve_one(_n: str) -> Step:
    def act(run: Run) -> None:
        evolved = [m for m in run.opp.all_pokemon_in_play() if m.prior_cards]
        if not evolved:
            return
        mon = max(evolved, key=lambda m: (core._prize_value(m.card), m.card.hp or 0))
        run.opp.hand.append(mon.card)
        mon.card = mon.prior_cards[0]
        mon.prior_cards = mon.prior_cards[1:]
        mon.hp_bonus = passives.hp_bonus(run.ctx.state, mon)

    return after(act)


@phrase("Switch a card from your hand with the top card of your deck")
def _swap_with_top() -> Step:
    def act(run: Run) -> None:
        if not run.me.hand or not run.me.deck:
            return
        worst = min(
            run.me.hand, key=lambda c: core.card_priority(run.ctx.state, run.ctx.player_id, c)
        )
        run.me.hand.remove(worst)
        run.me.hand.append(run.me.deck.pop(0))
        run.me.deck.insert(0, worst)

    return after(act)


def _other_than(mon: PokemonInPlay) -> Callable[[PokemonInPlay], bool]:
    return lambda other: other is not mon


@phrase("Move a Basic Energy from {N} of your Pokémon to another of your Pokémon")
def _shift_energy(_n: str) -> Step:
    def act(run: Run) -> None:
        for donor in run.me.all_pokemon_in_play():
            spare = [e for e in donor.attached_energies if e in core.BASIC_ENERGIES]
            if not spare:
                continue
            choice = core.least_useful_energy(donor) if donor.attached_energies else spare[0]
            if choice not in core.BASIC_ENERGIES:
                choice = spare[0]
            target = core.best_energy_target(
                run.ctx.state, run.ctx.player_id, choice, _other_than(donor)
            )
            if target is not None:
                core.attach_energy_card(target, core.detach_energy(donor, choice))
            return

    return after(act)


@phrase(
    "Search your deck for a Basic {E} Energy card, a Basic {E} Energy card, or 1 of each and "
    "attach them to your {E} Pokémon and {E} Pokémon in any way you like"
)
def _search_two_types(a: str, b: str, *_: str) -> Step:
    kinds = [energy(a), energy(b)]

    def act(run: Run) -> None:
        for kind_ in kinds:
            card = next(
                (c for c in run.me.deck if is_basic_energy(c) and energy_type_of(c) == kind_),
                None,
            )
            if card is None:
                continue
            target = core.best_energy_target(
                run.ctx.state,
                run.ctx.player_id,
                kind_,
                lambda m: pokemon_type(m.card) in kinds,
            )
            if target is not None:
                run.me.deck.remove(card)
                core.attach_energy_card(target, card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase("If heads, choose Burned, Confused, or Poisoned")
def _choose_condition() -> Step:
    def act(run: Run) -> None:
        if not run.heads:
            return
        # Confuso ameaça mais o próximo ataque; se já está Confuso, Envenenado
        defender = run.defender
        confused = defender is not None and defender.status == StatusCondition.CONFUSED
        run.chosen_status = StatusCondition.POISONED if confused else StatusCondition.CONFUSED

    return after(act)


@phrase("Your opponent's Active Pokémon is now affected by that Special Condition")
def _apply_chosen_condition() -> Step:
    def act(run: Run) -> None:
        if run.chosen_status is not None:
            attacks.status_on_defender(run.ctx, run.chosen_status)

    return after(act)


@phrase("Your opponent shuffles their hand and puts it on the bottom of their deck")
def _their_hand_to_bottom() -> Step:
    def act(run: Run) -> None:
        run.counted = len(run.opp.hand)
        run.opp.deck.extend(run.opp.hand)
        run.opp.hand.clear()
        run.did = run.counted > 0

    return after(act)


@phrase("If they put any cards on the bottom of their deck in this way, they draw {N} cards")
def _they_draw(n: str) -> Step:
    return after(lambda run: core.draw(run.opp, num(n)) if run.did else None)


@phrase(
    "Look at the top {N} cards of your deck and attach any number of {X} you find there to your "
    "Pokémon in any way you like"
)
def _attach_from_top(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None

    def act(run: Run) -> None:
        found = [c for c in run.me.deck[: num(n)] if card_filter(c)]
        for card in found:
            run.me.deck.remove(card)
        core.attach_from(run.ctx, found, lambda c: True, len(found))
        run.me.deck.extend(found)
        core.shuffle_deck(run.me)

    return after(act)


@phrase("Switch this Pokémon with your Active Pokémon")
def _switch_in_self_2() -> list[Step] | Step | None:
    return _switch_in_self()


@phrase("Search your deck for up to {N} {X} and discard them")
def _search_discard_kind(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    if card_filter is None:
        return None

    def act(run: Run) -> None:
        for card in [c for c in run.me.deck if card_filter(c)][: num(n)]:
            run.me.deck.remove(card)
            run.me.discard.append(card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase("Search your deck for {X} and put it onto this {X} to evolve it")
def _evolve_self_named(what: str, _base: str) -> Step | None:
    options = re.sub(r"^(?:an?|any) ", "", what.strip())
    card_filter = _combo_filter(options) if " or " in options else parse_kind(options)
    if card_filter is None:
        return None

    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        card = next(
            (c for c in run.me.deck if card_filter(c) and c.evolves_from == mon.card.name), None
        )
        if card is not None:
            run.me.deck.remove(card)
            core.evolve_into(run.ctx.state, run.me, mon, card)
        core.shuffle_deck(run.me)

    return after(act)


@condition(r"this Pokémon has any Energy attached")
def _c_self_any_energy() -> Predicate:
    return lambda run: run.source is not None and bool(run.source.attached_energies)


@kind(r"{E} Pokémon with {N} HP or less")
def _k_typed_small(symbol: str, hp: str) -> CardFilter:
    kind_ = energy(symbol)
    return (
        lambda card: card.is_pokemon and pokemon_type(card) == kind_ and (card.hp or 0) <= num(hp)
    )


# ---------------------------------------------------------------------------
# lote 7: cauda final dos ataques


def special_energy_name(text: str) -> str:
    """ "Shadowy {D}" → "Shadowy Darkness Energy"; "Team Rocket's" → "Team
    Rocket's Energy"."""
    text = re.sub(expand("{E}"), lambda m: energy(m.group(1)), text.strip())
    return text if text.endswith("Energy") else f"{text} Energy"


@condition(r"this Pokémon was healed during this turn")
def _c_healed() -> Predicate:
    return lambda run: run.source is not None and run.source.healed_this_turn


@condition(r"your Benched Pokémon have any (.+?) Energy attached")
def _c_bench_special(name: str) -> Predicate:
    wanted = special_energy_name(name)
    return lambda run: any(wanted in m.attached_energies for m in run.me.bench)


@condition(
    r"this Pokémon has any (.+?) Energy attached", r"this Pokémon has no (.+?) Energy attached"
)
def _c_self_special_named(name: str) -> Predicate | None:
    if re.fullmatch(expand("{E}"), name.strip()):
        return None  # "{L} Energy" básico é outra regra
    wanted = special_energy_name(name)
    negate = " has no " in _current[0]
    return lambda run: run.source is not None and (
        (wanted in run.source.attached_energies) != negate
    )


@condition(r"this Pokémon has {N} or more {E} Energy attached")
def _c_self_energy_count(n: str, symbol: str) -> Predicate:
    kind_ = energy(symbol)
    return lambda run: run.source is not None and run.source.attached_energies.count(kind_) >= num(
        n
    )


@condition(r"{X} and {X} are on your Bench")
def _c_both_on_bench(a: str, b: str) -> Predicate | None:
    names = [card_name(a), card_name(b)]
    if any(n is None for n in names):
        return None
    return lambda run: all(any(m.card.name == n for m in run.me.bench) for n in names)


@condition(r"any of your Benched {X} have any damage counters on them")
def _c_bench_named_hurt(name: str) -> Predicate | None:
    found = card_name(name)
    return (
        None
        if found is None
        else (lambda run: any(m.card.name == found and m.damage_counters for m in run.me.bench))
    )


@condition(r'a Pokémon that has "(.+?)" in its name is on your Bench')
def _c_bench_named_part(part: str) -> Predicate:
    return lambda run: any(part in m.card.name for m in run.me.bench)


@condition(r"your opponent's Active Pokémon is a Tera Pokémon")
def _c_tera() -> Predicate:
    return _defender_is(lambda m: is_tera(m.card))


@condition(r"this Pokémon has more Energy attached than your opponent's Active Pokémon")
def _c_more_energy() -> Predicate:
    return lambda run: (
        run.source is not None
        and run.defender is not None
        and len(run.source.attached_energies) > len(run.defender.attached_energies)
    )


@condition(
    r"this Pokémon and your opponent's Active Pokémon have the same amount of Energy attached"
)
def _c_same_energy() -> Predicate:
    return lambda run: (
        run.source is not None
        and run.defender is not None
        and len(run.source.attached_energies) == len(run.defender.attached_energies)
    )


@condition(r"the Defending Pokémon is a Basic Pokémon")
def _c_defender_basic() -> Predicate:
    return _defender_is(lambda m: stage_of(m.card) == "Basic")


@condition(r"you don't have {N} or more {X} in your discard pile")
def _c_not_discard_count(n: str, what: str) -> Predicate | None:
    positive = _c_discard_count(n, what)
    return None if positive is None else (lambda run: not positive(run))


@condition(r"the Retreat Cost of your opponent's Active Pokémon is ((?:[\[{]C[\]}])+) or more")
def _c_heavy(symbols: str) -> Predicate:
    least = len(re.findall(r"[\[{]C[\]}]", symbols))
    return lambda run: run.defender is not None and (
        passives.retreat_cost(run.ctx.state, run.ctx.opp_id, run.defender) >= least
    )


@condition(r"your opponent has {N} or more Benched Pokémon")
def _c_opp_bench_size(n: str) -> Predicate:
    return lambda run: len(run.opp.bench) >= num(n)


@condition(r"this Pokémon used {X} during your last turn")
def _c_used_last(name: str) -> Predicate:
    return lambda run: run.me.last_attack == (name.strip(), run.ctx.turn - 2)


@count(r"Pokémon Tool attached to all Pokémon")
def _n_all_tools() -> Counter_:
    return lambda run: sum(
        1 for m in run.me.all_pokemon_in_play() + run.opp.all_pokemon_in_play() if m.tool
    )


@count(r"Special Energy attached to all of your opponent's Pokémon")
def _n_opp_special() -> Counter_:
    return lambda run: sum(len(m.special_energy_cards) for m in run.opp.all_pokemon_in_play())


@count(r"of your Pokémon that has any damage counters on it")
def _n_my_hurt() -> Counter_:
    return lambda run: sum(1 for m in run.me.all_pokemon_in_play() if m.damage_counters)


@count(r"Prize card your opponent took during their last turn")
def _n_prizes_last_turn() -> Counter_:
    def count_(run: Run) -> int:
        last = run.opp.prizes_taken_last
        return last[0] if last and last[1] == run.ctx.turn - 1 else 0

    return count_


@count(r"Basic Energy attached to this Pokémon")
def _n_own_basic() -> Counter_:
    return lambda run: (
        sum(1 for e in run.source.attached_energies if e in core.BASIC_ENERGIES)
        if run.source
        else 0
    )


@count(r"{X} you discarded in this way")
def _n_you_discarded(what: str) -> Counter_ | None:
    return _n_discarded_kind(what)


@kind(r"Pokémon with {E} Resistance")
def _k_resistance(symbol: str) -> CardFilter:
    kind_ = energy(symbol)
    return lambda card: card.is_pokemon and any(r.energy_type == kind_ for r in card.resistances)


# efeitos


@phrase("You may turn {N} of your face-down Prize cards face up")
def _turn_prize_up(_n: str) -> Step:
    def act(run: Run) -> None:
        run.did = run.me.face_up_prizes < len(run.me.prizes)
        if run.did and not run.estimate:
            run.me.face_up_prizes += 1

    return pre(act)


@phrase("Choose {N} of your opponent's Pokémon {N} times")
def _choose_repeatedly(_n: str, times: str) -> Step:
    def act(run: Run) -> None:
        run.main_hit = False
        run.counted = num(times)

    return pre(act)


@phrase("For each time you chose a Pokémon, do {N} damage to it")
def _salvo(n: str) -> list[Step]:
    def estimate(run: Run) -> None:
        run.damage = num(n) * run.counted

    def act(run: Run) -> None:
        for _ in range(run.counted):
            targets = attacks.best_damage_targets(run.ctx, num(n), 1)
            if targets:
                attacks.hit(run.ctx, targets[0], num(n), weakness=False, resistance=False)

    return [pre(estimate), after(act)]


@phrase("This damage isn't affected by Weakness or Resistance")
def _salvo_note() -> Step:
    return after(lambda run: None)  # já aplicado em `_salvo`


@phrase(
    "Choose Basic {E} Energy cards from your discard pile up to the amount of Energy attached to "
    "all of your opponent's Pokémon and attach them to your {E} Pokémon in any way you like"
)
def _tail_generator(symbol: str, target_symbol: str) -> Step:
    kind_, target_kind = energy(symbol), energy(target_symbol)

    def act(run: Run) -> None:
        amount = sum(len(m.attached_energies) for m in run.opp.all_pokemon_in_play())
        core.attach_from(
            run.ctx,
            run.me.discard,
            _basic_energy_of(kind_),
            amount,
            allowed=lambda m: pokemon_type(m.card) == target_kind,
        )

    return after(act)


@phrase("Reveal the top {N} cards of your deck", "Reveal the bottom {N} cards of your deck")
def _reveal_own(n: str) -> Step:
    bottom = "bottom" in _current[0]

    def act(run: Run) -> None:
        run.picked = run.me.deck[-num(n) :] if bottom else run.me.deck[: num(n)]

    return pre(act)


@count(
    r"{X} card you find there that has the {X} attack",
    r"Pokémon you find there that has the {X} attack",
)
def _n_found_with_attack(*groups: str) -> Counter_:
    name = groups[-1]
    return lambda run: sum(
        1 for c in run.picked if c.is_pokemon and any(a.name == name for a in c.attacks)
    )


@count(r"Future card you find there")
def _n_found_future() -> Counter_:
    from pokemon_companion.engine.effects.cardinfo import is_future

    return lambda run: sum(1 for c in run.picked if is_future(c) or "Future" in c.subtypes)


@phrase("Then, discard those Future cards and shuffle the other cards back into your deck")
def _discard_future() -> Step:
    from pokemon_companion.engine.effects.cardinfo import is_future

    def act(run: Run) -> None:
        for card in [c for c in run.picked if is_future(c) or "Future" in c.subtypes]:
            if card in run.me.deck:
                run.me.deck.remove(card)
                run.me.discard.append(card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase(
    "Reveal the bottom {N} cards of your deck, and this attack does {N} damage for each Pokémon "
    "you find there that has the {X} attack"
)
def _bug_out(n: str, per: str, name: str) -> list[Step]:
    def act(run: Run) -> None:
        run.picked = run.me.deck[-num(n) :]
        run.damage = num(per) * sum(
            1 for c in run.picked if c.is_pokemon and any(a.name == name for a in c.attacks)
        )

    return [pre(act)]


@phrase("Then, shuffle any revealed Pokémon back into your deck")
def _revealed_pokemon_back() -> Step:
    return after(lambda run: core.shuffle_deck(run.me))


@phrase("Discard the other cards")
def _discard_others() -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.picked if not c.is_pokemon]:
            if card in run.me.deck:
                run.me.deck.remove(card)
                run.me.discard.append(card)

    return after(act)


@phrase(
    "Choose {N} of your opponent's Pokémon and flip a coin for each of your Pokémon in play that "
    'has "(.+?)" in its name'
)
def _target_together(_n: str, part: str) -> Step:
    def act(run: Run) -> None:
        run.main_hit = False
        total = sum(1 for m in run.me.all_pokemon_in_play() if part in m.card.name)
        run.heads = (
            total // 2 + total % 2 if run.estimate else sum(run.coin() for _ in range(total))
        )

    return pre(act)


@phrase("This attack does {N} damage to the chosen Pokémon for each heads")
def _hit_chosen_per_heads(n: str) -> list[Step]:
    def estimate(run: Run) -> None:
        run.damage = num(n) * run.heads

    def act(run: Run) -> None:
        targets = attacks.best_damage_targets(run.ctx, num(n) * run.heads, 1)
        if targets:
            attacks.hit(run.ctx, targets[0], num(n) * run.heads)

    return [pre(estimate), after(act)]


def _attach_trap(kind_: str) -> Act:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.attach_trap = (kind_, run.ctx.turn + 1)

    return act


@phrase(
    "During your opponent's next turn, Energy can't be attached from your opponent's hand to the "
    "Defending Pokémon",
    "During your opponent's next turn, Energy cards can't be attached from your opponent's hand "
    "to that Pokémon",
)
def _attach_lock() -> Step:
    return after(_attach_trap("lock"))


@phrase(
    "During your opponent's next turn, if they attach an Energy card from their hand to the "
    "Defending Pokémon, their turn ends"
)
def _attach_ends_turn() -> Step:
    return after(_attach_trap("end_turn"))


@phrase(
    "During your opponent's next turn, whenever they attach an Energy card from their hand to the "
    "Defending Pokémon, place {N} damage counters on that Pokémon"
)
def _attach_counters(n: str) -> Step:
    return after(_attach_trap(f"counters:{num(n)}"))


@phrase(
    "During your opponent's next turn, if the Defending Pokémon tries to use an attack, your "
    "opponent flips {N} coins"
)
def _smokescreen_n(n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None:
            defender.attack_coin_turn = run.ctx.turn + 1
            defender.attack_coins = num(n)

    return after(act)


@phrase("If either of them is tails, that attack doesn't happen")
def _smokescreen_rest_n() -> Step:
    return after(lambda run: None)


@phrase(
    "Each player may attach up to {N} Basic Energy cards from their hand to their Pokémon in any "
    "way they like"
)
def _pleasing_present(n: str) -> Step:
    def act(run: Run) -> None:
        for pid in (run.ctx.opp_id, run.ctx.player_id):
            ctx = Ctx(run.ctx.state, pid, messages=run.ctx.messages)
            core.attach_from(ctx, run.ctx.state.state_of(pid).hand, is_basic_energy, num(n))

    return after(act)


@phrase("Your opponent does this first")
def _opp_first() -> Step:
    return after(lambda run: None)  # a ordem já é essa em `_pleasing_present`


def _use_supporter(run: Run, card: Card) -> None:
    from pokemon_companion.engine.effects import trainers

    spec = trainers.spec_for(card)
    if spec is not None:
        run.ctx.log(f"Usou o efeito de {card.name}.")
        spec.fn(Ctx(run.ctx.state, run.ctx.player_id, None, None, run.ctx.messages))


@phrase(
    "Discard the top card of your deck, and if that card is a Supporter card, use the effect of "
    "that card as the effect of this attack"
)
def _shapeshifter() -> Step:
    def act(run: Run) -> None:
        if not run.me.deck:
            return
        card = run.me.deck.pop(0)
        run.me.discard.append(card)
        if trainer_kind(card) == "Supporter":
            _use_supporter(run, card)

    return after(act)


@phrase("You may use the effect of a Supporter card you find there as the effect of this attack")
def _look_alike() -> Step:
    def act(run: Run) -> None:
        supporters = [c for c in run.opp.hand if trainer_kind(c) == "Supporter"]
        if supporters:
            _use_supporter(run, supporters[0])

    return after(act)


@phrase("Discard up to {N} {X} from your Pokémon")
def _discard_up_to_team(n: str, what: str) -> Step | None:
    card_filter = parse_kind(what)
    return pre(_among_your_pokemon(card_filter, num(n))) if card_filter else None


@phrase("You may put {N} Energy attached to your opponent's Active Stage 2 Pokémon into their hand")
def _bounce_from_stage2(n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is None or stage_of(defender.card) != "Stage 2":
            return
        for _ in range(num(n)):
            if not defender.attached_energies:
                return
            run.opp.hand.append(core.detach_energy(defender, defender.attached_energies[0]))

    return after(act)


@phrase(
    "During your opponent's next turn, Pokémon that have {N} or less Energy attached can't attack"
)
def _frigid(n: str) -> Step:
    def act(run: Run) -> None:
        run.opp.attack_energy_min = (num(n), run.ctx.turn + 1)

    return after(act)


@phrase(
    "Place {N} damage counter on {N} of your opponent's Pokémon",
    "Put {N} damage counter on {N} of your opponent's Pokémon",
)
def _one_counter(n: str, k: str) -> list[Step] | Step | None:
    return _counters_one(n, k)


@phrase("Put {N} damage counters instead of {N} on that Pokémon for this Special Condition")
def _strong_confusion(n: str, _base: str) -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is not None and defender.status == StatusCondition.CONFUSED:
            defender.confusion_damage = 10 * num(n)

    return after(act)


@phrase(
    "Choose {N} of your opponent's Active Pokémon's attacks",
)
def _choose_their_attack(_n: str) -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is not None and defender.card.attacks:
            strongest = max(defender.card.attacks, key=lambda a: a.base_damage)
            run.chosen = [defender]
            run.counted = defender.card.attacks.index(strongest)

    return after(act)


@phrase("During your opponent's next turn, that Pokémon can't use that attack")
def _lock_chosen_attack() -> Step:
    def act(run: Run) -> None:
        for mon in run.chosen:
            if 0 <= run.counted < len(mon.card.attacks):
                mon.blocked_attack = (mon.card.attacks[run.counted].name, run.ctx.turn + 1)

    return after(act)


@phrase("Discard all Item cards and Pokémon Tool cards you find there")
def _discard_items_found() -> Step:
    def act(run: Run) -> None:
        for card in [c for c in run.opp.hand if trainer_kind(c) in ("Item", "Tool")]:
            run.opp.hand.remove(card)
            run.opp.discard.append(card)

    return after(act)


@phrase(
    "Put up to {N} damage counters on this Pokémon",
)
def _self_counters_up_to(n: str) -> Step:
    per = re.search(r"(\d+) damage for each damage counter you placed", _whole[0])
    unit = int(per.group(1)) if per else 0

    def act(run: Run) -> None:
        mon = run.source
        if mon is None:
            return
        room = max((mon.current_hp - 10) // 10, 0)  # não se nocauteia
        wanted = min(num(n), room)
        if unit and run.defender is not None:
            wanted = min(wanted, -(-run.defender.current_hp // unit))
        run.counted = wanted
        if not run.estimate:
            mon.damage_counters += 10 * wanted

    return pre(act)


@count(r"damage counter you placed in this way")
def _n_placed() -> Counter_:
    return lambda run: run.counted


@phrase(
    "Search your deck for an amount of Basic Energy up to the number of heads and attach it to "
    "this Pokémon"
)
def _energy_per_heads() -> Step:
    def act(run: Run) -> None:
        me = run.source
        core.attach_from(
            run.ctx, run.me.deck, is_basic_energy, run.heads, allowed=lambda m: m is me
        )
        core.shuffle_deck(run.me)

    return after(act)


@phrase("If you use this attack when you have exactly {N} Prize card remaining, you win this game")
def _victory_symbol(n: str) -> Step:
    def act(run: Run) -> None:
        if len(run.me.prizes) == num(n):
            run.ctx.state.winner = run.ctx.player_id
            run.ctx.log("Condição especial de vitória!")

    return after(act)


@phrase(
    "Search your deck for up to {N} Pokémon that are the same type as any Basic Energy attached "
    "to this Pokémon, reveal them, and put them into your hand"
)
def _same_type_search(n: str) -> Step:
    def act(run: Run) -> None:
        kinds = {
            e
            for e in (run.source.attached_energies if run.source else [])
            if e in core.BASIC_ENERGIES
        }
        core.search_deck(run.ctx, lambda c: c.is_pokemon and pokemon_type(c) in kinds, num(n))

    return after(act)


@phrase("Discard random cards from your opponent's hand until they have {N} cards in their hand")
def _random_trim(n: str) -> Step:
    def act(run: Run) -> None:
        import random

        while len(run.opp.hand) > num(n):
            card = random.choice(run.opp.hand)
            run.opp.hand.remove(card)
            run.opp.discard.append(card)

    return after(act)


@phrase("Move all Energy from this Pokémon to your Benched Pokémon in any way you like")
def _spread_all_energy() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or not run.me.bench:
            return
        for energy_name in list(mon.attached_energies):
            target = core.best_energy_target(
                run.ctx.state, run.ctx.player_id, energy_name, _on_bench(run.me)
            )
            if target is not None:
                core.attach_energy_card(target, core.detach_energy(mon, energy_name))

    return after(act)


@phrase("Search your deck for any number of {X} and put them onto your Bench")
def _search_bench_any(what: str) -> Step | None:
    return _search(f"up to 8 {what}", "bench")


@phrase("During your next turn, if the Defending Pokémon is Knocked Out, take {N} more Prize cards")
def _bounty(n: str) -> Step:
    def act(run: Run) -> None:
        if run.defender is not None:
            run.defender.bounty = (num(n), run.ctx.turn + 2)

    return after(act)


@phrase("Heal {N} damage from {N} of your Benched (.+?)Pokémon")
def _heal_bench_kind(n: str, k: str, qualifier: str) -> Step | None:
    test = _qualifier(qualifier)
    if test is None:
        return None
    only = test

    def act(run: Run) -> None:
        mons = [m for m in run.me.bench if only(m)]
        for mon in sorted(mons, key=lambda m: -m.damage_counters)[: num(k)]:
            core.heal(mon, num(n))

    return after(act)


@phrase("Knock Out {N} of your opponent's Pokémon")
def _ko_one(_n: str) -> Step:
    def act(run: Run) -> None:
        mons = run.opp.all_pokemon_in_play()
        if mons:
            _nocaute(run, max(mons, key=lambda m: (core._prize_value(m.card), -m.current_hp)))

    return after(act)


@phrase("This attack does {N} damage to {N} of your opponent's Benched Pokémon ex")
def _snipe_bench_ex(n: str, _k: str) -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False
        run.damage = num(n)

    def act(run: Run) -> None:
        targets = [i for i, m in enumerate(run.opp.bench) if is_ex(m.card)]
        if targets:
            attacks.hit(run.ctx, min(targets, key=lambda i: run.opp.bench[i].current_hp), num(n))

    return [pre(replace), after(act)]


@phrase(
    "Attach an amount of Basic Energy up to the number of heads from your discard pile to your "
    "Benched Pokémon in any way you like",
    "Attach a number of Basic {E} Energy cards up to the number of heads from your discard pile "
    "to your Benched Pokémon in any way you like",
)
def _attach_per_heads(symbol: str | None = None) -> Step:
    card_filter = _basic_energy_of(energy(symbol)) if symbol else is_basic_energy

    def act(run: Run) -> None:
        core.attach_from(run.ctx, run.me.discard, card_filter, run.heads, allowed=_on_bench(run.me))

    return after(act)


@phrase("Your opponent flips a coin for each of their Benched Pokémon")
def _opp_flips_per_bench() -> Step:
    return pre(_flip(lambda r: len(r.opp.bench)))


@phrase("This attack does {N} damage to your opponent's Active Pokémon for each tails")
def _per_tails(n: str) -> Step:
    def act(run: Run) -> None:
        run.damage = num(n) * run.tails

    return pre(act)


@phrase("You can use this attack only if this Pokémon used {X} during your last turn")
def _only_after(name: str) -> Step:
    def act(run: Run) -> None:
        if run.me.last_attack != (name.strip(), run.ctx.turn - 2):
            run.cancelled = True

    return pre(act)


@phrase("Put {N} damage counters on each Pokémon that has an Ability")
def _counters_on_ability_holders(n: str) -> Step:
    def act(run: Run) -> None:
        for mon in run.opp.all_pokemon_in_play():
            if mon.card.abilities:
                core.place_counters(run.ctx, run.ctx.opp_id, mon, num(n))
        for mon in run.me.all_pokemon_in_play():
            if mon.card.abilities:
                mon.damage_counters += 10 * num(n)

    return after(act)


@phrase(
    "If this Pokémon has any damage counters on it, this attack can be used for {E}",
    "If this Pokémon is affected by a Special Condition, ignore all Energy in this attack's cost",
)
def _cost_note(*_: str) -> Step:
    return after(lambda run: None)  # custo alternativo: `passives.attack_cost`


@phrase("Place {N} damage counters on {N} of your opponent's Benched Pokémon")
def _counters_bench_one(n: str, _k: str) -> Step:
    def act(run: Run) -> None:
        if run.opp.bench:
            target = max(run.opp.bench, key=lambda m: (core._prize_value(m.card), -m.current_hp))
            core.place_counters(run.ctx, run.ctx.opp_id, target, num(n))

    return after(act)


@phrase("If heads, choose a Special Condition")
def _choose_any_condition() -> Step:
    def act(run: Run) -> None:
        if run.heads:
            defender = run.defender
            confused = defender is not None and defender.status == StatusCondition.CONFUSED
            run.chosen_status = StatusCondition.ASLEEP if confused else StatusCondition.CONFUSED

    return after(act)


@phrase(
    "You may shuffle all Energy attached to this Pokémon into your deck and have this attack do "
    "{N} more damage"
)
def _energy_to_deck_for_damage(n: str) -> Step:
    def act(run: Run) -> None:
        mon = run.source
        if mon is None or not mon.attached_energies:
            return
        run.damage += num(n)
        if run.estimate:
            return
        for energy_name in list(mon.attached_energies):
            run.me.deck.append(core.detach_energy(mon, energy_name))
        core.shuffle_deck(run.me)

    return pre(act)


@phrase(
    "Your opponent reveals their hand, and you choose a card you find there and put it on the "
    "bottom of their deck"
)
def _card_to_their_bottom() -> Step:
    def act(run: Run) -> None:
        if run.opp.hand:
            best = core.choose_cards(run.ctx.state, run.ctx.opp_id, run.opp.hand, 1)[0]
            run.opp.hand.remove(best)
            run.opp.deck.append(best)

    return after(act)


@phrase(
    "Search your deck for a number of cards up to the number of heads and put them into your hand",
    "Search your deck for a number of cards up to the number of your Benched Pokémon and put "
    "them into your hand",
)
def _search_counted() -> Step:
    by_heads = "heads" in _current[0]

    def act(run: Run) -> None:
        amount = run.heads if by_heads else len(run.me.bench)
        core.search_deck(run.ctx, lambda c: True, amount)

    return after(act)


@phrase(
    "If the Defending Pokémon is a Basic Pokémon, it can't attack during your opponent's next turn"
)
def _stomp() -> Step:
    def act(run: Run) -> None:
        defender = run.exposed_defender
        if defender is not None and stage_of(defender.card) == "Basic":
            defender.cannot_attack_turn = run.ctx.turn + 1

    return after(act)


@phrase("Discard all Pokémon Tools from this Pokémon")
def _discard_own_tool() -> Step:
    def act(run: Run) -> None:
        mon = run.source
        run.did = mon is not None and mon.tool is not None
        if run.did and mon is not None and mon.tool is not None:
            run.me.discard.append(mon.tool)
            mon.tool = None

    return Step("before", act)


@phrase("If you can't discard any, this attack does nothing")
def _cancel_if_none() -> Step:
    def act(run: Run) -> None:
        if not run.did:
            run.main_hit = False

    return Step("before", act)


@phrase(
    "This attack also does {N} damage to each Benched Pokémon that has any damage counters on it"
)
def _sandstorm(n: str) -> Step:
    def act(run: Run) -> None:
        for i, mon in enumerate(list(run.opp.bench)):
            if mon.damage_counters:
                attacks.hit(run.ctx, i, num(n))
        for mon in run.me.bench:
            if mon.damage_counters:
                mon.damage_counters += num(n)

    return after(act)


@phrase(
    "You may move any number of damage counters from your opponent's Benched Pokémon to their "
    "Active Pokémon"
)
def _collect_counters() -> Step:
    def act(run: Run) -> None:
        defender = run.defender
        if defender is None or passives.counters_locked(run.ctx.state):
            return
        for mon in run.opp.bench:
            defender.damage_counters += mon.damage_counters
            mon.damage_counters = 0

    return after(act)


@phrase(
    "For each of those Pokémon, search your deck for a card that evolves from that Pokémon and "
    "put it onto that Pokémon to evolve it"
)
def _evolve_chosen() -> Step:
    def act(run: Run) -> None:
        for mon in list(run.chosen):
            card = next((c for c in run.me.deck if c.evolves_from == mon.card.name), None)
            if card is not None and mon in run.me.all_pokemon_in_play():
                run.me.deck.remove(card)
                core.evolve_into(run.ctx.state, run.me, mon, card)
        core.shuffle_deck(run.me)

    return after(act)


@phrase(
    "Attach up to {N} Energy cards from your opponent's discard pile to their Pokémon in any way "
    "you like"
)
def _feed_opponent(n: str) -> Step:
    def act(run: Run) -> None:
        # a escolha é de quem ataca: energia que não ajuda, no Pokémon que menos a usa
        found = [c for c in run.opp.discard if c.supertype.value == "Energy"][: num(n)]
        mons = run.opp.all_pokemon_in_play()
        for card in found:
            if not mons:
                return
            run.opp.discard.remove(card)
            core.attach_energy_card(min(mons, key=lambda m: len(m.card.attacks)), card)

    return after(act)


@phrase("Both Active Pokémon are now {S}")
def _both_status(name: str) -> Step:
    status = STATUSES[name.capitalize()]

    def act(run: Run) -> None:
        attacks.status_on_defender(run.ctx, status)
        mon = run.source
        if mon is not None and not passives.immune_to_special_conditions(run.ctx.state, mon):
            mon.status = status

    return after(act)


@phrase(
    "You may move any amount of Energy from your Pokémon to your other Pokémon in any way you like",
    "You may move any amount of {E} Energy from your Pokémon to your other Pokémon in any way "
    "you like",
)
def _rebalance(symbol: str | None = None) -> Step:
    kind_ = energy(symbol) if symbol else None

    def act(run: Run) -> None:
        active = run.me.active
        if active is None:
            return
        attack_ = max(active.card.attacks, key=lambda a: a.base_damage, default=None)
        for donor in list(run.me.bench):
            for energy_name in list(donor.attached_energies):
                if attack_ is None or passives.can_pay(
                    run.ctx.state, run.ctx.player_id, active, attack_
                ):
                    return
                if kind_ is None or energy_name == kind_:
                    core.attach_energy_card(active, core.detach_energy(donor, energy_name))

    return after(act)


@phrase(
    "Search your deck for {N} cards, shuffle your deck, then put those cards on top of it in any order"
)
def _stack_top(n: str) -> Step:
    def act(run: Run) -> None:
        chosen = core.choose_cards(run.ctx.state, run.ctx.player_id, run.me.deck, num(n))
        for card in chosen:
            run.me.deck.remove(card)
        core.shuffle_deck(run.me)
        run.me.deck[:0] = chosen

    return after(act)


@phrase("Discard an Energy from your opponent's Active Pokémon ex")
def _singe_ex() -> Step:
    def act(run: Run) -> None:
        if run.defender is not None and is_ex(run.defender.card):
            _discard_from_defender(1, False)(run)

    return after(act)


@phrase(
    "If {N} of them is heads, this attack does {N} more damage",
    "If {N} of them are heads, this attack does {N} more damage",
)
def _tiered(k: str, n: str) -> Step:
    def act(run: Run) -> None:
        if run.heads == num(k):
            run.damage += num(n)

    return pre(act)


@phrase("If all of them are heads, this attack does {N} more damage")
def _tiered_all(n: str) -> Step:
    def act(run: Run) -> None:
        if run.heads and not run.tails:
            run.damage += num(n)

    return pre(act)


# ---------------------------------------------------------------------------
# lote 8: mover energia repetidamente, cópia do topo do deck, fraqueza,
# redução contra Evolução, Treinador que volta ao deck, mãos no fundo do deck


def _provides(attached: str, kind_: str | None) -> bool:
    return kind_ is None or TYPED_SPECIAL_ENERGIES.get(attached, attached) == kind_


def _move_one(
    run: Run,
    donors: list[PokemonInPlay],
    receiver: PokemonInPlay | None,
    test: Callable[[str], bool],
) -> None:
    """Move 1 energia que passe em `test` de um doador para `receiver`
    (sempre no mesmo sentido, para o uso repetido não ficar indo e vindo)."""
    if receiver is None:
        return
    for donor in donors:
        if donor is receiver:
            continue
        spare = [e for e in donor.attached_energies if test(e)]
        if spare:
            core.attach_energy_card(receiver, core.detach_energy(donor, spare[-1]))
            return


def _needs_energy(run: Run, mon: PokemonInPlay | None) -> bool:
    """O receptor ainda não paga o ataque mais caro."""
    if mon is None:
        return False
    biggest = max((len(a.cost) for a in mon.card.attacks), default=0)
    return len(mon.attached_energies) < biggest


@phrase("Move a Basic {E} Energy from {N} of your Pokémon to another of your Pokémon")
def _shift_typed_basic(e: str, _n: str) -> Step:
    kind_ = energy(e)

    def act(run: Run) -> None:
        active = run.me.active
        if _needs_energy(run, active):
            _move_one(
                run, list(run.me.bench), active, lambda x: x in core.BASIC_ENERGIES and x == kind_
            )

    return after(act)


@phrase("Move a {E} Energy from {N} of your Benched Pokémon to your Active Pokémon")
def _bench_to_active(e: str, _n: str) -> Step:
    kind_ = energy(e)

    def act(run: Run) -> None:
        if _needs_energy(run, run.me.active):
            _move_one(run, list(run.me.bench), run.me.active, lambda x: _provides(x, kind_))

    return after(act)


@phrase("Move an Energy from {N} of your other Pokémon to this Pokémon")
def _gather_to_self(_n: str) -> Step:
    def act(run: Run) -> None:
        if _needs_energy(run, run.source):
            _move_one(run, run.me.all_pokemon_in_play(), run.source, lambda x: True)

    return after(act)


@phrase("You may choose an attack from a Pokémon you find there and use it as this attack")
def _copy_from_revealed() -> list[Step]:
    def replace(run: Run) -> None:
        run.main_hit = False

    def act(run: Run) -> None:
        found = [
            a
            for c in run.picked
            if c.is_pokemon
            for a in c.attacks
            if a.name not in attacks.COPY_ATTACKS
        ]
        if found:
            attacks.use_copied(run.ctx, max(found, key=lambda a: a.base_damage))

    return [pre(replace), after(act)]


@phrase("Shuffle the revealed cards into your opponent's deck")
def _reshuffle_opp() -> Step:
    return after(lambda run: core.shuffle_deck(run.opp))


@phrase("Until the end of your next turn, the Defending Pokémon's Weakness is now {E}")
def _set_weakness(e: str) -> Step:
    kind_ = energy(e)

    def act(run: Run) -> None:
        if run.defender is not None:
            run.defender.weakness_to = (kind_, run.ctx.turn + 2)

    return after(act)


@phrase(
    "During your opponent's next turn, this Pokémon takes {N} less damage from attacks from "
    "Evolution Pokémon"
)
def _less_from_evolutions(n: str) -> Step:
    def act(run: Run) -> None:
        if run.source is not None:
            run.source.shield = (f"less:{num(n)}:evolution", run.ctx.turn + 1)

    return after(act)


@phrase(
    "If you drew any cards in this way and if (.+?) is in play, shuffle this .+? into your deck "
    "instead of discarding it"
)
def _back_to_deck_with_stadium(stadium: str) -> Step:
    def act(run: Run) -> None:
        if run.drawn and passives.stadium_is(run.ctx.state, stadium):
            run.ctx.card_to_deck = True

    return after(act)


@phrase("Each player shuffles their hand and puts it on the bottom of their deck")
def _hands_to_bottom() -> Step:
    def act(run: Run) -> None:
        moved = 0
        for side in (run.me, run.opp):
            random.shuffle(side.hand)
            moved += len(side.hand)
            side.deck.extend(side.hand)
            side.hand.clear()
        run.did = moved > 0

    return after(act)


@phrase(
    "If either player put any cards on the bottom of their deck in this way, each player flips a "
    "coin"
)
def _each_player_flips() -> Step:
    def act(run: Run) -> None:
        if run.did:
            run.coins_by_player = {pid: core.coin() for pid in (run.ctx.player_id, run.ctx.opp_id)}

    return after(act)


@phrase("If heads, that player draws {N} cards")
def _each_heads_draws(n: str) -> Step:
    return after(lambda run: _each_draws(run, True, num(n)))


@phrase("If tails, they draw {N} cards")
def _each_tails_draws(n: str) -> Step:
    return after(lambda run: _each_draws(run, False, num(n)))


def _each_draws(run: Run, heads: bool, amount: int) -> None:
    for pid, result in run.coins_by_player.items():
        if result == heads:
            side = run.ctx.state.state_of(pid)
            got = core.draw(side, amount)
            if pid == run.ctx.player_id:
                run.drawn += got


@phrase(
    'Search your deck for up to {N} Item cards that have "(.+?)" in (?:their|its) name and put '
    "them onto your Bench"
)
def _items_onto_bench(n: str, part: str) -> Step:
    """Itens que entram em jogo como Pokémon (os Fósseis "Antique")."""

    def act(run: Run) -> None:
        found = [c for c in run.me.deck if part in c.name and is_fossil_item(c)][: num(n)]
        for card in found:
            if not core.bench_space(run.ctx.state, run.me):
                break
            run.me.deck.remove(card)
            core.put_on_bench(run.ctx.state, run.me, fossil_pokemon(card))
        core.shuffle_deck(run.me)

    return after(act)
