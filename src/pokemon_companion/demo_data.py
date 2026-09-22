"""Decks mockados para demonstrar o motor de regras + IA sem depender da
API de cartas (Fase 2 substitui isso por decklists reais)."""

from __future__ import annotations

from pokemon_companion.cards_db.models import Attack, Card, Supertype, WeaknessResistance


def _energy(name: str, energy_type: str) -> Card:
    return Card(
        id=f"energy-{name.lower()}",
        name=name,
        supertype=Supertype.ENERGY,
        types=[energy_type],
    )


def _basic(
    name: str,
    hp: int,
    energy_type: str,
    weak_to: str,
    image_url: str | None = None,
) -> Card:
    return Card(
        id=f"basic-{name.lower()}",
        name=name,
        supertype=Supertype.POKEMON,
        subtypes=["Basic"],
        hp=hp,
        types=[energy_type],
        attacks=[
            Attack(name="Investida", cost=[energy_type], damage="20"),
            Attack(name="Golpe Forte", cost=[energy_type, "Colorless"], damage="50"),
        ],
        weaknesses=[WeaknessResistance(weak_to, "×2")],
        retreat_cost=["Colorless"],
        image_url=image_url,
    )


def build_demo_deck() -> list[Card]:
    """Deck simples de 20 cartas (menor que os 60 oficiais) só para
    demonstrar uma partida completa de ponta a ponta via CLI."""
    # URLs reais da arte (Base Set 1999) — só para a demo ficar visualmente
    # equivalente ao que um deck real importado via decklist mostraria; a
    # carta em si continua mockada (regras/ataques não batem com a oficial).
    fire_mon = _basic(
        "Charmander",
        hp=60,
        energy_type="Fire",
        weak_to="Water",
        image_url="https://images.pokemontcg.io/base1/46_hires.png",
    )
    water_mon = _basic(
        "Squirtle",
        hp=60,
        energy_type="Water",
        weak_to="Lightning",
        image_url="https://images.pokemontcg.io/base1/63_hires.png",
    )
    fire_energy = _energy("Fire Energy", "Fire")
    water_energy = _energy("Water Energy", "Water")

    return [fire_mon] * 5 + [water_mon] * 5 + [fire_energy] * 5 + [water_energy] * 5
