"""Aviso de versão nova do app: compara a versão instalada com o release
mais novo do GitHub com tag `vX.Y.Z` (o release `data` fica de fora: é o
pacote de dados de cartas, que chega sozinho por `cards_db/updates.py`).
O app não se substitui sozinho; o aviso só leva à página de download."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version

import requests

from pokemon_companion import net

RELEASES_URL = "https://api.github.com/repos/ylanbenoliel/pokemon-companion/releases"
TIMEOUT = 5
_TAG = re.compile(r"v?(\d+(?:\.\d+)*)")


def current_version() -> str:
    try:
        return version("pokemon-companion")
    except PackageNotFoundError:
        return "0"


def _parse(text: str) -> tuple[int, ...] | None:
    match = _TAG.fullmatch(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer_release(releases: list[dict], installed: str) -> tuple[str, str] | None:
    """(tag, página) do release publicado mais novo que `installed`, se houver."""
    mine = _parse(installed) or (0,)
    best: tuple[tuple[int, ...], str, str] | None = None
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name", ""))
        parsed = _parse(tag)
        if parsed is not None and parsed > mine and (best is None or parsed > best[0]):
            best = (parsed, tag, str(release.get("html_url", "")))
    return None if best is None else (best[1], best[2])


def check() -> tuple[str, str] | None:
    """Consulta o GitHub; sem rede ou com resposta estranha, não avisa nada."""
    try:
        response = net.get(RELEASES_URL, params={"per_page": 20}, timeout=TIMEOUT, tries=2)
        releases = response.json()
    except (requests.RequestException, ValueError):
        return None
    return newer_release(releases, current_version()) if isinstance(releases, list) else None
