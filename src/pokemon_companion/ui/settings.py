"""Configurações do jogador, salvas entre sessões (QSettings: registro no
Windows, plist no macOS, arquivo .conf no Linux).

`Settings` é só dados; `apply` leva os valores aos lugares que os usam
(velocidade das animações, redução de movimento, som, música).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from PyQt6.QtCore import QSettings

ORGANIZATION = APPLICATION = "pokemon-companion"
#: arquivo .ini no lugar do armazenamento do sistema (testes)
STORE_FILE: Path | None = None
#: velocidade das animações oferecida na tela (rótulo, multiplicador)
SPEEDS = (("Lenta", 0.75), ("Normal", 1.0), ("Rápida", 1.5), ("Muito rápida", 2.5))
DIFFICULTIES = (("easy", "Fácil"), ("medium", "Média"), ("hard", "Difícil"))


@dataclass
class Settings:
    volume: float = 0.7
    muted: bool = False
    music: bool = True
    music_volume: float = 0.35
    #: multiplicador de velocidade das animações (2 = duas vezes mais rápido)
    speed: float = 1.0
    #: sem tremor de tela e sem clarões (sensibilidade à luz, enjoo)
    reduce_motion: bool = False
    fullscreen: bool = False
    difficulty: str = "medium"
    #: dicas contextuais para quem está começando
    hints: bool = True


def _store() -> QSettings:
    if STORE_FILE is not None:
        return QSettings(str(STORE_FILE), QSettings.Format.IniFormat)
    return QSettings(ORGANIZATION, APPLICATION)


def load_settings(store: QSettings | None = None) -> Settings:
    store = store or _store()
    values: dict[str, object] = {}
    defaults = Settings()
    for field in fields(Settings):
        default = getattr(defaults, field.name)
        values[field.name] = store.value(f"settings/{field.name}", default, type=type(default))
    return Settings(**values)  # type: ignore[arg-type]


def save_settings(settings: Settings, store: QSettings | None = None) -> None:
    store = store or _store()
    for field in fields(Settings):
        store.setValue(f"settings/{field.name}", getattr(settings, field.name))
    store.sync()


def seen_hints(store: QSettings | None = None) -> set[str]:
    store = store or _store()
    value = store.value("hints/seen", [], type=list)
    return {str(item) for item in value or []}


def mark_hint_seen(key: str, store: QSettings | None = None) -> None:
    store = store or _store()
    store.setValue("hints/seen", sorted(seen_hints(store) | {key}))


def reset_hints(store: QSettings | None = None) -> None:
    (store or _store()).remove("hints/seen")
