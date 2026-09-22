"""Aplicação PyQt6: mesma lógica de jogo do CLI (`main.py`), com o board do
jogador ainda controlado por clique manual — a câmera só substitui isso na
Fase 4. Board da IA sempre vem do `GameState` virtual."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.ai.opponent import AIPlayer, build_ai
from pokemon_companion.deck_loading import DeckLoadError, load_decks
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import Action
from pokemon_companion.engine.game_state import GameState, PlayerId
from pokemon_companion.engine.history import MatchRecorder
from pokemon_companion.presentation import describe_action
from pokemon_companion.ui.board_view import BoardView

AI_TURN_DELAY_MS = 400


class MainWindow(QMainWindow):
    def __init__(
        self,
        state: GameState,
        ai: AIPlayer,
        recorder: MatchRecorder | None = None,
        history_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._ai = ai
        self._recorder = recorder
        self._history_path = history_path
        self._current_actions: list[Action] = []

        self.setWindowTitle("pokemon-companion")

        central = QWidget()
        layout = QVBoxLayout(central)

        self.opponent_view = BoardView("IA", show_hand=False)
        self.player_view = BoardView("Você", show_hand=True)
        layout.addWidget(self.opponent_view)
        layout.addWidget(self.player_view)

        layout.addWidget(QLabel("Ações disponíveis:"))
        self.action_list = QListWidget()
        layout.addWidget(self.action_list)

        self.play_button = QPushButton("Jogar ação selecionada")
        self.play_button.clicked.connect(self.play_selected_action)
        layout.addWidget(self.play_button)

        layout.addWidget(QLabel("Histórico:"))
        self.log_view = QListWidget()
        layout.addWidget(self.log_view)

        self.setCentralWidget(central)
        self.refresh()

    def log_message(self, message: str) -> None:
        self.log_view.addItem(message)
        self.log_view.scrollToBottom()

    def refresh(self) -> None:
        self.opponent_view.update_state(self._state.opponent)
        self.player_view.update_state(self._state.player)

        if rules.is_game_over(self._state):
            self.action_list.clear()
            self.play_button.setEnabled(False)
            assert self._state.winner is not None
            self.log_message(f"Fim de jogo! Vencedor: {self._state.winner.value}")
            if self._recorder is not None and self._history_path is not None:
                self._recorder.save(self._history_path)
                self.log_message(f"Histórico salvo em {self._history_path}")
            return

        if self._state.active_player == PlayerId.PLAYER:
            self._populate_actions()
            self.play_button.setEnabled(True)
        else:
            self.action_list.clear()
            self.play_button.setEnabled(False)
            QTimer.singleShot(AI_TURN_DELAY_MS, self._play_ai_turn)

    def _populate_actions(self) -> None:
        self.action_list.clear()
        self._current_actions = rules.legal_actions(self._state)
        for action in self._current_actions:
            self.action_list.addItem(describe_action(self._state, action))
        if self._current_actions:
            self.action_list.setCurrentRow(0)

    def play_selected_action(self) -> None:
        row = self.action_list.currentRow()
        if not (0 <= row < len(self._current_actions)):
            return
        self._apply(self._current_actions[row])

    def _play_ai_turn(self) -> None:
        if rules.is_game_over(self._state):
            return
        actions = rules.legal_actions(self._state)
        action = self._ai.choose_action(self._state, actions)
        self.log_message(f"IA: {describe_action(self._state, action)}")
        self._apply(action)

    def _apply(self, action: Action) -> None:
        messages = rules.apply_action(self._state, action)
        if self._recorder is not None:
            self._recorder.record(self._state, action, messages)
        for message in messages:
            self.log_message(message)
        self.refresh()


def build_main_window(
    difficulty: str,
    player_deck_path: Path | None,
    opponent_deck_path: Path | None,
    history_path: Path | None = None,
) -> MainWindow:
    player_deck, opponent_deck, warnings = load_decks(player_deck_path, opponent_deck_path)
    state = turn_manager.start_new_game(player_deck, opponent_deck)
    ai = build_ai(difficulty)
    recorder = MatchRecorder() if history_path else None
    window = MainWindow(state, ai, recorder, history_path)
    for warning in warnings:
        window.log_message(f"! {warning}")
    return window


def main() -> None:
    parser = argparse.ArgumentParser(description="pokemon-companion — UI gráfica (PyQt6)")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    parser.add_argument("--player-deck", type=Path, default=None)
    parser.add_argument("--opponent-deck", type=Path, default=None)
    parser.add_argument("--record-history", type=Path, default=None, metavar="ARQUIVO.json")
    args = parser.parse_args()

    app = QApplication(sys.argv)

    try:
        window = build_main_window(
            args.difficulty, args.player_deck, args.opponent_deck, args.record_history
        )
    except DeckLoadError as exc:
        QMessageBox.critical(None, "Erro ao carregar deck", str(exc))
        sys.exit(1)

    window.resize(520, 700)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
