"""Dados de cartas que chegam pelo manifest publicado, sem código novo."""

from __future__ import annotations

import hashlib
import json

import requests

from pokemon_companion.cards_db import updates
from pokemon_companion.engine.effects import pack

URL = "https://example.test/data/manifest.json"


def _server(files: dict[str, bytes], min_app: int = updates.APP_DATA_VERSION, **manifest):
    manifest = {
        "version": "t",
        "min_app": min_app,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        **manifest,
    }
    served = {URL: json.dumps(manifest).encode()} | {
        f"https://example.test/data/{n}": d for n, d in files.items()
    }

    def fetch(url: str) -> bytes:
        if url not in served:
            raise requests.ConnectionError(url)
        return served[url]

    return fetch


def test_refresh_downloads_and_data_file_prefers_it(tmp_path):
    effects = json.dumps({"rewrites": {"Old text.": "Draw 2 cards."}}).encode()
    assert updates.refresh(URL, tmp_path, _server({"effects.json": effects})) == ["effects.json"]
    assert updates.data_file("effects.json", tmp_path).read_bytes() == effects
    # mesmo hash: não baixa de novo
    assert updates.refresh(URL, tmp_path, _server({"effects.json": effects})) == []


def test_refresh_keeps_bundled_data_on_bad_hash_newer_app_or_no_network(tmp_path):
    good = json.dumps({"rewrites": {}}).encode()
    tampered = _server({"effects.json": good})

    def bad_hash(url: str) -> bytes:
        return b'{"rewrites": {"x": "y"}}' if url.endswith("effects.json") else tampered(url)

    assert updates.refresh(URL, tmp_path, bad_hash) == []
    newer = _server({"effects.json": good}, min_app=updates.APP_DATA_VERSION + 1)
    assert updates.refresh(URL, tmp_path, newer) == []

    def offline(url: str) -> bytes:
        raise requests.ConnectionError(url)

    assert updates.refresh(URL, tmp_path, offline) == []
    assert updates.data_file("effects.json", tmp_path) == updates.BUNDLED["effects.json"]


def test_rewrite_makes_new_text_compile(monkeypatch):
    from pokemon_companion.engine.effects.text_effects import compile_text

    monkeypatch.setattr(pack, "rewrites", lambda: {"Totally new wording.": "Draw 2 cards."})
    assert compile_text("Totally new wording.") is not None
