"""Dicas para iniciantes: cada uma aparece uma única vez, na primeira vez em
que a situação acontece na partida (a primeira Energia que dá para ligar, o
primeiro ataque disponível...). O jogador desliga nas configurações ou
revê todas com "Mostrar as dicas de novo".

`next_hint` é pura: olha as jogadas legais agora e o que já foi visto.
"""

from __future__ import annotations

from dataclasses import dataclass

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
)
from pokemon_companion.engine.effects.cardinfo import trainer_kind
from pokemon_companion.engine.game_state import GameState, PlayerId


@dataclass(frozen=True)
class Hint:
    key: str
    text: str


SETUP = Hint(
    "setup",
    "Monte seu time: arraste um Pokémon Básico para o Ativo (o centro) e, se quiser, "
    "outros para o Banco. Depois toque em “Pronto”.",
)
PROMOTE = Hint(
    "promote",
    "Seu Ativo foi nocauteado. Escolha um Pokémon do Banco para ir para a frente — "
    "o oponente acabou de pegar Prêmios.",
)
ENERGY = Hint(
    "energy",
    "Ligue uma Energia por turno: arraste a carta de Energia até um Pokémon. Os ataques "
    "precisam das Energias mostradas no custo.",
)
BENCH = Hint(
    "bench",
    "Ponha Pokémon Básicos no Banco. Se o Ativo for nocauteado sem ninguém no Banco, "
    "você perde a partida.",
)
EVOLVE = Hint(
    "evolve",
    "Esta carta evolui um dos seus Pokémon: arraste-a por cima dele. A evolução mantém "
    "as Energias e cura as condições especiais.",
)
SUPPORTER = Hint(
    "supporter",
    "Apoiadores são os Treinadores mais fortes, mas só dá para jogar um por turno. "
    "Itens você pode jogar quantos quiser.",
)
ABILITY = Hint(
    "ability",
    "Um dos seus Pokémon tem uma Habilidade pronta (ele brilha). Clique nele para usar.",
)
ATTACK = Hint(
    "attack",
    "Seu Ativo pode atacar! Atacar encerra o turno, então jogue as outras cartas antes. "
    "Os botões de ataque ficam à direita do Ativo.",
)
RETREAT = Hint(
    "retreat",
    "Seu Ativo está quase nocauteado. Recuar troca ele por um Pokémon do Banco, "
    "descartando Energias no valor do custo de recuo.",
)

#: HP restante (fração) a partir do qual sugerir o recuo
LOW_HP = 0.35


def next_hint(state: GameState, legal: list[Action], seen: set[str]) -> Hint | None:
    """A dica mais útil agora que o jogador ainda não viu."""
    me = state.state_of(PlayerId.PLAYER)

    def offer(hint: Hint, condition: bool) -> Hint | None:
        return hint if condition and hint.key not in seen else None

    kinds = {type(action) for action in legal}
    supporter = any(
        isinstance(action, PlayTrainer)
        and 0 <= action.hand_index < len(me.hand)
        and trainer_kind(me.hand[action.hand_index]) == "Supporter"
        for action in legal
    )
    active = me.active
    weak = (
        active is not None
        and active.max_hp > 0
        and active.current_hp / active.max_hp <= LOW_HP
        and Retreat in kinds
        and bool(me.bench)
    )
    candidates = (
        offer(SETUP, bool(state.pending_setup) and PlayBasicToActive in kinds),
        offer(PROMOTE, PromoteActive in kinds),
        offer(BENCH, PlayBasicToBench in kinds and not me.bench and not state.pending_setup),
        offer(ENERGY, AttachEnergy in kinds),
        offer(EVOLVE, Evolve in kinds),
        offer(RETREAT, weak),
        offer(ABILITY, UseAbility in kinds),
        offer(SUPPORTER, supporter),
        offer(ATTACK, UseAttack in kinds),
    )
    return next((hint for hint in candidates if hint is not None), None)
