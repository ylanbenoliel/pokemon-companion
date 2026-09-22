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
uv run pytest -v        # confirma que tudo continua passando (90 testes)
uv run mypy src          # type-check
uv run black --check . && uv run ruff check .   # formatação/lint

# CLI (deck mockado por padrão):
uv run python -m pokemon_companion.main --difficulty medium

# CLI com decklists reais (precisa de internet; formato Limitless/PTCGO):
uv run python -m pokemon_companion.main --player-deck data/decks/meu_deck.txt --opponent-deck data/decks/deck_da_ia.txt --record-history data/partida1.json

# UI gráfica (PyQt6) — mesma lógica, board do jogador ainda por clique manual:
uv run python -m pokemon_companion.ui.app --difficulty hard
```

## Status — todas as 5 fases do plano original têm código funcional

- ✅ **Fase 0 — Setup**: `uv`, layout `src/`, `black`+`ruff`+`mypy` (+`pytest-qt` para testar a UI sem display real via `QT_QPA_PLATFORM=offscreen`).
- ✅ **Fase 1 — Motor de regras + IA**: completo, testado, jogável via CLI.
- ✅ **Fase 2 — Banco de cartas + decklist**: API `pokemontcg.io` + cache SQLite + parser Limitless/PTCGO.
- ✅ **Fase 3 — UI gráfica (PyQt6)**: janela funcional, board da IA/jogador, ações por clique, turno da IA via `QTimer`. **Redesenhada visualmente** (ver `ui/theme.py` + `ui/pokemon_card_widget.py`): fundo em degradê roxo, cards de Pokémon com borda colorida por tipo de energia, barra de HP com cor por porcentagem (verde/amarelo/vermelho), pips de energia anexada, pips de prêmios, botões e listas estilizados via QSS — inspirado no visual do Pokémon TCG Pocket em vez do label de texto monoespaçado original. **Validada visualmente** (screenshots renderizados offscreen antes/depois do redesign, ver nota abaixo) e com um teste de partida completa de ponta a ponta pela UI. Não há skill deste ambiente para layout de app desktop (as skills de design existentes aqui são para Artifacts web/HTML) — o redesign foi feito aplicando princípios de design diretamente via QSS.
- ✅ **Fase 4 — Visão computacional (building blocks)**: captura de câmera multiplataforma, calibração por homografia, mapeamento de zonas, detecção de ocupação/estabilidade, reconhecimento por pHash, download+cache de imagens de carta, e um widget de calibração/debug. **Todos os módulos têm testes unitários com dados sintéticos** (sem precisar de câmera real).
- ✅ **Fase 5 — Polimento (parcial, ver detalhes)**: prêmios diferenciados por raridade (ex/GX/V=2, VMAX/VSTAR=3), exemplo funcional de efeito de ataque registrado (`engine/effects/basic_effects.py`, testado de ponta a ponta via o registry real), histórico de partida exportável em JSON (`--record-history`).

88 testes passando (`uv run pytest`), `black`/`ruff`/`mypy` limpos. Dois
commits no histórico até agora (Fases 0+1, Fase 2) — o trabalho das Fases
3-5 ainda está para commitar nesta sessão.

## O que NÃO foi validado (limitações honestas deste ambiente de trabalho)

Este código foi escrito num ambiente sem internet, sem display gráfico real
e sem câmera/hardware. Isso não impediu testar a lógica (ver estratégias de
teste abaixo), mas alguns fluxos de ponta a ponta só podem ser confirmados
por você, com hardware/rede reais:

1. **Decklists reais via API** (`--player-deck`/`--opponent-deck`): a lógica
   está 100% coberta por testes com HTTP mockado, mas nunca foi executada
   contra a API real do `pokemontcg.io`. Teste com uma decklist real antes
   de confiar no fluxo.
2. **UI gráfica**: testada de verdade com `pytest-qt` rodando headless
   (`QT_QPA_PLATFORM=offscreen`) — inclusive um screenshot real foi
   renderizado e inspecionado durante o desenvolvimento, e um bug de
   crash real (`QImage`/`QPixmap` construído antes de existir uma
   `QApplication`, ou com um buffer temporário que era liberado antes do
   Qt terminar de usá-lo) foi encontrado e corrigido através dos testes.
   Ainda assim, vale abrir a janela de verdade (`uv run python -m
   pokemon_companion.ui.app`) numa máquina com display para conferir a
   experiência visual/de clique real.
3. **Visão computacional com câmera física**: `camera_capture.py`,
   `calibration.py`, `zone_mapper.py`, `card_detector.py`,
   `card_recognizer.py` e `recognition_index.py` têm testes unitários
   completos usando frames/imagens sintéticas (arrays numpy gerados por
   ruído, sem depender de OpenCV realmente abrir um dispositivo). **Nenhum
   desses módulos foi testado com uma câmera de verdade nem com fotos reais
   de cartas físicas** — isso é trabalho pendente que só pode ser feito com
   hardware.
4. **Integração "visão → jogada"**: os módulos de visão (Fase 4) e a UI
   (Fase 3) existem lado a lado, mas **ainda não estão conectados**. Não há
   hoje um fluxo automático "câmera detecta uma carta na zona do banco →
   aplica `PlayBasicToBench` no `GameState`". Ver "Próximo passo" abaixo.

## Estrutura completa do projeto

```
pokemon_companion/
├── pyproject.toml / uv.lock / .pre-commit-config.yaml / README.md
├── src/pokemon_companion/
│   ├── main.py                      # CLI: jogador humano vs IA
│   ├── presentation.py              # describe_action() — compartilhado CLI/UI
│   ├── deck_loading.py              # load_decks() — compartilhado CLI/UI
│   ├── demo_data.py                 # decks mockados (fallback)
│   ├── config/settings.yaml
│   ├── cards_db/                    # Fase 2 — completo
│   │   ├── models.py, api_client.py, cache.py, basic_energies.py, decklist_parser.py
│   ├── engine/                      # Fase 1 — completo
│   │   ├── game_state.py, actions.py, rules.py, status_conditions.py, turn_manager.py
│   │   ├── history.py               # Fase 5 — MatchRecorder (JSON)
│   │   └── effects/                 # registry.py + basic_effects.py (1 exemplo registrado)
│   ├── ai/                          # Fase 1 — completo
│   │   ├── opponent.py (Protocol + build_ai), heuristics_easy/medium/hard.py, weights.yaml
│   ├── ui/                          # Fase 3 — completo, com redesign visual
│   │   ├── app.py (MainWindow, QApplication), confirmation_dialog.py
│   │   ├── theme.py                 # QSS + paleta por tipo de energia
│   │   ├── board_view.py            # board de um lado (PrizeTracker + cards)
│   │   ├── pokemon_card_widget.py   # card individual: HP bar, pips de energia, status
│   │   └── camera_debug_view.py     # Fase 4 — calibração por clique + overlay de zonas
│   └── vision/                      # Fase 4 — completo (building blocks)
│       ├── camera_capture.py, calibration.py, zone_mapper.py
│       ├── card_detector.py, card_recognizer.py, recognition_index.py
└── tests/                           # 90 testes
    ├── conftest.py                  # fixtures de cartas mockadas + QT_QPA_PLATFORM=offscreen
    ├── test_rules.py, test_status_conditions.py, test_ai_heuristics.py
    ├── test_api_client.py, test_cache.py, test_decklist_parser.py
    ├── test_prizes_and_effects.py
    ├── test_ui.py, test_camera_debug_view.py
    └── test_camera_capture.py, test_calibration.py, test_zone_mapper.py,
        test_card_detector.py, test_card_recognizer.py, test_recognition_index.py
