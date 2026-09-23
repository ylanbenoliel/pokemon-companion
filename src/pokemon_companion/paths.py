"""Onde ficam os arquivos do app — iguais rodando do código-fonte ou
empacotado (PyInstaller), porque nada depende da pasta em que o programa foi
aberto.

- decks que acompanham o app: dentro do pacote (`pokemon_companion/decks`);
- dados do usuário (decks importados, cache de cartas, artes): na pasta de
  dados do usuário de cada SO (`platformdirs`).
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

PACKAGE_DIR = Path(__file__).parent
BUNDLED_DECKS = PACKAGE_DIR / "decks"
USER_DATA = Path(user_data_dir("pokemon-companion", "pokemon-companion"))
USER_DECKS = USER_DATA / "decks"
