"""Ações que um jogador (humano ou IA) pode realizar num turno.

`rules.legal_actions()` gera as instâncias válidas destas classes;
`rules.apply_action()` as executa sobre um `GameState`.
"""

from __future__ import annotations

from dataclasses import dataclass


class Action:
    """Classe base — todas as ações são dataclasses imutáveis."""


@dataclass(frozen=True)
class PlayBasicToBench(Action):
    hand_index: int


@dataclass(frozen=True)
class PlayBasicToActive(Action):
    """Só legal quando o ativo está vazio (início de jogo/pós-knockout)."""

    hand_index: int


@dataclass(frozen=True)
class Evolve(Action):
    hand_index: int
    """Índice, em `hand`, da carta de evolução."""
    target_is_active: bool
    bench_index: int | None = None


@dataclass(frozen=True)
class AttachEnergy(Action):
    hand_index: int
    target_is_active: bool
    bench_index: int | None = None


@dataclass(frozen=True)
class UseAttack(Action):
    attack_index: int


@dataclass(frozen=True)
class Retreat(Action):
    bench_index: int
    """Pokémon do banco que assume a posição ativa."""


@dataclass(frozen=True)
class EndTurn(Action):
    pass
