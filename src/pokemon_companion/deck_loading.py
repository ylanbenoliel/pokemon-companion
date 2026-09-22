"""Orquestra o carregamento de decks (mockado ou via decklist real) — usado
tanto pelo CLI quanto pela UI gráfica, para não duplicar essa lógica entre
os dois entry points."""

from __future__ import annotations

from pathlib import Path

from pokemon_companion.cards_db import standard
from pokemon_companion.cards_db.api_client import PokemonTcgApiClient
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.deck_rules import validate_deck
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.lookup import CardLookup, FallbackLookup
from pokemon_companion.cards_db.models import Card
from pokemon_companion.cards_db.tcgdex_client import TcgdexClient
from pokemon_companion.demo_data import build_demo_deck


class DeckLoadError(Exception):
    pass


def make_lookup() -> CardLookup:
    """pokemontcg.io primeiro; se estiver fora do ar, TCGdex (uma falha só)."""
    return FallbackLookup([PokemonTcgApiClient(max_retries=1), TcgdexClient()])


def _load_deck_file(
    path: Path, cache: CardCache, api_client: CardLookup
) -> tuple[list[Card], list[str]]:
    if not path.is_file():
        raise DeckLoadError(f"Arquivo de decklist não encontrado: {path}")
    cards, errors = load_deck(path, cache, api_client)
    if not cards:
        raise DeckLoadError(f"Não foi possível resolver nenhuma carta de {path}.")
    legal = standard.load_legal()
    errors += [f"{path.name}: {problem}" for problem in validate_deck(cards, legal)]
    return cards, errors


def load_decks(
    player_deck_path: Path | None,
    opponent_deck_path: Path | None,
) -> tuple[list[Card], list[Card], list[str]]:
    """Retorna (deck_do_jogador, deck_da_ia, avisos).

    Sem nenhum caminho informado, usa decks mockados dos dois lados
    (`demo_data.build_demo_deck`). Levanta `DeckLoadError` se um caminho
    informado não existir ou não resolver nenhuma carta.
    """
    if player_deck_path is None and opponent_deck_path is None:
        return build_demo_deck(), build_demo_deck(), []

    warnings: list[str] = []
    with CardCache() as cache:
        api_client = make_lookup()

        if player_deck_path is not None:
            player_deck, player_warnings = _load_deck_file(player_deck_path, cache, api_client)
            warnings.extend(player_warnings)
        else:
            player_deck = build_demo_deck()

        if opponent_deck_path is not None:
            opponent_deck, opponent_warnings = _load_deck_file(
                opponent_deck_path, cache, api_client
            )
            warnings.extend(opponent_warnings)
        else:
            opponent_deck = build_demo_deck()

    return player_deck, opponent_deck, warnings
