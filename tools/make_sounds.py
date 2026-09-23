"""Gera os efeitos sonoros do jogo por síntese (numpy → WAV mono 16 bits).

Os sons são agrupados por *efeito*, nunca por carta: ataque por tipo de
Pokémon, impacto por intensidade, energia por tipo, condições especiais,
Treinador por categoria, cartas (comprar, buscar, embaralhar...) e fluxo do
jogo (turno, moeda, vitória). `ui/sound_cues.py` decide qual grupo toca a
partir do que cada ação mudou no jogo.

Uso:
    uv run python tools/make_sounds.py                 # gera todos
    uv run python tools/make_sounds.py hit_heavy ko    # só alguns
    uv run python tools/make_sounds.py --play attack_fire   # gera e toca (macOS)

Tudo é determinístico (semente fixa por som). Grupos frequentes ganham
variações (`card_draw_1..3`) para não soarem repetitivos. Para trocar um
grupo por um sample gravado (ex.: pacote CC0), ponha o WAV com o mesmo nome
em `src/pokemon_companion/ui/sounds/` e não o regere.
"""

from __future__ import annotations

import argparse
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parents[1] / "src" / "pokemon_companion" / "ui" / "sounds"
Signal = np.ndarray
Rng = np.random.Generator

# ---------------------------------------------------------------------------
# primitivas


def n_of(dur: float) -> int:
    return max(int(SR * dur), 1)


def tl(dur: float) -> Signal:
    return np.arange(n_of(dur)) / SR


def glide(f0: float, f1: float, dur: float) -> Signal:
    """Frequência deslizando exponencialmente de f0 a f1."""
    return f0 * (f1 / f0) ** np.linspace(0, 1, n_of(dur))


def osc(freq: float | Signal, dur: float, shape: str = "sine") -> Signal:
    n = n_of(dur)
    f = np.full(n, float(freq)) if np.ndim(freq) == 0 else np.resize(freq, n)
    phase = 2 * np.pi * np.cumsum(f) / SR
    cycle = (phase / (2 * np.pi)) % 1
    if shape == "square":
        return np.tanh(6 * np.sin(phase))  # quadrada suave (menos aliasing)
    if shape == "saw":
        return 2 * cycle - 1
    if shape == "tri":
        return 2 * np.abs(2 * cycle - 1) - 1
    return np.sin(phase)


def env(dur: float, attack: float = 0.004, decay: float = 0.15) -> Signal:
    """Ataque linear e queda exponencial."""
    t = tl(dur)
    rise = np.minimum(1.0, t / max(attack, 1e-4))
    return rise * np.exp(-np.maximum(t - attack, 0) / decay)


def hump(dur: float, peak: float = 0.5) -> Signal:
    """Sobe e desce (seno), com o pico na fração `peak`."""
    x = np.linspace(0, 1, n_of(dur))
    warped = np.where(x < peak, x / peak * 0.5, 0.5 + (x - peak) / (1 - peak) * 0.5)
    return np.sin(np.pi * warped)


def noise(dur: float, rng: Rng) -> Signal:
    return rng.uniform(-1, 1, n_of(dur))


