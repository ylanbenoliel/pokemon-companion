"""Representação central do estado de uma partida.

`GameState` é a estrutura que todos os outros módulos (regras, IA, UI,
visão computacional) leem e modificam — nenhum deles guarda estado próprio
sobre o jogo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto

from pokemon_companion.cards_db.models import Card

MAX_BENCH_SIZE = 5
PRIZE_COUNT = 6


class StatusCondition(Enum):
    NONE = auto()
    ASLEEP = auto()
    CONFUSED = auto()
    PARALYZED = auto()
    POISONED = auto()
    BURNED = auto()


class PlayerId(StrEnum):
    PLAYER = "player"
    OPPONENT = "opponent"

    @property
    def other(self) -> PlayerId:
        return PlayerId.OPPONENT if self is PlayerId.PLAYER else PlayerId.PLAYER


@dataclass
class PokemonInPlay:
    card: Card
    attached_energies: list[str] = field(default_factory=list)
    damage_counters: int = 0
    status: StatusCondition = StatusCondition.NONE
    turn_played: int = 0
    evolved_this_turn: bool = False

    @property
    def current_hp(self) -> int:
        hp = self.card.hp or 0
        return max(hp - self.damage_counters, 0)

    @property
    def is_knocked_out(self) -> bool:
        return self.current_hp <= 0


@dataclass
class PlayerState:
    deck: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    prizes: list[Card] = field(default_factory=list)
    active: PokemonInPlay | None = None
    bench: list[PokemonInPlay] = field(default_factory=list)
    has_attached_energy_this_turn: bool = False
    has_retreated_this_turn: bool = False

    def all_pokemon_in_play(self) -> list[PokemonInPlay]:
        return ([self.active] if self.active else []) + self.bench

    def has_pokemon_in_play(self) -> bool:
        return self.active is not None or len(self.bench) > 0


@dataclass
class GameState:
    player: PlayerState
    opponent: PlayerState
    turn_number: int = 1
    active_player: PlayerId = PlayerId.PLAYER
    winner: PlayerId | None = None

    def state_of(self, player_id: PlayerId) -> PlayerState:
        return self.player if player_id is PlayerId.PLAYER else self.opponent
