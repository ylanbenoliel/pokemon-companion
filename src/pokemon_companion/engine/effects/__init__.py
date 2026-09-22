"""Importar este pacote registra os efeitos de `basic_effects.py` via
`registry.register` (efeito colateral do import, necessário para o
decorator rodar)."""

from pokemon_companion.engine.effects import basic_effects as _basic_effects  # noqa: F401
