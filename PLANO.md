# Plano do projeto — pokemon-companion

Companheiro digital para partidas físicas de Pokémon TCG: usa a câmera para
verificar as jogadas na mesa, tem um oponente controlado por IA com níveis
de dificuldade, e permite jogar contra decks específicos importados por
decklist. Documento de continuidade — atualize a seção "Status" conforme o
trabalho avançar.

## Como retomar

```bash
cd ~/pokemon_companion
uv sync                 # instala dependências em .venv
uv run pytest -v        # confirma que tudo continua passando
uv run python -m pokemon_companion.main --difficulty medium   # joga a demo via CLI (deck mockado)

# com decklists reais (precisa de internet; formato Limitless/PTCGO, ver exemplo abaixo):
uv run python -m pokemon_companion.main --player-deck data/decks/meu_deck.txt --opponent-deck data/decks/deck_da_ia.txt
```

Checks antes de qualquer commit: `uv run black --check .`, `uv run ruff check .`, `uv run mypy src`.

## Status

- ✅ **Fase 0 — Setup**: projeto `uv` (layout `src/`), `black`+`ruff`+`mypy` configurados em `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore` ajustado para `data/` (cache/decks).
- ✅ **Fase 1 — Motor de regras + IA, 100% virtual**: completo e testado. Partida jogável do início ao fim via CLI (`main.py`) com um vencedor declarado corretamente.
- ✅ **Fase 2 — Banco de cartas + decklist**: cliente da API `pokemontcg.io`, cache SQLite e parser Limitless/PTCGO completos e testados. `main.py` aceita `--player-deck`/`--opponent-deck` apontando para arquivos de decklist reais (fallback para o deck mockado quando omitido).
- ⬜ **Fase 3 — UI gráfica (PyQt6) sem câmera** (próximo passo, ver detalhes abaixo).
- ⬜ **Fase 4 — Visão computacional**.
- ⬜ **Fase 5 — Polimento (pós-MVP)**.

Um commit inicial já foi feito (Fases 0 e 1). O trabalho da Fase 2 ainda
está para commitar — ver `git status`.

**Nota de rede**: `--player-deck`/`--opponent-deck` fazem chamadas reais à
API pokemontcg.io na primeira vez que uma carta aparece numa decklist (fica
cacheada depois, em `~/Library/Application Support/pokemon-companion/` no
macOS via `platformdirs`). Isso não foi validado contra a API real nesta
sessão (sem acesso à rede no ambiente onde o código foi escrito) — a lógica
está coberta por testes unitários com HTTP mockado (`tests/test_api_client.py`,
`tests/test_decklist_parser.py`), mas vale testar manualmente com uma
decklist real antes de confiar no fluxo de ponta a ponta.

## O que já existe (Fases 0, 1 e 2)

```
pokemon_companion/
├── pyproject.toml                   # deps, black, ruff, mypy, pytest config
├── uv.lock
├── .pre-commit-config.yaml
├── README.md                        # escopo e limitações do MVP
├── src/pokemon_companion/
│   ├── main.py                      # CLI: jogador humano vs IA, deck mockado ou --player-deck/--opponent-deck
│   ├── demo_data.py                 # decks mockados (fallback quando nenhuma decklist é passada)
│   ├── config/settings.yaml
│   ├── cards_db/                    # completo
│   │   ├── models.py                # Card, Attack, Ability, Supertype, WeaknessResistance
│   │   ├── api_client.py            # PokemonTcgApiClient (retry/backoff) + api_card_to_card
│   │   ├── cache.py                 # CardCache (SQLite via platformdirs, roundtrip completo)
│   │   ├── basic_energies.py        # energias básicas resolvidas sem API
│   │   └── decklist_parser.py       # parse_decklist_text (puro) + resolve_entries + load_deck
│   ├── engine/                      # completo: game_state, actions, rules,
│   │   │                            # status_conditions, turn_manager, effects/
│   │   └── effects/registry.py      # registry de efeitos especiais de ataque (vazio por ora)
│   ├── ai/                          # completo: opponent (Protocol), heuristics_easy/
│   │   │                            # medium/hard, weights.yaml
│   ├── vision/                      # só __init__.py (Fase 4)
│   └── ui/                          # só __init__.py (Fase 3)
└── tests/                           # 38 testes, todos passando
    ├── conftest.py                  # fixtures de cartas mockadas (charmander, squirtle, ...)
    ├── test_rules.py
    ├── test_status_conditions.py
    ├── test_ai_heuristics.py        # inclui self-play fuzz tests (easy/medium/hard)
    ├── test_api_client.py           # HTTP mockado (sem rede)
    ├── test_cache.py                # SQLite real em tmp_path
    └── test_decklist_parser.py      # parser puro + resolve_entries com fakes de cache/API
```

