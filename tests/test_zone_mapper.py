from __future__ import annotations

import numpy as np

from pokemon_companion.vision.zone_mapper import (
    ACTIVE,
    BENCH_PREFIX,
    DISCARD,
    PRIZES,
    Zone,
    crop_zone,
    default_zone_map,
    load_zone_map,
    point_in_zone,
    save_zone_map,
)


def test_default_zone_map_has_active_bench_prizes_and_discard():
    zones = default_zone_map()

    assert ACTIVE in zones
    assert PRIZES in zones
    assert DISCARD in zones
    assert sum(1 for name in zones if name.startswith(BENCH_PREFIX)) == 5


def test_point_in_zone():
    zone = Zone("quad", ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))

    assert point_in_zone((0.5, 0.5), zone)
    assert not point_in_zone((2.0, 2.0), zone)


def test_crop_zone_extracts_expected_region():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[25:75, 25:75] = 255
    zone = Zone("center", ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)))

    crop = crop_zone(frame, zone)

    assert 45 <= crop.shape[0] <= 51
    assert 45 <= crop.shape[1] <= 51
    assert crop.mean() > 200


def test_zone_map_json_roundtrip(tmp_path):
    zones = default_zone_map()
    path = tmp_path / "zones.json"

    save_zone_map(zones, path)
    loaded = load_zone_map(path)

    assert set(loaded.keys()) == set(zones.keys())
    assert loaded[ACTIVE].polygon == zones[ACTIVE].polygon
