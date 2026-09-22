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
uv run pytest -v        # confirma que tudo continua passando (115 testes)
uv run mypy src          # type-check
uv run black --check . && uv run ruff check .   # formatação/lint

# CLI (deck mockado por padrão):
uv run python -m pokemon_companion.main --difficulty medium

# CLI com decklists reais (precisa de internet; formato Limitless/PTCGO):
uv run python -m pokemon_companion.main --player-deck data/decks/meu_deck.txt --opponent-deck data/decks/deck_da_ia.txt --record-history data/partida1.json

# Tabuleiro gráfico (PyQt6) — arrastar/clicar, animações; board do jogador ainda sem câmera:
uv run python -m pokemon_companion.ui.app --difficulty hard

# Modo espectador: duas IAs no difícil com decks competitivos (Regional de Baltimore, 09/2026):
uv run python -m pokemon_companion.ui.app --spectate --difficulty hard --player-difficulty hard \
  --player-deck examples/decks/dragapult_ex.txt --opponent-deck examples/decks/ns_zoroark_ex.txt --speed 1.5
```

## Status — todas as 5 fases do plano original têm código funcional

- ✅ **Fase 0 — Setup**: `uv`, layout `src/`, `black`+`ruff`+`mypy` (+`pytest-qt` para testar a UI sem display real via `QT_QPA_PLATFORM=offscreen`).
- ✅ **Fase 1 — Motor de regras + IA**: completo, testado, jogável via CLI.
- ✅ **Fase 2 — Banco de cartas + decklist**: API `pokemontcg.io` + cache SQLite + parser Limitless/PTCGO.
- ✅ **Fase 3 — UI gráfica (PyQt6)**: tabuleiro de jogo em `QGraphicsScene` (ver "Tabuleiro estilo Hearthstone/TCG Pocket" abaixo). Testado headless com `pytest-qt` e validado por screenshots/quadros de animação renderizados offscreen.
- ✅ **Fase 4 — Visão computacional (building blocks)**: captura de câmera multiplataforma, calibração por homografia, mapeamento de zonas, detecção de ocupação/estabilidade, reconhecimento por pHash, download+cache de imagens de carta, e um widget de calibração/debug. **Todos os módulos têm testes unitários com dados sintéticos** (sem precisar de câmera real).
- ✅ **Fase 5 — Polimento (parcial, ver detalhes)**: prêmios diferenciados por raridade (ex/GX/V=2, VMAX/VSTAR=3), exemplo funcional de efeito de ataque registrado (`engine/effects/basic_effects.py`, testado de ponta a ponta via o registry real), histórico de partida exportável em JSON (`--record-history`).
- ✅ **Tabuleiro estilo Hearthstone/TCG Pocket (pós-plano, a pedido do usuário)**:
  a UI foi reconstruída sobre `QGraphicsScene` (padrão do Qt para jogos 2D:
  posicionamento absoluto, rotação, z-order e animação por propriedade), após
  pesquisar referências (layout oficial do tapete, design do TCG Pocket e do
  Hearthstone, boas práticas de "game juice"):
  - layout do tapete oficial, espelhado para o oponente: prêmios à esquerda,
    deck/descarte à direita, ativo no centro, banco à frente, mão do oponente
    (versos) no topo;
  - arte oficial do Pokémon só com o bicho (PokeAPI via número da Pokédex,
    campo `nationalPokedexNumbers` da API do TCG; fallback: recorte da janela
    de ilustração da carta; fallback final: orbe do tipo);
  - mão em leque, com hover que levanta e amplia; cartas jogáveis com brilho
    verde, as demais esmaecidas e "tremendo" ao clique;
  - arrastar e soltar em alvos que pulsam (ou clicar: destino único joga
    direto; vários destinos entram em modo de escolha);
  - botões de jogo no lugar da lista de texto: ataques (custo em orbes +
    dano) ao lado do ativo, "Recuar" do outro lado, "Fim do turno" integrado
    ao tabuleiro, dourado e pulsando quando não há mais jogadas;
  - animações: orbe de energia voando e explodindo em anel/partículas ao
    anexar; investida do atacante + hit-stop + tremida de tela + flash +
    partículas da cor do tipo + dano flutuante + barra de HP drenando;
    nocaute (encolhe/gira/some), evolução (flash branco, troca da arte no
    pico), compra de cartas saindo do deck, prêmios voando para a mão,
    banner "SEU TURNO"/"TURNO DA IA";
  - painel de detalhes ao passar o mouse num Pokémon (ataques, fraqueza,
    resistência, recuo), mensagens do motor como toasts discretos, tela de
    vitória/derrota com "Jogar de novo".
  Ícones de energia são vetoriais (QPainter), não emoji — iguais em todo SO.

115 testes passando (`uv run pytest`), `black`/`ruff`/`mypy` limpos.

- ✅ **Decks competitivos + modo espectador (pós-plano)**: duas listas reais do
  Limitless em `examples/decks/` (Dragapult ex, 3º no Regional de Baltimore;
  N's Zoroark ex). Parser aceita o export atual do Limitless (`Pokémon (19)`,
  `3 Fire Energy MEE 2`, comentários `#`). Cartas via pokemontcg.io com
  fallback automático para a TCGdex (`cards_db/tcgdex_client.py`,
  `cards_db/lookup.py`) — a pokemontcg.io estava fora do ar (500/502) nesta
  sessão e as 120 cartas foram resolvidas pela TCGdex. `--spectate` põe uma
  IA também no lado de baixo; `--speed` acelera as animações.
  **Limitação importante com decks competitivos**: Treinadores (≈ 33 de 60
  cartas), habilidades e efeitos de ataque não são aplicados pelo motor. Os
  Treinadores entram no deck como cartas sem efeito (resolvidas localmente,
  sem rede). Na prática, as partidas simuladas (10 jogos IA difícil × IA
  difícil) quase sempre terminam em deck-out por volta do turno 96: com ~8
  energias por deck e sem busca/compra, os ex de 280–320 HP raramente caem.
  Próximo passo natural: motor de Treinadores com os efeitos das cartas
  dessas duas listas (compra, busca de básicos, Boss's Orders, Crispin...).
- ✅ **Correção na IA difícil**: o lookahead só simulava o oponente para ações
  que encerram o turno, então qualquer ação que não encerrasse (ex: recuar,
  descartando energia) parecia melhor que passar a vez — ela recuava em
  loop. Agora cada candidata é avaliada após completar o próprio turno e o
  turno inteiro do oponente; a avaliação também valoriza energia anexada e
  HP em campo. Nos 10 jogos simulados: recuos 76 → 16, ataques 133 → 263.

## O que foi e não foi validado neste ambiente de trabalho

Este código foi escrito sem display gráfico real e sem câmera/hardware —
mas **com acesso à internet** (confirmado nesta sessão: as URLs de imagem
usadas no deck de demonstração foram checadas contra o CDN real do
`pokemontcg.io` antes de serem usadas). Isso permitiu validar mais coisas
do que o esperado inicialmente:

1. **Decklists reais via API** (`--player-deck`/`--opponent-deck`): a lógica
   está coberta por testes com HTTP mockado; a API principal
   (`api.pokemontcg.io`) chegou a retornar 502 (fora do ar) durante um
   teste manual nesta sessão, mas o CDN de imagens (`images.pokemontcg.io`)
   respondeu normalmente. Ainda não foi feito um teste de ponta a ponta
   completo de "importar uma decklist real" — vale testar quando a API
   principal estiver disponível.
2. **UI gráfica**: testada de verdade com `pytest-qt` rodando headless
   (`QT_QPA_PLATFORM=offscreen`) — vários screenshots reais foram
   renderizados e inspecionados durante o desenvolvimento (inclusive com
   arte real baixada da internet), e as animações foram conferidas quadro a
   quadro (capturas em momentos fixos durante ataque, energia e evolução) —
   foi assim que apareceram e foram corrigidos, por exemplo, o atacante sendo
   desenhado atrás do defensor na investida e um falso "+30" de cura na
   evolução. Não há som (o "game feel" aqui é só visual). Ainda
   assim, vale abrir a janela de verdade (`uv run python -m
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
│   │   ├── tcgdex_client.py         # fonte alternativa (TCGdex), usada quando a pokemontcg.io cai
│   │   └── lookup.py                # CardLookup (Protocol) + FallbackLookup
│   ├── engine/                      # Fase 1 — completo
│   │   ├── game_state.py, actions.py, rules.py, status_conditions.py, turn_manager.py
│   │   ├── history.py               # Fase 5 — MatchRecorder (JSON)
│   │   └── effects/                 # registry.py + basic_effects.py (1 exemplo registrado)
│   ├── ai/                          # Fase 1 — completo
│   │   ├── opponent.py (Protocol + build_ai), heuristics_easy/medium/hard.py, weights.yaml
│   ├── ui/                          # tabuleiro em QGraphicsScene
│   │   ├── app.py                   # MainWindow, BattleView, BattleController (input → ações)
│   │   ├── battle_scene.py          # layout do tapete + sync animada estado→itens + efeitos
│   │   ├── items.py                 # token de Pokémon, carta da mão, pilhas, botões, partículas...
│   │   ├── anim.py                  # helpers de animação + fila de passos (velocidade 0 nos testes)
│   │   ├── art.py                   # arte oficial (PokeAPI) + orbes de energia/verso vetoriais
│   │   ├── theme.py                 # paleta por tipo, cores de destaque, fonte
│   │   ├── confirmation_dialog.py   # Fase 4 — confirmação de reconhecimento incerto
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
