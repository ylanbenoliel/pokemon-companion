"""Publica os dados de cartas no release `data` do GitHub, de onde o app
instalado baixa (`cards_db/updates.py`): catálogo, rotação, `effects.json` e os
decks do meta (`decks/top` empacotado em `meta_decks.json`).

    uv run python tools/publish_data.py
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from pokemon_companion.cards_db.updates import (
    APP_DATA_VERSION,
    BUNDLED,
    META_DECKS,
    pack_meta_decks,
)

TAG = "data"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        meta_decks = Path(tmp) / META_DECKS
        meta_decks.write_bytes(pack_meta_decks())
        paths = {**BUNDLED, META_DECKS: meta_decks}
        manifest = {
            "version": datetime.date.today().isoformat(),
            "min_app": APP_DATA_VERSION,
            "files": {
                name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()
            },
        }
        manifest_file = Path(tmp) / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        exists = subprocess.run(["gh", "release", "view", TAG], capture_output=True).returncode == 0
        if not exists:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "create",
                    TAG,
                    "--title",
                    "Dados de cartas",
                    "--notes",
                    "Catálogo, rotação, efeitos e decks do meta baixados pelo app instalado.",
                ],
                check=True,
            )
        # manifest por último: o app só vê a versão nova quando os arquivos já estão lá
        files = [str(path) for path in paths.values()]
        subprocess.run(["gh", "release", "upload", TAG, *files, "--clobber"], check=True)
        subprocess.run(
            ["gh", "release", "upload", TAG, str(manifest_file), "--clobber"], check=True
        )
    print(f"Publicado: versão {manifest['version']}, min_app {APP_DATA_VERSION}")


if __name__ == "__main__":
    main()
