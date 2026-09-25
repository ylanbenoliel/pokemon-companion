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

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from pokemon_companion.cards_db.models import Ability, Card
from pokemon_companion.engine.effects import pack
from pokemon_companion.engine.effects.cardinfo import (
    has_rule_box,
    is_ancient,
    is_evolution,
    is_ex,
    is_future,
    is_mega,
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
    #: "lock_stadiums", "ability_shield", "prize_minus", "prize_none", "prize_plus",
    #: "suppress", "early_evolve", "first_turn_attack", "no_attack", "cost_minus",
    #: "ignore_colorless", "tax_opponent", "checkup_burn", "checkup_basics",
    #: "on_opp_to_bench", "on_opp_attach", "on_attach_heal", "on_opp_evolve",
    #: "no_normal_play"
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
    #: o efeito só acontece com cara numa moeda
    coin: bool = False
    #: condição avaliada no momento do evento (ex.: Pokémon nocauteado, atacante)
    event: Callable[..., bool] = _always
    #: Ferramenta de uso único ("discard this card"): vai para o descarte ao agir
    discard_tool: bool = False
    #: efeito do dono do Pokémon (texto compilado como Treinador), ex.: "draw 3 cards"
    effect: str = ""
    #: custo que substitui o do ataque citado ("attack_cost"; `event` testa o nome)
    cost: tuple[str, ...] = ()
    #: tipos que o Pokémon passa a ter ("types")
    types: tuple[str, ...] = ()

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


def pokemon_filter(text: str) -> MonTest | None:
    """ "{W} ", "Evolution {R} ", "Hop's ", "Basic ", "Stage 2 ", "Tera ",
    "Ancient ", "Future ", "" → filtro do Pokémon; None se não reconhecer."""
    text = re.sub(r"Stage (\d)", r"Stage\1", text.strip())
    if not text:
        return _always
    tests: list[MonTest] = []
    words = {
        "Evolution": lambda m: is_evolution(m.card),
        "Basic": lambda m: stage_of(m.card) == "Basic",
        "Stage1": lambda m: stage_of(m.card) == "Stage 1",
        "Stage2": lambda m: stage_of(m.card) == "Stage 2",
        "Tera": lambda m: is_tera(m.card),
        "Ancient": lambda m: is_ancient(m.card),
        "Future": lambda m: is_future(m.card),
    }
    for part in text.split():
        typed = re.fullmatch(E, part)
        if typed:
            tests.append(_of_type(energy(typed.group(1))))
        elif part in words:
            tests.append(words[part])
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
        "Mega Evolution Pokémon ex": lambda m: is_mega(m.card) and is_ex(m.card),
    }
    if text in table:
        return table[text]
    few = re.fullmatch(rf"Pokémon that have {N} or less Energy attached", text)
    if few:
        limit = int(few.group(1))
        return lambda m: len(m.attached_energies) <= limit
    listed = re.fullmatch(rf"((?:{E}, )+){E},? or {E} Pokémon", text)
    if listed:
        kinds = {energy(sym) for sym in re.findall(E, text)}
        return lambda m: pokemon_type(m.card) in kinds
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
    low = re.fullmatch(rf"(?:this|that) Pokémon's remaining HP is {N} or less", text)
    if low:
        limit = int(low.group(1))
        return lambda s, o, h: h.current_hp <= limit
    any_in_play = re.fullmatch(r"you have any (.*?)Pokémon in play", text)
    if any_in_play:
        test = pokemon_filter(any_in_play.group(1))
        if test is not None:
            return lambda s, o, h: any(test(m) for m in s.state_of(o).all_pokemon_in_play())
    if text == "you have more Prize cards remaining than your opponent":
        return lambda s, o, h: len(s.state_of(o).prizes) > len(s.state_of(o.other).prizes)
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
    target = pokemon_filter(who)
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
    if not groups[1].isdigit():
        first, second, n = pokemon_filter(groups[0]), pokemon_filter(groups[1]), groups[2]
        if first is None or second is None:
            return None
        f, s = first, second
        return Passive("bonus", int(n), scope="team", target=lambda m: f(m) or s(m))
    who, n, defender = groups
    target = pokemon_filter(who)
    against = pokemon_filter(defender)
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


