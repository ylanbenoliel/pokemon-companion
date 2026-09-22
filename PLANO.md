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
uv run pytest -v        # confirma que tudo continua passando (233 testes)
uv run mypy src          # type-check
uv run black --check . && uv run ruff check .   # formatação/lint

# CLI (deck mockado por padrão):
uv run python -m pokemon_companion.main --difficulty medium

# CLI com decklists reais (precisa de internet; formato Limitless/PTCGO):
uv run python -m pokemon_companion.main --player-deck data/decks/meu_deck.txt --opponent-deck data/decks/deck_da_ia.txt --record-history data/partida1.json

# Tabuleiro gráfico (PyQt6): sem argumentos abre a tela de seleção de decks
# (seu deck × deck da IA × dificuldade, estilo TCG Pocket; Esc volta ao menu):
uv run python -m pokemon_companion.ui.app
# Direto numa partida, sem passar pelo menu:
uv run python -m pokemon_companion.ui.app --difficulty hard \
  --player-deck examples/decks/top/01_dragapult_ex.txt \
  --opponent-deck examples/decks/worlds2026/1_andrew_hedrick_dragapult.txt

# Modo espectador: duas IAs no difícil com decks competitivos (Regional de Baltimore, 09/2026):
uv run python -m pokemon_companion.ui.app --spectate --difficulty hard --player-difficulty hard \
  --player-deck examples/decks/dragapult_ex.txt --opponent-deck examples/decks/ns_zoroark_ex.txt \
  --speed 1.5 --record-history data/partida.json

