"""Publica os dados de cartas no release `data` do GitHub, de onde o app
instalado baixa (`cards_db/updates.py`): catálogo, rotação e `effects.json`.

    uv run python tools/publish_data.py
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from pokemon_companion.cards_db.updates import APP_DATA_VERSION, BUNDLED

TAG = "data"


def main() -> None:
    manifest = {
        "version": datetime.date.today().isoformat(),
        "min_app": APP_DATA_VERSION,
        "files": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in BUNDLED.items()
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
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
                    "Catálogo, rotação e efeitos baixados pelo app instalado.",
                ],
                check=True,
            )
        # manifest por último: o app só vê a versão nova quando os arquivos já estão lá
        files = [str(path) for path in BUNDLED.values()]
        subprocess.run(["gh", "release", "upload", TAG, *files, "--clobber"], check=True)
        subprocess.run(
            ["gh", "release", "upload", TAG, str(manifest_file), "--clobber"], check=True
        )
    print(f"Publicado: versão {manifest['version']}, min_app {APP_DATA_VERSION}")


if __name__ == "__main__":
    main()