@rule(rf"Attacks used by this Pokémon do {N} more damage to your opponent's Active Pokémon ex")
def _bonus_self_vs_ex(n: str) -> Passive:
    return Passive("bonus", int(n), defender=lambda m: is_ex(m.card))


@rule(r"[Aa]ttacks used by this Pokémon cost ((?:[\[{]C[\]}])+) less")
def _cheaper_attacks(symbols: str) -> Passive:
    return Passive("cost_minus", len(re.findall(r"[\[{]C[\]}]", symbols)))


@rule(
    rf"Attacks used by this Pokémon cost ((?:[\[{{]C[\]}}])+) less and do {N} more damage to "
    r"your opponent's Active Pokémon"
)
def _cheaper_and_stronger(symbols: str, n: str) -> list[Passive]:
    return [_cheaper_attacks(symbols), Passive("bonus", int(n))]


@rule(rf"this Pokémon can use the (.+?) attack for ((?:{E})+)")
def _attack_for(name: str, symbols: str, *_: str) -> Passive:
    cost = tuple(energy(sym) for sym in re.findall(E, symbols))
    return Passive("attack_cost", cost=cost, event=lambda attack: attack == name)


@rule(r"ignore all Energy in the cost of (.+?) used by this Pokémon")
def _attack_free(name: str) -> Passive:
    return Passive("attack_cost", event=lambda attack: attack == name)


@rule(r"This Pokémon can use the attack on this card")
def _tool_attack() -> Passive:
    return Passive("tool_attack")


@rule(r"If this card is attached to 1 of your Pokémon, discard it at the end of your turn")
def _tool_expires() -> Passive:
    return Passive("tool_expires")


@rule(r"Each of your evolved Pokémon can use any attack from its previous Evolutions")
def _prior_attacks() -> Passive:
    return Passive("prior_attacks", scope="team", target=lambda m: bool(m.prior_cards))


@rule(rf"it is ((?:{E}(?:, | and |, and ))+{E}) type")
def _types(symbols: str, *_: str) -> Passive:
    return Passive("types", types=tuple(energy(sym) for sym in re.findall(E, symbols)))


@rule(r"When this Pokémon uses an attack, that attack costs (\d+) Energy less")
def _cheaper_any(n: str) -> Passive:
    return Passive("cost_minus_any", int(n))


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


@rule(
    r"The Retreat Cost of this Pokémon is ((?:[\[{]C[\]}])+) (less|more)",
    r"This Pokémon's Retreat Cost is ((?:[\[{]C[\]}])+) (less|more)",
)
def _own_retreat(symbols: str, direction: str) -> Passive:
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return Passive("retreat", amount if direction == "more" else -amount)


@rule(r"the Retreat Cost of both Active Pokémon is ((?:[\[{]C[\]}])+) more")
def _both_retreat(symbols: str) -> list[Passive]:
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return [
        Passive("retreat", amount, scope="own_active"),
        Passive("retreat", amount, scope="opp_active"),
    ]


@rule(r"your Active Pokémon's Retreat Cost is ((?:[\[{]C[\]}])+) less")
def _cheaper_retreat(symbols: str) -> Passive:
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return Passive("retreat", -amount, scope="own_active")


@rule(r"Your opponent's Active (.*?)Pokémon's Retreat Cost is ((?:[\[{]C[\]}])+) more")
def _pricier_retreat(who: str, symbols: str) -> Passive | None:
    target = pokemon_filter(who)
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
    return _owner_effect("on_damaged", action)


def _owner_effect(kind_: str, action: str) -> Passive | None:
    """ "draw 3 cards", "search your deck for ..." — efeito a favor do dono."""
    from pokemon_companion.engine.effects.text_effects import compiled_trainer

    action = action[0].upper() + action[1:]
    return Passive(kind_, effect=action) if compiled_trainer(action) is not None else None


@rule(
    r"If this Pokémon is Knocked Out by damage from an attack from your opponent's Pokémon, (.+)",
)
def _when_knocked_out(action: str) -> Passive | None:
    return _owner_effect("on_knocked_out", action)


