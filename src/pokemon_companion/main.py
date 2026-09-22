"""CLI textual de demonstração: joga uma partida completa (jogador humano vs
IA) usando decks mockados ou decklists reais importadas, sem câmera nem UI
gráfica — cobre o critério de saída da Fase 1 (motor de regras + IA jogáveis
de ponta a ponta) e o importador de decklist da Fase 2."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from pokemon_companion.ai.opponent import AIPlayer, build_ai
from pokemon_companion.deck_loading import DeckLoadError, load_decks
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay
from pokemon_companion.engine.history import MatchRecorder
from pokemon_companion.presentation import describe_action


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


def _apply_and_record(
    state: GameState, action: Action, recorder: MatchRecorder | None
) -> list[str]:
    turn, actor = state.turn_number, state.active_player
    messages = rules.apply_action(state, action)
    if recorder is not None:
        recorder.record(turn, actor, action, messages)
    return messages


def human_turn(state: GameState, recorder: MatchRecorder | None) -> None:
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

    for message in _apply_and_record(state, chosen, recorder):
        print(f"  -> {message}")


def ai_turn(state: GameState, ai: AIPlayer, recorder: MatchRecorder | None) -> None:
    actions = rules.legal_actions(state)
    chosen = ai.choose_action(state, actions)
    print(f"IA escolheu: {describe_action(state, chosen)}")
    for message in _apply_and_record(state, chosen, recorder):
        print(f"  -> {message}")


def run_game(
    difficulty: str,
    player_deck_path: Path | None,
    opponent_deck_path: Path | None,
    history_path: Path | None = None,
) -> None:
    try:
        player_deck, opponent_deck, warnings = load_decks(player_deck_path, opponent_deck_path)
    except DeckLoadError as exc:
        print(str(exc))
        sys.exit(1)

    for warning in warnings:
        print(f"  ! {warning}")

    first = random.choice([PlayerId.PLAYER, PlayerId.OPPONENT])  # cara ou coroa
    state = turn_manager.start_new_game(player_deck, opponent_deck, first_player=first)
    print(f"Cara ou coroa: {'você' if first == PlayerId.PLAYER else 'a IA'} começa.")
    ai = build_ai(difficulty)
    recorder = MatchRecorder() if history_path else None
    print(f"Nova partida iniciada (dificuldade da IA: {difficulty}).")

    while not rules.is_game_over(state):
        if state.active_player == PlayerId.PLAYER:
            human_turn(state, recorder)
        else:
            ai_turn(state, ai, recorder)

    assert state.winner is not None
    print(f"\nFim de jogo! Vencedor: {state.winner.value}")

    if recorder is not None and history_path is not None:
        recorder.save(history_path)
        print(f"Histórico da partida salvo em {history_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pokemon-companion — partida de demonstração via CLI"
    )
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    parser.add_argument(
        "--player-deck",
        type=Path,
        default=None,
        help="Arquivo de decklist (formato Limitless/PTCGO) para o seu deck. "
        "Sem isso, usa um deck mockado de demonstração.",
    )
    parser.add_argument(
        "--opponent-deck",
        type=Path,
        default=None,
        help="Arquivo de decklist para o deck da IA. Sem isso, usa um deck mockado.",
    )
    parser.add_argument(
        "--record-history",
        type=Path,
        default=None,
        metavar="ARQUIVO.json",
        help="Salva o histórico de ações da partida em JSON ao final.",
    )
    args = parser.parse_args()

    try:
        run_game(args.difficulty, args.player_deck, args.opponent_deck, args.record_history)
    except (EOFError, KeyboardInterrupt):
        print("\nPartida interrompida.")
        sys.exit(1)


if __name__ == "__main__":
    main()
