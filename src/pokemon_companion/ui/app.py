"""Aplicação PyQt6: tabuleiro em `QGraphicsView` com interação de jogo de
cartas digital (Hearthstone / Pokémon TCG Pocket).

- Arraste uma carta da mão até um alvo destacado (pulsando) para jogá-la:
  básicos no banco, energias e evoluções sobre o Pokémon.
- Clique numa carta com um único destino possível para jogá-la direto; com
  vários destinos, os alvos pulsam e você clica no escolhido.
- Botões de ataque ao lado do seu Pokémon ativo; "Recuar" à esquerda dele;
  "Fim do turno" integrado ao tabuleiro (dourado quando não há mais jogadas).
- Passe o mouse sobre qualquer Pokémon para ver ataques, fraqueza e recuo.

`BattleController` é a ponte entre input, motor de regras e animações: nada
aqui decide regras — tudo passa por `rules.legal_actions`/`apply_action`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QAbstractAnimation, QObject, QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import QApplication, QGraphicsView, QMainWindow, QMessageBox

from pokemon_companion.ai.opponent import AIPlayer, build_ai
from pokemon_companion.deck_loading import DeckLoadError, load_decks
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
from pokemon_companion.engine.history import MatchRecorder
from pokemon_companion.ui.anim import AnimationQueue, Animator, par
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.battle_scene import BattleScene, Target
from pokemon_companion.ui.items import HandCard, PokemonToken
from pokemon_companion.ui.theme import primary_type, ui_font

AI_THINK_MS = 550
DIFFICULTY_LABELS = {"easy": "IA · FÁCIL", "medium": "IA · MÉDIO", "hard": "IA · DIFÍCIL"}


class BattleController(QObject):
    def __init__(
        self,
        scene: BattleScene,
        state_factory: Callable[[], GameState],
        ai_factory: Callable[[], AIPlayer],
        history_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.scene = scene
        self._state_factory = state_factory
        self._ai_factory = ai_factory
        self._history_path = history_path
        self.queue = AnimationQueue()
        self.queue.idle.connect(self._on_idle)
        self._pending_hand: dict[Target, Action] | None = None
        self._retreat_mode = False
        self._game_over_shown = False
        self._ai_timer = QTimer(self)
        self._ai_timer.setSingleShot(True)
        self._ai_timer.timeout.connect(self._ai_step)

        scene.hand_drag_started.connect(self._on_drag_started)
        scene.hand_card_dropped.connect(self._on_dropped)
        scene.hand_card_clicked.connect(self._on_hand_clicked)
        scene.token_clicked.connect(self._on_token_clicked)
        scene.attack_clicked.connect(self._on_attack_clicked)
        scene.retreat_clicked.connect(self._on_retreat_clicked)
        scene.end_turn_clicked.connect(lambda: self._perform_if_legal(EndTurn()))
        scene.restart_clicked.connect(self.new_game)
        scene.background_clicked.connect(self._cancel_modes)

        self.state: GameState
        self.ai: AIPlayer
        self.recorder: MatchRecorder | None = None
        self.new_game()

    # ------------------------------------------------------------------
    # ciclo de vida
    def new_game(self) -> None:
        self._ai_timer.stop()
        self.state = self._state_factory()
        self.ai = self._ai_factory()
        self.recorder = MatchRecorder() if self._history_path else None
        self._game_over_shown = False
        self._cancel_modes()
        self.scene.reset()
        self.scene.sync(self.state, animate=False)
        self._push_turn_banner()

    @property
    def busy(self) -> bool:
        return self.queue.busy

    @property
    def is_player_turn(self) -> bool:
        return not rules.is_game_over(self.state) and self.state.active_player == PlayerId.PLAYER

    def legal_actions(self) -> list[Action]:
        return rules.legal_actions(self.state) if self.is_player_turn else []

    def _on_idle(self) -> None:
        self.scene.clear_temp_items()
        if rules.is_game_over(self.state):
            self._refresh_controls()
            if not self._game_over_shown:
                self._game_over_shown = True
                if self.recorder is not None and self._history_path is not None:
                    self.recorder.save(self._history_path)
                self.scene.show_game_over(self.state.winner == PlayerId.PLAYER)
            return
        self._refresh_controls()
        if not self.is_player_turn:
            self._ai_timer.start(Animator.ms(AI_THINK_MS))

    def _push_turn_banner(self) -> None:
        if rules.is_game_over(self.state):
            self.queue.push(lambda: None)
            return
        if self.is_player_turn:
            self.queue.push(lambda: self.scene.banner("SEU TURNO", QColor("#1f6fe0")))
        else:
            self.queue.push(lambda: self.scene.banner("TURNO DA IA", QColor("#c0392b")))

    # ------------------------------------------------------------------
    # controles
    def _refresh_controls(self) -> None:
        legal = [] if self.busy else self.legal_actions()
        my_turn = self.is_player_turn and not self.busy
        if rules.is_game_over(self.state):
            mode = "over"
        elif not self.is_player_turn:
            mode = "ai"
        elif all(isinstance(action, EndTurn) for action in legal):
            mode = "done"
        else:
            mode = "play"
        self.scene.set_player_controls(
            state=self.state,
            my_turn=my_turn,
            ready_attacks={a.attack_index for a in legal if isinstance(a, UseAttack)},
            can_retreat=any(isinstance(a, Retreat) for a in legal),
            retreat_mode=self._retreat_mode,
            playable_hand={i for a in legal if (i := getattr(a, "hand_index", None)) is not None},
            end_turn_mode=mode,
        )

    def _lock_controls(self) -> None:
        self.scene.set_player_controls(
            state=self.state,
            my_turn=False,
            ready_attacks=set(),
            can_retreat=False,
            retreat_mode=False,
            playable_hand=set(),
            end_turn_mode="ai" if not self.is_player_turn else "play",
        )

    def _cancel_modes(self) -> None:
        self._pending_hand = None
        self._retreat_mode = False
        self.scene.clear_targets()
        if not self.busy:
            self._refresh_controls()

    def targets_for_hand(self, hand_index: int) -> dict[Target, Action]:
        targets: dict[Target, Action] = {}
        player = self.state.player
        for action in self.legal_actions():
            if getattr(action, "hand_index", None) != hand_index:
                continue
            if isinstance(action, PlayBasicToBench):
                targets[("zone", "bench")] = action
            elif isinstance(action, PlayBasicToActive):
                targets[("zone", "active")] = action
            elif isinstance(action, AttachEnergy | Evolve):
                mon = (
                    player.active
                    if action.target_is_active
                    else player.bench[action.bench_index or 0]
                )
                targets[("token", id(mon))] = action
        return targets

    # ------------------------------------------------------------------
    # input
    def _on_drag_started(self, item: HandCard) -> None:
        if self.busy or not self.is_player_turn:
            return
        self._retreat_mode = False
        self._pending_hand = self.targets_for_hand(item.hand_index)
        self.scene.highlight_targets(list(self._pending_hand))

    def _on_dropped(self, item: HandCard, scene_pos: QPointF) -> None:
        targets = self._pending_hand or {}
        target = self.scene.target_at(scene_pos)
        self._pending_hand = None
        self.scene.clear_targets()
        action = targets.get(target) if target is not None else None
        if action is None:
            item.return_to_fan()
            if targets and scene_pos.y() < 780:
                self.scene.show_toast("Solte a carta sobre um alvo destacado")
            return
        self.perform(action, card_source=scene_pos)

    def _on_hand_clicked(self, item: HandCard) -> None:
        if self.busy or not self.is_player_turn:
            return
        targets = self.targets_for_hand(item.hand_index)
        if len(targets) == 1:
            self.perform(next(iter(targets.values())), card_source=item.scenePos())
        elif targets:
            self._retreat_mode = False
            self._pending_hand = targets
            self.scene.highlight_targets(list(targets))
            self.scene.show_toast("Escolha um Pokémon destacado")

    def _on_token_clicked(self, token: PokemonToken) -> None:
        if self.busy or not self.is_player_turn:
            return
        target = self.scene.target_of_token(token)
        if target is None:
            return
        if self._pending_hand is not None and target in self._pending_hand:
            action = self._pending_hand[target]
            self.perform(action, card_source=token.scenePos())
        elif self._retreat_mode:
            for index, mon in enumerate(self.state.player.bench):
                if ("token", id(mon)) == target:
                    self._perform_if_legal(Retreat(bench_index=index))
                    return

    def _on_attack_clicked(self, attack_index: int) -> None:
        self._perform_if_legal(UseAttack(attack_index=attack_index))

    def _on_retreat_clicked(self) -> None:
        if self.busy or not self.is_player_turn:
            return
        retreats = [a for a in self.legal_actions() if isinstance(a, Retreat)]
        if not retreats:
            return
        if len(retreats) == 1:
            self.perform(retreats[0])
            return
        self._pending_hand = None
        self._retreat_mode = not self._retreat_mode
        if self._retreat_mode:
            bench = self.state.player.bench
            self.scene.highlight_targets([("token", id(bench[a.bench_index])) for a in retreats])
            self.scene.show_toast("Escolha quem vai para a posição ativa")
        else:
            self.scene.clear_targets()
        self._refresh_controls()

    def _perform_if_legal(self, action: Action) -> None:
        if self.busy or not self.is_player_turn:
            return
        if action in self.legal_actions():
            self.perform(action)

    # ------------------------------------------------------------------
    # execução + animação
    def perform(self, action: Action, card_source: QPointF | None = None) -> None:
        self._ai_timer.stop()
        self._pending_hand = None
        self._retreat_mode = False
        self.scene.clear_targets()
        # Trava os controles *antes* de enfileirar: a fila pode terminar de
        # forma síncrona (velocidade 0) e reabilitar os controles no idle.
        self._lock_controls()
        actor = self.state.active_player
        actor_state = self.state.state_of(actor)
        if isinstance(action, UseAttack) and actor_state.active is not None:
            attacker = actor_state.active
            defender = self.state.state_of(actor.other).active
            self.queue.push(lambda: self.scene.lunge(attacker, defender))
            self.queue.push(lambda: self._attack_step(action, attacker, defender))
        else:
            self.queue.push(lambda: self._apply_step(action, card_source))

    def _apply_step(self, action: Action, card_source: QPointF | None) -> QAbstractAnimation | None:
        before = self.state.active_player
        messages = rules.apply_action(self.state, action)
        if self.recorder is not None:
            self.recorder.record(self.state, action, messages)
        for message in messages:
            self.scene.show_toast(message)
        animation = self.scene.sync(
            self.state,
            animate=True,
            card_source=card_source,
            opponent_source=self.scene.opponent_hand_position(),
        )
        if self.state.active_player != before:
            self._push_turn_banner()
        return animation

    def _attack_step(
        self, action: UseAttack, attacker: PokemonInPlay, defender: PokemonInPlay | None
    ) -> QAbstractAnimation | None:
        defender_token = self.scene.token_for(defender)
        hp_before = defender.current_hp if defender is not None else 0
        sync_animation = self._apply_step(action, None)
        hit = defender is not None and (
            defender.current_hp < hp_before or self.scene.token_for(defender) is None
        )
        if defender_token is None or not hit:
            return sync_animation
        return par(
            sync_animation, self.scene.impact(defender_token, primary_type(attacker.card.types))
        )

    def _ai_step(self) -> None:
        if self.busy or rules.is_game_over(self.state) or self.is_player_turn:
            return
        actions = rules.legal_actions(self.state)
        if actions:
            self.perform(self.ai.choose_action(self.state, actions))


class BattleView(QGraphicsView):
    def __init__(self, scene: BattleScene) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setMinimumSize(900, 630)

    def _fit(self) -> None:
        scene = self.scene()
        if scene is not None:
            self.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        self._fit()


class MainWindow(QMainWindow):
    def __init__(
        self,
        state_factory: Callable[[], GameState],
        ai_factory: Callable[[], AIPlayer],
        opponent_label: str = "IA",
        art: ArtProvider | None = None,
        history_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Pokémon Companion")
        self.scene = BattleScene(art or ArtProvider(), opponent_label)
        self.view = BattleView(self.scene)
        self.setCentralWidget(self.view)
        self.controller = BattleController(self.scene, state_factory, ai_factory, history_path)

    def log_message(self, message: str) -> None:
        self.scene.show_toast(message)


def build_main_window(
    difficulty: str,
    player_deck_path: Path | None,
    opponent_deck_path: Path | None,
    history_path: Path | None = None,
    art: ArtProvider | None = None,
) -> MainWindow:
    player_deck, opponent_deck, warnings = load_decks(player_deck_path, opponent_deck_path)
    window = MainWindow(
        state_factory=lambda: turn_manager.start_new_game(list(player_deck), list(opponent_deck)),
        ai_factory=lambda: build_ai(difficulty),
        opponent_label=DIFFICULTY_LABELS.get(difficulty, "IA"),
        art=art,
        history_path=history_path,
    )
    for warning in warnings:
        window.log_message(f"! {warning}")
    return window


def main() -> None:
    parser = argparse.ArgumentParser(description="pokemon-companion — tabuleiro gráfico (PyQt6)")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    parser.add_argument("--player-deck", type=Path, default=None)
    parser.add_argument("--opponent-deck", type=Path, default=None)
    parser.add_argument("--record-history", type=Path, default=None, metavar="ARQUIVO.json")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setFont(ui_font(10))

    try:
        window = build_main_window(
            args.difficulty, args.player_deck, args.opponent_deck, args.record_history
        )
    except DeckLoadError as exc:
        QMessageBox.critical(None, "Erro ao carregar deck", str(exc))
        sys.exit(1)

    window.resize(1280, 900)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
