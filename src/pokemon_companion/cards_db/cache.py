"""Cache local (SQLite) das cartas resolvidas via API.

Só cacheia cartas conforme aparecem em decklists sendo resolvidas — nunca
baixa o pool inteiro (~18k cartas) de uma vez. O caminho padrão do banco
respeita o diretório de dados do usuário em cada SO (via `platformdirs`).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from pokemon_companion.cards_db.models import Ability, Attack, Card, Supertype, WeaknessResistance
from pokemon_companion.paths import USER_DATA

DEFAULT_DB_PATH = USER_DATA / "cards_cache.db"


def card_to_json(card: Card) -> str:
    payload = dataclasses.asdict(card)
    payload["supertype"] = str(card.supertype)
    return json.dumps(payload, ensure_ascii=False)


def card_from_json(raw: str) -> Card:
    data = json.loads(raw)
    return Card(
        id=data["id"],
        name=data["name"],
        supertype=Supertype(data["supertype"]),
        subtypes=data.get("subtypes", []),
        hp=data.get("hp"),
        types=data.get("types", []),
        attacks=[Attack(**a) for a in data.get("attacks", [])],
        weaknesses=[WeaknessResistance(**w) for w in data.get("weaknesses", [])],
        resistances=[WeaknessResistance(**r) for r in data.get("resistances", [])],
        retreat_cost=data.get("retreat_cost", []),
        evolves_from=data.get("evolves_from"),
        abilities=[Ability(**a) for a in data.get("abilities", [])],
        rules=data.get("rules", []),
        image_url=data.get("image_url"),
        image_local_path=data.get("image_local_path"),
        national_pokedex_numbers=data.get("national_pokedex_numbers", []),
    )


class CardCache:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                number TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_lookup ON cards (name, set_code, number)"
        )
        self._conn.commit()

    def find(self, name: str, set_code: str, number: str) -> Card | None:
        row = self._conn.execute(
            "SELECT data FROM cards WHERE name = ? AND set_code = ? AND number = ?",
            (name, set_code, number),
        ).fetchone()
        return card_from_json(row["data"]) if row else None

    def save(self, card: Card, set_code: str, number: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cards (id, name, set_code, number, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (card.id, card.name, set_code, number, card_to_json(card)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CardCache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
