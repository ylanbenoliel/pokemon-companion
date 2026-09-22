"""Decks mockados para demonstrar o motor de regras + IA sem depender da
API de cartas (decklists reais via `--player-deck` substituem isso)."""

from __future__ import annotations

from pokemon_companion.cards_db.models import Attack, Card, Supertype, WeaknessResistance


def _energy(name: str, energy_type: str) -> Card:
    return Card(
        id=f"energy-{name.lower()}",
        name=name,
        supertype=Supertype.ENERGY,
        types=[energy_type],
    )


def _pokemon(
    name: str,
    hp: int,
    energy_type: str,
    weak_to: str,
    dex: int,
    attacks: list[Attack],
    evolves_from: str | None = None,
    image_url: str | None = None,
) -> Card:
    return Card(
        id=f"demo-{name.lower()}",
        name=name,
        supertype=Supertype.POKEMON,
        subtypes=["Stage 1"] if evolves_from else ["Basic"],
        hp=hp,
        types=[energy_type],
        attacks=attacks,
        weaknesses=[WeaknessResistance(weak_to, "×2")],
        retreat_cost=["Colorless"],
        evolves_from=evolves_from,
        image_url=image_url,
        national_pokedex_numbers=[dex],
    )


def build_demo_deck() -> list[Card]:
    """Deck pequeno (24 cartas, menor que os 60 oficiais) só para demonstrar
    uma partida completa — básicos, evoluções e energias. A arte vem do
    número da Pokédex; as cartas em si são mockadas (regras/ataques não
    batem com as oficiais)."""
    charmander = _pokemon(
        "Charmander",
        hp=60,
        energy_type="Fire",
        weak_to="Water",
        dex=4,
        attacks=[
            Attack(name="Investida", cost=["Fire"], damage="20"),
            Attack(name="Brasa", cost=["Fire", "Colorless"], damage="40"),
        ],
        image_url="https://images.pokemontcg.io/base1/46_hires.png",
    )
    charmeleon = _pokemon(
        "Charmeleon",
        hp=90,
        energy_type="Fire",
        weak_to="Water",
        dex=5,
        attacks=[
            Attack(name="Garra", cost=["Colorless"], damage="30"),
            Attack(name="Lança-Chamas", cost=["Fire", "Fire"], damage="70"),
        ],
        evolves_from="Charmander",
    )
    squirtle = _pokemon(
        "Squirtle",
        hp=60,
        energy_type="Water",
        weak_to="Lightning",
        dex=7,
        attacks=[
            Attack(name="Investida", cost=["Water"], damage="20"),
            Attack(name="Jato d'Água", cost=["Water", "Colorless"], damage="40"),
        ],
        image_url="https://images.pokemontcg.io/base1/63_hires.png",
    )
    wartortle = _pokemon(
        "Wartortle",
        hp=90,
        energy_type="Water",
        weak_to="Lightning",
        dex=8,
        attacks=[
            Attack(name="Mordida", cost=["Colorless"], damage="30"),
            Attack(name="Hidro Bomba", cost=["Water", "Water"], damage="70"),
        ],
        evolves_from="Squirtle",
    )
    fire_energy = _energy("Fire Energy", "Fire")
    water_energy = _energy("Water Energy", "Water")

    return (
        [charmander] * 4
        + [charmeleon] * 2
        + [squirtle] * 4
        + [wartortle] * 2
        + [fire_energy] * 6
        + [water_energy] * 6
    )
