"""Gera as músicas de fundo em loop (menu e partida) por síntese.

    uv run python tools/make_music.py            # gera as duas
    uv run python tools/make_music.py --play     # gera e toca (macOS)

As faixas são loops sem emenda: tudo é escrito num buffer circular (a
cauda de uma nota ou do reverb que passa do fim volta para o começo), então
o último compasso encaixa no primeiro. Saem em 22,05 kHz mono (música de
fundo não precisa de mais, e cada faixa fica em ~1 MB). Mesmas primitivas
de `make_sounds.py`; determinístico.
"""

from __future__ import annotations

import argparse
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
from make_sounds import (
    OUT,
    SR,
    Rng,
    Signal,
    bell,
    env,
    highpass,
    lowpass,
    mix,
    noise,
    osc,
    reverb,
    thud,
)

MUSIC_DIR = OUT / "music"
OUT_RATE = SR // 2
#: loudness de referência (dB RMS): baixa, a música fica atrás dos efeitos
MUSIC_DB = -24.0

A4 = 440.0


def hz(midi: int) -> float:
    return A4 * 2 ** ((midi - 69) / 12)


class Loop:
    """Buffer circular de `bars` compassos de 4 tempos."""

    def __init__(self, bpm: float, bars: int) -> None:
        self.beat = 60.0 / bpm
        self.bars = bars
        self.length = int(round(SR * self.beat * 4 * bars))
        self.buffer = np.zeros(self.length)

    def at(self, bar: int, beat: float) -> int:
        return int(round(SR * self.beat * (bar * 4 + beat)))

    def place(self, signal: Signal, bar: int, beat: float, gain: float = 1.0) -> None:
        start = self.at(bar, beat) % self.length
        remaining = signal * gain
        while len(remaining):
            room = min(len(remaining), self.length - start)
            self.buffer[start : start + room] += remaining[:room]
            remaining = remaining[room:]
            start = 0

    def beats(self, count: float) -> float:
        return self.beat * count

    def wet(self, amount: float, tail: float = 1.2, size: float = 1.3) -> Signal:
        """Reverb circular: processa duas voltas e fica com a segunda."""
        twice = np.concatenate([self.buffer, self.buffer])
        wet = reverb(twice, mix=amount, tail=tail, size=size)
        out = wet[self.length : 2 * self.length].copy()
        overflow = wet[2 * self.length :]
        out[: len(overflow)] += overflow
        return out


# instrumentos --------------------------------------------------------------------


def pad(freqs: list[float], dur: float, bright: float = 1400) -> Signal:
    t_env = env(dur, attack=min(0.6, dur / 3), decay=dur * 1.2)
    voices = sum(osc(f * detune, dur, "saw") for f in freqs for detune in (0.997, 1.003))
    return lowpass(voices / (2 * len(freqs)), bright) * t_env


def keys(freq: float, dur: float) -> Signal:
    """Piano elétrico: sino suave com um pouco de corpo."""
    body = 0.25 * osc(freq, dur) * env(dur, 0.004, dur * 0.4)
    return mix(0.7 * bell(freq, dur, decay=dur * 0.5, bright=0.35), body)


def pluck(freq: float, dur: float) -> Signal:
    return lowpass(osc(freq, dur, "square"), 2600) * env(dur, 0.002, dur * 0.35)


def bass(freq: float, dur: float) -> Signal:
    return (osc(freq, dur) + 0.35 * osc(freq * 2, dur, "tri")) * env(dur, 0.005, dur * 0.8)


def kick(soft: bool = False) -> Signal:
    d = 0.35
    return thud(d, 110 if soft else 140, 42, 0.09 if soft else 0.12)


def snare(rng: Rng) -> Signal:
    d = 0.22
    body = 0.4 * osc(190, d) * env(d, 0.001, 0.04)
    return body + lowpass(noise(d, rng), 5000) * env(d, 0.001, 0.06)


def hat(rng: Rng, open_: bool = False) -> Signal:
    d = 0.18 if open_ else 0.05
    return (noise(d, rng) - lowpass(noise(d, rng), 7000)) * env(d, 0.0005, d / 3)


# faixas --------------------------------------------------------------------------


