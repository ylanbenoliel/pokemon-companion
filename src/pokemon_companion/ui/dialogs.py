"""Configurações e menu de pausa.

As configurações valem na hora (cada mudança emite `changed` e é salva), sem
botão "Aplicar": quem mexe no volume ouve o volume novo.
"""

from __future__ import annotations

import dataclasses

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.ui.settings import (
    DIFFICULTIES,
    SPEEDS,
    Settings,
    reset_hints,
    save_settings,
)
from pokemon_companion.ui.theme import ui_font
from pokemon_companion.ui.widgets import (
    hint_label,
    make_button,
    section_label,
    style_dialog,
    title_label,
)


def _percent_slider(value: float) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setSingleStep(5)
    slider.setPageStep(10)
    slider.setValue(round(value * 100))
    slider.setMinimumWidth(220)
    return slider


class SettingsDialog(QDialog):
    """Som, música, partida e acessibilidade."""

    changed = pyqtSignal(object)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = dataclasses.replace(settings)
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(520)
        style_dialog(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(title_label("Configurações"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        row = 0

        def label(text: str) -> QLabel:
            widget = QLabel(text)
            widget.setFont(ui_font(10.5))
            return widget

        def section(text: str) -> None:
            nonlocal row
            grid.addWidget(section_label(text), row, 0, 1, 2)
            row += 1

        section("Som")
        self.muted = QCheckBox("Sem som")
        self.muted.setChecked(self.settings.muted)
        self.muted.toggled.connect(lambda v: self._set(muted=v))
        grid.addWidget(self.muted, row, 0, 1, 2)
        row += 1
        grid.addWidget(label("Efeitos"), row, 0)
        self.volume = _percent_slider(self.settings.volume)
        self.volume.valueChanged.connect(lambda v: self._set(volume=v / 100))
        grid.addWidget(self.volume, row, 1)
        row += 1
        self.music = QCheckBox("Música de fundo")
        self.music.setChecked(self.settings.music)
        self.music.toggled.connect(lambda v: self._set(music=v))
        grid.addWidget(self.music, row, 0, 1, 2)
        row += 1
        grid.addWidget(label("Música"), row, 0)
        self.music_volume = _percent_slider(self.settings.music_volume)
        self.music_volume.valueChanged.connect(lambda v: self._set(music_volume=v / 100))
        grid.addWidget(self.music_volume, row, 1)
        row += 1

        section("Partida")
        grid.addWidget(label("Dificuldade padrão"), row, 0)
        self.difficulty = QComboBox()
        for key, text in DIFFICULTIES:
            self.difficulty.addItem(text, key)
        keys = [key for key, _ in DIFFICULTIES]
        current = self.settings.difficulty
        self.difficulty.setCurrentIndex(keys.index(current) if current in keys else 1)
        self.difficulty.currentIndexChanged.connect(
            lambda i: self._set(difficulty=self.difficulty.itemData(i))
        )
        grid.addWidget(self.difficulty, row, 1)
        row += 1
        grid.addWidget(label("Velocidade das animações"), row, 0)
        self.speed = QComboBox()
        for text, value in SPEEDS:
            self.speed.addItem(text, value)
        speeds = [value for _, value in SPEEDS]
        closest = min(range(len(speeds)), key=lambda i: abs(speeds[i] - self.settings.speed))
        self.speed.setCurrentIndex(closest)
        self.speed.currentIndexChanged.connect(lambda i: self._set(speed=self.speed.itemData(i)))
        grid.addWidget(self.speed, row, 1)
        row += 1
        self.hints = QCheckBox("Dicas para iniciantes durante a partida")
        self.hints.setChecked(self.settings.hints)
        self.hints.toggled.connect(lambda v: self._set(hints=v))
        grid.addWidget(self.hints, row, 0, 1, 2)
        row += 1

        section("Tela e acessibilidade")
        self.fullscreen = QCheckBox("Tela cheia")
        self.fullscreen.setChecked(self.settings.fullscreen)
        self.fullscreen.toggled.connect(lambda v: self._set(fullscreen=v))
        grid.addWidget(self.fullscreen, row, 0, 1, 2)
        row += 1
        self.reduce_motion = QCheckBox("Reduzir movimento e clarões")
        self.reduce_motion.setChecked(self.settings.reduce_motion)
        self.reduce_motion.toggled.connect(lambda v: self._set(reduce_motion=v))
        grid.addWidget(self.reduce_motion, row, 0, 1, 2)
        row += 1
        grid.addWidget(
            hint_label(
                "Tira o tremor da tela nos golpes e suaviza os clarões de impacto e "
                "evolução — para quem tem sensibilidade à luz ou enjoa com movimento."
            ),
            row,
            0,
            1,
            2,
        )
        row += 1
        layout.addLayout(grid)
        layout.addSpacing(10)

        buttons = QHBoxLayout()
        self.reset_hints_button = make_button("Mostrar as dicas de novo")
        self.reset_hints_button.clicked.connect(self._reset_hints)
        buttons.addWidget(self.reset_hints_button)
        buttons.addStretch(1)
        close = make_button("Fechar", primary=True)
        close.clicked.connect(self.accept)
        close.setDefault(True)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _set(self, **values: object) -> None:
        self.settings = dataclasses.replace(self.settings, **values)  # type: ignore[arg-type]
        save_settings(self.settings)
        self.changed.emit(self.settings)

    def _reset_hints(self) -> None:
        reset_hints()
        self.reset_hints_button.setText("Dicas restauradas")
        self.reset_hints_button.setEnabled(False)


class PauseMenu(QDialog):
    """Esc durante a partida: continuar, regras, configurações ou desistir."""

    CONTINUE, HELP, SETTINGS, CONCEDE, LEAVE = range(5)

    def __init__(self, game_over: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = self.CONTINUE
        self.setWindowTitle("Partida pausada")
        self.setMinimumWidth(340)
        style_dialog(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        layout.addWidget(title_label("Fim de partida" if game_over else "Pausa"))
        options: list[tuple[str, int, bool]] = [
            ("Continuar" if not game_over else "Ver o tabuleiro", self.CONTINUE, True),
            ("Como jogar", self.HELP, False),
            ("Configurações", self.SETTINGS, False),
        ]
        if game_over:
            options.append(("Voltar ao menu", self.LEAVE, False))
        else:
            options.append(("Desistir da partida", self.CONCEDE, False))
        self.buttons: dict[int, QPushButton] = {}
        for text, code, primary in options:
            button = make_button(text, primary=primary)
            button.clicked.connect(lambda _=False, c=code: self._pick(c))
            layout.addWidget(button)
            self.buttons[code] = button
        if not game_over:
            layout.addWidget(hint_label("Desistir conta como derrota nas estatísticas."))
        self.buttons[self.CONTINUE].setDefault(True)
        self.buttons[self.CONTINUE].setFocus()

    def _pick(self, code: int) -> None:
        self.choice = code
        self.accept()
