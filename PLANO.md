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
uv run python -m pokemon_companion.main --difficulty medium   # joga a demo via CLI
```

Checks antes de qualquer commit: `uv run black --check .`, `uv run ruff check .`, `uv run mypy src`.

## Status

- ✅ **Fase 0 — Setup**: projeto `uv` (layout `src/`), `black`+`ruff`+`mypy` configurados em `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore` ajustado para `data/` (cache/decks).
- ✅ **Fase 1 — Motor de regras + IA, 100% virtual**: completo e testado (26 testes em `uv run pytest`). Partida jogável do início ao fim via CLI (`main.py`) com um vencedor declarado corretamente.
- ⬜ **Fase 2 — Banco de cartas + decklist** (próximo passo, ver detalhes abaixo).
- ⬜ **Fase 3 — UI gráfica (PyQt6) sem câmera**.
- ⬜ **Fase 4 — Visão computacional**.
- ⬜ **Fase 5 — Polimento (pós-MVP)**.

Nenhum commit git foi feito ainda — o repositório local existe (criado pelo
`uv init`) mas está com tudo untracked/sem histórico.

## O que já existe (Fases 0 e 1)

```
pokemon_companion/
├── pyproject.toml                   # deps, black, ruff, mypy, pytest config
├── uv.lock
├── .pre-commit-config.yaml
├── README.md                        # escopo e limitações do MVP
├── src/pokemon_companion/
│   ├── main.py                      # CLI de demonstração (jogador humano vs IA)
│   ├── demo_data.py                 # decks mockados usados pelo CLI (Fase 2 substitui)
│   ├── config/settings.yaml
│   ├── cards_db/
│   │   └── models.py                # Card, Attack, Ability, Supertype (já pronto,
│   │                                 # usado tanto pelos mocks quanto pela API futura)
│   ├── engine/                      # completo: game_state, actions, rules,
│   │   │                            # status_conditions, turn_manager, effects/
│   │   └── effects/registry.py      # registry de efeitos especiais de ataque (vazio por ora)
│   ├── ai/                          # completo: opponent (Protocol), heuristics_easy/
│   │   │                            # medium/hard, weights.yaml
│   ├── vision/                      # só __init__.py (Fase 4)
│   └── ui/                          # só __init__.py (Fase 3)
└── tests/
    ├── conftest.py                  # fixtures de cartas mockadas (charmander, squirtle, ...)
    ├── test_rules.py
    ├── test_status_conditions.py
    └── test_ai_heuristics.py        # inclui self-play fuzz tests (easy/medium/hard)
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
  base é aplicado até a Fase 2 trazer decklists reais que precisem disso.

## Próximo passo: Fase 2 — Banco de cartas + decklist

Objetivo: substituir `demo_data.py` por cartas reais, resolvidas a partir de
decklists em formato Limitless/PTCGO, usando a API pública `pokemontcg.io`
cacheada localmente. Isso também cobre o requisito original de "jogar
contra decks específicos".

Arquivos a criar em `src/pokemon_companion/cards_db/`:
- `api_client.py` — cliente HTTP para `https://api.pokemontcg.io/v2/cards`
  (usar `requests`, já é dependência do projeto). Rate limit/retry básico.
  Ler a API key opcional de `POKEMONTCG_API_KEY` (variável de ambiente,
  nunca hardcoded) — ver `config/settings.yaml:pokemontcg_api`.
- `cache.py` — SQLite local (`sqlite3`, builtin) com tabelas `cards`, `sets`,
  `card_images`. Usar `platformdirs.user_data_dir("pokemon-companion")`
  para o caminho do banco (multiplataforma — já é dependência instalada).
  Fetch sob demanda: só baixa uma carta quando ela aparece numa decklist
  sendo resolvida, não o pool inteiro (~18k cartas).
- `decklist_parser.py` — parser do formato Limitless/PTCGO
  (`"4 Charmander SVI 26"` → quantidade, nome, código do set, número).
  Tratar seções (`Pokémon:`, `Trainer:`, `Energy:`) e energias básicas sem
  set/número (ex: `8 Fire Energy`) contra um pool built-in. Resolver cada
  linha contra `cache.py`/`api_client.py` usando `set.ptcgoCode` + `number`.
  Retornar erros claros por linha (número da linha + motivo) para o usuário
  corrigir.

Testes a escrever em `tests/`:
- `test_decklist_parser.py` — decklists reais válidas + uma com erro
  proposital; mockar `requests` para não depender de rede no CI.
- Teste de cache confirmando que uma segunda consulta não dispara nova
  chamada HTTP.

Depois de pronto: trocar `demo_data.build_demo_deck()` no `main.py` por
decklists carregadas de arquivos `.txt` em `data/decks/` (diretório já
existe, ignorado pelo git exceto `.gitkeep`), permitindo `--player-deck` e
`--opponent-deck` como argumentos do CLI.

**Critério de saída da Fase 2**: partida da Fase 1 rodando de ponta a ponta
com decks reais importados de arquivos de texto, exibindo nomes e dados
corretos das cartas.

## Fases seguintes (resumo — detalhes completos no plano original aprovado)

- **Fase 3 — UI gráfica (PyQt6) sem câmera**: `ui/app.py`, `ui/board_view.py`.
  Board do jogador ainda por clique manual (substitui a leitura do CLI).
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