def lowpass(x: Signal, cutoff: float | Signal) -> Signal:
    """Passa-baixa de um polo; `cutoff` pode variar no tempo."""
    c = np.resize(np.asarray(cutoff, dtype=float), len(x))
    a = 1 - np.exp(-2 * np.pi * np.minimum(c, SR / 2.2) / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, sample in enumerate(x):
        acc += a[i] * (sample - acc)
        y[i] = acc
    return y


def highpass(x: Signal, cutoff: float | Signal) -> Signal:
    return x - lowpass(x, cutoff)


def bandpass(x: Signal, center: float | Signal, q: float = 1.5) -> Signal:
    """Biquad passa-faixa (RBJ); o centro é atualizado a cada 32 amostras."""
    c = np.resize(np.asarray(center, dtype=float), len(x))
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for start in range(0, len(x), 32):
        w = 2 * np.pi * min(c[start], SR / 2.3) / SR
        alpha = np.sin(w) / (2 * q)
        a0 = 1 + alpha
        b0, b2 = alpha / a0, -alpha / a0
        a1, a2 = -2 * np.cos(w) / a0, (1 - alpha) / a0
        for i in range(start, min(start + 32, len(x))):
            out = b0 * x[i] + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x[i]
            y2, y1 = y1, out
            y[i] = out
    return y


def _comb(x: Signal, delay: int, gain: float) -> Signal:
    y = x.copy()
    for k in range(delay, len(x), delay):
        m = len(y[k : k + delay])
        y[k : k + m] += gain * y[k - delay : k - delay + m]
    return y


def _allpass(x: Signal, delay: int, gain: float) -> Signal:
    y = np.zeros_like(x)
    for k in range(0, len(x), delay):
        m = len(x[k : k + delay])
        past_x = x[k - delay : k - delay + m] if k >= delay else np.zeros(m)
        past_y = y[k - delay : k - delay + m] if k >= delay else np.zeros(m)
        y[k : k + m] = -gain * x[k : k + m] + past_x + gain * past_y
    return y


def reverb(x: Signal, mix: float = 0.25, tail: float = 0.6, size: float = 1.0) -> Signal:
    """Schroeder: 4 combs em paralelo + 2 all-pass."""
    padded = np.concatenate([x, np.zeros(n_of(tail))])
    wet = sum(
        _comb(padded, int(d * size), g)
        for d, g in ((1557, 0.8), (1617, 0.79), (1491, 0.78), (1422, 0.77))
    )
    wet = _allpass(_allpass(wet / 4, 225, 0.5), 556, 0.5)
    return padded * (1 - mix) + wet * mix


def drive(x: Signal, amount: float) -> Signal:
    return np.tanh(amount * x) / np.tanh(amount)


def at(x: Signal, offset: float) -> Signal:
    return np.concatenate([np.zeros(n_of(offset)), x])


def mix(*parts: Signal) -> Signal:
    length = max(len(p) for p in parts)
    out = np.zeros(length)
    for part in parts:
        out[: len(part)] += part
    return out


def bell(freq: float, dur: float, decay: float = 0.3, bright: float = 0.5) -> Signal:
    """Sino: parciais levemente inarmônicas, as agudas morrendo antes."""
    parts = [(1.0, 1.0, 1.0), (2.0, 0.5 * bright, 0.6), (3.01, 0.3 * bright, 0.4)]
    return sum(amp * osc(freq * ratio, dur) * env(dur, 0.002, decay * d) for ratio, amp, d in parts)


def metal(freq: float, dur: float, decay: float = 0.4) -> Signal:
    """Metal percutido: parciais inarmônicos de barra/placa."""
    ratios = (1.0, 2.76, 5.40, 8.93, 13.34)
    return sum(
        osc(freq * r, dur) * env(dur, 0.001, decay / (1 + i * 0.7)) / (1 + i * 0.6)
        for i, r in enumerate(ratios)
    )


def whoosh(dur: float, f0: float, f1: float, rng: Rng, q: float = 1.2) -> Signal:
    return bandpass(noise(dur, rng), glide(f0, f1, dur), q) * hump(dur, 0.4)


def thud(dur: float, f0: float, f1: float, decay: float) -> Signal:
    return osc(glide(f0, f1, dur), dur) * env(dur, 0.002, decay)


def crackle(dur: float, rng: Rng, density: float = 60, bright: float = 4000) -> Signal:
    """Estalos esparsos (fogo, queimadura, faísca)."""
    impulses = (rng.random(n_of(dur)) < density / SR) * rng.uniform(0.3, 1, n_of(dur))
    return highpass(lowpass(impulses * np.sign(rng.uniform(-1, 1, n_of(dur))), bright * 2), bright)


def blips(count: int, span: float, rng: Rng, f0: float, f1: float, length: float = 0.04) -> Signal:
    """Bolhas/pings: glissandos curtos em instantes aleatórios."""
    parts = []
    for _ in range(count):
        start = rng.uniform(0, span)
        scale = rng.uniform(0.8, 1.3)
        blip = osc(glide(f0 * scale, f1 * scale, length), length) * env(length, 0.003, length / 3)
        parts.append(at(blip * rng.uniform(0.4, 1), start))
    return mix(*parts)


NOTE = {
    "C4": 261.6, "D4": 293.7, "Eb4": 311.1, "E4": 329.6, "G4": 392.0, "A4": 440.0,
    "C5": 523.3, "D5": 587.3, "E5": 659.3, "G5": 784.0, "A5": 880.0, "B5": 987.8,
    "C6": 1046.5, "E6": 1318.5, "G6": 1568.0, "B6": 1975.5,
}  # fmt: skip


# ---------------------------------------------------------------------------
# sons: cada função recebe (rng, variação de altura) e devolve o sinal

Synth = Callable[[Rng, float], Signal]
SOUNDS: dict[str, tuple[Synth, int]] = {}  # nome → (síntese, variações)

#: loudness-alvo (dB RMS nos primeiros 300 ms) por prefixo de grupo: impacto
#: e ataque na frente, cartas e interface discretas
LOUDNESS = {
    "ui_": -26.0, "card_": -22.0, "shuffle": -22.0, "discard": -22.0, "switch": -21.0,
    "coin": -20.0, "turn_start": -20.0, "ability": -18.0, "trainer_": -18.0,
    "trainer_stadium": -14.0, "energy_": -19.0, "status_": -18.0, "heal": -18.0,
    "prize": -17.0, "shield": -17.0, "evolve": -15.0, "attack_": -14.0,
    "hit_light": -16.0, "hit_heavy": -12.0, "hit_massive": -10.0, "ko": -13.0,
    "victory": -15.0, "defeat": -16.0,
}  # fmt: skip
PEAK_CEILING = 0.95


def loudness_target(name: str) -> float:
    prefixes = [p for p in LOUDNESS if name.startswith(p)]
    return LOUDNESS[max(prefixes, key=len)] if prefixes else -18.0


def sound(name: str, variants: int = 1) -> Callable[[Synth], Synth]:
    def register(fn: Synth) -> Synth:
        SOUNDS[name] = (fn, variants)
        return fn

    return register


# fluxo do jogo ---------------------------------------------------------------


@sound("ui_click", variants=2)
def _ui_click(r: Rng, p: float) -> Signal:
    d = 0.06
    return osc(2100 * p, d) * env(d, 0.001, 0.012) + 0.3 * lowpass(noise(d, r), 7000) * env(
        d, 0.0005, 0.004
    )


@sound("turn_start")
def _turn_start(r: Rng, p: float) -> Signal:
    return reverb(mix(bell(NOTE["E5"], 0.5, 0.25), at(bell(NOTE["B5"], 0.6, 0.3), 0.09)), 0.3)


@sound("victory")
def _victory(r: Rng, p: float) -> Signal:
    notes = ("C5", "E5", "G5", "C6")
    run = mix(
        *(
            at(
                (0.6 * osc(NOTE[n], 0.16, "tri") + 0.25 * osc(NOTE[n], 0.16, "square"))
                * env(0.16, 0.005, 0.1),
                i * 0.1,
            )
            for i, n in enumerate(notes)
        )
    )
    chord_len = 1.1
    vibrato = 1 + 0.006 * np.sin(2 * np.pi * 5.5 * tl(chord_len))
    chord = sum(osc(NOTE[n] * vibrato, chord_len, "tri") for n in ("C6", "E6", "G6")) * env(
        chord_len, 0.02, 0.5
    )
    brass = lowpass(osc(NOTE["C5"] * vibrato, chord_len, "saw"), 2500) * env(chord_len, 0.03, 0.45)
    return reverb(mix(run, at(0.5 * chord + 0.35 * brass, 0.42)), 0.3, 1.0)


@sound("defeat")
def _defeat(r: Rng, p: float) -> Signal:
    notes = (("G4", 0.0, 0.3), ("Eb4", 0.3, 0.3), ("C4", 0.6, 0.8))
    parts = []
    for name, start, length in notes:
        f = NOTE[name] * glide(1.0, 0.97 if length > 0.5 else 1.0, length)
        voice = lowpass(osc(f, length, "saw"), 1400) + 0.4 * osc(f / 2, length)
        parts.append(at(voice * env(length, 0.02, length * 0.6), start))
    return reverb(mix(*parts), 0.3, 1.0)


@sound("coin_flip")
def _coin_flip(r: Rng, p: float) -> Signal:
    times = np.cumsum([0, 0.075, 0.065, 0.055, 0.047, 0.04, 0.035])
    pings = [
        at(0.7 * metal(r.uniform(2500, 3300), 0.09, 0.05) * (1 - i / 9), t)
        for i, t in enumerate(times)
    ]
    return mix(whoosh(0.35, 800, 4000, r) * 0.3, *pings)


@sound("coin_heads")
def _coin_heads(r: Rng, p: float) -> Signal:
    return reverb(
        mix(bell(NOTE["G6"], 0.5, 0.3), at(bell(NOTE["B6"] * 1.07, 0.5, 0.25), 0.06)), 0.25
    )


@sound("coin_tails")
def _coin_tails(r: Rng, p: float) -> Signal:
    dull = lowpass(bell(NOTE["C5"], 0.35, 0.12, bright=0.2), 1800)
    return mix(dull, 0.8 * thud(0.2, 220, 90, 0.05))


# cartas ---------------------------------------------------------------------


@sound("card_draw", variants=3)
def _card_draw(r: Rng, p: float) -> Signal:
    return whoosh(0.14, 1500 * p, 5000 * p, r, 1.1) * env(0.14, 0.02, 0.06)


@sound("card_place", variants=3)
def _card_place(r: Rng, p: float) -> Signal:
    swish = whoosh(0.1, 3200 * p, 1200 * p, r)
    slap = lowpass(noise(0.08, r), 2500) * env(0.08, 0.001, 0.015)
    return mix(0.6 * swish, at(mix(slap, 0.9 * thud(0.15, 170 * p, 70, 0.05)), 0.07))


@sound("card_search")
def _card_search(r: Rng, p: float) -> Signal:
    clicks = [
        at(bandpass(noise(0.025, r), 2600, 3) * env(0.025, 0.001, 0.006), t)
        for t in np.sort(r.uniform(0, 0.3, 8))
    ]
    found = osc(glide(1100, 1800, 0.08), 0.08) * env(0.08, 0.004, 0.03)
    return mix(*clicks, at(0.5 * found, 0.34))


@sound("shuffle")
def _shuffle(r: Rng, p: float) -> Signal:
    times = np.sort(r.uniform(0, 0.55, 22))
    clicks = [
        at(
            bandpass(noise(0.02, r), r.uniform(2000, 3500), 3)
            * env(0.02, 0.001, 0.005)
            * (0.5 + t),
            t,
        )
        for t in times
    ]
    return mix(0.25 * whoosh(0.6, 700, 1800, r), *clicks)


@sound("discard")
def _discard(r: Rng, p: float) -> Signal:
    slap = lowpass(noise(0.08, r), 1800) * env(0.08, 0.001, 0.02)
    return mix(0.7 * whoosh(0.16, 3500, 800, r), at(slap, 0.1))


# Treinadores ------------------------------------------------------------------


@sound("trainer_item")
def _trainer_item(r: Rng, p: float) -> Signal:
    pop = osc(glide(300, 1100, 0.12), 0.12) * env(0.12, 0.002, 0.045)
    sparkle = mix(at(bell(2800, 0.15, 0.05), 0.05), at(bell(3700, 0.15, 0.04), 0.09))
    return mix(pop, 0.35 * sparkle, 0.3 * lowpass(noise(0.02, r), 6000) * env(0.02, 0.0005, 0.004))


@sound("trainer_supporter")
def _trainer_supporter(r: Rng, p: float) -> Signal:
    chime = mix(
        0.6 * osc(NOTE["G4"], 0.5, "tri") * env(0.5, 0.01, 0.25),
        at(0.5 * bell(NOTE["D5"], 0.6, 0.3), 0.08),
    )
    return reverb(mix(0.5 * whoosh(0.3, 700, 2600, r), at(chime, 0.08)), 0.3)


@sound("trainer_stadium")
def _trainer_stadium(r: Rng, p: float) -> Signal:
    boom = thud(0.9, 95, 40, 0.35) + 0.5 * lowpass(noise(0.9, r), 220) * env(0.9, 0.003, 0.25)
    shimmer = sum(bell(f, 1.0, 0.5, 0.3) for f in (NOTE["A5"], NOTE["E6"], 1760.0)) * 0.25
    return reverb(mix(drive(boom, 1.5), at(shimmer, 0.05)), 0.35, 1.0, 1.3)


@sound("trainer_tool")
def _trainer_tool(r: Rng, p: float) -> Signal:
    def click() -> Signal:
        return bandpass(noise(0.04, r), 3500, 8) * env(0.04, 0.0005, 0.008)

    return mix(click(), at(click(), 0.07), at(0.4 * metal(2300, 0.2, 0.08), 0.07))


# energia (uma cor por tipo) ------------------------------------------------------


def _power_up(f0: float, f1: float, dur: float, shape: str = "sine") -> Signal:
    rise = osc(glide(f0, f1, dur), dur, shape) + 0.3 * osc(glide(2 * f0, 2 * f1, dur), dur)
    return rise * env(dur, 0.02, dur * 0.45)


def energy_sound(kind: str) -> Synth:
    def synth(r: Rng, p: float) -> Signal:
        d = 0.38
        if kind == "fire":
            base = _power_up(220, 660, d) + 0.5 * crackle(d, r, 90) * env(d, 0.01, 0.2)
        elif kind == "water":
            wobble = 1 + 0.08 * np.sin(2 * np.pi * 16 * tl(d))
            base = osc(glide(330, 880, d) * wobble, d) * env(d, 0.02, 0.17)
            base = mix(base, 0.4 * blips(4, 0.25, r, 500, 1200))
        elif kind == "lightning":
            jitter = 1 + 0.05 * lowpass(noise(d, r), 60) * 8
            base = 0.6 * osc(glide(440, 1320, d) * jitter, d, "square") * env(d, 0.005, 0.15)
            base = bandpass(base, 1800, 0.8) * 1.5 + 0.4 * crackle(d, r, 50, 5000)
        elif kind == "grass":
            base = _power_up(294, 784, d, "tri") + 0.3 * whoosh(d, 2500, 5000, r) * env(
                d, 0.03, 0.2
            )
        elif kind == "psychic":
            trem = 1 + 0.3 * np.sin(2 * np.pi * 7 * tl(d))
            base = (_power_up(392, 1175, d) + _power_up(392 * 1.008, 1175 * 1.008, d)) * trem * 0.6
        elif kind == "fighting":
            base = _power_up(147, 440, d) + thud(d, 140, 60, 0.05)
        elif kind == "darkness":
            base = lowpass(
                _power_up(110, 330, d, "saw") + 0.6 * _power_up(131, 392, d, "saw"), 1200
            )
        elif kind == "metal":
            base = 0.5 * _power_up(330, 880, d) + 0.6 * metal(660, d, 0.25)
        elif kind == "dragon":
            growl = 1 + 0.4 * np.sin(2 * np.pi * 28 * tl(d))
            base = lowpass(osc(glide(110, 440, d), d, "saw") * growl, 1600) * env(d, 0.02, 0.18)
        elif kind == "fairy":
            base = mix(_power_up(523, 1568, d), 0.5 * blips(5, 0.3, r, 2500, 4000, 0.05))
        else:  # colorless
            base = _power_up(330, 990, d)
        return mix(base, at(0.3 * bell(1760, 0.2, 0.06), d * 0.6))

    return synth


TYPES = (
    "fire", "water", "lightning", "grass", "psychic", "fighting",
    "darkness", "metal", "dragon", "fairy", "colorless",
)  # fmt: skip
for _kind in TYPES:
    sound(f"energy_{_kind}")(energy_sound(_kind))


@sound("energy_discard")
def _energy_discard(r: Rng, p: float) -> Signal:
    d = 0.32
    down = osc(glide(900, 180, d), d) * env(d, 0.005, 0.14)
    return mix(down, 0.3 * lowpass(noise(d, r), glide(3000, 300, d)) * env(d, 0.005, 0.1))


# Pokémon ------------------------------------------------------------------------


def _rising_arpeggio(notes: tuple[str, ...], step: float) -> Signal:
    return mix(
        *(
            at(
                (osc(NOTE[n], 0.25, "tri") + 0.3 * osc(2 * NOTE[n], 0.25)) * env(0.25, 0.005, 0.1),
                i * step,
            )
            for i, n in enumerate(notes)
        )
    )


@sound("evolve")
def _evolve(r: Rng, p: float) -> Signal:
    d = 1.0
    sweep = 0.35 * bandpass(noise(d, r), glide(400, 6000, d), 2) * env(d, 0.4, 0.3)
    arp = _rising_arpeggio(("C5", "E5", "G5", "C6", "E6", "G6"), 0.07)
    chord = sum(bell(NOTE[n], 0.8, 0.4) for n in ("C6", "E6", "G6")) * 0.3
    return reverb(mix(sweep, arp, at(chord, 0.45)), 0.3, 0.8)


@sound("evolve_mega")
def _evolve_mega(r: Rng, p: float) -> Signal:
    d = 1.3
    sweep = 0.45 * bandpass(noise(d, r), glide(200, 7000, d), 1.5) * env(d, 0.6, 0.35)
    riser = 0.4 * lowpass(osc(glide(55, 220, d), d, "saw"), 1200) * env(d, 0.7, 0.3)
    arp = _rising_arpeggio(("C4", "G4", "C5", "E5", "G5", "C6", "E6", "G6"), 0.08)
    impact = drive(
        thud(1.0, 110, 35, 0.4) + 0.6 * lowpass(noise(1.0, r), 400) * env(1.0, 0.002, 0.3), 2
    )
    chord = sum(bell(NOTE[n], 1.0, 0.5) for n in ("C5", "G5", "C6", "E6")) * 0.3
    return reverb(mix(sweep, riser, arp, at(mix(impact, chord), 0.7)), 0.3, 1.0, 1.2)


@sound("ability")
def _ability(r: Rng, p: float) -> Signal:
    rise = osc(glide(600, 1300, 0.3), 0.3) * env(0.3, 0.05, 0.1) * 0.5
    sparkles = mix(
        *(
            at(bell(r.uniform(1800, 4200), 0.12, 0.05, 0.3) * r.uniform(0.3, 0.8), t)
            for t in r.uniform(0, 0.35, 9)
        )
    )
    return reverb(mix(rise, sparkles), 0.3)


@sound("switch")
def _switch(r: Rng, p: float) -> Signal:
    d = 0.32
    center = np.concatenate([glide(600, 3000, d / 2), glide(3000, 800, d / 2)])
    return bandpass(noise(d, r), center, 1.4) * hump(d, 0.5)


# ataques (um por tipo) -------------------------------------------------------------


def attack_sound(kind: str) -> Synth:
    def synth(r: Rng, p: float) -> Signal:
        if kind == "fire":
            d = 0.6
            roar = lowpass(
                noise(d, r), np.concatenate([glide(500, 3500, 0.2), glide(3500, 700, d - 0.2)])
            )
            return mix(
                roar * hump(d, 0.3) * 1.3, 0.6 * crackle(d, r, 120), 0.5 * thud(d, 90, 50, 0.2)
            )
        if kind == "water":
            d = 0.5
            splash = highpass(noise(d, r), 1200) * env(d, 0.004, 0.12)
            return mix(splash, 0.6 * blips(12, 0.35, r, 350, 1300), 0.4 * whoosh(0.2, 800, 3000, r))
        if kind == "lightning":
            d = 0.45
            wander = 300 + 500 * np.abs(lowpass(noise(d, r), 40)) * 6
            zap = osc(wander, d, "square") * (r.random(n_of(d)) > 0.25).astype(float)
            zap = lowpass(zap, 5000) * env(d, 0.001, 0.15)
            return mix(
                0.7 * zap, 0.6 * crackle(d, r, 200, 3000), 0.5 * highpass(noise(0.05, r), 3000)
            )
        if kind == "grass":
            d = 0.45
            flutter = 0.6 + 0.4 * np.sin(2 * np.pi * 24 * tl(d))
            leaves = bandpass(noise(d, r), 3500, 1.0) * flutter * env(d, 0.03, 0.18)
            whip = whoosh(0.12, 1500, 6000, r, 2) * 1.2
            return mix(whip, at(leaves * 0.8, 0.05), at(0.4 * highpass(noise(0.02, r), 4000), 0.11))
        if kind == "psychic":
            d = 0.6
            t = tl(d)
            carrier = np.concatenate([glide(300, 900, d * 0.5), glide(900, 500, d * 0.5)])
            modulator = 1 + 0.25 * np.sin(2 * np.pi * 9 * t) * np.linspace(0.2, 1, len(t))
            wave_ = osc(carrier * modulator, d) + 0.5 * osc(carrier * modulator * 1.5, d)
            return reverb(wave_ * hump(d, 0.3), 0.35, 0.4)
        if kind == "fighting":
            d = 0.35
            lead = 0.4 * whoosh(0.07, 800, 2500, r)
            punch = thud(0.3, 120, 45, 0.09) + 0.6 * lowpass(noise(0.3, r), 3000) * env(
                0.3, 0.0005, 0.02
            )
            return mix(lead, at(drive(punch, 2), 0.06))
        if kind == "darkness":
            d = 0.55
            swell = (
                lowpass(noise(0.3, r), glide(300, 1500, 0.3)) * np.linspace(0, 1, n_of(0.3)) ** 2
            )
            low = thud(0.4, 70, 40, 0.15)
            dissonant = lowpass(osc(98, 0.4, "saw") + osc(104, 0.4, "saw"), 700) * env(
                0.4, 0.005, 0.15
            )
            return mix(swell, at(mix(low, 0.4 * dissonant), 0.28))
        if kind == "metal":
            d = 0.7
            return mix(
                metal(420 * p, d, 0.5),
                0.6 * highpass(noise(0.03, r), 2500) * env(0.03, 0.0005, 0.01),
            )
        if kind == "dragon":
            d = 0.75
            f = np.concatenate([glide(90, 160, 0.25), glide(160, 70, d - 0.25)])
            growl = osc(f, d, "saw") * (1 + 0.5 * np.sin(2 * np.pi * 27 * tl(d)))
            rumble = lowpass(noise(d, r), 900)
            return drive(lowpass(growl, 1800) * 0.7 + 0.5 * rumble, 2.2) * hump(d, 0.3)
        if kind == "fairy":
            d = 0.5
            sparkle = blips(10, 0.35, r, 2200, 4200, 0.06)
            return reverb(
                mix(whoosh(0.2, 1500, 5000, r), 0.8 * sparkle, bell(NOTE["E6"], 0.4, 0.2) * 0.4),
                0.3,
            )
        # colorless
        d = 0.3
        return mix(whoosh(0.13, 1000, 4000, r) * 1.2, at(thud(0.2, 130, 60, 0.06), 0.1))

    return synth


for _kind in TYPES:
    sound(f"attack_{_kind}")(attack_sound(_kind))


# impactos e consequências -------------------------------------------------------------


@sound("hit_light", variants=3)
def _hit_light(r: Rng, p: float) -> Signal:
    d = 0.2
    return thud(d, 170 * p, 60, 0.07) + 0.5 * lowpass(noise(d, r), 2500) * env(d, 0.0005, 0.015)


@sound("hit_heavy", variants=2)
def _hit_heavy(r: Rng, p: float) -> Signal:
    d = 0.4
    body = thud(d, 130 * p, 40, 0.15) + 0.7 * lowpass(noise(d, r), 1800) * env(d, 0.0005, 0.05)
    return drive(body, 2.5)


@sound("hit_massive")
def _hit_massive(r: Rng, p: float) -> Signal:
    d = 0.9
    crack = highpass(noise(0.04, r), 2000) * env(0.04, 0.0003, 0.01)
    body = thud(d, 100, 30, 0.35) + 0.8 * lowpass(noise(d, r), 600) * env(d, 0.001, 0.35)
    return reverb(mix(crack, drive(body, 3.5)), 0.2, 0.5, 1.4)


@sound("shield")
def _shield(r: Rng, p: float) -> Signal:
    glass = bell(2637, 0.4, 0.2, 0.8) + 0.6 * bell(3951, 0.4, 0.15)
    return reverb(mix(glass, 0.4 * thud(0.15, 300, 200, 0.03)), 0.3)


@sound("ko")
def _ko(r: Rng, p: float) -> Signal:
    d = 0.65
    vib = 1 + 0.03 * np.sin(2 * np.pi * 8 * tl(d))
    faint = (osc(glide(880, 110, d) * vib, d, "tri") + 0.3 * osc(glide(440, 55, d), d)) * env(
        d, 0.01, 0.35
    )
    boom = drive(
        thud(0.7, 110, 35, 0.25) + 0.6 * lowpass(noise(0.7, r), 500) * env(0.7, 0.002, 0.2), 2
    )
    return reverb(mix(faint * 0.7, at(boom, 0.5)), 0.25, 0.6)


@sound("prize")
def _prize(r: Rng, p: float) -> Signal:
    ding = mix(bell(NOTE["E6"], 0.5, 0.25), at(bell(NOTE["B6"], 0.6, 0.3), 0.08))
    coins = 0.3 * highpass(noise(0.1, r), 5000) * env(0.1, 0.001, 0.03)
    return reverb(mix(ding, coins), 0.25)


@sound("heal")
def _heal(r: Rng, p: float) -> Signal:
    arp = mix(
        *(
            at(bell(NOTE[n], 0.5, 0.25, 0.3), i * 0.07)
            for i, n in enumerate(("C5", "E5", "G5", "C6"))
        )
    )
    air = 0.12 * highpass(noise(0.6, r), 6000) * hump(0.6, 0.4)
    return reverb(mix(arp, air), 0.35, 0.6)


# condições especiais ---------------------------------------------------------------


@sound("status_poison")
def _poison(r: Rng, p: float) -> Signal:
    d = 0.7
    drone = 0.3 * osc(90, d) * (0.6 + 0.4 * np.sin(2 * np.pi * 6 * tl(d))) * env(d, 0.05, 0.3)
    return mix(lowpass(blips(14, 0.55, r, 180, 520, 0.05), 2500), drone)


@sound("status_burn")
def _burn(r: Rng, p: float) -> Signal:
    d = 0.6
    sizzle = highpass(noise(d, r), 4000) * (0.5 + 0.5 * np.abs(lowpass(noise(d, r), 30)) * 8)
    flare = lowpass(noise(0.3, r), glide(400, 2500, 0.3)) * hump(0.3, 0.3)
    return mix(0.4 * sizzle * env(d, 0.02, 0.25), 0.6 * crackle(d, r, 100), 0.6 * flare)


@sound("status_sleep")
def _sleep(r: Rng, p: float) -> Signal:
    def note(freq: float) -> Signal:
        d = 0.45
        return osc(freq, d) * (0.7 + 0.3 * np.sin(2 * np.pi * 5 * tl(d))) * env(d, 0.08, 0.25)

    return reverb(mix(note(NOTE["E5"]), at(note(NOTE["C5"]), 0.3)), 0.4, 0.7)


@sound("status_paralysis")
def _paralysis(r: Rng, p: float) -> Signal:
    d = 0.5
    gate = (np.sin(2 * np.pi * 14 * tl(d)) > 0).astype(float)
    buzz = (
        bandpass(osc(90, d, "square") + osc(181, d, "square"), 1200, 1.2)
        * gate
        * env(d, 0.005, 0.25)
    )
    return mix(1.5 * buzz, 0.4 * crackle(d, r, 60, 5000))


@sound("status_confusion")
def _confusion(r: Rng, p: float) -> Signal:
    d = 0.6
    wobble = 1 + 0.25 * np.sin(2 * np.pi * 9 * tl(d))
    boing = (
        np.resize(np.concatenate([glide(400, 800, 0.15), glide(800, 350, d - 0.15)]), len(wobble))
        * wobble
    )
    return (osc(boing, d) + 0.5 * osc(boing * 1.414, d)) * env(d, 0.01, 0.25)


# ---------------------------------------------------------------------------


def render(name: str) -> list[tuple[str, Signal]]:
    synth, variants = SOUNDS[name]
    out = []
    for k in range(variants):
        seed = sum(map(ord, name)) * 31 + k
        pitch = 1.0 if variants == 1 else (0.94, 1.0, 1.06)[k % 3]
        signal = synth(np.random.default_rng(seed), pitch)
        signal = signal - signal.mean()
        head = signal[: n_of(0.3)]
        rms = float(np.sqrt(np.mean(head**2))) or 1.0
        gain = 10 ** (loudness_target(name) / 20) / rms
        peak = float(np.max(np.abs(signal))) or 1.0
        signal = signal * min(gain, PEAK_CEILING / peak)
        fade_in, fade_out = min(len(signal), n_of(0.002)), min(len(signal), n_of(0.01))
        signal[:fade_in] *= np.linspace(0, 1, fade_in)
        signal[-fade_out:] *= np.linspace(1, 0, fade_out)
        out.append((name if variants == 1 else f"{name}_{k + 1}", signal))
    return out


def write_wav(path: Path, signal: Signal) -> None:
    data = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(data.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="sons a gerar (padrão: todos)")
    parser.add_argument("--play", action="store_true", help="toca os gerados (macOS: afplay)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    unknown = set(args.names) - set(SOUNDS)
    if unknown:
        parser.error(f"sons desconhecidos: {', '.join(sorted(unknown))}")
    total = 0
    for name in args.names or SOUNDS:
        for filename, signal in render(name):
            path = OUT / f"{filename}.wav"
            write_wav(path, signal)
            total += path.stat().st_size
            print(f"{filename:22s} {len(signal) / SR:4.2f}s")
            if args.play:
                subprocess.run(["afplay", str(path)], check=False)
    print(f"{total / 1024:.0f} KB em {OUT}")


if __name__ == "__main__":
    main()
