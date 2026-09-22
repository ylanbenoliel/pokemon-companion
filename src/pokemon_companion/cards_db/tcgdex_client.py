"""Cliente da API pública TCGdex (api.tcgdex.net), usado como fonte
alternativa quando a pokemontcg.io está fora do ar.

Decklists do Limitless usam a sigla oficial do set ("TWM 130"); a TCGdex
indexa por id interno ("sv06-130"). A resolução é: busca exata por nome →
filtra pelo número da carta → confirma a sigla oficial do set
(`abbreviation.official`, cacheada por set) → baixa os detalhes da carta.
"""

from __future__ import annotations

import requests

from pokemon_companion.cards_db.models import (
    Ability,
    Attack,
    Card,
    Supertype,
    WeaknessResistance,
)

BASE_URL = "https://api.tcgdex.net/v2/en"

_CATEGORY_MAP = {
    "Pokemon": Supertype.POKEMON,
    "Trainer": Supertype.TRAINER,
    "Energy": Supertype.ENERGY,
}
_STAGE_MAP = {"Basic": "Basic", "Stage1": "Stage 1", "Stage2": "Stage 2"}


def _same_number(local_id: str, number: str) -> bool:
    return local_id.lstrip("0") == number.lstrip("0")


def tcgdex_card_to_card(data: dict) -> Card:
    """Converte o JSON de detalhe de carta da TCGdex no modelo `Card`."""
    subtypes = []
    stage = data.get("stage")
    if stage:
        subtypes.append(_STAGE_MAP.get(stage, stage))
    name = data.get("name", "")
    suffix = data.get("suffix")
    if suffix:
        subtypes.append(suffix)
    if name.endswith(" ex") and "ex" not in subtypes:
        subtypes.append("ex")  # Megas vêm sem `suffix` na TCGdex
    if name.startswith("Mega "):
        subtypes.append("Mega")
    trainer_type = data.get("trainerType")
    if trainer_type:
        subtypes.append(trainer_type)  # Item, Supporter, Stadium, Tool
    is_energy = str(data.get("category", "")).lower() == "energy"
    if data.get("energyType") == "Special" or (is_energy and data.get("effect")):
        # Algumas especiais recentes vêm sem energyType; energia com texto é especial.
        subtypes.append("Special")
    if str(data.get("rarity", "")).upper().startswith("ACE SPEC"):
        subtypes.append("ACE SPEC")

    image = data.get("image")
    return Card(
        id=f"tcgdex-{data['id']}",
        name=data["name"],
        supertype=_CATEGORY_MAP.get(data.get("category", ""), Supertype.TRAINER),
        subtypes=subtypes,
        hp=data.get("hp"),
        types=data.get("types", []),
        attacks=[
            Attack(
                name=attack.get("name", ""),
                cost=attack.get("cost", []),
                damage=str(attack.get("damage", "")),
                text=attack.get("effect", ""),
            )
            for attack in data.get("attacks", [])
        ],
        weaknesses=[
            WeaknessResistance(w.get("type", ""), w.get("value", "×2"))
            for w in data.get("weaknesses", [])
        ],
        resistances=[
            WeaknessResistance(r.get("type", ""), r.get("value", "-30"))
            for r in data.get("resistances", [])
        ],
        retreat_cost=["Colorless"] * int(data.get("retreat", 0) or 0),
        evolves_from=data.get("evolveFrom"),
        abilities=[
            Ability(
                name=a.get("name", ""),
                text=a.get("effect", ""),
                ability_type=a.get("type", "Ability"),
            )
            for a in data.get("abilities", [])
        ],
        rules=[data["effect"]] if data.get("effect") else [],
        image_url=f"{image}/high.png" if image else None,
        national_pokedex_numbers=list(data.get("dexId", [])),
    )


class TcgdexClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 15) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self._set_codes: dict[str, str | None] = {}

    def _get(self, path: str, params: dict[str, str] | None = None) -> object:
        response = self._session.get(f"{BASE_URL}{path}", params=params, timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    def _set_code(self, set_id: str) -> str | None:
        if set_id not in self._set_codes:
            data = self._get(f"/sets/{set_id}")
            abbreviation = data.get("abbreviation", {}) if isinstance(data, dict) else {}
            self._set_codes[set_id] = abbreviation.get("official")
        return self._set_codes[set_id]

    def find_card(self, name: str, set_code: str, number: str) -> Card | None:
        briefs = self._get("/cards", params={"name": f"eq:{name}"})
        if not isinstance(briefs, list):
            return None
        for brief in briefs:
            card_id = str(brief.get("id", ""))
            if "-" not in card_id or not _same_number(str(brief.get("localId", "")), number):
                continue
            set_id = card_id.rsplit("-", 1)[0]
            if (self._set_code(set_id) or "").upper() != set_code.upper():
                continue
            detail = self._get(f"/cards/{card_id}")
            return tcgdex_card_to_card(detail) if isinstance(detail, dict) else None
        return None
