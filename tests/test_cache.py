from __future__ import annotations

from pathlib import Path

from pokemon_companion.cards_db.cache import CardCache


def test_cache_returns_none_when_missing(tmp_path: Path):
    with CardCache(tmp_path / "cache.db") as cache:
        assert cache.find("Charmander", "SVI", "26") is None


def test_cache_roundtrip_preserves_card_data(tmp_path: Path, charmander):
    with CardCache(tmp_path / "cache.db") as cache:
        cache.save(charmander, "SVI", "26")
        loaded = cache.find("Charmander", "SVI", "26")

    assert loaded is not None
    assert loaded.name == charmander.name
    assert loaded.hp == charmander.hp
    assert loaded.types == charmander.types
    assert loaded.attacks[0].name == charmander.attacks[0].name
    assert loaded.weaknesses[0].energy_type == charmander.weaknesses[0].energy_type


def test_cache_persists_across_reopen(tmp_path: Path, charmander):
    db_path = tmp_path / "cache.db"

    with CardCache(db_path) as cache:
        cache.save(charmander, "SVI", "26")

    with CardCache(db_path) as reopened:
        loaded = reopened.find("Charmander", "SVI", "26")

    assert loaded is not None
    assert loaded.name == "Charmander"
