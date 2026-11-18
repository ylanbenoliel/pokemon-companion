"""IA média: simula cada ação legal e escolhe a de maior pontuação heurística.

Os pesos são configuráveis (ver `ai/weights.yaml`) para facilitar ajuste sem
tocar na lógica de avaliação.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pokemon_companion.engine import rules
from pokemon_companion.engine.actions import Action, Retreat
from pokemon_companion.engine.effects import attacks, passives
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay

DEFAULT_WEIGHTS: dict[str, float] = {
    "win": 1000.0,
    "prizes_taken": 25.0,
    "board_presence": 5.0,
    # HP *restante* em campo, não contadores de dano: com contadores, nocautear
    # "apagava" o dano já causado (o Pokémon vai para o descarte) e um KO
    # chegava a pontuar negativo — a IA deixava de atacar para não nocautear.
    "opponent_hp": 1.0,
    "own_hp": 0.4,
    "lose_own_active": 80.0,
    # Recuar descarta energia; sem este termo a IA recuava sem propósito.
    "energy_attached": 5.0,
    # Cartas na mão (até HAND_CAP): sem isto, Apoiadores de compra e buscas
    # não valiam nada para a IA.
    "hand_size": 2.0,
    # Ativo que já consegue atacar no próximo turno.
    "attack_ready": 12.0,
    # Progresso de energia rumo ao melhor ataque de cada Pokémon (banco
    # inclusive), ponderado pelo dano: sem isto a IA anexava energia em
    # qualquer um e o atacante principal nunca ficava pronto.
    "attack_progress": 20.0,
    # Deck acabando: perder por não conseguir comprar (cresce ao quadrado
    # abaixo de LOW_DECK cartas; sem isto a IA comprava até o deck-out).
    "deck_out": 150.0,
    # Dano total causado no oponente, contando o HP máximo dos nocauteados:
    # ao contrário de `opponent_hp`, o Pokémon novo que ele baixa no Banco
    # não pesa contra (com `opponent_hp`, nocautear abria vaga, ele baixava
    # outro e o KO parecia ruim).
    "damage_dealt": 0.0,
    # 1 = potencial de ataque medido contra um Ativo sem dano: senão ataques
    # que crescem com o dano do alvo (Mad Bite) "preferiam" não nocautear.
    "potential_vs_fresh": 0.0,
}

HAND_CAP = 8
LOW_DECK = 12


def load_weights(path: Path | None = None, profile: str = "medium") -> dict[str, float]:
    if path is None:
        return dict(DEFAULT_WEIGHTS)
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return {**DEFAULT_WEIGHTS, **data.get(profile, {})}


def evaluate_state(state: GameState, perspective: PlayerId, weights: dict[str, float]) -> float:
    if state.winner == perspective:
        return weights["win"]
    if state.winner == perspective.other:
        return -weights["win"]

    me = state.state_of(perspective)
    opponent = state.state_of(perspective.other)
    score = 0.0

    score += weights["prizes_taken"] * (state.prize_count - len(me.prizes))
    score -= weights["prizes_taken"] * (state.prize_count - len(opponent.prizes))

    score += weights["board_presence"] * len(me.bench)
    score -= weights["board_presence"] * len(opponent.bench)

    my_mons, their_mons = me.all_pokemon_in_play(), opponent.all_pokemon_in_play()
    score += weights["own_hp"] * sum(m.current_hp for m in my_mons)
    score -= weights["opponent_hp"] * sum(m.current_hp for m in their_mons)
    dealt = weights.get("damage_dealt", 0.0)
    if dealt:
        score += dealt * (sum(m.damage_counters for m in their_mons) + opponent.hp_knocked_out)
    energy = weights["energy_attached"]
    score += energy * sum(len(m.attached_energies) for m in my_mons)
    score -= energy * sum(len(m.attached_energies) for m in their_mons)

    if me.active is None:
        score -= weights["lose_own_active"]

    hand = weights.get("hand_size", 0.0)
    score += hand * min(len(me.hand), HAND_CAP)
    score -= hand * 0.5 * min(len(opponent.hand), HAND_CAP)

    progress = weights.get("attack_progress", 0.0)
    if progress:
        fresh = bool(weights.get("potential_vs_fresh", 0.0))
        score += progress * sum(_attack_potential(state, perspective, m, fresh) for m in my_mons)
        score -= (
            progress
            * 0.5
            * sum(_attack_potential(state, perspective.other, m, fresh) for m in their_mons)
        )

    ready = weights.get("attack_ready", 0.0)
    for side, sign in ((perspective, 1), (perspective.other, -1)):
        active = state.state_of(side).active
        if active is not None and any(
            passives.can_pay(state, side, active, attack) for attack in active.card.attacks
        ):
            score += sign * ready

    if len(me.deck) < LOW_DECK:
        score -= weights.get("deck_out", 0.0) * ((LOW_DECK - len(me.deck)) / LOW_DECK) ** 2

    return score


def _attack_potential(
    state: GameState, side: PlayerId, mon: PokemonInPlay, vs_fresh: bool = False
) -> float:
    """Melhor (dano/100 × fração do custo paga) entre os ataques do Pokémon.
    `vs_fresh`: estima contra o Ativo do oponente sem contadores de dano."""
    units = passives.provided_energy(state, side, mon)
    target = state.state_of(side.other).active
    saved = target.damage_counters if target is not None else 0
    if vs_fresh and target is not None:
        target.damage_counters = 0
    try:
        return _best_potential(state, side, mon, units)
    finally:
        if target is not None:
            target.damage_counters = saved


def _best_potential(
    state: GameState, side: PlayerId, mon: PokemonInPlay, units: list[str]
) -> float:
    best = 0.0
    for attack in mon.card.attacks:
        damage = attacks.estimated_damage(state, side, mon, attack)
        if damage <= 0:
            continue
        cost = passives.attack_cost(state, side, mon, attack)
        best = max(best, min(damage, 300) / 100 * passives.cost_progress(units, cost))
    return best


def retreats_last(actions: list[Action]) -> list[Action]:
    """Em caso de empate de pontuação, prefere qualquer coisa a recuar (o
    primeiro melhor vence) — evita trocas de ativo sem propósito."""
    return sorted(actions, key=lambda action: isinstance(action, Retreat))


class MediumAI:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = weights or DEFAULT_WEIGHTS

    def choose_action(self, state: GameState, legal_actions: list[Action]) -> Action:
        perspective = rules.decision_player(state)
        best_action = legal_actions[0]
        best_score = float("-inf")
        for action in retreats_last(legal_actions):
            simulated = state.clone(auto_choices=True)
            rules.apply_action(simulated, action)
            score = evaluate_state(simulated, perspective, self._weights)
            if score > best_score:
                best_score = score
                best_action = action
        return best_action
