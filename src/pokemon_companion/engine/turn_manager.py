"""Setup e início de partida.

Simplificação do MVP: o Pokémon ativo inicial de cada jogador é escolhido
automaticamente (primeiro básico encontrado na mão), em vez de o jogador
escolher manualmente — evita modelar uma fase de setup separada do loop de
turnos.
"""

from __future__ import annotations

import random

from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine.game_state import PRIZE_COUNT, GameState, PlayerState, PokemonInPlay

STARTING_HAND_SIZE = 7
MAX_MULLIGAN_ATTEMPTS = 10


def _setup_player(deck: list[Card], rng: random.Random) -> PlayerState:
    remaining = list(deck)
    rng.shuffle(remaining)
    hand = [remaining.pop() for _ in range(min(STARTING_HAND_SIZE, len(remaining)))]

    attempts = 0
    while not any(c.is_basic for c in hand) and remaining and attempts < MAX_MULLIGAN_ATTEMPTS:
        remaining.extend(hand)
        rng.shuffle(remaining)
        hand = [remaining.pop() for _ in range(min(STARTING_HAND_SIZE, len(remaining)))]
        attempts += 1

    prizes = [remaining.pop() for _ in range(min(PRIZE_COUNT, len(remaining)))]
    player = PlayerState(deck=remaining, hand=hand, prizes=prizes)

    for i, card in enumerate(hand):
        if card.is_basic:
            player.active = PokemonInPlay(card=card, turn_played=0)
            del hand[i]
            break

    return player


def start_new_game(
    player_deck: list[Card],
    opponent_deck: list[Card],
    rng: random.Random | None = None,
) -> GameState:
    rng = rng or random.Random()
    return GameState(
        player=_setup_player(player_deck, rng),
        opponent=_setup_player(opponent_deck, rng),
    )
