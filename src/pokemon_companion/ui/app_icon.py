"""Ícone e identidade do app na barra de tarefas / Dock.

Rodando pelo Python (sem o executável empacotado), o sistema mostra o ícone
do interpretador — ou um documento em branco. `install_app_identity`
resolve nos três sistemas:

- ícone da janela e do Dock (`setWindowIcon`, com todos os tamanhos);
- Windows: um AppUserModelID próprio, senão a barra de tarefas agrupa a
  janela com o python.exe e usa o ícone dele;
- Linux: o nome do arquivo .desktop (Wayland associa o ícone por ele).

Os PNGs vêm de `tools/make_icon.py`, que também gera o .icns e o .ico do
executável.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QGuiApplication, QIcon

ICON_DIR = Path(__file__).resolve().parent / "icons"
APP_ID = "pokemon-companion"
WINDOWS_APP_ID = "PokemonCompanion.Treino"


def app_icon() -> QIcon:
    icon = QIcon()
    for path in sorted(ICON_DIR.glob("app_*.png")):
        size = int(path.stem.split("_")[1])
        icon.addFile(str(path), QSize(size, size))
    return icon


def set_windows_app_id() -> None:
    """Chame antes de criar a primeira janela (no Windows; nos outros, nada)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def install_app_identity(app: QGuiApplication) -> None:
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(app_icon())
