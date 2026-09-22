"""Representação central do estado de uma partida.

`GameState` é a estrutura que todos os outros módulos (regras, IA, UI,
visão computacional) leem e modificam — nenhum deles guarda estado próprio
sobre o jogo.
"""

from __future__ import annotations

import copy
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


@dataclass(eq=False)  # identidade: dois Pokémon iguais continuam sendo dois
class PokemonInPlay:
    card: Card
    #: tipo de cada energia anexada (energia básica) ou nome da energia especial
    attached_energies: list[str] = field(default_factory=list)
    damage_counters: int = 0
    status: StatusCondition = StatusCondition.NONE
    turn_played: int = 0
    evolved_this_turn: bool = False
    #: cartas de estágios anteriores (vão para o descarte junto no nocaute)
    prior_cards: list[Card] = field(default_factory=list)
    special_energy_cards: list[Card] = field(default_factory=list)
    tool: Card | None = None
    #: bônus de HP de Ferramentas/Estádio, recalculado após cada ação
    hp_bonus: int = 0
    abilities_used: set[str] = field(default_factory=set)
    #: marcas de efeitos com duração ("no seu próximo turno, este Pokémon...")
    cannot_attack_turn: int | None = None
    blocked_attack: tuple[str, int] | None = None
    cannot_retreat_turn: int | None = None
    damage_reduction: tuple[int, int] | None = None  # (quantidade, turno)
    attack_debuff: tuple[int, int] | None = None  # (quantidade, turno)
    moved_to_active_turn: int | None = None
    #: turno em que foi jogado da mão para o banco (Habilidades "quando você
    #: jogar este Pokémon da mão no seu banco")
    played_from_hand_turn: int | None = None

    @property
    def max_hp(self) -> int:
        return max((self.card.hp or 0) + self.hp_bonus, 10)

    @property
    def current_hp(self) -> int:
        return max(self.max_hp - self.damage_counters, 0)

    @property
    def is_knocked_out(self) -> bool:
        return self.current_hp <= 0

    def all_cards(self) -> list[Card]:
        cards = [self.card, *self.prior_cards, *self.special_energy_cards]
        return cards + ([self.tool] if self.tool is not None else [])

    def clone(self) -> PokemonInPlay:
        twin = copy.copy(self)
        twin.attached_energies = list(self.attached_energies)
        twin.prior_cards = list(self.prior_cards)
        twin.special_energy_cards = list(self.special_energy_cards)
        twin.abilities_used = set(self.abilities_used)
        return twin


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
    supporter_played_this_turn: bool = False
    stadium_played_this_turn: bool = False
    stadium_used_this_turn: bool = False
    used_ability_names: set[str] = field(default_factory=set)
    #: bônus de dano válidos só neste turno: (quantidade, condição)
    damage_bonus_this_turn: list[tuple[int, str]] = field(default_factory=list)
    items_blocked_turn: int | None = None
    evolution_blocked_turn: int | None = None
    #: turno em que um Pokémon deste jogador foi nocauteado (para "se algum dos
    #: seus Pokémon foi nocauteado no último turno do oponente")
    knocked_out_turn: int | None = None
    played_team_rocket_supporter_turn: int | None = None
    legacy_energy_used: bool = False
    #: ataques já feitos neste turno (Festival Lead permite 2)
    attacks_this_turn: int = 0
    #: Briar: prêmio extra se um Tera nocautear o Ativo neste turno
    extra_prize_turn: int | None = None

    def all_pokemon_in_play(self) -> list[PokemonInPlay]:
        return ([self.active] if self.active else []) + self.bench

    def has_pokemon_in_play(self) -> bool:
        return self.active is not None or len(self.bench) > 0

    def clone(self) -> PlayerState:
        twin = copy.copy(self)
        twin.deck = list(self.deck)
        twin.hand = list(self.hand)
        twin.discard = list(self.discard)
        twin.prizes = list(self.prizes)
        twin.active = self.active.clone() if self.active is not None else None
        twin.bench = [mon.clone() for mon in self.bench]
        twin.used_ability_names = set(self.used_ability_names)
        twin.damage_bonus_this_turn = list(self.damage_bonus_this_turn)
        return twin


@dataclass
class GameState:
    player: PlayerState
    opponent: PlayerState
    turn_number: int = 1
    active_player: PlayerId = PlayerId.PLAYER
    winner: PlayerId | None = None
    stadium: Card | None = None
    stadium_owner: PlayerId | None = None
    #: jogadores que fazem as próprias escolhas (humano); os demais são
    #: resolvidos automaticamente por heurística
    manual_choices: frozenset[PlayerId] = frozenset()
    #: jogador que precisa escolher o novo Ativo antes de o jogo seguir
    pending_promotion: PlayerId | None = None
    #: o que retomar depois da promoção: "end_turn" (o ataque encerrou o
    #: turno) ou "next_turn" (o Checkup já rodou); "" = segue o turno atual
    resume_phase: str = ""
    #: jogadores que ainda estão montando Ativo/Banco no setup
    pending_setup: tuple[PlayerId, ...] = ()
    #: os dois jogadores venceram ao mesmo tempo → Morte Súbita
    sudden_death: bool = False
    prize_count: int = PRIZE_COUNT

    def state_of(self, player_id: PlayerId) -> PlayerState:
        return self.player if player_id is PlayerId.PLAYER else self.opponent

    def clone(self, auto_choices: bool = False) -> GameState:
        """Cópia rápida para as simulações da IA (cartas são imutáveis e
        compartilhadas). `auto_choices=True` resolve escolhas humanas
        pendentes por heurística dentro da simulação."""
        twin = copy.copy(self)
        twin.player = self.player.clone()
        twin.opponent = self.opponent.clone()
        if auto_choices:
            twin.manual_choices = frozenset()
        return twin
