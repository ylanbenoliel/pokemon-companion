"""Atualização de dados de cartas sem atualizar o app: o jogo instalado não
recebe código, então cartas novas chegam como dados publicados num release
fixo do GitHub (tag `data`):

- `manifest.json`: `{"version", "min_app", "files": {nome: sha256}}`;
- `standard_legal.json` e `standard_catalog.json`: rotação e construtor de deck;
- `effects.json`: `{"rewrites": {texto original: texto reescrito}}` — textos
  de carta reescritos com frases que os compiladores de efeito já conhecem.

`refresh()` baixa o que mudou para a pasta do usuário (conferindo o sha256);
`data_file()` devolve a cópia baixada ou a que acompanha o app. Sem internet,
tudo segue com os arquivos embutidos.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import requests

from pokemon_companion.paths import PACKAGE_DIR, USER_DATA

#: versão do vocabulário do motor; sobe quando os compiladores ganham frases
#: novas que um `effects.json` publicado passe a usar
APP_DATA_VERSION = 1
MANIFEST_URL = (
    "https://github.com/ylanbenoliel/pokemon-companion/releases/download/data/manifest.json"
)
UPDATES_DIR = USER_DATA / "updates"
BUNDLED = {
    "standard_legal.json": PACKAGE_DIR / "cards_db" / "standard_legal.json",
    "standard_catalog.json": PACKAGE_DIR / "cards_db" / "standard_catalog.json",
    "effects.json": PACKAGE_DIR / "engine" / "effects" / "effects.json",
}
TIMEOUT = 10


def data_file(name: str, updates_dir: Path | None = None) -> Path:
    downloaded = (updates_dir or UPDATES_DIR) / name
    return downloaded if downloaded.exists() else BUNDLED[name]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def refresh(
    url: str = MANIFEST_URL,
    updates_dir: Path | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> list[str]:
    """Baixa os arquivos novos do manifest; devolve os nomes atualizados.
    Falha de rede, manifest para app mais novo ou hash errado: não muda nada."""
    fetch = fetch or _fetch
    updates_dir = updates_dir or UPDATES_DIR
    try:
        manifest = json.loads(fetch(url))
        if int(manifest["min_app"]) > APP_DATA_VERSION:
            return []
        files = {str(k): str(v) for k, v in manifest["files"].items() if k in BUNDLED}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return []
    updated: list[str] = []
    base = url.rsplit("/", 1)[0]
    for name, digest in files.items():
        local = updates_dir / name
        if local.exists() and _sha256(local.read_bytes()) == digest:
            continue
        try:
            data = fetch(f"{base}/{name}")
            json.loads(data)
        except (requests.RequestException, ValueError):
            continue
        if _sha256(data) != digest:
            continue
        updates_dir.mkdir(parents=True, exist_ok=True)
        partial = local.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(local)
        updated.append(name)
    return updated


def _fetch(url: str) -> bytes:
    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.content