@rule(
    r"If this Pokémon is in the Active Spot and is damaged by an attack from your opponent's "
    r"Pokémon, (.+)"
)
def _when_hit_active(action: str) -> Passive | None:
    passive = _counter_attack(action)
    return None if passive is None else Passive(**{**passive.__dict__, "holder_at": "active"})


@rule(
    rf"If this Pokémon is damaged by an attack from your opponent's (.+), it takes {N} less damage"
)
def _reduce_from_kind(who: str, n: str) -> Passive | None:
    attacker = _attacker_filter(who)
    return None if attacker is None else Passive("reduce", int(n), attacker=attacker)


def _holder_condition(text: str) -> HolderTest | None:
    """Condições sobre o próprio Pokémon numa lista "has …, is …, and …"."""
    exact = re.fullmatch(rf"has a Retreat Cost of exactly {N}", text)
    if exact:
        size = int(exact.group(1))
        return lambda s, o, h: len(h.card.retreat_cost) == size
    if text == "has Weakness to your opponent's Active Pokémon's type":
        return lambda s, o, h: bool(
            (opp := s.state_of(o.other).active) is not None
            and {w.energy_type for w in h.card.weaknesses} & set(opp.card.types)
        )
    if text == "isn't a Mega Evolution Pokémon ex":
        return lambda s, o, h: not (is_mega(h.card) and is_ex(h.card))
    return None


@rule(
    rf"If this Pokémon ((?:[^,]+, )+)and (is damaged by|is Knocked Out by damage from|takes {N} or "
    r"more damage from) an attack from your opponent's (.+?), (.+)"
)
def _conditional_reaction(
    conditions: str, event: str, minimum: str | None, who: str, action: str
) -> Passive | None:
    """ "If this Pokémon has …, is in the Active Spot, and is damaged by an
    attack from your opponent's Pokémon, draw 3 cards" (e parecidos)."""
    holder_at, tests = "any", []
    for text in conditions.rstrip(", ").split(", "):
        if text == "is in the Active Spot":
            holder_at = "active"
            continue
        test = _holder_condition(text)
        if test is None:
            return None
        tests.append(test)
    attacker = _attacker_filter(who)
    if attacker is None:
        return None
    knocked_out = event.startswith("is Knocked Out")
    passive = (
        _owner_effect("on_knocked_out", action) if knocked_out else _counter_attack(action)
    )
    if passive is None:
        return None
    least = int(minimum or 0)
    return Passive(
        **{
            **passive.__dict__,
            "holder_at": holder_at,
            "when": lambda s, o, h: all(test(s, o, h) for test in tests),
            "event": lambda mon, amount: attacker(mon) and amount >= least,
        }
    )


@rule(r"If this Pokémon is damaged by an attack from your opponent's Pokémon, (.+)")
def _when_hit(action: str) -> Passive | None:
    return _counter_attack(action)


@rule(r"If your Active (.*?)Pokémon is damaged by an attack from your opponent's Pokémon, (.+)")
def _when_team_hit(who: str, action: str) -> Passive | None:
    target = pokemon_filter(who)
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
    r"Knocked Out, and its remaining HP becomes 10",
    r"If this Pokémon has full HP and would be Knocked Out by damage from an attack from your "
    r"opponent's Pokémon, it is not Knocked Out, and its remaining HP becomes 10",
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


def _lock_pokemon(what: str) -> list[Passive] | None:
    """ "Pokémon that has an Ability, except for Team Rocket's Pokémon" → trava
    de jogar esses Pokémon da mão (o filtro recebe a carta)."""
    match = re.fullmatch(r"Pokémon that ha(?:s|ve) an Ability(?:, except for (.+?)Pokémon)?", what)
    if match is None:
        return None
    group = (match.group(1) or "").strip()
    if group and not group.endswith("'s"):
        return None
    return [
        Passive(
            "lock_pokemon",
            scope="opp",
            target=lambda card: bool(card.abilities)
            and not (group and card.name.startswith(group)),
        )
    ]


