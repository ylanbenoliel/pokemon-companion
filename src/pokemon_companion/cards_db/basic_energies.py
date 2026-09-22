"""Energias básicas, resolvidas localmente sem tocar a API/cache.

Decklists costumam referenciar energias básicas sem set/número
(ex: "8 Fire Energy") — como o efeito é idêntico em qualquer edição, não
vale a pena buscá-las na API.
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Card, Supertype

_TYPES = [
    "Grass",
    "Fire",
    "Water",
    "Lightning",
    "Psychic",
    "Fighting",
    "Darkness",
    "Metal",
    "Fairy",
]

BASIC_ENERGIES: dict[str, Card] = {
    energy_type: Card(
        id=f"basic-energy-{energy_type.lower()}",
        name=f"{energy_type} Energy",
        supertype=Supertype.ENERGY,
        types=[energy_type],
    )
    for energy_type in _TYPES
}
