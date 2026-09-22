"""Helpers de animação e a fila que encadeia as animações da partida.

Toda duração passa por `Animator.ms()`, que aplica um fator de velocidade
global: 1.0 no app, 0 nos testes (as animações terminam no próximo ciclo do
event loop, exercitando o mesmo código sem esperar).

`AnimationQueue` roda "passos" em sequência. Cada passo é uma função que
aplica seus efeitos colaterais (ex: aplicar a ação no motor e sincronizar o
tabuleiro) e devolve a animação a tocar — assim, uma investida pode tocar,
*depois* o dano ser aplicado, *depois* a barra de HP drenar.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    pyqtSignal,
)

Step = Callable[[], QAbstractAnimation | None]


class Animator:
    speed: float = 1.0

    @classmethod
    def ms(cls, value: float) -> int:
        return int(value * cls.speed)


def prop(
    target: QObject,
    name: bytes,
    end: Any,
    duration: float,
    start: Any = None,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
) -> QPropertyAnimation:
    animation = QPropertyAnimation(target, name)
    animation.setDuration(Animator.ms(duration))
    if start is not None:
        animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(easing)
    return animation


def seq(*animations: QAbstractAnimation | None) -> QSequentialAnimationGroup:
    group = QSequentialAnimationGroup()
    for animation in animations:
        if animation is not None:
            group.addAnimation(animation)
    return group


def par(*animations: QAbstractAnimation | None) -> QParallelAnimationGroup:
    group = QParallelAnimationGroup()
    for animation in animations:
        if animation is not None:
            group.addAnimation(animation)
    return group


def pause(duration: float) -> QPauseAnimation:
    return QPauseAnimation(Animator.ms(duration))


class AnimationQueue(QObject):
    """Executa passos um após o outro; `busy` fica verdadeiro enquanto houver
    algo tocando ou pendente (a UI usa isso para bloquear input)."""

    idle = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._steps: deque[Step] = deque()
        self._running: QAbstractAnimation | None = None
        self._active = False

    @property
    def busy(self) -> bool:
        return self._active

    def push(self, step: Step) -> None:
        self._steps.append(step)
        if not self._active:
            self._active = True
            self._next()

    def _next(self) -> None:
        while self._steps:
            step = self._steps.popleft()
            animation = step()
            if animation is not None and animation.totalDuration() != 0:
                self._running = animation
                animation.finished.connect(self._on_finished)
                animation.start()
                return
            if animation is not None:
                # Duração zero: aplica os valores finais sem esperar.
                animation.start()
                animation.setCurrentTime(animation.totalDuration())
                animation.stop()
        self._running = None
        self._active = False
        self.idle.emit()

    def _on_finished(self) -> None:
        self._running = None
        self._next()
