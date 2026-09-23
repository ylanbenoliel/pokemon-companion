"""Mede o compilador de texto (`engine/effects/text_effects.py`) contra o
pool legal do Standard: quantos ataques sem registro à mão compilam, quais
frases ainda não são reconhecidas (por frequência) e se os compilados rodam
sem erro num estado de jogo sintético montado com cartas reais do pool.

    uv run python tools/compile_report.py            # resumo + 40 frases
    uv run python tools/compile_report.py --top 150  # mais frases
"""

from __future__ import annotations

import argparse
import random
import re
import traceback
from collections import Counter

from pokemon_companion.cards_db import standard
from pokemon_companion.cards_db.models import Card
from pokemon_companion.engine.effects import attacks, text_effects
from pokemon_companion.engine.effects.core import Ctx
from pokemon_companion.engine.game_state import GameState, PlayerId, PlayerState, PokemonInPlay


def normalize(sentence: str) -> str:
    sentence = re.sub(r"[\[{]\w[\]}]", "{X}", sentence)
    return re.sub(r"\b\d+\b", "N", sentence)


def synthetic_state(card: Card, pool: list[Card], rng: random.Random) -> GameState:
    mons = [c for c in pool if c.is_pokemon]
    energies = ["Fire", "Water", "Grass", "Lightning", "Psychic", "Fighting", "Darkness", "Metal"]

    def mon(c: Card) -> PokemonInPlay:
        return PokemonInPlay(
            card=c,
            attached_energies=rng.sample(energies, rng.randint(0, 4)),
            damage_counters=rng.choice([0, 0, 30, 60]),
        )

    def player(active: Card) -> PlayerState:
        return PlayerState(
            deck=rng.sample(pool, 30),
            hand=rng.sample(pool, rng.randint(0, 7)),
            discard=rng.sample(pool, 10),
            prizes=rng.sample(pool, rng.randint(1, 6)),
            active=mon(active),
            bench=[mon(c) for c in rng.sample(mons, rng.randint(0, 5))],
        )

    return GameState(
        player=player(card), opponent=player(rng.choice(mons)), turn_number=rng.randint(3, 12)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--explain", type=int, default=0, help="mostra N programas compilados")
    args = parser.parse_args()
    pool = standard.load_pool()
    missing: dict[tuple[str, str], Card] = {}
    for card in pool:
        for attack in card.attacks if card.is_pokemon else []:
            if attack.text and attack.name not in attacks.ATTACKS:
                missing.setdefault((attack.name, attack.text), card)

    unknown: Counter[str] = Counter()
    compiled: list[tuple[Card, int]] = []
    for (_, text), card in missing.items():
        if text_effects.compile_text(text) is None:
            for sentence in text_effects.unknown_sentences(text):
                unknown[normalize(sentence)] += 1
        else:
            index = next(i for i, a in enumerate(card.attacks) if a.text == text)
            compiled.append((card, index))

    rng = random.Random(7)
    errors: Counter[str] = Counter()
    for card, index in compiled:
        attack = card.attacks[index]
        for _ in range(3):
            state = synthetic_state(card, pool, rng)
            ctx = Ctx(state, PlayerId.PLAYER, state.player.active)
            try:
                attacks.estimated_damage(state, PlayerId.PLAYER, state.player.active, attack)
                options = attacks.attack_options(ctx, attack)
                ctx.target = rng.choice(options)
                attacks.resolve_attack(ctx, attack)
            except Exception:  # noqa: BLE001 - relatório de falhas
                errors[f"{attack.name}: {traceback.format_exc().splitlines()[-1]}"] += 1

    total = len(missing)
    print(
        f"Ataques sem registro à mão: {total}; compilados: {len(compiled)} "
        f"({100 * len(compiled) // max(total, 1)}%)"
    )
    print(f"Execuções com erro: {sum(errors.values())}")
    for line, n in errors.most_common(20):
        print(f"  {n}× {line}")
    print(f"\nFrases não reconhecidas ({len(unknown)} formas):")
    for sentence, n in unknown.most_common(args.top):
        print(f"{n:4d} {sentence[:160]}")
    for card, index in random.Random(args.explain).sample(
        compiled, min(args.explain, len(compiled))
    ):
        attack = card.attacks[index]
        print(f"\n{attack.name} [{attack.damage}] — {attack.text}")
        for line in text_effects.explain(attack.text):
            print(f"    {line}")


if __name__ == "__main__":
    main()