# Torneio IA vs IA (round robin, sem interface, paralelo) com os 25 decks do meta:
uv run python tools/tournament.py examples/decks/top --games 4 --level hard
# Quais textos de cartas dos decks ainda não têm efeito implementado:
uv run python tools/effect_coverage.py examples/decks/top
# Atualizar os decks do meta a partir do limitlesstcg.com (o ranking tem 25):
uv run python tools/fetch_top_decks.py --top 25
# Reproduzir o top cut de um torneio real (padrão: Mundial 2026):
uv run python tools/replay_top_cut.py --series 40
```

## Status — todas as 5 fases do plano original têm código funcional

- ✅ **Fase 0 — Setup**: `uv`, layout `src/`, `black`+`ruff`+`mypy` (+`pytest-qt` para testar a UI sem display real via `QT_QPA_PLATFORM=offscreen`).
- ✅ **Fase 1 — Motor de regras + IA**: completo, testado, jogável via CLI.
- ✅ **Fase 2 — Banco de cartas + decklist**: API `pokemontcg.io` + cache SQLite + parser Limitless/PTCGO.
- ✅ **Fase 3 — UI gráfica (PyQt6)**: tabuleiro de jogo em `QGraphicsScene` (ver "Tabuleiro estilo Hearthstone/TCG Pocket" abaixo). Testado headless com `pytest-qt` e validado por screenshots/quadros de animação renderizados offscreen.
- ✅ **Fase 4 — Visão computacional (building blocks)**: captura de câmera multiplataforma, calibração por homografia, mapeamento de zonas, detecção de ocupação/estabilidade, reconhecimento por pHash, download+cache de imagens de carta, e um widget de calibração/debug. **Todos os módulos têm testes unitários com dados sintéticos** (sem precisar de câmera real).
- ✅ **Fase 5 — Polimento (parcial, ver detalhes)**: prêmios diferenciados por raridade (ex/GX/V=2, VMAX/VSTAR=3), motor de efeitos completo para o meta atual (ver "Regras completas" abaixo), histórico de partida exportável em JSON (`--record-history`).
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

125 testes passando (`uv run pytest`), `black`/`ruff`/`mypy` limpos.

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

- ✅ **Revisão contra o livro de regras oficial (Paradox Rift, SV)**:
  implementado o que faltava — quem começa não ataca no 1º turno; ninguém
  evolui no próprio 1º turno; todo turno começa com compra (inclusive o 1º);
  moeda decide quem começa; mulligan dá compra extra ao outro jogador;
  Checkup na ordem oficial (Envenenado → Queimado → Adormecido →
  Paralisado), com Adormecido checado em todo checkup e Paralisado se
  recuperando após o turno do dono (antes nunca se recuperava); evoluir ou
  ir para o banco remove condições especiais; novo ativo após nocaute
  escolhido por heurística (pronto para atacar > mais energia > mais HP);
  validação de deck (60 cartas, máx. 4 cópias, ≥1 Básico, 1 ACE SPEC, 1
  Radiante). Não existe limite de cartas na mão no TCG — correto não haver
  descarte. **Ainda faltam**: efeitos de Treinador (Item/Apoiador com limite
  de 1 por turno/Estádio/Ferramenta), Habilidades, Energias Especiais,
  efeitos de texto de ataques, escolha manual do novo ativo e montagem do
  banco no setup, morte súbita.
- ✅ **IA: correção da avaliação**: o termo "dano causado" contava contadores
  de dano ainda em campo, então nocautear (o Pokémon vai para o descarte)
  "apagava" o dano e um KO pontuava negativo — a IA passava a vez podendo
  atacar (~188 vezes em 10 jogos). Agora a avaliação usa HP restante. Com os
  decks competitivos: 11 de 12 partidas terminam por prêmios/nocaute (antes
  9 de 10 iam a deck-out) e "podia atacar e passou" caiu para 12.
- ✅ **Treinadores com imagem e texto**: buscados na API (TCGdex) para exibir
  a ilustração recortada, o tipo (Item/Apoiador/Estádio/Ferramenta) e o
  texto no painel de detalhes, com o aviso de que o efeito ainda não é
  aplicado; se a busca falhar, viram carta local só com nome, sem erro.
- ✅ **Importar deck sem sair do app** (`cards_db/limitless.py` +
  `ui/deck_import.py`): botão "Importar deck" na tela inicial abre um
  diálogo com três caminhos — procurar o arquétipo pelo nome no Limitless
  (baixa a melhor lista publicada), colar o link de uma lista/arquétipo, ou
  colar o texto da decklist. O HTML é lido por regex (o site não tem API
  pública), então o parsing é testável sem rede; só `fetch()` faz requisição
  de verdade, isolado por injeção nos testes. O deck importado é salvo em
  `data/decks/` e a tira da tela inicial se atualiza na hora, já selecionado
  para o lado armado.

## Regras completas e motor de efeitos (09/2026)

Revisão contra o livro de regras oficial, tudo implementado e testado
(`tests/test_effects.py`):

- **Treinadores**: 1 Apoiador por turno, nenhum no 1º turno de quem começa
  (exceto Team Rocket's Proton); Itens podem ser bloqueados (Itchy Pollen);
  ACE SPEC bloqueável (ACE Nullifier); **Estádio** em jogo (substitui o
  anterior, 1 por turno, efeito "uma vez por turno" via `UseStadium`);
  **Ferramentas** anexadas (1 por Pokémon).
- **Habilidades** ativadas, gatilhos "ao jogar no banco"/"ao evoluir" (como
  ação opcional no turno) e passivas (efeitos contínuos em `passives.py`);
  limites "não mais de 1 Habilidade X por turno".
- **Energias especiais** (Legacy, Neo Upper, Team Rocket's, Mist, Spiky,
  Enriching, Boomerang, Ignition, Telepathic/Growing/Rocky tipadas).
- **Efeitos de texto dos ataques**: dano variável, moedas, condições,
  contadores no banco, dano em alvo escolhido, recuo, "não pode atacar no
  próximo turno", cópia de ataques (Night Joker, Seek Inspiration...).
- **Nocaute de qualquer Pokémon** (banco inclusive) com descarte de tudo o
  que estava anexado e prêmios ajustados (Lillie's Pearl, Legacy, Briar).
- **Escolhas do humano**: montagem do setup (Ativo + Banco, botão PRONTO) e
  escolha do novo Ativo após nocaute (`state.manual_choices`,
  `rules.decision_player`). A IA decide por heurística.
- **Regra Tera**: Pokémon Tera no Banco não recebem dano de ataques (mas
  contadores de dano colocados por efeitos, como Phantom Dive, continuam
  valendo). A API não marca Tera: a lista está em `cardinfo.TERA_NAMES`.
- **Mega Evolução ex** (era 2025/2026): evoluem normalmente e podem atacar
  no mesmo turno; valem 3 prêmios.
- **Morte Súbita** quando os dois vencem ao mesmo tempo.
- Não existe limite de cartas na mão (confirmado no livro de regras).

Arquitetura: `engine/effects/` — `core.py` (primitivas: comprar, buscar,
trocar, dano com Fraqueza/Resistência e prevenções), `attacks.py`,
`abilities.py`, `trainers.py` (registros por nome), `passives.py`,
`cardinfo.py` (Tera/Antigo/Futuro por nome). Escolhas estratégicas viram
alvos da ação (`target`), para a IA avaliar cada opção e a UI mostrar
destaques/painel. `tools/effect_coverage.py` mostra 100% dos textos das
cartas dos 25 decks do meta cobertos (todo o ranking do Limitless).

### Direção visual (skill `frontend-design`, .claude/skills/)

A tela é a mesa de torneio sob a luz do abajur: tapete de feltro verde-pinho
com a trama desenhada, poça de luz quente vinda de cima, cartas em cartolina
creme e tinta escura. A cor forte vem de onde ela existe no jogo: o **tipo do
Pokémon Ativo**, que tinge o lado do tabuleiro e o arco central, e o **brilho
holográfico** das cartas com regra especial (ex/Mega). Botões são placas
físicas com relevo, não pílulas de vidro; os rótulos são frases em caixa
normal ("Passar o turno", "Você", "6 prêmios").

Tipografia empacotada em `ui/fonts/` (licença OFL junto): **Archivo Black**
para números e títulos, **Barlow** para o resto — mesma cara nos três
sistemas. Tokens de cor e as duas fontes ficam em `ui/theme.py`.

Cada carta de Treinador mostra **o que faz, em português**, na própria carta
e no painel de detalhes; as traduções curtas estão em
`engine/effects/descriptions.py` (uma linha por carta, Habilidades
incluídas), com o texto original em inglês abaixo. Carta cujo efeito o motor
ainda não aplica avisa isso no painel.

Tela inicial (`ui/deck_menu.py`): o confronto em si — seu deck e o da IA
frente a frente em tamanho de carta, com a marca "VS" nas cores dos dois
tipos, e uma tira de miniaturas embaixo (filtrada por grupo: meta atual,
Mundial, exemplos, seus decks). Clicar num dos lados diz qual deles a próxima
escolha troca. A arte vem do cache local (abre rápido e offline) e as cartas
são importadas numa thread à parte para a tela não travar.

UI: Treinadores soltos no tabuleiro ou sobre o Pokémon alvo; painel de
escolha para opções que não são um Pokémon; selo "HAB." nos Pokémon com
Habilidade disponível (clique para usar); carta do Estádio à direita
(brilha quando pode ser usada); Ferramenta como etiqueta no token.

### Meta completo: decks 21–25 (09/2026)

O ranking de arquétipos do Limitless tem só 25 decks; os 20 primeiros já
estavam 100% cobertos, mas os 5 restantes (Mega Absol Box, Mega Chandelure
ex, Mega Starmie ex, Mega Kangaskhan ex, Greninja ex) tinham 34 textos sem
efeito (~23% das cartas). Todos implementados. Mecanismos novos, genéricos
(não amarrados a uma carta):

- **Revide** (`PokemonInPlay.retaliation` + `attacks.retaliate_next_turn`):
  "no próximo turno do oponente, se sofrer dano de ataque, N contadores no
  atacante" — aplicado em `core.deal_damage`.
- **Redução contínua de dano** (`passives.static_damage_reduction`, Curly
  Wall), **recuo maior do Ativo adversário** (Binding Flame em
  `passives.retreat_cost`) e **bônus contra Pokémon com Habilidade**
  (Compound Eyes em `passives.attacker_bonus`).
- Helpers de ataque: `discard_own_energy`, `attach_from_deck`,
  `opp_hand_damage`; valores lidos do texto da carta (`number_in_text`) para
  que versões diferentes do mesmo efeito reusem a mesma função (Shadow
  Bullet/Jetting Blow, Psychic/Ear Force...).
- Bubbly Water Energy fornece {W} e protege/cura Pokémon de Água.

Torneio só entre os 5 novos (40 partidas, IA difícil): 0 erros, Mega Starmie
81% e Mega Chandelure 25%.

## Manutenção das regras: rotação do Standard e rotina semanal

`cards_db/standard.py` baixa da TCGdex todas as cartas legais no Standard
(`legal.standard=true`) e grava as assinaturas legais em
`cards_db/standard_legal.json` (versionado). Ao carregar um deck, carta fora
da rotação vira aviso (não bloqueia). `tools/maintenance.py --refresh`
atualiza pool + meta, mede a cobertura e grava `data/maintenance_report.md`;
sai com código 1 se o meta tiver texto sem efeito ou carta fora da rotação.

Situação em 22/09/2026 (marcas G–J, 3345 impressões, 1409 nomes): meta com
0 textos faltando e 0 cartas fora da rotação; pool legal com 881 de 3636
textos-impressão cobertos — faltam ~1460 efeitos distintos (1125 ataques,
218 Habilidades, 97 Treinadores, 16 Estádios, 6 energias especiais).

**Lote 1 (22/09/2026)**: ~50 ataques com 5+ impressões, por *modelos de
texto* em `attacks.py` (uma função por forma de frase: condição especial com
moeda opcional, descartar N/tipo/todas as energias próprias, dano por moeda,
cura, redução de dano, recuo, Hide, Round, dano por energia de um tipo...).
`number_in_text`/`energy_in_text`/`effect_happens` leem valores, ícones
`{W}`/`[W]` e "Flip a coin. If heads," do texto; versões sem texto do mesmo
ataque só causam dano. Cobertura do pool: 881 → 1239 de 3636
textos-impressão; torneio de 600 partidas com os 25 decks, 0 erros.

**Rotina agendada (pendente: precisa do repositório no GitHub)** — segunda
às 9h de Belém (`0 12 * * 1` UTC), lote de ~40 efeitos por PR. Prompt:

> Você é a rotina semanal de manutenção das regras do projeto
> pokemon_companion (Pokémon TCG em Python; use `uv`; o PLANO.md explica o
> projeto). 1) `uv sync` e `uv run python tools/maintenance.py --refresh`. Se o
> download da TCGdex ou do Limitless falhar, não improvise: pare e explique o
> erro. 2) Leia `data/maintenance_report.md`. Prioridade: (a) textos sem
> efeito nos decks do meta e cartas fora da rotação neles; (b) depois, até
> somar ~40 efeitos, textos da seção "Faltando no pool legal", começando
> pelos que aparecem em mais impressões. 3) Implemente em
> `src/pokemon_companion/engine/effects/`: ataques em `attacks.py`
> (`@attack`), Habilidades ativadas em `abilities.py`, Treinadores e
> Estádios em `trainers.py`, efeitos contínuos em `passives.py`, e uma linha
> em português por Treinador/Habilidade em `descriptions.py`. Nunca coloque
> nome de carta ou número fixo em código compartilhado (`core.py`, funções
> gerais de `passives.py`): crie um mecanismo genérico. Leia valores do
> texto com `number_in_text` e, quando o efeito for igual ao de uma carta já
> implementada, só acrescente o nome ao decorator. Escolhas estratégicas
> viram `options`; o resto é heurística. 4) Teste cada mecanismo novo em
> `tests/test_effects.py`. Tudo verde antes do PR: `uv run black . && uv run
> ruff check . && uv run mypy src && uv run pytest -q`; `uv run python
> tools/effect_coverage.py examples/decks/top` com 0 faltando; `uv run
> python tools/tournament.py examples/decks/top --games 2 --level hard` sem
> erros. 5) Atualize os números desta seção do PLANO.md. 6) Commits em
> português, numa branch nova; abra um PR para `main` com a cobertura antes e
> depois e a lista do que foi implementado. Nunca faça push na `main` nem
> force push. Se não houver nada a fazer, não abra PR.

## Torneio IA vs IA — top 20 do meta (resultados e ajustes)

`tools/tournament.py` joga um round robin headless (IA difícil nos dois
lados, 4 partidas por par alternando quem começa, 1 processo por núcleo) e
grava `data/tournament/*/results.json` + `summary.txt`.

| Rodada | Partidas | Erros | Decididas | Deck-out | Quem começa vence | Faixa de vitória |
|---|---|---|---|---|---|---|
| 1ª (motor novo) | 380 | 54 | 326 | 63 | 55% | 18% – 71% |
| 2ª | 380 | 0 | 380 | 18 | 52% | 18% – 74% |
| final | 760 | 0 | 760 | 42 | 51% | 33% – 72% |
| final (com os ajustes do Mundial) | 760 | 0 | 760 | 27 | 53% | 30% – 75% |

Final: média de 18 turnos por partida (≈ 9–10 de cada jogador); decisão
da IA com média de 65 ms (p99 0,4 s). Topo: Crustle 72%, Cynthia's
Garchomp 68%, Basic Box 67%, Lillie's Clefairy 66%. Fundo: Festival Lead,
Toxtricity e Team Rocket's Honchkrow com 33–34%.

Ajustes feitos a partir das partidas:
1. **Bugs**: índice da mão do Rare Candy (a própria carta sai da mão antes
   do efeito; o alvo agora usa o nome do Estágio 2) e Rapid Vernier
   usável depois de o Pokémon já ter ido para o Ativo.
2. **IA comprava até o deck-out** (17% das partidas): penalidade
   quadrática abaixo de 12 cartas no deck e limite de 8 cartas no valor da
   mão (deck-out caiu para 5%).
3. **IA deixava de nocautear** (efeito horizonte): a IA difícil só avaliava
   depois da resposta do oponente, e o oponente simulado (guloso, 1 passo)
   promove um atacante pronto quando leva KO, mas não sabe recuar para ele
   sozinho. A avaliação agora é 60% fim do próprio turno + 40% após a
   resposta (`OPPONENT_REPLY_WEIGHT`).
4. **Energias especiais sem a marca "Special" na TCGdex** (Ignition,
   Telepathic Psychic, Growing Grass, Rocky Fighting) viravam energias de
   tipo inexistente: classificação corrigida e efeitos implementados.
5. **Empate infinito com Academy at Night**: devolver uma carta ao topo do
   deck "diminuía" a penalidade de deck acabando e os dois lados travavam.
   O efeito do Estádio agora fica só para o humano.
6. `weights.yaml` passou a ser carregado de fato por `build_ai` (antes era
   ignorado), com os pesos calibrados.

Pontos em aberto: "passou podendo atacar" restante é quase sempre decisão
legítima (ex: não nocautear um Pokémon preso no Ativo sem energia para
recuar); a IA simula com informação completa (vê a mão do oponente e a
ordem do deck); os decks mais fracos no torneio dependem de sequências
longas (Festival Lead com dois ataques, Hide 'n' Sneak contando descarte)
que a busca de um turno não enxerga bem. Pequena fonte de não-determinismo
entre processos: a mesma seed nem sempre reproduz a partida.

## Reprodução do Mundial 2026 (top cut real vs simulação)

`tools/replay_top_cut.py` baixa do Limitless as listas do Top 8 e a chave de
eliminação com os vencedores reais (Limitless Labs, páginas de pairings) e
simula cada confronto em melhores de 3. **Não existe registro jogada a
jogada** de partidas físicas: o Mundial só está em vídeo, então o que dá
para reproduzir são os confrontos, não os lances.

Mundial 2026 (San Francisco, 28–30/08/2026, 797 jogadores, formato
TEF–Pitch Black): campeão Andrew Hedrick (Dragapult); as 8 listas ficam em
`examples/decks/worlds2026/` (o 5º e o 6º colocados usaram a mesma lista, é
assim no Limitless).

Medida mais estável que "acertou o vencedor" (7 confrontos em melhor de 3
têm muito ruído, ainda mais no espelho Dragapult × Dragapult): um round
robin entre os 8 decks (8 partidas por par) comparado à classificação real
por correlação de Spearman.

| Versão da IA | Acertos na chave | Round robin: 1º lugar | Spearman |
|---|---|---|---|
| antes dos ajustes | 3/7 | Crustle (real 6º) | — |
| + progresso de energia | 5/7 | — | — |
| + estimativa de dano | 3/7 (ruído) | **Dragapult do campeão** (68%) | **+0,26** |
| (sem estimativa, controle) | — | Crustle (real 6º) | +0,17 |

Ajustes que saíram daqui:
1. **Progresso de energia** (`attack_progress`): a avaliação passou a medir,
   para cada Pokémon (banco inclusive), quanto da energia do seu melhor
   ataque já está paga, ponderado pelo dano. Sem isso a IA espalhava energia
   e o atacante principal nunca ficava pronto — o Dragapult ex passou a
   partida inteira com uma Psychic e nunca usou Phantom Dive.
2. **Estimativa de dano por ataque** (`AttackSpec.estimate`): ataques com
   número variável ("20× cartas na mão") valiam 60 fixos para a IA, o que
   tornava o Alakazam (Powerful Hand) e outros decks de escala invisíveis.
3. **6 cartas novas das listas do Mundial** implementadas (Enhanced Hammer,
   Strange Timepiece, Fighting Wings, Watchful Eye, Teleporter,
   Electromagnetic Sonar) — `tools/effect_coverage.py` mostra 0 faltando.

O que a simulação ainda erra: superestima Crustle (parede contra ex) e
subestima Alakazam. Os dois dependem de planejamento de vários turnos
(guardar cartas na mão para o Powerful Hand; furar a parede com contadores
no banco) que a busca de um turno não enxerga.

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

- Efeitos de cartas cobrem os 25 decks do meta; cartas fora deles podem não
  ter efeito (Treinador sem registro é jogado sem efeito e avisa no log;
  ataque sem registro causa só o dano base). Rode `tools/effect_coverage.py`
  ao adicionar decks. Tera/Antigo/Futuro são listas por nome em `cardinfo.py`.
- Escolhas internas de efeitos (qual carta buscar, onde colocar contadores)
  são feitas por heurística também para o humano; só as escolhas
  estratégicas (alvos de Boss's Orders, Ferramentas, ataques com alvo...)
  são perguntadas.
- Prêmios: ex/GX/V = 2, VMAX/VSTAR = 3, demais = 1 (implementado na Fase 5;
  raridades mais raras como TAG TEAM ainda contam como 1).
- Reconhecimento de câmera usa a imagem inteira da carta baixada da API
  (não um recorte só da arte) — mais simples, funciona bem com pHash, mas
  exige enquadramento de câmera relativamente consistente.
- Histórico de partida (Fase 5) grava a sequência de ações em JSON; não há
  player de replay visual, só o registro para consulta posterior.
