"""Reproduz o top cut de um torneio real com a IA: baixa as listas do Top 8
(Limitless), a chave de eliminação com os vencedores reais (Limitless Labs)
e simula cada confronto muitas vezes em melhor de 3, comparando o favorito
da simulação com quem venceu de verdade.

    uv run python tools/replay_top_cut.py --tournament 515 --labs 0071 --series 40

Mundial 2026 (San Francisco): tournament 515, labs 0071. As listas ficam em
`src/pokemon_companion/decks/worlds2026/` e o relatório em `data/top_cut/`.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from fetch_top_decks import decklist_text, slug  # noqa: E402
from tournament import _init_worker, load_all, play_game  # noqa: E402

from pokemon_companion.paths import BUNDLED_DECKS  # noqa: E402

LIMITLESS = "https://limitlesstcg.com"
LABS = "https://labs.limitlesstcg.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (pokemon-companion top cut replay)"}

STANDING_RE = re.compile(
    r'<tr data-rank="(\d+)" data-name="([^"]+)" data-country="[^"]*" data-deck="([^"]*)".*?'
    r'href="/decks/list/(\d+)"',
    re.S,
)
ROW_RE = re.compile(r"<tr><td[^>]*>\d+</td>(.*?)</tr>", re.S)
CELL_RE = re.compile(r'<td class="([^"]*)">(.*?)</td>', re.S)
NAME_RE = re.compile(r'<span class="text-center">([^<]+)</span>')


@dataclass(frozen=True)
class Player:
    rank: int
    name: str
    deck: str
    list_id: str

    @property
    def deck_key(self) -> str:
        return f"{self.rank}_{slug(self.name)}_{slug(self.deck)}"


@dataclass(frozen=True)
class Match:
    stage: str
    a: str
    b: str
    winner: str


def _get(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = "utf-8"  # o Labs não declara o charset (nomes como "Łaszkiewicz")
    return response.text


def top_players(tournament: str, count: int = 8) -> list[Player]:
    page = _get(f"{LIMITLESS}/tournaments/{tournament}")
    players = [
        Player(int(rank), html.unescape(name), html.unescape(deck), list_id)
        for rank, name, deck, list_id in STANDING_RE.findall(page)
    ]
    return sorted(players, key=lambda p: p.rank)[:count]


def round_matches(labs: str, round_number: int) -> list[tuple[str, str, str]]:
    """(jogador 1, jogador 2, vencedor) de uma rodada."""
    page = _get(f"{LABS}/{labs}/pairings?round={round_number}")
    table = page[page.find("<table") : page.find("</table>")]
    matches = []
    for row in ROW_RE.findall(table):
        cells = CELL_RE.findall(row)
        names = [NAME_RE.search(body) for _, body in cells]
        if len(cells) != 2 or not all(names):
            continue
        a, b = (html.unescape(n.group(1)).strip() for n in names if n)
        winner = a if "winner" in cells[0][0] else b if "winner" in cells[1][0] else ""
        matches.append((a, b, winner))
    return matches


def bracket(labs: str) -> list[Match]:
    """As últimas rodadas com 4, 2 e 1 mesa são quartas, semifinais e final."""
    page = _get(f"{LABS}/{labs}/pairings")
    last = max(int(r) for r in re.findall(r"pairings\?round=(\d+)", page))
    stages = {4: "Quartas", 2: "Semifinal", 1: "Final"}
    result: list[Match] = []
    for round_number in range(last - 2, last + 1):
        matches = round_matches(labs, round_number)
        stage = stages.get(len(matches), f"Rodada {round_number}")
        result += [Match(stage, a, b, w) for a, b, w in matches]
    return result


def save_lists(players: list[Player], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for player in players:
        title, text, total = decklist_text(player.list_id)
        header = (
            f"# {player.deck} — {player.rank}º no torneio, {player.name}\n"
            f"# {title}\n# fonte: {LIMITLESS}/decks/list/{player.list_id}\n"
        )
        (out / f"{player.deck_key}.txt").write_text(header + text + "\n", encoding="utf-8")
        print(f"  {player.rank}º {player.name:<22} {player.deck:<28} {total} cartas")


def simulate_series(
    pool: ProcessPoolExecutor, deck_a: str, deck_b: str, series: int, level: str, seed: int
) -> tuple[int, int, float]:
    """Joga `series` melhores de 3 (quem começa alterna a cada jogo).
    Devolve (séries de A, séries de B, % de jogos de A)."""
    jobs = [
        (deck_a, deck_b, seed + 10 * s + g, level, (s + g) % 2)
        for s in range(series)
        for g in range(3)
    ]
    results = list(pool.map(play_game, jobs, chunksize=3))
    series_a = series_b = games_a = games_total = 0
    for s in range(series):
        wins: Counter[str] = Counter()
        for record in results[3 * s : 3 * s + 3]:
            if max(wins.values(), default=0) >= 2:
                break  # melhor de 3 já decidida
            if record["winner"]:
                wins[record["winner"]] += 1
                games_total += 1
                games_a += record["winner"] == deck_a
        if wins[deck_a] > wins[deck_b]:
            series_a += 1
        elif wins[deck_b] > wins[deck_a]:
            series_b += 1
    return series_a, series_b, games_a / max(games_total, 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--tournament", default="515", help="id do torneio no limitlesstcg.com")
    parser.add_argument("--labs", default="0071", help="id do torneio no labs.limitlesstcg.com")
    parser.add_argument("--series", type=int, default=40, help="melhores de 3 por confronto")
    parser.add_argument("--level", default="hard", choices=["easy", "medium", "hard"])
    parser.add_argument("--decks", type=Path, default=BUNDLED_DECKS / "worlds2026")
    parser.add_argument("--out", type=Path, default=Path("data/top_cut"))
    args = parser.parse_args()

    print("Top 8:")
    players = top_players(args.tournament)
    save_lists(players, args.decks)
    by_name = {p.name: p for p in players}
    matches = bracket(args.labs)
    decks = load_all(args.decks)

    lines = [
        f"Confronto real → simulação (IA nível {args.level}, {args.series} melhores de 3 cada)",
        "",
    ]
    report = []
    agree = 0
    started = time.time()
    with ProcessPoolExecutor(initializer=_init_worker, initargs=(decks,)) as pool:
        for index, match in enumerate(matches):
            pa, pb = by_name[match.a], by_name[match.b]
            sa, sb, games_a = simulate_series(
                pool, pa.deck_key, pb.deck_key, args.series, args.level, 5000 * (index + 1)
            )
            favorite = match.a if sa > sb else match.b if sb > sa else "empate"
            hit = favorite == match.winner
            agree += hit
            line = (
                f"{match.stage:<10} {pa.name} ({pa.deck}) x {pb.name} ({pb.deck})\n"
                f"           real: {match.winner}  |  simulação: {pa.name.split()[0]} "
                f"{sa / args.series:.0%} x {sb / args.series:.0%} {pb.name.split()[0]} "
                f"(jogos {games_a:.0%}–{1 - games_a:.0%})  {'✓ bate' if hit else '✗ não bate'}"
            )
            print(line, flush=True)
            lines.append(line)
            report.append(
                {
                    "stage": match.stage,
                    "a": pa.name,
                    "deck_a": pa.deck,
                    "b": pb.name,
                    "deck_b": pb.deck,
                    "real_winner": match.winner,
                    "sim_series_a": sa,
                    "sim_series_b": sb,
                    "sim_games_a": games_a,
                    "agrees": hit,
                }
            )
    summary = f"\nA simulação apontou o vencedor real em {agree}/{len(matches)} confrontos ({time.time() - started:.0f}s)."
    print(summary)
    lines.append(summary)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (args.out / "report.txt").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
