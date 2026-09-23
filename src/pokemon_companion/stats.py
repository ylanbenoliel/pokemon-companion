"""Histórico das suas partidas e estatísticas (taxa de vitória por deck,
contra cada deck, sequência atual).

Uma linha JSON por partida em `USER_DATA/matches.jsonl`: fácil de ler, de
apagar e de levar para outro computador.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pokemon_companion import paths


def matches_file() -> Path:
    return paths.USER_DATA / "matches.jsonl"


@dataclass(frozen=True)
class MatchRecord:
    date: str
    player_deck: str
    opponent_deck: str
    difficulty: str
    won: bool
    turns: int
    #: prêmios que você pegou / que o oponente pegou
    prizes_taken: int
    prizes_lost: int
    conceded: bool = False
    replay: str = ""


def record_match(record: MatchRecord, path: Path | None = None) -> None:
    path = path or matches_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_matches(path: Path | None = None) -> list[MatchRecord]:
    path = path or matches_file()
    if not path.exists():
        return []
    records = []
    known = set(MatchRecord.__dataclass_fields__)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
            records.append(MatchRecord(**{k: v for k, v in data.items() if k in known}))
        except (ValueError, TypeError):
            continue  # linha corrompida não derruba o histórico inteiro
    return records


@dataclass
class Tally:
    wins: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    def add(self, won: bool) -> None:
        if won:
            self.wins += 1
        else:
            self.losses += 1


@dataclass
class Summary:
    overall: Tally = field(default_factory=Tally)
    by_deck: dict[str, Tally] = field(default_factory=dict)
    by_opponent: dict[str, Tally] = field(default_factory=dict)
    by_difficulty: dict[str, Tally] = field(default_factory=dict)
    #: vitórias (positivo) ou derrotas (negativo) seguidas até agora
    streak: int = 0
    best_streak: int = 0
    average_turns: float = 0.0


def summarize(records: list[MatchRecord]) -> Summary:
    summary = Summary()
    by_deck: dict[str, Tally] = defaultdict(Tally)
    by_opponent: dict[str, Tally] = defaultdict(Tally)
    by_difficulty: dict[str, Tally] = defaultdict(Tally)
    run = 0
    for record in records:
        summary.overall.add(record.won)
        by_deck[record.player_deck].add(record.won)
        by_opponent[record.opponent_deck].add(record.won)
        by_difficulty[record.difficulty].add(record.won)
        if record.won:
            run = run + 1 if run > 0 else 1
            summary.best_streak = max(summary.best_streak, run)
        else:
            run = run - 1 if run < 0 else -1
    summary.streak = run
    summary.by_deck = dict(by_deck)
    summary.by_opponent = dict(by_opponent)
    summary.by_difficulty = dict(by_difficulty)
    if records:
        summary.average_turns = sum(r.turns for r in records) / len(records)
    return summary