def _parse_sentence(sentence: str) -> list[Passive] | None:
    sentence = sentence.strip()
    if re.fullmatch(r"The effect of .+ doesn't stack", sentence):
        return []
    in_play = re.fullmatch(r"As long as this Pokémon is in play, (.+)", sentence)
    if in_play:
        return _parse_sentence(in_play.group(1))
    holding = re.fullmatch(r"As long as this Pokémon has an? (.+?) attached, (.+)", sentence)
    if holding:
        name = holding.group(1)
        inner = _parse_sentence(holding.group(2))
        has = lambda s, o, h: (h.tool is not None and h.tool.name == name) or any(  # noqa: E731
            c.name == name for c in h.special_energy_cards
        )
        return None if inner is None else _with_when(inner, has)
    head = re.match(
        r"As long as this Pokémon is (in the Active Spot|on your Bench), (.+)", sentence
    )
    if head:
        inner = _parse_sentence(head.group(2)[0].upper() + head.group(2)[1:])
        if inner is None:
            inner = _parse_sentence(head.group(2))
        return None if inner is None else _with_holder(inner, _HOLDER_AT[head.group(1)])
    lock = re.fullmatch(
        r"[Yy]our opponent can't play any (.+?) from their hand(, except for .+)?", sentence
    )
    if lock:
        kinds = {
            "Item cards": ["lock_items"],
            "Item cards or Pokémon Tool cards": ["lock_items", "lock_tools"],
            "Stadium cards": ["lock_stadiums"],
        }.get(lock.group(1))
        if kinds is not None and lock.group(2) is None:
            return [Passive(k, scope="opp") for k in kinds]
        return _lock_pokemon(lock.group(1) + (lock.group(2) or ""))
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
    text = pack.rewrite(text)
    found: list[Passive] = []
    stacks = True
    whole = re.sub(r"\s*The effect of .+ doesn't stack\.?$", "", _clean(text)).rstrip(".")
    for pattern, build in RULES:
        match = pattern.fullmatch(whole)
        if match:
            result = build(*match.groups())
            if result is not None:
                stacks = "doesn't stack" not in text
                items = result if isinstance(result, list) else [result]
                return tuple(Passive(**{**p.__dict__, "stacks": stacks}) for p in items)
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


