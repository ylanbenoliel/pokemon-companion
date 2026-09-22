"""CLI textual de demonstração: joga uma partida completa (jogador humano vs
IA) usando decks mockados, sem câmera nem UI gráfica — cobre o critério de
saída da Fase 1 (motor de regras + IA jogáveis de ponta a ponta)."""

from __future__ import annotations

import argparse
import sys

from pokemon_companion.ai.heuristics_easy import EasyAI
from pokemon_companion.ai.heuristics_hard import HardAI
from pokemon_companion.ai.heuristics_medium import MediumAI
from pokemon_companion.ai.opponent import AIPlayer
from pokemon_companion.demo_data import build_demo_deck
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    Retreat,
    UseAttack,
)
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay


def build_ai(difficulty: str) -> AIPlayer:
    if difficulty == "easy":
        return EasyAI()
    if difficulty == "hard":
        return HardAI()
    return MediumAI()


def render_pokemon(label: str, mon: PokemonInPlay | None) -> str:
    if mon is None:
        return f"{label}: (vazio)"
    energies = ", ".join(mon.attached_energies) or "nenhuma"
    status = "-" if mon.status.name == "NONE" else mon.status.name
    return (
        f"{label}: {mon.card.name} HP {mon.current_hp}/{mon.card.hp} "
        f"| Energia: {energies} | Status: {status}"
    )


def render_state(state: GameState) -> str:
    lines = [f"--- Turno {state.turn_number} ({state.active_player.value}) ---"]
    lines.append(f"Você — prêmios restantes: {len(state.player.prizes)}")
    lines.append(render_pokemon("  Ativo", state.player.active))
    for i, mon in enumerate(state.player.bench):
        lines.append(render_pokemon(f"  Banco {i}", mon))
    lines.append(f"IA — prêmios restantes: {len(state.opponent.prizes)}")
    lines.append(render_pokemon("  Ativo", state.opponent.active))
    for i, mon in enumerate(state.opponent.bench):
        lines.append(render_pokemon(f"  Banco {i}", mon))
    lines.append("Mão: " + ", ".join(c.name for c in state.player.hand))
    return "\n".join(lines)


def describe_action(state: GameState, action: Action) -> str:
    player = state.state_of(state.active_player)
    if isinstance(action, PlayBasicToActive):
        return f"Jogar {player.hand[action.hand_index].name} como ativo"
    if isinstance(action, PlayBasicToBench):
        return f"Jogar {player.hand[action.hand_index].name} no banco"
    if isinstance(action, Evolve):
        target = "ativo" if action.target_is_active else f"banco {action.bench_index}"
        return f"Evoluir {target} para {player.hand[action.hand_index].name}"
    if isinstance(action, AttachEnergy):
        target = "ativo" if action.target_is_active else f"banco {action.bench_index}"
        return f"Anexar {player.hand[action.hand_index].name} no {target}"
    if isinstance(action, UseAttack):
        assert player.active is not None
        attack = player.active.card.attacks[action.attack_index]
        return f"Atacar com {attack.name} ({attack.damage} dano)"
    if isinstance(action, Retreat):
        return f"Recuar para {player.bench[action.bench_index].card.name}"
    if isinstance(action, EndTurn):
        return "Passar o turno"
    return str(action)


def human_turn(state: GameState) -> None:
    print(render_state(state))
    actions = rules.legal_actions(state)
    for i, action in enumerate(actions):
        print(f"  [{i}] {describe_action(state, action)}")

    chosen: Action | None = None
    while chosen is None:
        choice = input("Escolha uma ação: ").strip()
        if choice.isdigit() and 0 <= int(choice) < len(actions):
            chosen = actions[int(choice)]
        else:
            print("Entrada inválida.")

    for message in rules.apply_action(state, chosen):
        print(f"  -> {message}")


def ai_turn(state: GameState, ai: AIPlayer) -> None:
    actions = rules.legal_actions(state)
    chosen = ai.choose_action(state, actions)
    print(f"IA escolheu: {describe_action(state, chosen)}")
    for message in rules.apply_action(state, chosen):
        print(f"  -> {message}")


def run_game(difficulty: str) -> None:
    state = turn_manager.start_new_game(build_demo_deck(), build_demo_deck())
    ai = build_ai(difficulty)
    print(f"Nova partida iniciada (dificuldade da IA: {difficulty}).")

    while not rules.is_game_over(state):
        if state.active_player == PlayerId.PLAYER:
            human_turn(state)
        else:
            ai_turn(state, ai)

    assert state.winner is not None
    print(f"\nFim de jogo! Vencedor: {state.winner.value}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pokemon-companion — partida de demonstração via CLI"
    )
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    args = parser.parse_args()

    try:
        run_game(args.difficulty)
    except (EOFError, KeyboardInterrupt):
        print("\nPartida interrompida.")
        sys.exit(1)


if __name__ == "__main__":
    main()
