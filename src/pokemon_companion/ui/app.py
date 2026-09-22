"""Aplicação PyQt6: mesma lógica de jogo do CLI (`main.py`), com o board do
jogador ainda controlado por clique manual — a câmera só substitui isso na
Fase 4. Board da IA sempre vem do `GameState` virtual.

Interação estilo Hearthstone/TCG Pocket: clique num ataque pronto no seu
card ativo para atacar, clique num Pokémon do banco (quando destacado) para
recuar, clique numa carta na mão para jogá-la. A lista de ações continua
disponível como método completo/alternativo (e para desempatar quando o
clique é ambíguo, ex: em qual Pokémon anexar energia)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
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
from pokemon_companion.engine.actions import Action, Retreat, UseAttack
from pokemon_companion.engine.game_state import GameState, PlayerId
from pokemon_companion.engine.history import MatchRecorder
from pokemon_companion.presentation import describe_action
from pokemon_companion.ui.board_view import BoardView
from pokemon_companion.ui.theme import APP_STYLESHEET

AI_TURN_DELAY_MS = 400


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(
        "background-color: rgba(255, 255, 255, 0.25); max-height: 1px; border: none;"
    )
    return line


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
        central.setObjectName("rootBackground")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        title = QLabel("⚡ POKÉMON COMPANION")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.opponent_view = BoardView("Oponente (IA)", show_hand=False)
        self.player_view = BoardView("Você", show_hand=True)
        self.player_view.active_attack_clicked.connect(self._on_attack_clicked)
        self.player_view.bench_clicked.connect(self._on_bench_clicked)
        if self.player_view.hand_view is not None:
            self.player_view.hand_view.card_clicked.connect(self._on_hand_card_clicked)
        layout.addWidget(self.opponent_view)
        layout.addWidget(_divider())
        layout.addWidget(self.player_view)

        actions_title = QLabel("AÇÕES DISPONÍVEIS (lista completa)")
        actions_title.setObjectName("sectionTitle")
        layout.addWidget(actions_title)
        self.action_list = QListWidget()
        self.action_list.setObjectName("actionList")
        layout.addWidget(self.action_list)

        self.play_button = QPushButton("Jogar ação selecionada")
        self.play_button.setObjectName("playButton")
        self.play_button.clicked.connect(self.play_selected_action)
        layout.addWidget(self.play_button)

        log_title = QLabel("HISTÓRICO")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_view = QListWidget()
        self.log_view.setObjectName("logView")
        layout.addWidget(self.log_view)

        self.setCentralWidget(central)
        self.refresh()

    def log_message(self, message: str) -> None:
        self.log_view.addItem(message)
        self.log_view.scrollToBottom()

    def refresh(self) -> None:
        is_player_turn = (
            not rules.is_game_over(self._state) and self._state.active_player == PlayerId.PLAYER
        )
        self.opponent_view.update_state(self._state.opponent, interactive=False)
        self.player_view.update_state(self._state.player, interactive=is_player_turn)

        if rules.is_game_over(self._state):
            self.action_list.clear()
            self.play_button.setEnabled(False)
            self.player_view.set_bench_targetable(set())
            assert self._state.winner is not None
            self.log_message(f"Fim de jogo! Vencedor: {self._state.winner.value}")
            if self._recorder is not None and self._history_path is not None:
                self._recorder.save(self._history_path)
                self.log_message(f"Histórico salvo em {self._history_path}")
            return

        if is_player_turn:
            self._populate_actions()
            self.play_button.setEnabled(True)
            retreat_targets = {
                action.bench_index
                for action in self._current_actions
                if isinstance(action, Retreat)
            }
            self.player_view.set_bench_targetable(retreat_targets)
        else:
            self.action_list.clear()
            self.play_button.setEnabled(False)
            self.player_view.set_bench_targetable(set())
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

    def _on_attack_clicked(self, attack_index: int) -> None:
        if self._state.active_player != PlayerId.PLAYER:
            return
        matching = [
            action
            for action in self._current_actions
            if isinstance(action, UseAttack) and action.attack_index == attack_index
        ]
        if matching:
            self._apply(matching[0])

    def _on_bench_clicked(self, bench_index: int) -> None:
        if self._state.active_player != PlayerId.PLAYER:
            return
        matching = [
            action
            for action in self._current_actions
            if isinstance(action, Retreat) and action.bench_index == bench_index
        ]
        if matching:
            self._apply(matching[0])

    def _on_hand_card_clicked(self, hand_index: int) -> None:
        if self._state.active_player != PlayerId.PLAYER:
            return
        matching = [
            action
            for action in self._current_actions
            if getattr(action, "hand_index", None) == hand_index
        ]
        if len(matching) == 1:
            self._apply(matching[0])
        elif matching:
            # Ambíguo (ex: anexar energia no ativo vs num banco específico)
            # — pré-seleciona a primeira opção pro jogador escolher o alvo
            # usando "Jogar ação selecionada" logo abaixo.
            self.action_list.setCurrentRow(self._current_actions.index(matching[0]))

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
    app.setStyleSheet(APP_STYLESHEET)

    try:
        window = build_main_window(
            args.difficulty, args.player_deck, args.opponent_deck, args.record_history
        )
    except DeckLoadError as exc:
        QMessageBox.critical(None, "Erro ao carregar deck", str(exc))
        sys.exit(1)

    window.resize(560, 860)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