#: Cartas e Habilidades com efeito escrito à mão no motor (citadas pelo nome
#: em passives, rules, core, trainers, abilities ou attacks): o texto delas não
#: é compilado. `tests/test_hand_written.py` confere a lista contra o código.
HAND_WRITTEN: frozenset[str] = frozenset(
    {
        "ACE Nullifier",
        "AZ's Tranquility",
        "Academy at Night",
        "Adrena-Brain",
        "Air Balloon",
        "Alakazam",
        "Alluring Light",
        "Ange Floette",
        "Area Zero Underdepths",
        "Attract Customers",
        "Battle Cage",
        "Bianca's Devotion",
        "Binding Mochi",
        "Black Belt's Training",
        "Blowtorch",
        "Boom Boom Groove",
        "Boomerang Energy",
        "Boss's Orders",
        "Brave Bangle",
        "Briar",
        "Brock's Scouting",
        "Bubbly Water Energy",
        "Buddy-Buddy Poffin",
        "Bug Catching Set",
        "Champion's Call",
        "Charging Up",
        "Ciphermaniac's Codebreaking",
        "Cobalt Command",
        "Colress's Tenacity",
        "Compound Eyes",
        "Crispin",
        "Crushing Hammer",
        "Curly Wall",
        "Cursed Blast",
        "Cynthia's Power Weight",
        "Cyrano",
        "Damp",
        "Dark Bell",
        "Dawn",
        "Diver's Catch",
        "Dizzying Valley",
        "Dusknoir",
        "Duskull",
        "Eevee",
        "Energy Recycler",
        "Energy Search",
        "Energy Switch",
        "Enhanced Hammer",
        "Enriching Energy",
        "Eri",
        "Explorer's Guidance",
        "Fairy Zone",
        "Festival Grounds",
        "Festival Lead",
        "Fighting Gong",
        "Final Chain",
        "Flip the Script",
        "Flower Curtain",
        "Forest of Vitality",
        "Freezing Shroud",
        "Froslass",
        "Full Metal Lab",
        "Gladion's Final Battle",
        "Glass Trumpet",
        "Grand Tree",
        "Granite Cave",
        "Gravity Mountain",
        "Growing Grass Energy",
        "Gwynn",
        "Handheld Fan",
        "Hassel",
        "Heave-Ho Catcher",
        "Hero's Cape",
        "Hide 'n' Sneak",
        "Hilda",
        "Ignition Energy",
        "Infinite Shadow",
        "Iron Crown ex",
        "Jamming Tower",
        "Judge",
        "Jumbo Ice Cream",
        "Kieran",
        "Koffing",
        "Lana's Aid",
        "Last-Ditch Catch",
        "Legacy Energy",
        "Lillie's Determination",
        "Lillie's Pearl",
        "Lively Stadium",
        "Lucky Helmet",
        "Lumiose City",
        "Lunar Cycle",
        "Lunatone",
        "Magnetic Metal Energy",
        "Mega Floette ex",
        "Mega Signal",
        "Metal Maker",
        "Metallic Signal",
        "Miracle Headset",
        "Mist Energy",
        "N's Castle",
        "N's PP Up",
        "Neo Upper Energy",
        "Neutralization Zone",
        "Night Stretcher",
        "Nighttime Mine",
        "Nitro Fire Energy",
        "Paradise Resort",
        "Pecharunt ex",
        "Perilous Jungle",
        "Photon Cord",
        "Plasma Bane",
        "Poké Pad",
        "Pokégear 3.0",
        "Pokémon Center Lady",
        "Postwick",
        "Power Saver",
        "Powerglass",
        "Precious Trolley",
        "Premium Power Pro",
        "Prime Catcher",
        "Prism Energy",
        "Prism Tower",
        "Protective Sail",
        "Psychic Draw",
        "Psyduck",
        "Punk Up",
        "Rainbow DNA",
        "Rapid Vernier",
        "Rare Candy",
        "Recon Directive",
        "Repelling Veil",
        "Reversal Energy",
        "Ripening Charge",
        "Risky Ruins",
        "Rocky Fighting Energy",
        "Rosa's Encouragement",
        "Roto-Stick",
        "Run Away Draw",
        "Run Errand",
        "Sacred Ash",
        "Salvatore",
        "Seasoned Skill",
        "Secret Box",
        "Shadowy Darkness Energy",
        "Sinister Surge",
        "Skyliner",
        "Slimy Sliding",
        "Smog Signals",
        "Snow Camouflage",
        "Snow Sink",
        "Solrock",
        "Special Red Card",
        "Spherical Shield",
        "Spikemuth Gym",
        "Spiky Energy",
        "Strange Timepiece",
        "Subjugating Chains",
        "Super Potion",
        "Surfer",
        "Switch",
        "Teal Dance",
        "Team Rocket's Archer",
        "Team Rocket's Ariana",
        "Team Rocket's Energy",
        "Team Rocket's Factory",
        "Team Rocket's Giovanni",
        "Team Rocket's Petrel",
        "Team Rocket's Proton",
        "Team Rocket's Transceiver",
        "Team Rocket's Watchtower",
        "Telepathic Psychic Energy",
        "Teleporter",
        "Tool Scrapper",
        "Toxic Subjugation",
        "Trade",
        "Ultra Ball",
        "Unfair Stamp",
        "Unnerve",
        "Voltaic Lightning Energy",
        "Wally's Compassion",
        "Watchful Eye",
        "Wide Wall",
        "Wild Growth",
        "Wondrous Patch",
        "Xerosic's Machinations",
    }
)


@lru_cache(maxsize=4096)
def passives_of(ability: Ability) -> tuple[Passive, ...]:
    """Efeitos contínuos da Habilidade (vazio se escrita à mão ou não reconhecida)."""
    if not ability.text or ability.name in HAND_WRITTEN:
        return ()
    return compile_passive(ability.text) or ()


@lru_cache(maxsize=8192)
def _by_kind(abilities: tuple[Ability, ...]) -> dict[str, tuple[tuple[Passive, str], ...]]:
    """Passivas de um conjunto de Habilidades, agrupadas por tipo (a maioria
    dos Pokémon não tem nenhuma: a consulta vira um `dict.get` vazio)."""
    table: dict[str, list[tuple[Passive, str]]] = {}
    for ability in abilities:
        for passive in passives_of(ability):
            table.setdefault(passive.kind, []).append((passive, ability.name))
    return {kind: tuple(entries) for kind, entries in table.items()}


