"""Torneio IA vs IA (round robin) entre decklists, sem interface.

Uso:
    uv run python tools/tournament.py src/pokemon_companion/decks/top --games 4 --level hard

Cada par de decks joga `--games` partidas (alternando quem começa). Gera
`<saída>/results.json` (uma linha por partida) e imprime um resumo: taxa de
vitória por deck, duração, como as partidas terminaram, turnos em que a IA
podia atacar e passou, cartas de Treinador sem efeito implementado, erros e
tempo de decisão da IA. Partidas rodam em paralelo (um processo por núcleo).
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from pokemon_companion.ai.opponent import build_ai
from pokemon_companion.cards_db.cache import CardCache
from pokemon_companion.cards_db.decklist_parser import load_deck
from pokemon_companion.cards_db.models import Card
from pokemon_companion.deck_loading import make_lookup
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import (
    EndTurn,
    PlayTrainer,
    UseAbility,
    UseAttack,
)
from pokemon_companion.engine.game_state import GameState, PlayerId

MAX_TURNS = 120
MAX_ACTIONS = 3000

_DECKS: dict[str, list[Card]] = {}


def _init_worker(decks: dict[str, list[Card]]) -> None:
    _DECKS.update(decks)


def _damaging(state: GameState, action: object) -> bool:
    """Ataque legal que causa dano (ignora utilitários como Burst Roar)."""
    if not isinstance(action, UseAttack) or action.target == ("mode", 0):
        return False
    active = state.state_of(rules.decision_player(state)).active
    return active is not None and bool(active.card.attacks[action.attack_index].damage)


def play_game(job: tuple[str, str, int, str, int]) -> dict[str, Any]:
    deck_a, deck_b, seed, level, first_index = job
    random.seed(seed)
    first = PlayerId.PLAYER if first_index == 0 else PlayerId.OPPONENT
    names = {PlayerId.PLAYER: deck_a, PlayerId.OPPONENT: deck_b}
    record: dict[str, Any] = {
        "player": deck_a,
        "opponent": deck_b,
        "seed": seed,
        "first": names[first],
        "winner": None,
        "end": "",
        "turns": 0,
        "passed_with_attack": {deck_a: 0, deck_b: 0},
        "attacks": {deck_a: 0, deck_b: 0},
        "unimplemented": [],
        "trainers": [],
        "abilities": [],
        "think": [],
        "error": None,
    }
    try:
        state = turn_manager.start_new_game(
            list(_DECKS[deck_a]), list(_DECKS[deck_b]), first_player=first
        )
        ais = {PlayerId.PLAYER: build_ai(level), PlayerId.OPPONENT: build_ai(level)}
        actions_done = 0
        messages: list[str] = []
        while (
            state.winner is None and state.turn_number <= MAX_TURNS and actions_done < MAX_ACTIONS
        ):
            legal = rules.legal_actions(state)
            decider = rules.decision_player(state)
            started = time.perf_counter()
            action = ais[decider].choose_action(state, legal)
            record["think"].append(time.perf_counter() - started)
            deck = names[decider]
            if isinstance(action, EndTurn) and any(_damaging(state, a) for a in legal):
                record["passed_with_attack"][deck] += 1
            if isinstance(action, UseAttack):
                record["attacks"][deck] += 1
            if isinstance(action, PlayTrainer):
                card = state.state_of(decider).hand[action.hand_index]
                record["trainers"].append(card.name)
            if isinstance(action, UseAbility):
                record["abilities"].append(action.ability_name)
            messages = rules.apply_action(state, action)
            record["unimplemented"].extend(m for m in messages if "não implementado" in m)
            actions_done += 1
        record["turns"] = state.turn_number
        record["sudden_death"] = state.sudden_death
        if state.winner is None:
            record["end"] = "limite de turnos"
        else:
            record["winner"] = names[state.winner]
            loser = state.state_of(state.winner.other)
            if any("não tem cartas" in m for m in messages):
                record["end"] = "deck-out"
            elif not loser.has_pokemon_in_play():
                record["end"] = "sem Pokémon"
            else:
                record["end"] = "prêmios"
        record["prizes_left"] = {
            deck_a: len(state.player.prizes),
            deck_b: len(state.opponent.prizes),
        }
    except Exception:  # noqa: BLE001 - o torneio registra e segue
        record["error"] = traceback.format_exc()
        record["end"] = "erro"
    return record


def load_all(folder: Path) -> dict[str, list[Card]]:
    decks: dict[str, list[Card]] = {}
    with CardCache() as cache:
        lookup = make_lookup()
        for path in sorted(folder.glob("*.txt")):
            cards, errors = load_deck(path, cache, lookup)
            if errors or len(cards) != 60:
                print(f"! {path.name}: {len(cards)} cartas, erros: {errors[:3]}")
            decks[path.stem] = cards
    return decks


def summarize(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    wins: Counter[str] = Counter()
    games: Counter[str] = Counter()
    first_wins = 0
    decided = 0
    for r in results:
        for deck in (r["player"], r["opponent"]):
            games[deck] += 1
        if r["winner"]:
            wins[r["winner"]] += 1
            decided += 1
            first_wins += r["winner"] == r["first"]
    lines.append(f"Partidas: {len(results)}  (decididas: {decided})")
    lines.append(f"Quem começa venceu: {first_wins / max(decided, 1):.0%}")
    lines.append(
        "Fim: " + ", ".join(f"{k}={v}" for k, v in Counter(r["end"] for r in results).most_common())
    )
    turns = [r["turns"] for r in results if r["end"] != "erro"]
    if turns:
        lines.append(
            f"Turnos: média {statistics.mean(turns):.1f}, mediana {statistics.median(turns)}, "
            f"mín {min(turns)}, máx {max(turns)}"
        )
    think = [t for r in results for t in r["think"]]
    if think:
        think.sort()
        lines.append(
            f"Decisão da IA: média {statistics.mean(think) * 1000:.0f} ms, "
            f"p99 {think[int(len(think) * 0.99)] * 1000:.0f} ms, máx {think[-1] * 1000:.0f} ms"
        )
    lines.append(f"Morte súbita: {sum(bool(r.get('sudden_death')) for r in results)}")
    lines.append("")
    lines.append("Taxa de vitória por deck:")
    passed: Counter[str] = Counter()
    attacks: Counter[str] = Counter()
    for r in results:
        passed.update(r["passed_with_attack"])
        attacks.update(r["attacks"])
    for deck, _ in sorted(games.items(), key=lambda kv: -wins[kv[0]] / kv[1]):
        lines.append(
            f"  {deck:34s} {wins[deck] / games[deck]:6.0%}  ({wins[deck]}/{games[deck]})"
            f"  ataques/partida {attacks[deck] / games[deck]:4.1f}"
            f"  passou-podendo-atacar {passed[deck]}"
        )
    unimplemented = Counter(m for r in results for m in r["unimplemented"])
    if unimplemented:
        lines.append("")
        lines.append(
            "Treinadores sem efeito jogados: "
            + ", ".join(f"{k} ×{v}" for k, v in unimplemented.most_common())
        )
    errors = [r for r in results if r["error"]]
    if errors:
        lines.append("")
        lines.append(f"ERROS ({len(errors)}):")
        by_msg: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in errors:
            by_msg[r["error"].strip().splitlines()[-1]].append(r)
        for msg, rs in by_msg.items():
            lines.append(
                f"  {len(rs)}× {msg}  (ex: {rs[0]['player']} vs {rs[0]['opponent']}, seed {rs[0]['seed']})"
            )
            lines.append("    " + rs[0]["error"].strip().splitlines()[-3].strip())
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("folder", type=Path)
    parser.add_argument("--games", type=int, default=2, help="partidas por par de decks")
    parser.add_argument("--level", default="hard", choices=["easy", "medium", "hard"])
    parser.add_argument("--out", type=Path, default=Path("data/tournament"))
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    decks = load_all(args.folder)
    jobs = [
        (a, b, args.seed + 1000 * pair + g, args.level, g % 2)
        for pair, (a, b) in enumerate(itertools.combinations(sorted(decks), 2))
        for g in range(args.games)
    ]
    print(f"{len(decks)} decks, {len(jobs)} partidas...")
    started = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(decks,)) as pool:
        results = list(pool.map(play_game, jobs, chunksize=2))
    print(f"concluído em {time.time() - started:.0f}s\n")

    args.out.mkdir(parents=True, exist_ok=True)
    slim = [{k: v for k, v in r.items() if k != "think"} for r in results]
    (args.out / "results.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1))
    summary = summarize(results)
    (args.out / "summary.txt").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
