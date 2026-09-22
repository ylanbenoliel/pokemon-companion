"""Busca e download de decklists do limitlesstcg.com.

Três formas de trazer um deck do competitivo para o app:

- pelo **nome do arquétipo** (`search_archetypes("dragapult")`), que devolve
  os arquétipos do formato atual com a fatia do meta de cada um;
- por **link** de uma lista ou de um arquétipo (`deck_text_from_url`);
- colando o texto da decklist (formato Limitless/PTCGO), que não passa por
  aqui — é só salvar o arquivo em `data/decks/`.

O HTML é lido por expressão regular (o site não tem API pública): as funções
de parse recebem a página como texto, então dá para testá-las sem rede.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://limitlesstcg.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-companion deck import)"}
TIMEOUT = 30

ARCHETYPE_RE = re.compile(
    r"<tr>\s*<td>(\d+)</td>.*?<a href=\"/decks/(\d+)\">(.*?)</a>.*?<td>([\d.]+)%</td>", re.S
)
LIST_ID_RE = re.compile(r"/decks/list/(\d+)")
CARD_RE = re.compile(
    r'data-set="([^"]*)" data-number="([^"]*)".*?card-count">(\d+)<.*?card-name">([^<]+)<', re.S
)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


@dataclass(frozen=True)
class Archetype:
    """Um arquétipo do formato atual, como o Limitless lista."""

    rank: int
    deck_id: str
    name: str
    share: float

    @property
    def label(self) -> str:
        return f"{self.name} — #{self.rank} do meta ({self.share:g}% dos pontos)"


def fetch(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_archetypes(page: str) -> list[Archetype]:
    found = []
    for rank, deck_id, raw_name, share in ARCHETYPE_RE.findall(page):
        name = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(raw_name)).split())
        found.append(Archetype(int(rank), deck_id, name, float(share)))
    return found


def parse_decklist(page: str) -> tuple[str, str, int]:
    """(título, texto da decklist, total de cartas) de uma página de lista."""
    title_match = TITLE_RE.search(page)
    title = html.unescape(title_match.group(1)).strip() if title_match else "Deck"
    body = page[page.find("data-text-decklist") :]
    lines: list[str] = []
    total = 0
    for column in body.split('<div class="decklist-column-heading">')[1:]:
        lines.append(html.unescape(column[: column.find("<")]).strip())
        for set_code, number, count, name in CARD_RE.findall(column):
            lines.append(f"{count} {html.unescape(name).strip()} {set_code} {number}")
            total += int(count)
    return title, "\n".join(lines), total


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def search_archetypes(query: str, page: str | None = None) -> list[Archetype]:
    """Arquétipos do formato atual cujo nome casa com `query` (sem acento e
    sem caixa). Sem `query`, devolve o ranking inteiro."""
    archetypes = parse_archetypes(page if page is not None else fetch(f"{BASE}/decks"))
    if not query.strip():
        return archetypes
    words = _normalize(query).split()
    return [a for a in archetypes if all(word in _normalize(a.name) for word in words)]


def first_list_id(deck_page: str) -> str | None:
    match = LIST_ID_RE.search(deck_page)
    return match.group(1) if match else None


def archetype_decklist(archetype: Archetype) -> tuple[str, str, int]:
    """Melhor lista publicada do arquétipo (a primeira que o site mostra)."""
    list_id = first_list_id(fetch(f"{BASE}/decks/{archetype.deck_id}"))
    if list_id is None:
        raise ValueError(f"Nenhuma lista publicada para {archetype.name}.")
    return parse_decklist(fetch(f"{BASE}/decks/list/{list_id}"))


def deck_text_from_url(url: str) -> tuple[str, str, int]:
    """Aceita link de lista (`/decks/list/123`) ou de arquétipo (`/decks/45`)."""
    if "/decks/list/" not in url:
        list_id = first_list_id(fetch(url))
        if list_id is None:
            raise ValueError("A página não tem nenhuma decklist.")
        url = f"{BASE}/decks/list/{list_id}"
    return parse_decklist(fetch(url))


def slug(name: str) -> str:
    plain = _normalize(name).replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_") or "deck"


def save_deck(folder: Path, name: str, title: str, text: str, source: str = "") -> Path:
    """Grava a decklist em `folder/<nome>.txt` com um cabeçalho de origem."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug(name)}.txt"
    header = f"# {name}\n# {title}\n"
    if source:
        header += f"# fonte: {source}\n"
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    return path