#: índice por carta: id(carta) → (carta, passivas por tipo). Guardar a carta
#: mantém o objeto vivo, então o id não é reaproveitado por outro enquanto
#: estiver aqui (as cartas são imutáveis e compartilhadas).
_CARD_INDEX: dict[int, tuple[Card, dict[str, tuple[tuple[Passive, str], ...]]]] = {}
_EMPTY: dict[str, tuple[tuple[Passive, str], ...]] = {}


def kind_entries(mon: PokemonInPlay, kind: str) -> tuple[tuple[Passive, str], ...]:
    card = mon.card
    if not card.abilities:
        return ()
    entry = _CARD_INDEX.get(id(card))
    if entry is None or entry[0] is not card:
        entry = (card, _by_kind(tuple(card.abilities)) or _EMPTY)
        _CARD_INDEX[id(card)] = entry
    return entry[1].get(kind, ())


def _tool_entries(
    state: GameState, mon: PokemonInPlay, kind: str
) -> tuple[tuple[Passive, str], ...]:
    from pokemon_companion.engine.effects.passives import tool_active

    tool = mon.tool
    assert tool is not None
    if not tool_active(state, mon, tool.name):
        return ()
    return tuple((p, tool.name) for p in tool_passives(tool) if p.kind == kind)


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
        entries = kind_entries(mon, kind)
        if mon.tool is not None:
            entries += _tool_entries(state, mon, kind)
        for passive, name in entries:
            if passive.holder_at == "active" and player.active is not mon:
                continue
            if passive.holder_at == "bench" and not any(mon is b for b in player.bench):
                continue
            from_tool = mon.tool is not None and name == mon.tool.name
            if not from_tool and not active_test(state, mon, name):
                continue
            if not passive.when(state, owner, mon):
                continue
            if not passive.stacks:
                if name in seen_non_stacking:
                    continue
                seen_non_stacking.add(name)
            found.append((passive, mon, name))
    return found


_TOOL_HOLDER = re.compile(
    r"\b[Tt]he ((?:(?![Tt]he )\S+ )*?)(Pokémon(?: ex)?|[A-Z][\w']*(?: [A-Z][\w']*)* ex) "
    r"this card is attached to"
)
_TOOL_DISCARD = re.compile(
    r"\s*(?:,? and discard this card|Then, discard this card\.?"
    r"|If you placed any damage counters in this way, discard this card\.?)"
)


def _holder_test(qualifier: str, noun: str) -> MonTest | None:
    """ "{D} " + "Pokémon" / "" + "Pikachu ex" → filtro do Pokémon equipado."""
    if noun.startswith("Pokémon"):
        test = pokemon_filter(qualifier)
        if test is None or noun == "Pokémon":
            return test
        return lambda m: test(m) and is_ex(m.card)
    name = (qualifier + noun).strip()
    return lambda m: m.card.name == name


def tool_passives(tool: Card) -> tuple[Passive, ...]:
    """Efeitos contínuos de uma Ferramenta, compilados do texto como se fosse
    uma Habilidade de quem está equipado ("the Pokémon this card is attached
    to" vira "this Pokémon")."""
    if tool.name in HAND_WRITTEN:
        return ()
    return _tool_passives(" ".join(tool.rules))


def tool_text(rules_text: str) -> tuple[str, MonTest, bool]:
    """(texto como Habilidade, filtro de quem está equipado, é de uso único)."""
    text = pack.rewrite(rules_text)
    holders: list[MonTest] = []

    def as_this(match: re.Match[str]) -> str:
        test = _holder_test(match.group(1), match.group(2))
        holders.append(test if test is not None else (lambda m: False))
        return "this Pokémon" if match.group(0)[0] == "t" else "This Pokémon"

    text = _TOOL_HOLDER.sub(as_this, text)
    discard = bool(_TOOL_DISCARD.search(text))
    text = _TOOL_DISCARD.sub("", text).replace(" (even if this Pokémon is Knocked Out)", "")
    return text, (holders[0] if holders else _always), discard