```

## Próximo passo real: ligar visão → jogadas (o que falta para o produto final)

Isso é o que separa "todos os módulos existem e funcionam isoladamente" de
"jogo o Pokémon TCG físico com a câmera verificando minhas jogadas de
verdade". Sugestão de abordagem, a ser feita e testada com hardware real:

1. **Fluxo de calibração dentro do app**: em `ui/app.py`, adicionar um modo
   `--camera` que abre uma `CameraCapture` de verdade, mostra
   `ui/camera_debug_view.CameraDebugView` num diálogo, espera os 4 cliques
   de calibração e salva a `vision.calibration.Calibration` resultante
   (`Calibration.save`) para reuso nas próximas partidas.
2. **Índice de reconhecimento por partida**: ao carregar a decklist do
   jogador (`deck_loading.load_decks`), chamar
   `vision.recognition_index.build_recognition_index_for_deck` para montar
   o `CardRecognizer` só com as cartas daquele deck.
3. **Loop de detecção**: um `QTimer` (parecido com o `_play_ai_turn` que já
   existe) lendo frames via `CameraCapture.read_frame()`, retificando com
   `vision.calibration.rectify`, e passando cada zona por
   `card_detector.is_occupied` + `ZoneStabilityTracker.observe`.
4. **Mapear "zona mudou" → ação do jogo**: esse é o elo que falta e exige
   decisão de design — a câmera só informa "apareceu/sumiu uma carta na
   zona X", não qual ação de `engine/actions.py` isso representa (jogar
   básico? evoluir? anexar energia?). Abordagens possíveis: (a) pedir ao
   jogador para confirmar qual ação aquilo corresponde via um pequeno menu
   contextual quando uma zona muda, usando o resultado do
   `CardRecognizer.recognize()` só para preencher automaticamente "qual
   carta é essa" (não "qual ação é essa"); (b) inferir por eliminação
   comparando o novo estado da zona com `legal_actions(state)` e casando
   pela carta reconhecida. A opção (a) é mais simples e mais robusta pra
   começar.
5. Quando a confiança do reconhecimento (`CardRecognizer.is_confident`) for
   baixa, abrir `ui/confirmation_dialog.ConfirmationDialog` com os
   candidatos, para o jogador confirmar manualmente.

Nada disso pôde ser escrito e testado nesta sessão por falta de câmera —
implementar às cegas seria só código especulativo sem verificação nenhuma,
por isso ficou para quando você puder testar com hardware real.

## Simplificações do MVP (lembrete, ver README.md)

- Efeitos de Trainer/Item complexos, GX/V/VSTAR especiais, abilities
  passivas complexas, ACE SPEC e regras de torneio ficam fora de escopo.
- Prêmios: ex/GX/V = 2, VMAX/VSTAR = 3, demais = 1 (implementado na Fase 5;
  raridades mais raras como TAG TEAM ainda contam como 1).
- Ativo inicial de cada jogador é escolhido automaticamente (primeiro
  básico da mão); promoção pós-knockout também é automática (primeiro do
  banco) — nenhuma exige escolha manual do jogador ainda.
- Reconhecimento de câmera usa a imagem inteira da carta baixada da API
  (não um recorte só da arte) — mais simples, funciona bem com pHash, mas
  exige enquadramento de câmera relativamente consistente.
- Histórico de partida (Fase 5) grava a sequência de ações em JSON; não há
  player de replay visual, só o registro para consulta posterior.
