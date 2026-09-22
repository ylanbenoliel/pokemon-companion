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
from pokemon_companion.engine.effects import passives
from pokemon_companion.engine.game_state import GameState, PlayerId

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
    # Deck acabando: perder por não conseguir comprar (cresce ao quadrado
    # abaixo de LOW_DECK cartas; sem isto a IA comprava até o deck-out).
    "deck_out": 150.0,
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
    energy = weights["energy_attached"]
    score += energy * sum(len(m.attached_energies) for m in my_mons)
    score -= energy * sum(len(m.attached_energies) for m in their_mons)

    if me.active is None:
        score -= weights["lose_own_active"]

    hand = weights.get("hand_size", 0.0)
    score += hand * min(len(me.hand), HAND_CAP)
    score -= hand * 0.5 * min(len(opponent.hand), HAND_CAP)

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
