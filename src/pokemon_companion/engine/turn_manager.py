"""Setup e início de partida, seguindo o livro de regras oficial:

1. Cada jogador compra 7 cartas; sem Pokémon Básico na mão = mulligan
   (revela, embaralha e compra 7 de novo, quantas vezes for preciso).
2. Um Básico vai para o Ativo; 6 cartas viram os prêmios.
3. Para cada mulligan *a mais* que o oponente fez, o outro jogador compra 1
   carta extra (a regra diz "pode comprar"; aqui sempre compra).
4. A moeda decide quem começa (`first_player`, sorteado pelo app).
5. Todo turno começa comprando uma carta — inclusive o primeiro de quem
   começa (que, em compensação, não pode atacar nesse turno).

Jogadores em `manual` (o humano) montam Ativo e Banco pela ação de setup
(`state.pending_setup`); os demais têm o Ativo escolhido por heurística
(Básico sem regra de prêmio extra e com mais HP) e montam o banco no turno.

Morte Súbita: se os dois vencem ao mesmo tempo, uma nova partida começa com
1 prêmio para cada (`start_sudden_death`).
"""

from __future__ import annotations

import random

from pokemon_companion.cards_db.basic_energies import BASIC_ENERGIES
from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine.game_state import (
    PRIZE_COUNT,
    GameState,
    PlayerId,
    PlayerState,
    PokemonInPlay,
)
from pokemon_companion.engine.rules import prize_count_for

STARTING_HAND_SIZE = 7
MAX_MULLIGAN_ATTEMPTS = 20


def _draw_opening_hand(deck: list[Card], rng: random.Random) -> tuple[list[Card], list[Card], int]:
    """Devolve (mão, resto do deck, número de mulligans)."""
    remaining = list(deck)
    rng.shuffle(remaining)
    hand = [remaining.pop() for _ in range(min(STARTING_HAND_SIZE, len(remaining)))]
    mulligans = 0
    while not any(c.is_basic for c in hand) and mulligans < MAX_MULLIGAN_ATTEMPTS:
        remaining.extend(hand)
        rng.shuffle(remaining)
        hand = [remaining.pop() for _ in range(min(STARTING_HAND_SIZE, len(remaining)))]
        mulligans += 1
    return hand, remaining, mulligans


def choose_starting_active(hand: list[Card]) -> int | None:
    """Quem pode começar no Ativo (Básico, ou Explosiveness): sem regra de
    prêmio extra (não-ex/V) primeiro, depois mais HP."""
    from pokemon_companion.engine.effects.passive_text import can_start_active

    starters = [i for i, card in enumerate(hand) if can_start_active(card)]
    if not starters:
        return None
    return max(starters, key=lambda i: (prize_count_for(hand[i]) == 1, hand[i].hp or 0))


def _setup_player(
    hand: list[Card], remaining: list[Card], prize_count: int, manual: bool
) -> PlayerState:
    prizes = [remaining.pop() for _ in range(min(prize_count, len(remaining)))]
    player = PlayerState(deck=remaining, hand=hand, prizes=prizes)
    index = choose_starting_active(hand)
    if index is not None and not manual:
        player.active = PokemonInPlay(card=hand.pop(index), turn_played=0)
    return player


def start_new_game(
    player_deck: list[Card],
    opponent_deck: list[Card],
    rng: random.Random | None = None,
    first_player: PlayerId = PlayerId.PLAYER,
    manual: frozenset[PlayerId] = frozenset(),
    prize_count: int = PRIZE_COUNT,
) -> GameState:
    """`manual`: jogadores que fazem as próprias escolhas (setup e novo Ativo)."""
    rng = rng or random.Random()
    player_hand, player_rest, player_mulligans = _draw_opening_hand(player_deck, rng)
    opponent_hand, opponent_rest, opponent_mulligans = _draw_opening_hand(opponent_deck, rng)

    state = GameState(
        player=_setup_player(player_hand, player_rest, prize_count, PlayerId.PLAYER in manual),
        opponent=_setup_player(
            opponent_hand, opponent_rest, prize_count, PlayerId.OPPONENT in manual
        ),
        active_player=first_player,
        manual_choices=manual,
        pending_setup=tuple(pid for pid in (first_player, first_player.other) if pid in manual),
        prize_count=prize_count,
    )

    for receiver, extra in (
        (state.player, opponent_mulligans - player_mulligans),
        (state.opponent, player_mulligans - opponent_mulligans),
    ):
        for _ in range(max(extra, 0)):
            if receiver.deck:
                receiver.hand.append(receiver.deck.pop(0))

    starter = state.state_of(first_player)
    if starter.deck:
        starter.hand.append(starter.deck.pop(0))  # compra do primeiro turno
    return state


def all_cards_of(player: PlayerState) -> list[Card]:
    cards = [*player.deck, *player.hand, *player.discard, *player.prizes]
    for mon in player.all_pokemon_in_play():
        cards.extend(mon.all_cards())
        cards.extend(BASIC_ENERGIES[e] for e in mon.attached_energies if e in BASIC_ENERGIES)
    return cards


def start_sudden_death(state: GameState) -> GameState:
    """Nova partida com os mesmos decks e 1 prêmio (Morte Súbita). Quem
    começa é sorteado de novo."""
    first = random.choice([PlayerId.PLAYER, PlayerId.OPPONENT])
    fresh = start_new_game(
        all_cards_of(state.player),
        all_cards_of(state.opponent),
        first_player=first,
        manual=state.manual_choices,
        prize_count=1,
    )
    fresh.sudden_death = True
    return fresh