### Decisões e simplificações já implementadas

- Motor cobre: energia (1/turno), ataques com fraqueza ×2 / resistência -30,
  evolução (não no turno em que o Pokémon entrou em jogo), retreat pagando
  custo, status básicos (veneno, queimadura, sono, paralisia, confusão),
  knockout, prêmios, condições de vitória (prêmios esgotados, sem Pokémon
  em jogo, deck-out).
- Simplificações documentadas no README: todo knockout vale 1 prêmio; o
  ativo inicial de cada jogador é escolhido automaticamente (primeiro
  básico da mão); após knockout, o próximo ativo é promovido automaticamente
  (primeiro do banco) — nenhuma dessas exige escolha manual do jogador por
  ora.
- IA: `EasyAI` (ataca se possível, senão aleatório), `MediumAI` (simula cada
  ação legal e pontua via `evaluate_state`, pesos em `ai/weights.yaml`),
  `HardAI` (mesmo framework + 1 ply prevendo a resposta do `MediumAI`
  oponente).
- Efeitos de texto de ataques (coin flip, descarte, etc.) ainda não têm
  nenhuma carta cadastrada em `engine/effects/basic_effects.py` — só dano
  base é aplicado; popular esse registry conforme decklists reais de teste
  precisarem de efeitos específicos.
- `cards_db.cache.CardCache` busca sob demanda (uma carta só é baixada/salva
  quando aparece numa decklist sendo resolvida), nunca o pool inteiro.
- Energias básicas (`"8 Fire Energy"`) são resolvidas localmente via
  `cards_db.basic_energies.BASIC_ENERGIES`, sem tocar cache/API.
- Entry point do pacote (`pokemon-companion` / `python -m pokemon_companion.main`)
  aponta direto para `pokemon_companion.main:main`; `__init__.py` do pacote
  ficou vazio de propósito para evitar import duplicado (`RuntimeWarning`) ao
  rodar via `-m`.

## Próximo passo: Fase 3 — UI gráfica (PyQt6) sem câmera

Objetivo: substituir a leitura/escrita via terminal (`input()`/`print()` em
`main.py`) por uma UI gráfica real, mantendo o board do jogador ainda por
clique manual (a câmera só entra na Fase 4). Isso isola o risco de UI do
risco de visão computacional.

Arquivos a criar em `src/pokemon_companion/ui/`:
- `app.py` — ponto de entrada da aplicação PyQt6 (`QApplication`, janela
  principal). Deve reusar `engine.rules.legal_actions`/`apply_action` e
  `ai.opponent.AIPlayer` exatamente como `main.py` faz hoje — a lógica de
  jogo não muda, só a camada de apresentação.
- `board_view.py` — widget que renderiza um `GameState` (ativo, banco,
  prêmios, mão) para os dois lados. Pode se inspirar diretamente em
  `main.py:render_state`/`render_pokemon` para saber quais dados mostrar.
- Reaproveitar `main.py:describe_action` como referência para gerar os
  rótulos dos botões de ação (substituindo a lista numerada do CLI).

Sugestão de abordagem incremental: manter `main.py` funcionando (CLI) como
está, e criar a UI em paralelo sem quebrá-lo — o objetivo da Fase 3 é ter
as duas interfaces funcionando sobre o mesmo motor, não substituir uma pela
outra ainda.

**Critério de saída da Fase 3**: jogar uma partida completa via UI gráfica,
com paridade de ações em relação ao CLI (tudo que dá pra fazer no CLI hoje
deve dar pra fazer clicando na UI).

## Fases seguintes (resumo — detalhes completos no plano original aprovado)

- **Fase 4 — Visão computacional**: `vision/camera_capture.py` (backend por
  SO: DSHOW/MSMF no Windows, AVFoundation no macOS, V4L2 no Linux),
  `calibration.py` (homografia via 4 cliques), `zone_mapper.py`,
  `card_recognizer.py` (perceptual hashing + fallback ORB, reconhecendo só
  contra o pool do deck importado do jogador), `ui/confirmation_dialog.py`
  para baixa confiança.
- **Fase 5 — Polimento**: lookahead maior na IA difícil, marcadores ArUco
  para calibração automática, download offline do pool completo de cartas,
  mais efeitos de ataque no registry, prêmios diferenciados por raridade,
  histórico/replay de partidas.

## Fora de escopo do MVP (lembrete)

Efeitos de Trainer/Item complexos, ataques especiais GX/V/VSTAR, abilities
passivas complexas, regras de ACE SPEC, regras de torneio (deck check,
tempo), prêmios diferenciados por raridade.