def menu_track(rng: Rng) -> Signal:
    """Calma, para escolher deck: ré maior, 88 bpm, 8 compassos."""
    loop = Loop(bpm=88, bars=8)
    # Dmaj7 – Bm7 – Gmaj7 – A6, duas vezes
    chords = [
        [50, 54, 57, 61],
        [47, 50, 54, 57],
        [43, 47, 50, 54],
        [45, 49, 52, 54],
    ]
    for bar in range(loop.bars):
        chord = chords[bar % 4]
        loop.place(pad([hz(n + 12) for n in chord], loop.beats(4.4)), bar, 0, 0.4)
        loop.place(bass(hz(chord[0] - 12), loop.beats(1.8)), bar, 0, 0.2)
        loop.place(bass(hz(chord[0] - 12), loop.beats(0.9)), bar, 2.5, 0.13)
        # arpejo de piano elétrico em colcheias, subindo e descendo
        pattern = [0, 1, 2, 3, 2, 1, 2, 3] if bar % 2 == 0 else [3, 2, 1, 2, 0, 2, 3, 1]
        for step, index in enumerate(pattern):
            if rng.random() < 0.12:
                continue  # respiros, para não soar mecânico
            note = chord[index] + 24
            loop.place(keys(hz(note), loop.beats(1.2)), bar, step * 0.5, 0.34 * rng.uniform(0.8, 1))
        loop.place(kick(soft=True), bar, 0, 0.13)
        loop.place(kick(soft=True), bar, 2.5, 0.08)
        for step in range(8):
            loop.place(hat(rng), bar, step * 0.5 + 0.02, 0.12 if step % 2 else 0.08)
    return loop.wet(amount=0.28)


def battle_track(rng: Rng) -> Signal:
    """Partida: lá menor, 126 bpm, 16 compassos com uma melodia na segunda metade."""
    loop = Loop(bpm=126, bars=16)
    # Am – F – C – G
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]
    melody = {  # (compasso, tempo): (nota, duração em tempos) — só nos compassos 8–15
        (8, 0): (76, 1.5), (8, 1.5): (74, 0.5), (8, 2): (72, 2),
        (9, 0): (72, 1), (9, 1): (69, 1), (9, 2): (72, 1), (9, 3): (74, 1),
        (10, 0): (76, 1.5), (10, 1.5): (79, 0.5), (10, 2): (76, 2),
        (11, 0): (74, 3), (11, 3): (71, 1),
        (12, 0): (72, 1.5), (12, 1.5): (74, 0.5), (12, 2): (76, 2),
        (13, 0): (77, 1), (13, 1): (76, 1), (13, 2): (72, 2),
        (14, 0): (72, 1), (14, 1): (76, 1), (14, 2): (79, 2),
        (15, 0): (79, 2), (15, 2): (74, 2),
    }  # fmt: skip
    for bar in range(loop.bars):
        chord = chords[bar % 4]
        root = chord[0] - 24
        loop.place(pad([hz(n) for n in chord], loop.beats(4.2), bright=1800), bar, 0, 0.32)
        for step in range(8):  # baixo em colcheias, com a oitava no contratempo
            note = root + (12 if step % 4 == 3 else 0)
            loop.place(bass(hz(note), loop.beats(0.45)), bar, step * 0.5, 0.2)
        arp = [0, 1, 2, 1, 2, 1, 0, 1, 2, 1, 2, 1, 0, 1, 2, 1]
        for step, index in enumerate(arp):  # arpejo em semicolcheias
            loop.place(pluck(hz(chord[index] + 12), loop.beats(0.3)), bar, step * 0.25, 0.15)
        for beat in (0, 1, 2, 3):
            loop.place(kick(), bar, beat, 0.26 if beat in (0, 2) else 0.18)
        for beat in (1, 3):
            loop.place(snare(rng), bar, beat, 0.3)
        for step in range(8):
            loop.place(hat(rng, open_=step == 7), bar, step * 0.5 + 0.25, 0.2)
        if bar % 4 == 3:  # virada no fim de cada frase
            for step in (3.25, 3.5, 3.75):
                loop.place(snare(rng), bar, step, 0.2)
    for (bar, beat), (note, length) in melody.items():
        lead = mix(
            keys(hz(note), loop.beats(length) + 0.3), 0.4 * pluck(hz(note), loop.beats(length))
        )
        loop.place(lead, bar, beat, 0.4)
    return loop.wet(amount=0.2, tail=0.9)


TRACKS: dict[str, Callable[[Rng], Signal]] = {"menu": menu_track, "battle": battle_track}


def finish(signal: Signal) -> Signal:
    """Normaliza pela loudness e reduz para 22,05 kHz (média de pares depois
    de um passa-baixa, o bastante para música de fundo)."""
    signal = highpass(signal - signal.mean(), 45)
    rms = float(np.sqrt(np.mean(signal**2))) or 1.0
    signal = signal * min(10 ** (MUSIC_DB / 20) / rms, 0.9 / (np.max(np.abs(signal)) or 1))
    smooth = lowpass(signal, OUT_RATE * 0.45)
    return smooth[: len(smooth) // 2 * 2].reshape(-1, 2).mean(axis=1)


def write(path: Path, signal: Signal) -> None:
    data = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(OUT_RATE)
        f.writeframes(data.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="faixas (padrão: todas)")
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in args.names or TRACKS:
        signal = finish(TRACKS[name](np.random.default_rng(sum(map(ord, name)))))
        path = MUSIC_DIR / f"{name}.wav"
        write(path, signal)
        print(f"{name:8s} {len(signal) / OUT_RATE:5.1f}s  {path.stat().st_size / 1024:.0f} KB")
        if args.play:
            subprocess.run(["afplay", str(path)], check=False)


if __name__ == "__main__":
    main()
