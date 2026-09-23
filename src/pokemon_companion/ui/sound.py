"""Toca os efeitos sonoros (QSoundEffect, baixa latência para WAV curto).

Cada grupo (`hit_heavy`, `energy_fire`...) é um WAV em `ui/sounds/`; grupos
com variações (`card_draw_1..3`) sorteiam uma diferente da última, para o
mesmo som repetido não cansar. Mudo e volume ficam salvos em QSettings.
`sound_cues.cues_for` decide o que tocar; aqui só se toca.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication

from pokemon_companion.ui.anim import Animator
from pokemon_companion.ui.sound_cues import Cue

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"
DEFAULT_VOLUME = 0.7
#: instâncias por arquivo: o mesmo som pode se sobrepor (dois impactos)
VOICES = 2


class NullSounds:
    """Sem áudio (testes, ambiente sem saída de som)."""

    muted = True
    volume = 0.0

    def play(self, group: str) -> None:
        pass

    def play_cues(self, cues: list[Cue]) -> None:
        pass

    def toggle_mute(self) -> bool:
        return True

    def set_volume(self, volume: float) -> None:
        pass


class SoundPlayer(NullSounds):
    def __init__(self, folder: Path = SOUNDS_DIR) -> None:
        from PyQt6.QtMultimedia import QSoundEffect

        self._settings = QSettings("pokemon-companion", "pokemon-companion")
        self.muted = self._settings.value("sound/muted", False, type=bool)
        self.volume = float(self._settings.value("sound/volume", DEFAULT_VOLUME, type=float))
        self._groups: dict[str, list[list[QSoundEffect]]] = {}
        self._last: dict[str, int] = {}
        for path in sorted(folder.glob("*.wav")):
            group = re.sub(r"_\d+$", "", path.stem)
            voices = []
            for _ in range(VOICES):
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setVolume(self.volume)
                voices.append(effect)
            self._groups.setdefault(group, []).append(voices)

    @property
    def groups(self) -> set[str]:
        return set(self._groups)

    def play(self, group: str) -> None:
        variants = self._groups.get(group)
        if self.muted or not variants:
            return
        choices = [i for i in range(len(variants)) if i != self._last.get(group)] or [0]
        index = random.choice(choices)
        self._last[group] = index
        voices = variants[index]
        voice = next((v for v in voices if not v.isPlaying()), voices[0])
        voice.play()

    def play_cues(self, cues: list[Cue]) -> None:
        """Toca cada grupo no seu atraso (acompanha a velocidade das animações)."""
        for delay, group in cues:
            wait = Animator.ms(delay)
            if wait <= 0:
                self.play(group)
            else:
                QTimer.singleShot(wait, lambda g=group: self.play(g))

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        self._settings.setValue("sound/muted", self.muted)
        return self.muted

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        self._settings.setValue("sound/volume", self.volume)
        for variants in self._groups.values():
            for voices in variants:
                for voice in voices:
                    voice.setVolume(self.volume)


def create_sound_player() -> NullSounds:
    """O tocador real; mudo sem saída de áudio ou na plataforma offscreen."""
    if QGuiApplication.platformName() == "offscreen":
        return NullSounds()
    try:
        from PyQt6.QtMultimedia import QMediaDevices
    except ImportError:
        return NullSounds()
    if not QMediaDevices.audioOutputs():
        return NullSounds()
    return SoundPlayer()
