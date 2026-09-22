# pokemon-companion

Companheiro digital para partidas físicas de Pokémon TCG: usa a câmera para
verificar as jogadas na mesa e oferece um oponente controlado por IA, com
níveis de dificuldade e suporte a decklists específicas (formato
Limitless/PTCGO).

Funciona em Windows, Linux e macOS.

## Setup

Requer [uv](https://docs.astral.sh/uv/) instalado.

```bash
uv sync                 # instala dependências (runtime + dev) em .venv
uv run pytest           # roda os testes
uv run black --check .  # checa formatação
uv run ruff check .     # lint
```

## Status do projeto (MVP em desenvolvimento)

O projeto é construído em fases incrementais, isolando o risco técnico da
visão computacional por último:

1. **Fase 1** — motor de regras + IA jogando 100% virtual (sem câmera).
2. **Fase 2** — banco de cartas (API `pokemontcg.io`) + importador de decklist.
3. **Fase 3** — interface gráfica (PyQt6), ainda sem câmera.
4. **Fase 4** — visão computacional: reconhecimento de cartas físicas via câmera.
5. **Fase 5** — polimento (IA com lookahead maior, calibração automática, etc).

## Limitações conhecidas do MVP

- Cobre as regras essenciais do TCG (energia, ataques, dano, evolução, banco,
  prêmios, condições de status básicas). **Não** cobre: efeitos de
  Trainer/Item complexos, ataques especiais GX/V/VSTAR, abilities passivas
  complexas, regras de ACE SPEC, ou regras de torneio.
- Todos os Pokémon nocauteados valem 1 prêmio no MVP (sem diferenciação por
  raridade ex/GX/V).
- Efeitos de texto de ataques (coin flips, descarte de energia, etc.) só são
  aplicados para cartas explicitamente cadastradas em
  `engine/effects/basic_effects.py`; as demais aplicam apenas o dano base.
- O reconhecimento de cartas por câmera é feito por hashing perceptual
  contra o pool do deck importado do jogador — não contra o card pool
  inteiro. Quando a confiança do reconhecimento é baixa, o app pede
  confirmação manual.
