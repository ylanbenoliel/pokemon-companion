"""Cliente HTTP para a API pública pokemontcg.io.

Rate limit: 30 req/dia sem API key, 20000 req/dia com uma key gratuita
(ver `POKEMONTCG_API_KEY` em `config/settings.yaml`). O cliente faz
retry com backoff exponencial em respostas 429/5xx.
"""

from __future__ import annotations

import os
import time

import requests

from pokemon_companion.cards_db.models import (
    Attack,
    Card,
    Supertype,
    WeaknessResistance,
    signature,
)

BASE_URL = "https://api.pokemontcg.io/v2"

_SUPERTYPE_MAP = {
    "Pokémon": Supertype.POKEMON,
    "Pokemon": Supertype.POKEMON,
    "Trainer": Supertype.TRAINER,
    "Energy": Supertype.ENERGY,
}


class CardNotFoundError(Exception):
    pass


def _parse_attack(data: dict) -> Attack:
    return Attack(
        name=data.get("name", ""),
        cost=data.get("cost", []),
        damage=data.get("damage", ""),
        text=data.get("text", ""),
    )


def _parse_weakness_resistance(data: dict) -> WeaknessResistance:
    return WeaknessResistance(energy_type=data.get("type", ""), value=data.get("value", ""))


def api_card_to_card(data: dict) -> Card:
    """Converte o JSON bruto da API pokemontcg.io no nosso modelo `Card`."""
    images = data.get("images", {})
    hp_raw = data.get("hp")
    return Card(
        id=data["id"],
        name=data["name"],
        supertype=_SUPERTYPE_MAP.get(data.get("supertype", "Pokémon"), Supertype.POKEMON),
        subtypes=data.get("subtypes", []),
        hp=int(hp_raw) if hp_raw else None,
        types=data.get("types", []),
        attacks=[_parse_attack(a) for a in data.get("attacks", [])],
        weaknesses=[_parse_weakness_resistance(w) for w in data.get("weaknesses", [])],
        resistances=[_parse_weakness_resistance(r) for r in data.get("resistances", [])],
        retreat_cost=data.get("retreatCost", []),
        evolves_from=data.get("evolvesFrom"),
        rules=data.get("rules", []),
        image_url=images.get("large") or images.get("small"),
        national_pokedex_numbers=data.get("nationalPokedexNumbers", []),
    )


class PokemonTcgApiClient:
    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        timeout: float = 10,
    ) -> None:
        self._timeout = timeout
        self._api_key = api_key or os.environ.get("POKEMONTCG_API_KEY")
        self._session = session or requests.Session()
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key} if self._api_key else {}

    def _get(self, path: str, params: dict[str, str]) -> dict:
        url = f"{BASE_URL}{path}"
        delay = self._backoff_seconds
        for attempt in range(self._max_retries):
            response = self._session.get(
                url, params=params, headers=self._headers(), timeout=self._timeout
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self._max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Falha ao consultar {url} após {self._max_retries} tentativas.")

    def find_card(self, name: str, set_ptcgo_code: str, number: str) -> Card | None:
        query = f'name:"{name}" set.ptcgoCode:{set_ptcgo_code} number:{number}'
        data = self._get("/cards", params={"q": query})
        results = data.get("data", [])
        return api_card_to_card(results[0]) if results else None

    def signatures_with_subtype(self, subtype: str) -> set[str]:
        """Assinaturas (`models.signature`) das cartas com a marca ("Tera",
        "Ancient", "Future") — a TCGdex não traz essas marcas."""
        found: set[str] = set()
        page = 1
        while True:
            data = self._get(
                "/cards",
                params={"q": f"subtypes:{subtype}", "page": str(page), "pageSize": "250"},
            )
            found |= {signature(api_card_to_card(item)) for item in data.get("data", [])}
            if page * 250 >= int(data.get("totalCount", 0)):
                return found
            page += 1
