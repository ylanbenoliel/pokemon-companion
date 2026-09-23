"""Quais efeitos sonoros tocam após uma ação — sem Qt, testável.

Os sons são por *grupo de efeito*, nunca por carta: o ataque soa pelo tipo
do atacante, o impacto pela quantidade de dano, a energia pelo tipo dela,
o Treinador pela categoria, e as consequências (nocaute, prêmio, cura,
condição especial, compra, busca...) pelo que a ação mudou no jogo. Por
isso qualquer carta nova — escrita à mão ou compilada do texto — já soa
certo sem cadastro: `cues_for` compara um retrato do jogo antes da ação
(`snapshot`) com o estado depois dela.

Os WAVs de cada grupo são gerados por `tools/make_sounds.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
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
from pokemon_companion.engine.effects.cardinfo import (
    TYPED_SPECIAL_ENERGIES,
    is_basic_energy,
    is_fossil_item,
    is_mega,
    pokemon_type,
    trainer_kind,
)
from pokemon_companion.engine.game_state import (
    GameState,
    PlayerId,
    PokemonInPlay,
    StatusCondition,
)

#: (atraso em ms, grupo) — o atraso encadeia causa e consequência
Cue = tuple[int, str]

#: tipo de Pokémon/energia → sufixo dos grupos attack_* / energy_*
TYPE_GROUP = {
    "Fire": "fire",
    "Water": "water",
    "Lightning": "lightning",
    "Grass": "grass",
    "Psychic": "psychic",
    "Fighting": "fighting",
    "Darkness": "darkness",
    "Metal": "metal",
    "Dragon": "dragon",
    "Fairy": "fairy",
}
STATUS_GROUP = {
    StatusCondition.POISONED: "status_poison",
    StatusCondition.BURNED: "status_burn",
    StatusCondition.ASLEEP: "status_sleep",
    StatusCondition.PARALYZED: "status_paralysis",
    StatusCondition.CONFUSED: "status_confusion",
}
#: dano a partir do qual o impacto fica "pesado" / "massivo"
HEAVY_HIT = 100
MASSIVE_HIT = 200
#: todos os grupos que a UI pode pedir (cada um precisa de um WAV)
ALL_GROUPS = frozenset(
    {f"attack_{t}" for t in (*TYPE_GROUP.values(), "colorless")}
    | {f"energy_{t}" for t in (*TYPE_GROUP.values(), "colorless")}
    | {f"trainer_{k}" for k in ("item", "supporter", "stadium", "tool")}
    | set(STATUS_GROUP.values())
    | {
        "ui_click", "turn_start", "victory", "defeat", "coin_flip", "coin_heads",
        "coin_tails", "card_draw", "card_place", "card_search", "shuffle", "discard",
        "energy_discard", "evolve", "evolve_mega", "ability", "switch", "hit_light",
        "hit_heavy", "hit_massive", "shield", "ko", "prize", "heal",
    }
)  # fmt: skip
#: no máximo tantos sons por ação (evita cacofonia em efeitos grandes)
MAX_CUES = 5

_COIN = re.compile(r"\b(\d+) cara\(s\)|\((cara|coroa)\)")


@dataclass(frozen=True)
class MonSnap:
    hp: int
    status: StatusCondition
    energies: int


@dataclass(frozen=True)
class SideSnap:
    hand: int
    deck: int
    discard: int
    prizes: int
    #: id(PokemonInPlay) → retrato (o motor muta os Pokémon no lugar)
    mons: dict[int, MonSnap]
    #: mantém os Pokémon vivos: sem isso um `id` liberado (nocaute) poderia
    #: ser reutilizado por um Pokémon novo criado pela própria ação
    alive: tuple[PokemonInPlay, ...]


@dataclass(frozen=True)
class Snapshot:
    sides: dict[PlayerId, SideSnap]
    #: carta da mão usada pela ação (energia, Treinador, evolução...)
    played: Card | None
    #: tipo do Pokémon Ativo de quem age (som do ataque)
    attacker_type: str
    attack_damage: int


def _side(state: GameState, pid: PlayerId) -> SideSnap:
    side = state.state_of(pid)
    return SideSnap(
        hand=len(side.hand),
        deck=len(side.deck),
        discard=len(side.discard),
        prizes=len(side.prizes),
        mons={
            id(m): MonSnap(m.current_hp, m.status, len(m.attached_energies))
            for m in side.all_pokemon_in_play()
        },
        alive=tuple(side.all_pokemon_in_play()),
    )


def snapshot(state: GameState, actor: PlayerId, action: Action) -> Snapshot:
    me = state.state_of(actor)
    index = getattr(action, "hand_index", None)
    played = me.hand[index] if index is not None and 0 <= index < len(me.hand) else None
    attacker_type, damage = "Colorless", 0
    if me.active is not None:
        attacker_type = pokemon_type(me.active.card)
        if isinstance(action, UseAttack) and action.attack_index < len(me.active.card.attacks):
            damage = me.active.card.attacks[action.attack_index].base_damage
    return Snapshot(
        sides={pid: _side(state, pid) for pid in PlayerId},
        played=played,
        attacker_type=attacker_type,
        attack_damage=damage,
    )


def type_group(card_type: str) -> str:
    return TYPE_GROUP.get(card_type, "colorless")


def energy_group(card: Card) -> str:
    if is_basic_energy(card) and card.types:
        return f"energy_{type_group(card.types[0])}"
    typed = TYPED_SPECIAL_ENERGIES.get(card.name)
    return f"energy_{type_group(typed)}" if typed else "energy_colorless"


def _primary(action: Action, before: Snapshot) -> str | None:
    played = before.played
    if isinstance(action, UseAttack):
        return attack_cue(before.attacker_type)
    if isinstance(action, (PlayBasicToBench, PlayBasicToActive)):
        return "card_place"
    if isinstance(action, Evolve):
        return "evolve_mega" if played is not None and is_mega(played) else "evolve"
    if isinstance(action, AttachEnergy):
        return energy_group(played) if played is not None else "energy_colorless"
    if isinstance(action, PlayTrainer) and played is not None:
        if is_fossil_item(played):
            return "card_place"
        kind = trainer_kind(played)
        return f"trainer_{kind.lower()}" if kind else "trainer_item"
    if isinstance(action, (UseAbility, UseStadium)):
        return "ability"
    if isinstance(action, (Retreat, PromoteActive)):
        return "switch"
    return None


def attack_cue(attacker_type: str) -> str:
    """Som da investida do atacante (antes do motor resolver o ataque)."""
    return f"attack_{type_group(attacker_type)}"


def _coin_cues(messages: list[str]) -> list[str]:
    """Moedas registradas pelo motor: "N cara(s)." ou "(cara)"/"(coroa)"."""
    cues: list[str] = []
    for message in messages:
        for match in _COIN.finditer(message):
            heads = int(match.group(1)) > 0 if match.group(1) else match.group(2) == "cara"
            cues += ["coin_flip", "coin_heads" if heads else "coin_tails"]
    return cues[:4]


def _hit_group(damage: int) -> str:
    if damage >= MASSIVE_HIT:
        return "hit_massive"
    return "hit_heavy" if damage >= HEAVY_HIT else "hit_light"


def cues_for(
    action: Action,
    before: Snapshot,
    after: GameState,
    actor: PlayerId,
    messages: list[str],
    attack_announced: bool = False,
) -> list[Cue]:
    """Sons desta ação, em ordem e com atraso: moeda → causa (ataque,
    Treinador, energia...) → impacto → consequências (nocaute, prêmio,
    condição, cura, cartas). Com `attack_announced`, o som do ataque já
    tocou na investida (`attack_cue`) e o impacto vem sem atraso."""
    sequence: list[str] = []
    gaps: list[int] = []

    def add(group: str, gap: int) -> None:
        if group not in sequence:
            sequence.append(group)
            gaps.append(gap)

    for coin in _coin_cues(messages):
        sequence.append(coin)
        gaps.append(0 if coin == "coin_flip" else 380)

    primary = None if attack_announced else _primary(action, before)
    if primary is not None:
        add(primary, 250 if sequence else 0)

    # dano e cura: Pokémon que continuam em jogo pela diferença de HP; os que
    # saíram de jogo do lado do oponente levaram ao menos o HP que tinham
    hits = {pid: 0 for pid in PlayerId}
    healed = energy_lost = energy_gained = False
    new_status: list[str] = []
    for pid in PlayerId:
        side_before = before.sides[pid]
        present = {id(m) for m in after.state_of(pid).all_pokemon_in_play()}
        if pid != actor:
            for key, gone in side_before.mons.items():
                if key not in present:
                    hits[pid] = max(hits[pid], gone.hp)
        for mon in after.state_of(pid).all_pokemon_in_play():
            old = side_before.mons.get(id(mon))
            if old is None:
                continue
            if mon.current_hp < old.hp:
                hits[pid] = max(hits[pid], old.hp - mon.current_hp)
            elif mon.current_hp > old.hp:
                healed = True
            if mon.status != old.status and mon.status in STATUS_GROUP:
                new_status.append(STATUS_GROUP[mon.status])
            energies = len(mon.attached_energies)
            energy_lost |= energies < old.energies
            energy_gained |= energies > old.energies
    knocked_out = sum(m.count("foi nocauteado") for m in messages)

    if isinstance(action, UseAttack):
        dealt = hits[actor.other]
        if knocked_out:
            dealt = max(dealt, HEAVY_HIT)  # nocaute sempre soa pesado
        if dealt:
            add(_hit_group(dealt), 0 if attack_announced else 120)
        elif before.attack_damage > 0:
            add("shield", 150)
    elif hits[actor.other] or hits[actor]:
        add("hit_light", 150)
    if knocked_out:
        add("ko", 350)
    for pid in PlayerId:
        if len(after.state_of(pid).prizes) < before.sides[pid].prizes:
            add("prize", 450)
    for status in new_status:
        add(status, 200)
    if healed:
        add("heal", 200)
    if energy_gained and not isinstance(action, AttachEnergy):
        add("energy_colorless", 150)
    # descarte como custo de um ataque que acertou: o impacto já diz tudo
    if energy_lost and not (isinstance(action, UseAttack) and hits[actor.other]):
        add("energy_discard", 150)

    # cartas: busca, compra, embaralhar
    me_before, me_after = before.sides[actor], after.state_of(actor)
    searched = any("buscou" in m or "recuperou" in m for m in messages)
    if searched:
        add("card_search", 200)
    elif len(me_after.hand) > me_before.hand + (0 if before.played is None else -1):
        add("card_draw", 150)
    if any("embaralh" in m for m in messages):
        add("shuffle", 150)

    cues: list[Cue] = []
    elapsed = 0
    for group, gap in zip(sequence[:MAX_CUES], gaps[:MAX_CUES], strict=True):
        elapsed += gap
        cues.append((elapsed, group))
    return cues
