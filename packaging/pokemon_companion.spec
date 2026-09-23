# Executável do pokemon-companion (PyInstaller). Gere com:
#     uv run pyinstaller --noconfirm packaging/pokemon_companion.spec
# Resultado em dist/: "Pokemon Companion.app" no macOS, pasta com o .exe no Windows.
# Não há build cruzado: cada sistema gera o seu.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

NAME = "Pokemon Companion"

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    # decks, fontes, pesos da IA, legalidade do Standard, settings
    datas=collect_data_files("pokemon_companion"),
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        bundle_identifier="com.pokemoncompanion.app",
        info_plist={
            # sem esta chave o macOS encerra o app ao abrir a câmera
            "NSCameraUsageDescription": "A câmera verifica as jogadas na mesa durante a partida.",
            "NSHighResolutionCapable": True,
        },
    )
