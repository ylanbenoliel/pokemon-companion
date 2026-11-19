"""Relatório da manutenção: a seção do Expandido (só informativa)."""

from __future__ import annotations

import sys
from pathlib import Path

from pokemon_companion.cards_db.catalog import _set_age
from pokemon_companion.cards_db.models import Card, Supertype

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import maintenance  # noqa: E402


def test_report_shows_expanded_coverage_without_failing_the_routine():
    known = Card(id="a", name="Boss's Orders", supertype=Supertype.TRAINER, subtypes=["Supporter"])
    unknown = Card(id="b", name="Carta Inventada", supertype=Supertype.TRAINER, subtypes=["Item"])

    text, needs_work = maintenance.report([], {}, set(), [], expanded=[known, unknown])

    assert "Pool do Expandido: 2 impressões; cobertura 1/2 textos-impressão (50%" in text
    assert "## Expandido" in text and "1× efeito do Treinador (Carta Inventada)" in text
    assert not needs_work
    assert "não baixado" in maintenance.report([], {}, set(), [])[0]


def test_set_age_orders_series_from_black_and_white_on():
    ids = ["me02", "bw1", "sv01", "sm7.5", "xy12", "swsh12.5", "g1"]
    assert sorted(ids, key=_set_age) == ["g1", "bw1", "xy12", "sm7.5", "swsh12.5", "sv01", "me02"]
