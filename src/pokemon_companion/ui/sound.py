"""Toca os efeitos sonoros (QSoundEffect, baixa latência para WAV curto) e
a música de fundo (QMediaPlayer em loop).

Cada grupo (`hit_heavy`, `energy_fire`...) é um WAV em `ui/sounds/`; grupos
com variações (`card_draw_1..3`) sorteiam uma diferente da última, para o
mesmo som repetido não cansar. Volume, mudo e música vêm de `Settings`.
`sound_cues.cues_for` decide o que tocar; aqui só se toca.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication

from pokemon_companion.ui.anim import Animator
from pokemon_companion.ui.settings import Settings
from pokemon_companion.ui.sound_cues import Cue

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"
MUSIC_DIR = SOUNDS_DIR / "music"
#: instâncias por arquivo: o mesmo som pode se sobrepor (dois impactos)
VOICES = 2


class NullSounds:
    """Sem áudio (testes, ambiente sem saída de som)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def play(self, group: str) -> None:
        pass

    def play_cues(self, cues: list[Cue]) -> None:
        pass

    def play_music(self, track: str) -> None:
        pass

    def apply(self, settings: Settings) -> None:
        self.settings = settings


class SoundPlayer(NullSounds):
    def __init__(self, settings: Settings, folder: Path = SOUNDS_DIR) -> None:
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect

        super().__init__(settings)
        #: variações sorteadas com gerador próprio (o global é o da partida)
        self._random = random.Random()
        self._groups: dict[str, list[list[QSoundEffect]]] = {}
        self._last: dict[str, int] = {}
        for path in sorted(folder.glob("*.wav")):
            group = re.sub(r"_\d+$", "", path.stem)
            voices = []
            for _ in range(VOICES):
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(str(path)))
                voices.append(effect)
            self._groups.setdefault(group, []).append(voices)
        self._music = QMediaPlayer()
        self._music_output = QAudioOutput()
        self._music.setAudioOutput(self._music_output)
        self._music.setLoops(QMediaPlayer.Loops.Infinite)
        self._track = ""
        self.apply(settings)

    @property
    def groups(self) -> set[str]:
        return set(self._groups)

    def apply(self, settings: Settings) -> None:
        self.settings = settings
        for variants in self._groups.values():
            for voices in variants:
                for voice in voices:
                    voice.setVolume(settings.volume)
        self._music_output.setVolume(settings.music_volume)
        self._music_output.setMuted(settings.muted or not settings.music)

    def play(self, group: str) -> None:
        variants = self._groups.get(group)
        if self.settings.muted or not variants:
            return
        choices = [i for i in range(len(variants)) if i != self._last.get(group)] or [0]
        index = self._random.choice(choices)
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

    def play_music(self, track: str) -> None:
        """Troca a faixa em loop ("menu", "battle"); "" para."""
        if track == self._track:
            return
        self._track = track
        path = MUSIC_DIR / f"{track}.wav"
        if not track or not path.exists():
            self._music.stop()
            return
        self._music.setSource(QUrl.fromLocalFile(str(path)))
        self._music.play()


_PLAYER: NullSounds | None = None


def sound_player(settings: Settings) -> NullSounds:
    """O tocador do app (um só, compartilhado entre menu e partidas); mudo
    sem saída de áudio ou na plataforma offscreen."""
    global _PLAYER
    if _PLAYER is None:
        _PLAYER = _create(settings)
    _PLAYER.apply(settings)
    return _PLAYER


def _create(settings: Settings) -> NullSounds:
    if QGuiApplication.platformName() == "offscreen":
        return NullSounds(settings)
    try:
        from PyQt6.QtMultimedia import QMediaDevices
    except ImportError:
        return NullSounds(settings)
    if not QMediaDevices.audioOutputs():
        return NullSounds(settings)
    return SoundPlayer(settings)
