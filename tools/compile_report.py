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
from pokemon_companion.engine.effects import attacks, passive_text, text_effects
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


def exercise_passives(state: GameState, rng: random.Random) -> None:
    """Passa pelos pontos em que as regras consultam passivas."""
    from pokemon_companion.engine import rules
    from pokemon_companion.engine.effects import core, passives

    passives.refresh_hp_bonuses(state)
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        for mon in state.state_of(pid).all_pokemon_in_play():
            passives.retreat_cost(state, pid, mon)
    for pid in (PlayerId.PLAYER, PlayerId.OPPONENT):
        attacker = state.state_of(pid).active
        target = state.state_of(pid.other).active
        if attacker is not None and target is not None:
            ctx = Ctx(state, pid, attacker)
            core.deal_damage(ctx, pid.other, target, rng.choice([30, 120, 300]), is_active=True)
    rules.legal_actions(state)


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

    from pokemon_companion.engine.effects import trainers
    from pokemon_companion.engine.effects.cardinfo import trainer_kind

    trainer_cards = {
        c.name: c
        for c in pool
        if c.supertype.value == "Trainer"
        and c.rules
        and c.name not in trainers.TRAINERS
        and c.name not in trainers.STADIUMS
        and trainer_kind(c) in ("Item", "Supporter", "Stadium")
    }
    trainer_ok = 0
    for card in trainer_cards.values():
        stadium = trainer_kind(card) == "Stadium"
        spec = trainers.stadium_spec_for(card) if stadium else trainers.spec_for(card)
        if spec is None:
            text = " ".join(card.rules)
            for sentence in text_effects.unknown_sentences(text):
                if text_effects._requirement(sentence) is None:
                    unknown["[T] " + normalize(sentence)] += 1
            continue
        trainer_ok += 1
        for _ in range(3):
            state = synthetic_state(rng.choice([c for c in pool if c.is_pokemon]), pool, rng)
            ctx = Ctx(state, PlayerId.PLAYER)
            try:
                if spec.can_play(ctx):
                    ctx.target = rng.choice(spec.options(ctx) or [None]) if spec.options else None
                    spec.fn(ctx)
            except Exception:  # noqa: BLE001 - relatório de falhas
                errors[f"{card.name}: {traceback.format_exc().splitlines()[-1]}"] += 1
    print(
        f"Treinadores/Estádios sem registro à mão: {len(trainer_cards)}; compilados: {trainer_ok}"
    )

    from pokemon_companion.engine.effects import abilities

    ability_cards = {
        (ab.name, ab.text): card
        for card in pool
        if card.is_pokemon
        for ab in card.abilities
        if ab.name not in abilities.ABILITIES and ab.text
    }
    ability_ok = 0
    for (name, text), card in ability_cards.items():
        spec = text_effects.compiled_ability(text)
        if spec is None and passive_text.compile_passive(text):
            ability_ok += 1
            for _ in range(3):
                state = synthetic_state(card, pool, rng)
                state.opponent.bench.append(PokemonInPlay(card=card))
                try:
                    exercise_passives(state, rng)
                except Exception:  # noqa: BLE001 - relatório de falhas
                    errors[f"[P] {name}: {traceback.format_exc().splitlines()[-1]}"] += 1
            continue
        if spec is None:
            parts = text_effects._ability_parts(text)
            effect = parts.effect if parts else text
            label = "[H] " if parts else "[H passiva] "
            for sentence in text_effects.unknown_sentences(effect) or [effect[:120]]:
                unknown[label + normalize(sentence)] += 1
            continue
        ability_ok += 1
        for _ in range(3):
            state = synthetic_state(card, pool, rng)
            ctx = Ctx(state, PlayerId.PLAYER, state.player.active)
            try:
                if spec.can_use(ctx):
                    options = spec.options(ctx) if spec.options else [None]
                    ctx.target = rng.choice(options or [None])
                    spec.fn(ctx)
            except Exception:  # noqa: BLE001 - relatório de falhas
                errors[f"[H] {name}: {traceback.format_exc().splitlines()[-1]}"] += 1
    print(f"Habilidades sem registro à mão: {len(ability_cards)}; compiladas: {ability_ok}")

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
