"""Aplicação PyQt6: tabuleiro em `QGraphicsView` com interação de jogo de
cartas digital (Hearthstone / Pokémon TCG Pocket).

- Arraste uma carta da mão até um alvo destacado (pulsando) para jogá-la:
  básicos no banco, energias e evoluções sobre o Pokémon.
- Clique numa carta com um único destino possível para jogá-la direto; com
  vários destinos, os alvos pulsam e você clica no escolhido.
- Botões de ataque ao lado do seu Pokémon ativo; "Recuar" à esquerda dele;
  "Fim do turno" integrado ao tabuleiro (dourado quando não há mais jogadas).
- Passe o mouse sobre qualquer Pokémon para ver ataques, fraqueza e recuo.
- Treinadores: solte no tabuleiro (ou sobre o Pokémon alvo, para Ferramentas
  e cartas como Boss's Orders/Switch); opções extras aparecem num painel.
- Habilidades: Pokémon com o selo "HAB." pode usar uma — clique nele.
- Estádio em jogo fica à direita; brilha quando o efeito pode ser usado.
- Setup: escolha o Ativo e o Banco e toque em "PRONTO"; após um nocaute, os
  seus Pokémon do banco pulsam para você escolher o novo Ativo.

`BattleController` é a ponte entre input, motor de regras e animações: nada
aqui decide regras — tudo passa por `rules.legal_actions`/`apply_action`.

Modo espectador (`--spectate`): uma segunda IA controla o lado de baixo e o
jogo corre sozinho, com as mesmas animações — útil para testar decks.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QAbstractAnimation, QObject, QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPainter, QResizeEvent, QShortcut, QShowEvent
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsView,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
)

from pokemon_companion.ai.opponent import AIPlayer, build_ai
from pokemon_companion.cards_db.models import Card
from pokemon_companion.deck_loading import DeckLoadError, load_decks
from pokemon_companion.engine import rules, turn_manager
from pokemon_companion.engine.actions import (
    Action,
    AttachEnergy,
    EndSetup,
    EndTurn,
    Evolve,
    PlayBasicToActive,
    PlayBasicToBench,
    PlayTrainer,
    PromoteActive,
    Retreat,
    UseAbility,
    UseAttack,
    UseStadium,
)
from pokemon_companion.engine.effects import core
from pokemon_companion.engine.effects.cardinfo import pokemon_type
from pokemon_companion.engine.game_state import GameState, PlayerId, PokemonInPlay
from pokemon_companion.engine.history import MatchRecorder
from pokemon_companion.ui.anim import AnimationQueue, Animator, par
from pokemon_companion.ui.art import ArtProvider
from pokemon_companion.ui.battle_scene import BattleScene, Target
from pokemon_companion.ui.deck_menu import DeckMenu, menu_size_hint, read_entry
from pokemon_companion.ui.items import HandCard, PokemonToken
from pokemon_companion.ui.sound import NullSounds, create_sound_player
from pokemon_companion.ui.sound_cues import attack_cue, cues_for, snapshot
from pokemon_companion.ui.theme import FELT_DEEP, primary_type, ui_font

AI_THINK_MS = 550
DIFFICULTY_LABELS = {"easy": "IA fácil", "medium": "IA média", "hard": "IA difícil"}


class BattleController(QObject):
    def __init__(
        self,
        scene: BattleScene,
        state_factory: Callable[[], GameState],
        ai_factory: Callable[[], AIPlayer],
        history_path: Path | None = None,
        player_ai_factory: Callable[[], AIPlayer] | None = None,
        sounds: NullSounds | None = None,
    ) -> None:
        super().__init__()
        self.scene = scene
        self.sounds = sounds or NullSounds()
        self._state_factory = state_factory
        self._ai_factory = ai_factory
        self._player_ai_factory = player_ai_factory
        self.player_ai: AIPlayer | None = None
        self._history_path = history_path
        self.queue = AnimationQueue()
        self.queue.idle.connect(self._on_idle)
        #: alvos destacados à espera de clique/soltura → ações possíveis
        self._pending: dict[Target, list[Action]] | None = None
        self._retreat_mode = False
        self._choice_actions: list[Action] = []
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
        scene.end_turn_clicked.connect(self._on_end_turn_clicked)
        scene.stadium_clicked.connect(lambda: self._perform_if_legal(UseStadium()))
        scene.choice_made.connect(self._on_choice_made)
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
        self.player_ai = self._player_ai_factory() if self._player_ai_factory else None
        self.recorder = MatchRecorder() if self._history_path else None
        self._game_over_shown = False
        self._cancel_modes()
        self.scene.reset()
        self.scene.sync(self.state, animate=False)
        starter = self.scene.name_of(self.state.active_player)
        self.scene.show_toast(f"Cara ou coroa: {starter} começa")
        if PlayerId.PLAYER in self.state.pending_setup:
            self.scene.show_toast(
                "Monte seu time: arraste um Básico para o Ativo e outros ao Banco"
            )
        self._push_turn_banner()

    @property
    def busy(self) -> bool:
        return self.queue.busy

    @property
    def spectating(self) -> bool:
        return self._player_ai_factory is not None

    @property
    def is_player_turn(self) -> bool:
        """Vez do humano decidir (turno dele, setup ou escolha do novo Ativo).
        Sempre falso no modo espectador."""
        return (
            not self.spectating
            and not rules.is_game_over(self.state)
            and rules.decision_player(self.state) == PlayerId.PLAYER
        )

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
                self._show_game_over()
            return
        self._refresh_controls()
        if self.is_player_turn and self.state.pending_promotion == PlayerId.PLAYER:
            self._offer_promotion()
        if not self.is_player_turn:
            self._ai_timer.start(Animator.ms(AI_THINK_MS))

    def _show_game_over(self) -> None:
        winner = self.state.winner
        if self.spectating and winner is not None:
            self.sounds.play("victory")
            self.scene.show_game_over(f"{self.scene.name_of(winner)} vence!", won=True)
        else:
            won = winner == PlayerId.PLAYER
            self.sounds.play("victory" if won else "defeat")
            self.scene.show_game_over("Vitória!" if won else "Derrota", won=won)

    def _push_turn_banner(self) -> None:
        if rules.is_game_over(self.state) or self.state.pending_setup:
            self.queue.push(lambda: None)
            return
        bottom = self.state.active_player == PlayerId.PLAYER
        color = self.scene.side_tint(self.state.active_player).darker(150 if bottom else 175)
        if self.spectating:
            title = f"Vez de {self.scene.name_of(self.state.active_player)}"
        else:
            title = "Seu turno" if bottom else "Turno da IA"

        def show() -> QAbstractAnimation:
            self.sounds.play("turn_start")
            return self.scene.banner(title, color)

        self.queue.push(show)

    # ------------------------------------------------------------------
    # controles
    def _end_turn_mode(self, legal: list[Action]) -> str:
        if rules.is_game_over(self.state):
            return "over"
        if self.spectating:
            return "watch"
        if not self.is_player_turn:
            return "ai"
        if self.state.pending_setup:
            return "setup" if any(isinstance(a, EndSetup) for a in legal) else "choose"
        if self.state.pending_promotion is not None:
            return "choose"
        if all(isinstance(action, EndTurn) for action in legal):
            return "done"
        return "play"

    def _refresh_controls(self) -> None:
        legal = [] if self.busy else self.legal_actions()
        my_turn = self.is_player_turn and not self.busy
        player = self.state.player
        ability_tokens = frozenset(
            id(mon)
            for a in legal
            if isinstance(a, UseAbility) and (mon := core.mon_at(player, a.position)) is not None
        )
        self.scene.set_player_controls(
            state=self.state,
            my_turn=my_turn,
            ready_attacks={a.attack_index for a in legal if isinstance(a, UseAttack)},
            can_retreat=any(isinstance(a, Retreat) for a in legal),
            retreat_mode=self._retreat_mode,
            playable_hand={i for a in legal if (i := getattr(a, "hand_index", None)) is not None},
            end_turn_mode=self._end_turn_mode(legal),
            ability_tokens=ability_tokens,
            stadium_usable=any(isinstance(a, UseStadium) for a in legal),
        )

    def _lock_controls(self) -> None:
        self.scene.set_player_controls(
            state=self.state,
            my_turn=False,
            ready_attacks=set(),
            can_retreat=False,
            retreat_mode=False,
            playable_hand=set(),
            end_turn_mode=(
                "watch" if self.spectating else ("ai" if not self.is_player_turn else "play")
            ),
        )

    def _cancel_modes(self) -> None:
        self._pending = None
        self._retreat_mode = False
        self._choice_actions = []
        self.scene.close_choice()
        self.scene.clear_targets()
        if not self.busy:
            self._refresh_controls()
            if self.is_player_turn and self.state.pending_promotion == PlayerId.PLAYER:
                self._offer_promotion()

    # -- mapeamento ação → alvo na tela ---------------------------------
    def _token_target(self, side: PlayerId, position: int) -> Target | None:
        mon = core.mon_at(self.state.state_of(side), position)
        return ("token", id(mon)) if mon is not None else None

    def _scene_target(self, action: Action) -> Target | None:
        """Onde na tela o jogador aponta para escolher esta ação."""
        player = self.state.player
        if isinstance(action, PlayBasicToBench):
            return ("zone", "bench")
        if isinstance(action, PlayBasicToActive):
            return ("zone", "active")
        if isinstance(action, AttachEnergy | Evolve):
            mon = (
                player.active if action.target_is_active else player.bench[action.bench_index or 0]
            )
            return ("token", id(mon))
        target = getattr(action, "target", None)
        if target and target[0] in ("own", "opp"):
            side = PlayerId.PLAYER if target[0] == "own" else PlayerId.OPPONENT
            return self._token_target(side, cast(int, target[1]))
        if target and target[0] == "candy":
            return self._token_target(PlayerId.PLAYER, cast(int, target[2]))
        if isinstance(action, PlayTrainer):
            return ("zone", "play")
        return None

    def _group(self, actions: Sequence[Action]) -> dict[Target, list[Action]]:
        grouped: dict[Target, list[Action]] = {}
        for action in actions:
            target = self._scene_target(action)
            if target is not None:
                grouped.setdefault(target, []).append(action)
        return grouped

    def targets_for_hand(self, hand_index: int) -> dict[Target, list[Action]]:
        return self._group(
            [a for a in self.legal_actions() if getattr(a, "hand_index", None) == hand_index]
        )

    def _label(self, action: Action) -> str:
        if isinstance(action, UseAbility):
            detail = rules.describe_target(self.state, PlayerId.PLAYER, action)
            return f"{action.ability_name}" + (f" → {detail}" if detail else "")
        if isinstance(action, UseAttack) and self.state.player.active is not None:
            name = self.state.player.active.card.attacks[action.attack_index].name
            detail = rules.describe_target(self.state, PlayerId.PLAYER, action)
            return f"{name}" + (f": {detail}" if detail else "")
        detail = rules.describe_target(self.state, PlayerId.PLAYER, action)
        return detail or "Jogar"

    def _resolve(
        self, actions: Sequence[Action], title: str, card_source: QPointF | None = None
    ) -> None:
        """Uma ação → executa; várias → painel de escolha."""
        if len(actions) == 1:
            self.perform(actions[0], card_source=card_source)
            return
        self._choice_actions = list(actions)
        self.scene.show_choice(title, [self._label(a) for a in actions])

    def _offer(self, actions: Sequence[Action], title: str, toast: str) -> None:
        """Ações que diferem só no alvo: se todas apontam para Pokémon,
        destaca-os para clique; senão abre o painel de escolha."""
        if not actions:
            return
        grouped = self._group(actions)
        on_tokens = all(target[0] == "token" for target in grouped) and sum(
            len(group) for group in grouped.values()
        ) == len(actions)
        if len(actions) == 1 and getattr(actions[0], "target", None) is None:
            self.perform(actions[0])
        elif on_tokens and grouped:
            self._pending = grouped
            self.scene.highlight_targets(list(grouped))
            self.scene.show_toast(toast)
        else:
            self._resolve(actions, title)

    def _offer_promotion(self) -> None:
        if self._pending is not None or self.scene.choice_panel is not None:
            return
        actions = [a for a in self.legal_actions() if isinstance(a, PromoteActive)]
        bench = self.state.player.bench
        self._pending = {("token", id(bench[a.bench_index])): [a] for a in actions}
        self.scene.highlight_targets(list(self._pending))
        self.scene.show_toast("Escolha seu novo Pokémon Ativo")

    # ------------------------------------------------------------------
    # input
    def _on_drag_started(self, item: HandCard) -> None:
        if self.busy or not self.is_player_turn:
            return
        self._retreat_mode = False
        self.sounds.play("ui_click")
        self._pending = self.targets_for_hand(item.hand_index)
        self.scene.highlight_targets(list(self._pending))

    def _on_dropped(self, item: HandCard, scene_pos: QPointF) -> None:
        targets = self._pending or {}
        target = self.scene.target_at(scene_pos)
        self._pending = None
        self.scene.clear_targets()
        actions = targets.get(target) if target is not None else None
        if not actions:
            item.return_to_fan()
            if targets and scene_pos.y() < 780:
                self.scene.show_toast("Solte a carta sobre um alvo destacado")
            return
        self._resolve(actions, f"{item.card.name}: escolha", card_source=scene_pos)

    def _on_hand_clicked(self, item: HandCard) -> None:
        if self.busy or not self.is_player_turn:
            return
        targets = self.targets_for_hand(item.hand_index)
        if len(targets) == 1:
            (actions,) = targets.values()
            self._resolve(actions, f"{item.card.name}: escolha", card_source=item.scenePos())
        elif targets:
            self._retreat_mode = False
            self._pending = targets
            self.scene.highlight_targets(list(targets))
            self.scene.show_toast("Escolha um alvo destacado")

    def _on_token_clicked(self, token: PokemonToken) -> None:
        if self.busy or not self.is_player_turn:
            return
        target = self.scene.target_of_token(token)
        if self._pending is not None and target is not None and target in self._pending:
            actions = self._pending[target]
            self._pending = None
            self.scene.clear_targets()
            self._resolve(actions, "Escolha", card_source=token.scenePos())
            return
        if self._retreat_mode:
            for index, mon in enumerate(self.state.player.bench):
                if ("token", id(mon)) == target:
                    self._perform_if_legal(Retreat(bench_index=index))
                    return
            return
        self._offer_abilities(token)

    def _offer_abilities(self, token: PokemonToken) -> None:
        player = self.state.player
        position = next(
            (
                p
                for p in core.positions(player)
                if self.scene.token_for(core.mon_at(player, p)) is token
            ),
            None,
        )
        if position is None:
            return
        actions = [
            a for a in self.legal_actions() if isinstance(a, UseAbility) and a.position == position
        ]
        if not actions:
            return
        names = sorted({a.ability_name for a in actions})
        if len(names) == 1 and all(a.target is None for a in actions):
            # confirmação: usar Habilidade sem querer é fácil com um clique
            self._choice_actions = list[Action](actions)
            self.scene.show_choice(f"Usar {names[0]}?", [f"Usar {names[0]}"])
            return
        if len(names) == 1:
            self._offer(actions, f"{names[0]}: escolha o alvo", f"{names[0]}: escolha o alvo")
            return
        self._resolve(actions, "Qual Habilidade?")

    def _on_choice_made(self, index: int) -> None:
        self.sounds.play("ui_click")
        actions, self._choice_actions = self._choice_actions, []
        if 0 <= index < len(actions):
            self._perform_if_legal(actions[index])
        else:
            self._cancel_modes()

    def _on_attack_clicked(self, attack_index: int) -> None:
        if self.busy or not self.is_player_turn:
            return
        actions = [
            a
            for a in self.legal_actions()
            if isinstance(a, UseAttack) and a.attack_index == attack_index
        ]
        name = (
            self.state.player.active.card.attacks[attack_index].name
            if self.state.player.active
            else ""
        )
        self._offer(actions, f"{name}: escolha", f"{name}: escolha o alvo")

    def _on_end_turn_clicked(self) -> None:
        if self.state.pending_setup:
            self._perform_if_legal(EndSetup())
        else:
            self._perform_if_legal(EndTurn())

    def _on_retreat_clicked(self) -> None:
        if self.busy or not self.is_player_turn:
            return
        retreats = [a for a in self.legal_actions() if isinstance(a, Retreat)]
        if not retreats:
            return
        if len(retreats) == 1:
            self.perform(retreats[0])
            return
        self._pending = None
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
        self._pending = None
        self._retreat_mode = False
        self._choice_actions = []
        self.scene.close_choice()
        self.scene.clear_targets()
        # Trava os controles *antes* de enfileirar: a fila pode terminar de
        # forma síncrona (velocidade 0) e reabilitar os controles no idle.
        self._lock_controls()
        actor = rules.decision_player(self.state)
        actor_state = self.state.state_of(actor)
        hand_index = getattr(action, "hand_index", None)
        if (
            card_source is None
            and actor == PlayerId.PLAYER
            and hand_index is not None
            and 0 <= hand_index < len(self.scene.hand_items)
        ):
            card_source = self.scene.hand_items[hand_index].scenePos()
        if isinstance(action, UseAttack) and actor_state.active is not None:
            attacker = actor_state.active
            defender = self.state.state_of(actor.other).active
            cue = attack_cue(pokemon_type(attacker.card))

            def lunge() -> QAbstractAnimation | None:
                self.sounds.play_cues([(100, cue)])
                return self.scene.lunge(attacker, defender)

            self.queue.push(lunge)
            self.queue.push(lambda: self._attack_step(action, attacker, defender))
        else:
            self.queue.push(lambda: self._apply_step(action, card_source))

    def _apply_step(self, action: Action, card_source: QPointF | None) -> QAbstractAnimation | None:
        before = self.state.active_player
        actor = rules.decision_player(self.state)
        turn = self.state.turn_number
        was_setup = bool(self.state.pending_setup)
        before_sound = snapshot(self.state, actor, action)
        # o som do ataque já tocou na investida (ver `perform`)
        announced = isinstance(action, UseAttack) and self.state.state_of(actor).active is not None
        messages = rules.apply_action(self.state, action)
        self.sounds.play_cues(
            cues_for(action, before_sound, self.state, actor, messages, announced)
        )
        if self.recorder is not None:
            self.recorder.record(turn, actor, action, messages)
        for message in messages:
            self.scene.show_toast(message)
        animation = self.scene.sync(
            self.state,
            animate=True,
            card_source=card_source,
            opponent_source=self.scene.opponent_hand_position(),
        )
        if self.state.active_player != before or (was_setup and not self.state.pending_setup):
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
        bottom = rules.decision_player(self.state) == PlayerId.PLAYER
        ai = self.player_ai if bottom else self.ai
        actions = rules.legal_actions(self.state)
        if actions and ai is not None:
            self.perform(ai.choose_action(self.state, actions))


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
        player_ai_factory: Callable[[], AIPlayer] | None = None,
        player_label: str = "Você",
    ) -> None:
        super().__init__()
        self.setWindowTitle("Pokémon Companion")
        self.scene = BattleScene(art or ArtProvider(), opponent_label, player_label)
        if player_ai_factory is not None:
            self.scene.set_names(display_name(player_label), display_name(opponent_label))
        self.view = BattleView(self.scene)
        self.setCentralWidget(self.view)
        self.sounds = create_sound_player()
        self.controller = BattleController(
            self.scene, state_factory, ai_factory, history_path, player_ai_factory, self.sounds
        )
        mute = QShortcut(QKeySequence(Qt.Key.Key_M), self)
        mute.activated.connect(self._toggle_mute)
        for key, step in (
            (Qt.Key.Key_Plus, 0.1),
            (Qt.Key.Key_Equal, 0.1),
            (Qt.Key.Key_Minus, -0.1),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda s=step: self._change_volume(s))

    def _toggle_mute(self) -> None:
        muted = self.sounds.toggle_mute()
        self.scene.show_toast("Som desligado (M)" if muted else "Som ligado (M)")

    def _change_volume(self, step: float) -> None:
        self.sounds.set_volume(self.sounds.volume + step)
        self.scene.show_toast(f"Volume {round(self.sounds.volume * 100)}%")

    def log_message(self, message: str) -> None:
        self.scene.show_toast(message)


class DeckLoader(QThread):
    """Importa as duas decklists fora da thread da interface (pode ir à rede
    na primeira vez que um deck é usado)."""

    finished_loading = pyqtSignal(object, object, list, str)

    def __init__(self, player_deck: Path, opponent_deck: Path) -> None:
        super().__init__()
        self._paths = (player_deck, opponent_deck)

    def run(self) -> None:
        try:
            player, opponent, warnings = load_decks(*self._paths)
        except (DeckLoadError, OSError) as exc:
            self.finished_loading.emit(None, None, [], str(exc))
            return
        self.finished_loading.emit(player, opponent, warnings, "")


class LauncherWindow(QMainWindow):
    """Janela do app: tela de seleção de decks e, depois, o tabuleiro
    (Esc volta ao menu)."""

    def __init__(self, art: ArtProvider | None = None, history_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Pokémon Companion")
        self._art = art or ArtProvider()
        self._history_path = history_path
        self._loader: DeckLoader | None = None
        self.battle: MainWindow | None = None

        self.setStyleSheet(f"QMainWindow {{ background: {FELT_DEEP.name()}; }}")
        self.stack = QStackedWidget()
        self.menu = DeckMenu(art=self._art)
        self.menu.start_requested.connect(self._load_and_start)
        self.stack.addWidget(self.menu)
        self.setCentralWidget(self.stack)
        self.resize(menu_size_hint())

        back = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        back.activated.connect(self.show_menu)

    def show_menu(self) -> None:
        self.stack.setCurrentWidget(self.menu)
        self.menu.play_button.setEnabled(True)
        self.menu.status.setText("")

    def _load_and_start(self, player_deck: Path, opponent_deck: Path, difficulty: str) -> None:
        self._difficulty = difficulty
        self._deck_paths = (player_deck, opponent_deck)
        self._loader = DeckLoader(player_deck, opponent_deck)
        self._loader.finished_loading.connect(self._on_decks_loaded)
        self._loader.start()

    def _on_decks_loaded(
        self,
        player: list[Card] | None,
        opponent: list[Card] | None,
        warnings: list[str],
        error: str,
    ) -> None:
        if error or player is None or opponent is None:
            QMessageBox.critical(self, "Erro ao carregar deck", error or "Deck vazio.")
            self.show_menu()
            return
        opponent_path = self._deck_paths[1]
        window = MainWindow(
            state_factory=lambda: turn_manager.start_new_game(
                list(player),
                list(opponent),
                first_player=coin_flip_first_player(),
                manual=frozenset({PlayerId.PLAYER}),
            ),
            ai_factory=lambda: build_ai(self._difficulty),
            opponent_label=read_entry(opponent_path).title,
            art=self._art,
            history_path=self._history_path,
            player_label="Você",
        )
        window.scene.set_names("Você", read_entry(opponent_path).title)
        for warning in warnings:
            window.log_message(f"! {warning}")
        if self.battle is not None:
            self.stack.removeWidget(self.battle)
            self.battle.deleteLater()
        self.battle = window
        self.stack.addWidget(window)
        self.stack.setCurrentWidget(window)
        self.resize(1280, 900)


def display_name(label: str) -> str:
    """ "NS ZOROARK EX" → "Ns Zoroark ex" (o sufixo "ex" é minúsculo nas cartas)."""
    return label.title().replace(" Ex", " ex")


def coin_flip_first_player() -> PlayerId:
    """Cara ou coroa decide quem começa (livro de regras, setup passo 2)."""
    return random.choice([PlayerId.PLAYER, PlayerId.OPPONENT])


def deck_label(path: Path | None, fallback: str) -> str:
    """Nome curto para a tela a partir do arquivo: "ns_zoroark_ex" → "NS ZOROARK EX"."""
    return path.stem.replace("_", " ").upper() if path is not None else fallback


def build_main_window(
    difficulty: str,
    player_deck_path: Path | None,
    opponent_deck_path: Path | None,
    history_path: Path | None = None,
    art: ArtProvider | None = None,
    player_difficulty: str | None = None,
) -> MainWindow:
    """`player_difficulty` definido = modo espectador (IA também no lado de baixo)."""
    player_deck, opponent_deck, warnings = load_decks(player_deck_path, opponent_deck_path)
    spectate = player_difficulty is not None
    window = MainWindow(
        state_factory=lambda: turn_manager.start_new_game(
            list(player_deck),
            list(opponent_deck),
            first_player=coin_flip_first_player(),
            manual=frozenset() if spectate else frozenset({PlayerId.PLAYER}),
        ),
        ai_factory=lambda: build_ai(difficulty),
        opponent_label=(
            deck_label(opponent_deck_path, "IA 2")
            if spectate
            else DIFFICULTY_LABELS.get(difficulty, "IA")
        ),
        art=art,
        history_path=history_path,
        player_ai_factory=(lambda: build_ai(player_difficulty)) if player_difficulty else None,
        player_label=deck_label(player_deck_path, "IA 1") if spectate else "Você",
    )
    for warning in warnings:
        window.log_message(f"! {warning}")
    return window


def main() -> None:
    parser = argparse.ArgumentParser(description="pokemon-companion — tabuleiro gráfico (PyQt6)")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="medium")
    parser.add_argument(
        "--menu",
        action="store_true",
        help="Abre a tela de seleção de decks (padrão quando nenhum deck é informado).",
    )
    parser.add_argument("--player-deck", type=Path, default=None)
    parser.add_argument("--opponent-deck", type=Path, default=None)
    parser.add_argument("--record-history", type=Path, default=None, metavar="ARQUIVO.json")
    parser.add_argument(
        "--spectate",
        action="store_true",
        help="IA contra IA: assista a partida (o lado de baixo usa --player-difficulty).",
    )
    parser.add_argument("--player-difficulty", choices=["easy", "medium", "hard"], default="hard")
    parser.add_argument(
        "--speed", type=float, default=1.0, help="Velocidade das animações (2 = 2x mais rápido)."
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setFont(ui_font(10))
    Animator.speed = 1.0 / max(args.speed, 0.1)

    if args.menu or (args.player_deck is None and args.opponent_deck is None and not args.spectate):
        launcher = LauncherWindow(history_path=args.record_history)
        launcher.show()
        sys.exit(app.exec())

    try:
        window = build_main_window(
            args.difficulty,
            args.player_deck,
            args.opponent_deck,
            args.record_history,
            player_difficulty=args.player_difficulty if args.spectate else None,
        )
    except DeckLoadError as exc:
        QMessageBox.critical(None, "Erro ao carregar deck", str(exc))
        sys.exit(1)

    window.resize(1280, 900)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
