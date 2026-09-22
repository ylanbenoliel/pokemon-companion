"""Fixtures compartilhadas: cartas mockadas para testar o motor de regras
sem depender da API pokemontcg.io, e configuração de ambiente headless para
os testes de UI (PyQt6) rodarem sem display real."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from pokemon_companion.cards_db.models import (  # noqa: E402
    Attack,
    Card,
    Supertype,
    WeaknessResistance,
)


def make_energy(name: str, energy_type: str) -> Card:
    return Card(
        id=f"energy-{name.lower()}",
        name=name,
        supertype=Supertype.ENERGY,
        types=[energy_type],
    )


def make_basic_pokemon(
    name: str,
    hp: int,
    energy_type: str,
    attack_name: str = "Tackle",
    attack_cost: list[str] | None = None,
    attack_damage: str = "20",
    weakness: str | None = None,
    resistance: str | None = None,
    retreat_cost: int = 1,
) -> Card:
    return Card(
        id=f"basic-{name.lower()}",
        name=name,
        supertype=Supertype.POKEMON,
        subtypes=["Basic"],
        hp=hp,
        types=[energy_type],
        attacks=[
            Attack(
                name=attack_name,
                cost=attack_cost or [energy_type],
                damage=attack_damage,
            )
        ],
        weaknesses=[WeaknessResistance(weakness, "×2")] if weakness else [],
        resistances=[WeaknessResistance(resistance, "-30")] if resistance else [],
        retreat_cost=["Colorless"] * retreat_cost,
    )


def make_evolution(name: str, evolves_from: str, hp: int, energy_type: str, damage: str) -> Card:
    return Card(
        id=f"stage1-{name.lower()}",
        name=name,
        supertype=Supertype.POKEMON,
        subtypes=["Stage 1"],
        hp=hp,
        types=[energy_type],
        evolves_from=evolves_from,
        attacks=[Attack(name="Strike", cost=[energy_type], damage=damage)],
        retreat_cost=["Colorless"],
    )


@pytest.fixture
def fire_energy() -> Card:
    return make_energy("Fire Energy", "Fire")


@pytest.fixture
def water_energy() -> Card:
    return make_energy("Water Energy", "Water")


@pytest.fixture
def charmander() -> Card:
    return make_basic_pokemon("Charmander", hp=60, energy_type="Fire", weakness="Water")


@pytest.fixture
def squirtle() -> Card:
    return make_basic_pokemon("Squirtle", hp=60, energy_type="Water", weakness="Lightning")


@pytest.fixture
def charmeleon() -> Card:
    return make_evolution("Charmeleon", "Charmander", hp=90, energy_type="Fire", damage="50")


@pytest.fixture
def sample_api_card_json() -> dict:
    """Formato bruto real da API pokemontcg.io, usado para testar a conversão
    em `api_client.api_card_to_card` sem depender de rede."""
    return {
        "id": "svi-26",
        "name": "Charmander",
        "supertype": "Pokémon",
        "subtypes": ["Basic"],
        "hp": "60",
        "types": ["Fire"],
        "attacks": [
            {"name": "Ember", "cost": ["Fire"], "damage": "30", "text": ""},
        ],
        "weaknesses": [{"type": "Water", "value": "×2"}],
        "retreatCost": ["Colorless"],
        "images": {
            "small": "https://images.pokemontcg.io/svi/26.png",
            "large": "https://images.pokemontcg.io/svi/26_hires.png",
        },
    }