@lru_cache(maxsize=512)
def _tool_passives(rules_text: str) -> tuple[Passive, ...]:
    text, holder, discard = tool_text(rules_text)
    compiled_ = compile_passive(text)
    if not compiled_:
        return ()
    return tuple(
        Passive(
            **{
                **p.__dict__,
                "discard_tool": discard,
                "when": (lambda s, o, h, w=p.when: holder(h) and w(s, o, h)),
            }
        )
        for p in compiled_
    )


def is_card_passive(card: Card) -> bool:
    return any(passives_of(ability) for ability in card.abilities)


# ---------------------------------------------------------------------------
# prêmios, supressão, evolução, custo de ataque, Checkup e gatilhos


def _has_in_play(name: str) -> HolderTest:
    return lambda s, o, h: any(m.card.name == name for m in s.state_of(o).all_pokemon_in_play())


@rule(
    r"If 1 of your (.*?)Pokémon is Knocked Out by damage from an attack from your opponent's "
    r"(.+), that player takes 1 fewer Prize card"
)
def _fewer_prizes_team(qualifier: str, who: str) -> Passive | None:
    target = pokemon_filter(qualifier)
    attacker = _attacker_filter(who)
    if target is None or attacker is None:
        return None
    return Passive("prize_minus", 1, scope="team", target=target, attacker=attacker)


@rule(
    r"If this Pokémon is Knocked Out by damage from an attack from your opponent's Pokémon, and "
    r"if you have any (.+) in play, your opponent takes 1 fewer Prize card"
)
def _fewer_prizes_if(name: str) -> Passive:
    return Passive("prize_minus", 1, when=_has_in_play(name.strip()))


@rule(
    r"If this Pokémon is Knocked Out by damage from an attack from your opponent's (.+), your "
    r"opponent can't take any Prize cards for it"
)
def _no_prizes(who: str) -> Passive | None:
    attacker = _attacker_filter(who)
    return None if attacker is None else Passive("prize_none", attacker=attacker)


@rule(
    r"If your opponent's (.*?)Pokémon is Knocked Out by damage from an attack used by this "
    r"Pokémon, take 1 more Prize card"
)
def _more_prizes(qualifier: str) -> Passive | None:
    target = pokemon_filter(qualifier)
    return None if target is None else Passive("prize_plus", 1, target=target)


@rule(
    r"When your opponent's Active Pokémon is Knocked Out, flip a coin\. If heads, take 1 more Prize card"
)
def _wonder_kiss() -> Passive:
    return Passive("prize_plus", 1, scope="team", coin=True)


@rule(r"your opponent's Active Pokémon has no Abilities, except for (.+)")
def _silence_opp_active(except_name: str) -> Passive:
    name = except_name.strip()
    return Passive("suppress", scope="opp_active", event=lambda ability: ability != name)


@rule(r"Pokémon with a Rule Box in play have no Abilities, except for Future Pokémon")
def _silence_rule_box() -> Passive:
    from pokemon_companion.engine.effects.cardinfo import is_future

    return Passive(
        "suppress",
        scope="all",
        target=lambda m: has_rule_box(m.card) and not is_future(m.card),
    )


@rule(r"Benched Stage 2 Pokémon have no Abilities")
def _silence_benched_stage2() -> Passive:
    return Passive("suppress", scope="all_bench", target=lambda m: stage_of(m.card) == "Stage 2")


@rule(r"it can evolve during your first turn or the turn you play it")
def _early_evolve_self() -> Passive:
    return Passive("early_evolve")


@rule(
    r"If you have (.+?) in play, this Pokémon can evolve during your first turn or the turn you play it"
)
def _early_evolve_with(name: str) -> Passive:
    return Passive("early_evolve", when=_has_in_play(name.strip()))


@rule(
    r"If your opponent's Active Pokémon is a Pokémon ex, this Pokémon can evolve during your "
    r"first turn or the turn you play it"
)
def _early_evolve_vs_ex() -> Passive:
    return Passive(
        "early_evolve",
        when=lambda s, o, h: s.state_of(o.other).active is not None
        and is_ex(s.state_of(o.other).active.card),  # type: ignore[union-attr]
    )


@rule(r"If you go first, this Pokémon can use attacks during your first turn")
def _debut() -> Passive:
    return Passive("first_turn_attack")


