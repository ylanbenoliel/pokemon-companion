"""Ações que um jogador (humano ou IA) pode realizar.

`rules.legal_actions()` gera as instâncias válidas destas classes;
`rules.apply_action()` as executa sobre um `GameState`.

Alvos (`target`) usam tuplas simples, para as ações poderem ser comparadas,
guardadas no histórico e enumeradas pela IA:
- ("own", i) / ("opp", i): Pokémon próprio / do oponente, i = -1 para o
  Ativo e 0..n para o Banco;
- ("copy", i, j): ataque j do Pokémon i do seu Banco (ex: Night Joker);
- ("mode", k): opção k de uma carta "escolha 1".
"""

from __future__ import annotations

from dataclasses import dataclass

Target = tuple[object, ...]


class Action:
    """Classe base — todas as ações são dataclasses imutáveis."""


@dataclass(frozen=True)
class PlayBasicToBench(Action):
    hand_index: int


@dataclass(frozen=True)
class PlayBasicToActive(Action):
    """Legal com o Ativo vazio (setup)."""

    hand_index: int


@dataclass(frozen=True)
class Evolve(Action):
    hand_index: int
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
    target: Target | None = None


@dataclass(frozen=True)
class Retreat(Action):
    bench_index: int


@dataclass(frozen=True)
class PlayTrainer(Action):
    hand_index: int
    target: Target | None = None


@dataclass(frozen=True)
class UseAbility(Action):
    position: int  # -1 = Ativo, 0..n = Banco
    ability_name: str
    target: Target | None = None


@dataclass(frozen=True)
class UseStadium(Action):
    """Efeito "uma vez por turno" do Estádio em jogo."""


@dataclass(frozen=True)
class PromoteActive(Action):
    """Escolha do novo Ativo após um nocaute (jogador humano)."""

    bench_index: int


@dataclass(frozen=True)
class EndSetup(Action):
    """Termina a montagem de Ativo/Banco no setup (jogador humano)."""


@dataclass(frozen=True)
class EndTurn(Action):
    pass
