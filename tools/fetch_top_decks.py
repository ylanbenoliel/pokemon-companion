"""Baixa os N arquétipos mais jogados do Limitless (formato atual) e salva a
lista mais bem colocada de cada um em `src/pokemon_companion/decks/top/NN_nome.txt`.

    uv run python tools/fetch_top_decks.py --top 20
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import requests

from pokemon_companion.paths import BUNDLED_DECKS

BASE = "https://limitlesstcg.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-companion deck fetcher)"}
ROW_RE = re.compile(
    r"<tr>\s*<td>(\d+)</td>.*?<a href=\"/decks/(\d+)\">(.*?)</a>.*?<td>([\d.]+)%</td>",
    re.S,
)
CARD_RE = re.compile(
    r'data-set="([^"]*)" data-number="([^"]*)".*?card-count">(\d+)<.*?card-name">([^<]+)<', re.S
)


def _get(path: str) -> str:
    response = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def archetypes(top: int) -> list[tuple[int, str, str, float]]:
    page = _get("/decks")
    result = []
    for rank, deck_id, raw_name, share in ROW_RE.findall(page)[:top]:
        name = re.sub(r"<[^>]+>", " ", html.unescape(raw_name))
        result.append((int(rank), deck_id, " ".join(name.split()), float(share)))
    return result


def first_list_id(deck_id: str) -> str | None:
    match = re.search(r"/decks/list/(\d+)", _get(f"/decks/{deck_id}"))
    return match.group(1) if match else None


def decklist_text(list_id: str) -> tuple[str, str, int]:
    page = _get(f"/decks/list/{list_id}")
    title = html.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1)).strip()
    body = page[page.find("data-text-decklist") :]
    lines, total = [], 0
    for column in body.split('<div class="decklist-column-heading">')[1:]:
        lines.append(html.unescape(column[: column.find("<")]).strip())
        for set_code, number, count, name in CARD_RE.findall(column):
            lines.append(f"{count} {html.unescape(name).strip()} {set_code} {number}")
            total += int(count)
    return title, "\n".join(lines), total


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("'", "")).strip("_")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", type=Path, default=BUNDLED_DECKS / "top")
    parser.add_argument(
        "--clean", action="store_true", help="remove os decks que saíram do ranking"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    ranking = archetypes(args.top)
    if not ranking:
        raise SystemExit("O Limitless não devolveu nenhum arquétipo: o layout do site mudou?")
    if args.clean:
        for old in args.out.glob("*.txt"):
            old.unlink()
    for rank, deck_id, name, share in ranking:
        list_id = first_list_id(deck_id)
        if list_id is None:
            print(f"#{rank} {name}: nenhuma lista encontrada")
            continue
        title, text, total = decklist_text(list_id)
        path = args.out / f"{rank:02d}_{slug(name)}.txt"
        header = (
            f"# {name} — #{rank} do meta ({share}% dos pontos)\n"
            f"# {title}\n# fonte: {BASE}/decks/list/{list_id}\n"
        )
        path.write_text(header + text + "\n", encoding="utf-8")
        print(f"#{rank:>2} {name:<32} {share:>5}%  {total} cartas → {path.name}")


if __name__ == "__main__":
    main()
