"""Habilidades passivas lidas do texto: efeitos contínuos que as regras
consultam na hora certa (dano recebido e causado, HP, recuo, condições
especiais, contra-ataque, travas de cartas).

Cada frase reconhecida vira uma `Passive` (dados, sem estado); `passives.py`
pergunta `rules_in_play(...)` nos pontos em que já consulta as Habilidades
escritas à mão. Habilidade com texto não reconhecido fica sem efeito — nunca
com um efeito parecido. Habilidades já tratadas à mão (nome citado no código
de `passives`/`rules`/`core`) são ignoradas aqui, para não contar duas vezes.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, lru_cache

from pokemon_companion.cards_db.models import Ability, Card
from pokemon_companion.engine.effects.cardinfo import (
    has_rule_box,
    is_evolution,
    is_ex,
    is_tera,
    pokemon_type,
    stage_of,
)
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay, StatusCondition

MonTest = Callable[[PokemonInPlay], bool]
#: condição avaliada com (estado, dono da Habilidade, Pokémon que a tem)
HolderTest = Callable[[GameState, PlayerId, PokemonInPlay], bool]
Scaler = Callable[[GameState, PlayerId, PokemonInPlay], int]


def _always(*_: object) -> bool:
    return True


@dataclass(frozen=True)
class Passive:
    #: "reduce", "weaken" (ataques do Ativo do oponente fazem menos), "prevent", "prevent_effects", "prevent_big", "bonus", "pierce",
    #: "hp", "no_retreat", "retreat", "on_damaged", "on_knocked_out", "immune",
    #: "survive_full", "survive_coin", "coin_prevent", "lock_items", "lock_tools",
    #: "lock_stadiums", "ability_shield"
    kind: str
    amount: int = 0
    #: onde o dono da Habilidade precisa estar: "any", "active", "bench"
    holder_at: str = "any"
    #: a quem se aplica: "self", "team" (seus Pokémon), "opp_active"
    scope: str = "self"
    target: MonTest = _always
    attacker: MonTest = _always
    defender: MonTest = _always
    when: HolderTest = _always
    per: Scaler | None = None
    status: StatusCondition | None = None
    #: contra-ataque: (contadores, status, descartar energia)
    counters: int = 0
    discard_energy: bool = False
    stacks: bool = True

    def value(self, state: GameState, owner: PlayerId, holder: PokemonInPlay) -> int:
        return self.amount * (self.per(state, owner, holder) if self.per else 1)


# ---------------------------------------------------------------------------
# vocabulário

SYMBOLS = {
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
E = r"[\[{](\w)[\]}]"
N = r"(\d+)"
STATUSES = {s.name.capitalize(): s for s in StatusCondition if s != StatusCondition.NONE}


def energy(symbol: str) -> str:
    return SYMBOLS.get(symbol.upper(), "Colorless")


def _clean(text: str) -> str:
    text = re.sub(r"\s*\([^)]*\)", "", text).replace("’", "'")
    return text.strip()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", _clean(text))
    return [p.rstrip(".").strip() for p in parts if p.strip()]


def _pokemon_filter(text: str) -> MonTest | None:
    """ "{W} ", "Evolution {R} ", "Hop's ", "Basic ", "Tera ", "" → filtro."""
    text = text.strip()
    if not text:
        return _always
    parts = text.split()
    tests: list[MonTest] = []
    for part in parts:
        typed = re.fullmatch(E, part)
        if typed:
            kind_ = energy(typed.group(1))
            tests.append(_of_type(kind_))
        elif part == "Evolution":
            tests.append(lambda m: is_evolution(m.card))
        elif part == "Basic":
            tests.append(lambda m: stage_of(m.card) == "Basic")
        elif part == "Tera":
            tests.append(lambda m: is_tera(m.card))
        elif re.fullmatch(r"[A-Z][\w.]*'s", part):
            tests.append(_of_group(part))
        else:
            return None
    return lambda m: all(test(m) for test in tests)


def _of_type(kind_: str) -> MonTest:
    return lambda m: pokemon_type(m.card) == kind_


def _of_group(group: str) -> MonTest:
    return lambda m: m.card.name.startswith(group)


def _attacker_filter(text: str) -> MonTest | None:
    """Fim de "from your opponent's ..." (quem ataca)."""
    text = text.strip()
    table: dict[str, MonTest] = {
        "Pokémon": _always,
        "Pokémon ex": lambda m: is_ex(m.card),
        "Basic Pokémon ex": lambda m: is_ex(m.card) and stage_of(m.card) == "Basic",
        "Pokémon that have an Ability": lambda m: bool(m.card.abilities),
        "Tera Pokémon": lambda m: is_tera(m.card),
        "Pokémon that have any Special Energy attached": lambda m: bool(m.special_energy_cards),
        "Pokémon ex and Pokémon V": lambda m: is_ex(m.card) or "V" in m.card.subtypes,
        "Pokémon with a Rule Box": lambda m: has_rule_box(m.card),
    }
    if text in table:
        return table[text]
    few = re.fullmatch(rf"Pokémon that have {N} or less Energy attached", text)
    if few:
        limit = int(few.group(1))
        return lambda m: len(m.attached_energies) <= limit
    either = re.fullmatch(rf"{E} or {E} Pokémon", text)
    if either:
        kinds = {energy(either.group(1)), energy(either.group(2))}
        return lambda m: pokemon_type(m.card) in kinds
    single = re.fullmatch(rf"{E} Pokémon", text)
    if single:
        kind_ = energy(single.group(1))
        return lambda m: pokemon_type(m.card) == kind_
    return None


def _self_condition(text: str) -> HolderTest | None:
    """ "If this Pokémon has ..." / "If you have ..." aplicados ao dono."""
    text = text.strip()
    if text == "this Pokémon has any Energy attached":
        return lambda s, o, h: bool(h.attached_energies)
    if text == "this Pokémon has no Energy attached":
        return lambda s, o, h: not h.attached_energies
    if text == "this Pokémon has any Special Energy attached":
        return lambda s, o, h: bool(h.special_energy_cards)
    typed = re.fullmatch(rf"this Pokémon has any {E} Energy attached", text)
    if typed:
        kind_ = energy(typed.group(1))
        return lambda s, o, h: kind_ in h.attached_energies
    counters = re.fullmatch(rf"this Pokémon has {N} or more damage counters on it", text)
    if counters:
        least = int(counters.group(1))
        return lambda s, o, h: h.damage_counters // 10 >= least
    if text == "you have the same number of cards in your hand as your opponent":
        return lambda s, o, h: len(s.state_of(o).hand) == len(s.state_of(o.other).hand)
    mega = re.fullmatch(rf"you have any {E} Mega Evolution Pokémon ex in play", text)
    if mega:
        kind_ = energy(mega.group(1))
        return lambda s, o, h: any(
            "Mega" in m.card.subtypes and pokemon_type(m.card) == kind_
            for m in s.state_of(o).all_pokemon_in_play()
        )
    return None


def _prizes_taken_by_opponent(state: GameState, owner: PlayerId, _h: PokemonInPlay) -> int:
    return state.prize_count - len(state.state_of(owner.other).prizes)


def _energy_count(kind_: str) -> Scaler:
    return lambda s, o, h: h.attached_energies.count(kind_)


# ---------------------------------------------------------------------------
# frases

Rule = tuple[re.Pattern[str], Callable[..., list[Passive] | Passive | None]]
RULES: list[Rule] = []


def rule(*patterns: str) -> Callable[[Callable[..., object]], Callable[..., object]]:
    def decorator(fn: Callable[..., object]) -> Callable[..., object]:
        for pattern in patterns:
            RULES.append((re.compile(pattern), fn))  # type: ignore[arg-type]
        return fn

    return decorator


_HOLDER_AT = {"in the Active Spot": "active", "on your Bench": "bench"}


def _with_holder(passives: list[Passive], where: str) -> list[Passive]:
    return [Passive(**{**p.__dict__, "holder_at": where}) for p in passives]


def _with_when(passives: list[Passive], when: HolderTest) -> list[Passive]:
    return [Passive(**{**p.__dict__, "when": when}) for p in passives]


@rule(
    rf"This Pokémon takes {N} less damage from attacks", rf"it takes {N} less damage from attacks"
)
def _reduce_self(n: str) -> Passive:
    return Passive("reduce", int(n))


@rule(rf"This Pokémon takes {N} less damage from attacks from your opponent's (.+)")
def _reduce_self_from(n: str, who: str) -> Passive | None:
    attacker = _attacker_filter(who)
    return None if attacker is None else Passive("reduce", int(n), attacker=attacker)


@rule(
    rf"All of your (.*?)Pokémon take {N} less damage from attacks from your opponent's Pokémon",
    rf"All of your (.*?)Pokémon take {N} less damage from attacks",
)
def _reduce_team(who: str, n: str) -> Passive | None:
    target = _pokemon_filter(who)
    return None if target is None else Passive("reduce", int(n), scope="team", target=target)


@rule(
    rf"All of your Pokémon that have any {E} Energy attached take {N} less damage from attacks "
    r"from your opponent's Pokémon",
    rf"All of your Pokémon that have {E} Energy attached take {N} less damage from attacks "
    r"from your opponent's Pokémon",
)
def _reduce_team_energy(symbol: str, n: str) -> Passive:
    kind_ = energy(symbol)
    return Passive("reduce", int(n), scope="team", target=lambda m: kind_ in m.attached_energies)


@rule(rf"Attacks used by your opponent's Active Pokémon do {N} less damage")
def _weaken_opponent(n: str) -> Passive:
    return Passive("weaken", int(n))


@rule(
    rf"Attacks used by your opponent's Active Pokémon that has a Pokémon Tool attached do {N} "
    r"less damage"
)
def _weaken_tooled(n: str) -> Passive:
    return Passive("weaken", int(n), attacker=lambda m: m.tool is not None)


@rule(
    r"Prevent all damage done to this Pokémon by attacks from your opponent's (.+)",
    r"Prevent all damage from attacks done to this Pokémon by your opponent's (.+)",
)
def _prevent_from(who: str) -> Passive | None:
    big = re.fullmatch(rf"Pokémon if that damage is {N} or more", who)
    if big:
        return Passive("prevent_big", int(big.group(1)))
    attacker = _attacker_filter(who)
    return None if attacker is None else Passive("prevent", attacker=attacker)


@rule(
    r"Prevent all damage from and effects of attacks from your opponent's (.+) done to this "
    r"Pokémon",
    r"Prevent all damage from and effects of attacks done to this Pokémon by your opponent's (.+)",
)
def _prevent_all_from(who: str) -> list[Passive] | None:
    attacker = _attacker_filter(who)
    if attacker is None:
        return None
    return [Passive("prevent", attacker=attacker), Passive("prevent_effects", attacker=attacker)]


@rule(
    r"Prevent all effects of attacks used by your opponent's Pokémon done to this Pokémon",
)
def _prevent_effects() -> Passive:
    return Passive("prevent_effects")


@rule(r"Prevent all effects of your opponent's Pokémon's Abilities done to this Pokémon")
def _ability_shield() -> Passive:
    return Passive("ability_shield")


@rule(
    r"Prevent all damage done to each of your Pokémon by attacks from your opponent's (.+)",
)
def _prevent_team(who: str) -> Passive | None:
    attacker = _attacker_filter(who)
    return None if attacker is None else Passive("prevent", scope="team", attacker=attacker)


@rule(
    rf"Attacks used by your (.*?)Pokémon do {N} more damage to your opponent's Active (.*?)Pokémon",
    rf"Attacks used by your (.*?)Pokémon and (.*?)Pokémon do {N} more damage to your "
    r"opponent's Active Pokémon",
)
def _bonus_team(*groups: str) -> Passive | None:
    if len(groups) == 4:
        first, second, n = _pokemon_filter(groups[0]), _pokemon_filter(groups[1]), groups[2]
        if first is None or second is None:
            return None
        f, s = first, second
        return Passive("bonus", int(n), scope="team", target=lambda m: f(m) or s(m))
    who, n, defender = groups
    target = _pokemon_filter(who)
    against = _pokemon_filter(defender)
    if target is None or against is None:
        return None
    return Passive("bonus", int(n), scope="team", target=target, defender=against)


@rule(
    rf"Attacks used by this Pokémon do {N} more damage to your opponent's Active Pokémon",
    rf"attacks used by this Pokémon do {N} more damage to your opponent's Active Pokémon",
    rf"the attacks it uses do {N} more damage to your opponent's Active Pokémon",
)
def _bonus_self(n: str) -> Passive:
    return Passive("bonus", int(n))


@rule(
    rf"Attacks used by this Pokémon do {N} more damage to your opponent's Active Pokémon for "
    r"each Prize card your opponent has taken"
)
def _bonus_per_prize(n: str) -> Passive:
    return Passive("bonus", int(n), per=_prizes_taken_by_opponent)


@rule(
    r"Damage from attacks used by this Pokémon isn't affected by any effects on your opponent's "
    r"Active Pokémon"
)
def _pierce() -> Passive:
    return Passive("pierce")


@rule(rf"This Pokémon gets \+{N} HP", rf"it gets \+{N} HP")
def _hp(n: str) -> Passive:
    return Passive("hp", int(n))


@rule(rf"This Pokémon gets \+{N} HP for each {E} Energy attached to it")
def _hp_per_energy(n: str, symbol: str) -> Passive:
    return Passive("hp", int(n), per=_energy_count(energy(symbol)))


@rule(rf"This Pokémon gets \+{N} HP for each Prize card your opponent has taken")
def _hp_per_prize(n: str) -> Passive:
    return Passive("hp", int(n), per=_prizes_taken_by_opponent)


@rule(rf"All of your Pokémon in play get \+{N} HP")
def _hp_team(n: str) -> Passive:
    return Passive("hp", int(n), scope="team")


@rule(r"it has no Retreat Cost", r"This Pokémon has no Retreat Cost")
def _free_retreat() -> Passive:
    return Passive("no_retreat")


@rule(rf"All of your Pokémon that have {E} Energy attached have no Retreat Cost")
def _free_retreat_team(symbol: str) -> Passive:
    kind_ = energy(symbol)
    return Passive("no_retreat", scope="team", target=lambda m: kind_ in m.attached_energies)


@rule(r"your Active Pokémon's Retreat Cost is ((?:[\[{]C[\]}])+) less")
def _cheaper_retreat(symbols: str) -> Passive:
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return Passive("retreat", -amount, scope="own_active")


@rule(r"Your opponent's Active (.*?)Pokémon's Retreat Cost is ((?:[\[{]C[\]}])+) more")
def _pricier_retreat(who: str, symbols: str) -> Passive | None:
    target = _pokemon_filter(who)
    if target is None:
        return None
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return Passive("retreat", amount, scope="opp_active", target=target)


def _counter_attack(action: str) -> Passive | None:
    """ "put 3 damage counters on the Attacking Pokémon [for each {G} Energy
    attached to this Pokémon]" / "the Attacking Pokémon is now Poisoned" /
    "discard an Energy from the Attacking Pokémon"."""
    action = action.strip()
    counters = re.fullmatch(
        rf"(?:put|place) {N} damage counters? on the Attacking Pokémon"
        rf"(?: for each {E} Energy attached to this Pokémon)?",
        action,
    )
    if counters:
        per = _energy_count(energy(counters.group(2))) if counters.group(2) else None
        return Passive("on_damaged", counters=int(counters.group(1)), per=per)
    status = re.fullmatch(r"the Attacking Pokémon is now (\w+)", action)
    if status and status.group(1) in STATUSES:
        return Passive("on_damaged", status=STATUSES[status.group(1)])
    if action == "discard an Energy from the Attacking Pokémon":
        return Passive("on_damaged", discard_energy=True)
    return None


@rule(
    r"If this Pokémon is in the Active Spot and is damaged by an attack from your opponent's "
    r"Pokémon, (.+)"
)
def _when_hit_active(action: str) -> Passive | None:
    passive = _counter_attack(action)
    return None if passive is None else Passive(**{**passive.__dict__, "holder_at": "active"})


@rule(r"If this Pokémon is damaged by an attack from your opponent's Pokémon, (.+)")
def _when_hit(action: str) -> Passive | None:
    return _counter_attack(action)


@rule(r"If your Active (.*?)Pokémon is damaged by an attack from your opponent's Pokémon, (.+)")
def _when_team_hit(who: str, action: str) -> Passive | None:
    target = _pokemon_filter(who)
    passive = _counter_attack(action)
    if target is None or passive is None:
        return None
    return Passive(**{**passive.__dict__, "scope": "team", "target": target})


@rule(
    r"If this Pokémon is in the Active Spot and is Knocked Out by damage from an attack from "
    rf"your opponent's Pokémon, put {N} damage counters on the Attacking Pokémon"
)
def _when_knocked_out(n: str) -> Passive:
    return Passive("on_knocked_out", counters=int(n), holder_at="active")


@rule(r"This Pokémon can't be (\w+)")
def _immune(name: str) -> Passive | None:
    status = STATUSES.get(name)
    return None if status is None else Passive("immune", status=status)


@rule(
    r"If this Pokémon has full HP and would be Knocked Out by damage from an attack, it is not "
    r"Knocked Out, and its remaining HP becomes 10"
)
def _sturdy() -> Passive:
    return Passive("survive_full")


@rule(
    r"If this Pokémon would be Knocked Out by damage from an attack, flip a coin\. If heads, "
    r"this Pokémon is not Knocked Out, and its remaining HP becomes 10"
)
def _tenacious() -> Passive:
    return Passive("survive_coin")


@rule(
    r"If any damage is done to this Pokémon by attacks, flip a coin\. If heads, prevent that "
    r"damage"
)
def _coin_prevent() -> Passive:
    return Passive("coin_prevent")


@rule(
    rf"If this Pokémon has any {E} Energy attached and is damaged by an attack, flip a coin\. If "
    r"heads, prevent that damage"
)
def _coin_prevent_energy(symbol: str) -> Passive:
    kind_ = energy(symbol)
    return Passive("coin_prevent", when=lambda s, o, h: kind_ in h.attached_energies)


# ---------------------------------------------------------------------------
# compilação


def _parse_sentence(sentence: str) -> list[Passive] | None:
    sentence = sentence.strip()
    if re.fullmatch(r"The effect of .+ doesn't stack", sentence):
        return []
    head = re.match(
        r"As long as this Pokémon is (in the Active Spot|on your Bench), (.+)", sentence
    )
    if head:
        inner = _parse_sentence(head.group(2)[0].upper() + head.group(2)[1:])
        if inner is None:
            inner = _parse_sentence(head.group(2))
        return None if inner is None else _with_holder(inner, _HOLDER_AT[head.group(1)])
    lock = re.fullmatch(r"[Yy]our opponent can't play any (.+?) from their hand", sentence)
    if lock:
        kinds = {
            "Item cards": ["lock_items"],
            "Item cards or Pokémon Tool cards": ["lock_items", "lock_tools"],
            "Stadium cards": ["lock_stadiums"],
        }.get(lock.group(1))
        return None if kinds is None else [Passive(k, scope="opp") for k in kinds]
    compound = re.fullmatch(r"If (.+?), (it gets .+?), and (the attacks it uses .+)", sentence)
    if compound:
        when = _self_condition(compound.group(1))
        first = _parse_sentence(compound.group(2))
        second = _parse_sentence(compound.group(3))
        if when is None or first is None or second is None:
            return None
        return _with_when(first + second, when)
    for pattern, build in RULES:
        match = pattern.fullmatch(sentence)
        if match:
            result = build(*match.groups())
            if result is not None:
                return result if isinstance(result, list) else [result]
    conditional = re.fullmatch(r"If (.+?), (.+)", sentence)
    if conditional:
        when = _self_condition(conditional.group(1))
        inner = _parse_sentence(conditional.group(2))
        if when is not None and inner is not None:
            return _with_when(inner, when)
    return None


@lru_cache(maxsize=2048)
def compile_passive(text: str) -> tuple[Passive, ...] | None:
    found: list[Passive] = []
    stacks = True
    for sentence in _sentences(text):
        if re.fullmatch(r"The effect of .+ doesn't stack", sentence):
            stacks = False
            continue
        parsed = _parse_sentence(sentence)
        if parsed is None:
            return None
        found.extend(parsed)
    if not found:
        return None
    return tuple(Passive(**{**p.__dict__, "stacks": stacks}) for p in found)


@cache
def _hand_written_source() -> str:
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.effects import core, passives

    return "".join(inspect.getsource(m) for m in (passives, rules, core))


def passives_of(ability: Ability) -> tuple[Passive, ...]:
    """Efeitos contínuos da Habilidade (vazio se escrita à mão ou não reconhecida)."""
    if not ability.text or f'"{ability.name}"' in _hand_written_source():
        return ()
    return compile_passive(ability.text) or ()


def rules_in_play(
    state: GameState, owner: PlayerId, kind: str, active_test: Callable[..., bool]
) -> list[tuple[Passive, PokemonInPlay, str]]:
    """(regra, Pokémon que tem a Habilidade, nome) do `kind` pedido, entre os
    Pokémon de `owner` com Habilidade ativa, respeitando onde o dono precisa
    estar e a condição. `active_test(state, mon, name)` é
    `passives.ability_active` (injetado para evitar import circular)."""
    player = state.state_of(owner)
    found: list[tuple[Passive, PokemonInPlay, str]] = []
    seen_non_stacking: set[str] = set()
    for mon in player.all_pokemon_in_play():
        for ability in mon.card.abilities:
            for passive in passives_of(ability):
                if passive.kind != kind or not active_test(state, mon, ability.name):
                    continue
                if passive.holder_at == "active" and player.active is not mon:
                    continue
                if passive.holder_at == "bench" and not any(mon is b for b in player.bench):
                    continue
                if not passive.when(state, owner, mon):
                    continue
                if not passive.stacks:
                    if ability.name in seen_non_stacking:
                        continue
                    seen_non_stacking.add(ability.name)
                found.append((passive, mon, ability.name))
    return found


def is_card_passive(card: Card) -> bool:
    return any(passives_of(ability) for ability in card.abilities)
