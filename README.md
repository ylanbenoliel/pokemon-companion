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

- Segue o livro de regras oficial: Treinadores (1 Apoiador por turno),
  Estádio, Ferramentas, Habilidades, energias especiais, efeitos de texto
  de ataques, nocaute no banco, prêmios por tipo (ex/V = 2, Mega ex/VMAX/
  VSTAR = 3), escolha manual do Ativo/Banco no setup e após nocaute, e
  Morte Súbita. Os efeitos de cartas são registrados por nome em
  `engine/effects/` e cobrem os 20 decks do meta em `examples/decks/top/`;
  cartas fora deles podem ficar sem efeito (ver `tools/effect_coverage.py`).
- Regras de torneio (tempo, deck check) não são aplicadas.
- A interface abre numa tela de confronto (seu deck × deck da IA e a
  dificuldade); `--player-deck`/`--opponent-deck` pulam direto para a partida.
- As cartas de Treinador mostram em português o que fazem; as descrições
  ficam em `engine/effects/descriptions.py` e cobrem os decks incluídos.
- O reconhecimento de cartas por câmera é feito por hashing perceptual
  contra o pool do deck importado do jogador — não contra o card pool
  inteiro. Quando a confiança do reconhecimento é baixa, o app pede
  confirmação manual.