@rule(r"If your opponent has no Pokémon ex or Pokémon V in play, this Pokémon can't attack")
def _born_to_slack() -> Passive:
    return Passive(
        "no_attack",
        when=lambda s, o, h: not any(
            is_ex(m.card) or "V" in m.card.subtypes
            for m in s.state_of(o.other).all_pokemon_in_play()
        ),
    )


@rule(r"Attacks used by this Pokémon cost ((?:[\[{]C[\]}])+) less for each (.+)")
def _cost_minus(symbols: str, what: str) -> Passive | None:
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    what = what.strip()
    if what == "of your opponent's Benched Pokémon":
        return Passive("cost_minus", amount, per=lambda s, o, h: len(s.state_of(o.other).bench))
    card = re.fullmatch(r"(.+?) card in your discard pile", what)
    if card:
        name = card.group(1)
        return Passive(
            "cost_minus",
            amount,
            per=lambda s, o, h: sum(1 for c in s.state_of(o).discard if c.name == name),
        )
    return None


@rule(
    rf"If your opponent has exactly {N} cards in their hand, ignore all {E} Energy in the costs "
    r"of attacks used by this Pokémon"
)
def _ignore_colorless(n: str, _symbol: str) -> Passive:
    size = int(n)
    return Passive("ignore_colorless", when=lambda s, o, h: len(s.state_of(o.other).hand) == size)


@rule(r"attacks used by your opponent's (.*?)Pokémon cost ((?:[\[{]C[\]}])+) more")
def _tax_opponent(qualifier: str, symbols: str) -> Passive | None:
    target = pokemon_filter(qualifier)
    if target is None:
        return None
    amount = len(re.findall(r"[\[{]C[\]}]", symbols))
    return Passive("tax_opponent", amount, target=target)


@rule(rf"During Pokémon Checkup, put {N} more damage counters on your opponent's Burned Pokémon")
def _checkup_burn(n: str) -> Passive:
    return Passive("checkup_burn", counters=int(n))


@rule(
    rf"During Pokémon Checkup, if this Pokémon is in the Active Spot, put {N} damage counters on "
    r"each of your opponent's Basic Pokémon"
)
def _checkup_basics(n: str) -> Passive:
    return Passive("checkup_basics", counters=int(n), holder_at="active")


@rule(
    r"whenever your opponent's Active Pokémon moves to the Bench during their turn, their new "
    r"Active Pokémon is now (\w+)",
    r"Whenever your opponent's Active Pokémon moves to the Bench during their turn, their new "
    r"Active Pokémon is now (\w+)",
)
def _on_retreat_status(name: str) -> Passive | None:
    status = STATUSES.get(name)
    return None if status is None else Passive("on_opp_to_bench", status=status)


@rule(
    rf"Whenever your opponent's Active Pokémon moves to the Bench during their turn, place {N} "
    r"damage counters on that Pokémon"
)
def _on_retreat_counters(n: str) -> Passive:
    return Passive("on_opp_to_bench", counters=int(n))


@rule(
    rf"Whenever your opponent attaches an Energy card from their hand to 1 of their Pokémon, put "
    rf"{N} damage counters on that Pokémon"
)
def _gnawing(n: str) -> Passive:
    return Passive("on_opp_attach", counters=int(n))


@rule(
    rf"whenever you attach an Energy card from your hand to 1 of your Pokémon, heal {N} damage "
    r"from that Pokémon"
)
def _auto_heal(n: str) -> Passive:
    return Passive("on_attach_heal", int(n))


@rule(
    rf"Whenever your opponent plays a Pokémon from their hand to evolve 1 of their Pokémon, put "
    rf"{N} damage counters on that Pokémon"
)
def _darkest_impulse(n: str) -> Passive:
    return Passive("on_opp_evolve", counters=int(n))


@rule(r"Put this Pokémon into play only with the effect of .+")
def _no_normal_play() -> Passive:
    return Passive("no_normal_play")


def card_passives(card: Card) -> tuple[Passive, ...]:
    """Passivas de uma carta que ainda não está em jogo (ex.: na mão)."""
    return tuple(p for ability in card.abilities for p in passives_of(ability))
