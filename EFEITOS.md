# Como montar efeitos de carta

Guia para qualquer pessoa ou LLM que for implementar efeitos de carta neste
motor. Leia inteiro antes de mexer em `src/pokemon_companion/engine/effects/`.

## As três regras que não se negociam

1. **Nenhum código para uma carta específica no motor compartilhado.** Um
   efeito novo é um *mecanismo* (uma forma de frase, uma condição, uma
   contagem) que funciona para toda carta com a mesma redação, inclusive as de
   sets futuros. Nada de `if card.name == "..."` em `text_effects.py`,
   `passive_text.py`, `passives.py`, `core.py`, `rules.py` ou `wording.py`.
2. **Carta nova chega como dado, nunca como código.** O app instalado não
   recebe código: recebe `effects.json` (e catálogo/legalidade) pelo manifesto
   publicado (`cards_db/updates.py`, `tools/publish_data.py`). Se o texto de
   uma carta nova tem redação estranha, a correção é uma *reescrita* no
   `effects.json` para uma frase que o compilador já conhece.
3. **Texto não reconhecido fica sem efeito, nunca com um efeito parecido.**
   Um ataque/Treinador/Habilidade só compila quando **todas** as frases são
   reconhecidas. Se uma frase não casa, devolva `None` e deixe a carta como
   "sem efeito" (a UI avisa o jogador). Errar o significado é pior que faltar.

## Onde cada coisa mora (ordem de prioridade)

| Camada | Arquivo | Para quê |
| --- | --- | --- |
| Registro à mão | `attacks.ATTACKS`, `abilities.ABILITIES`, `trainers.TRAINERS` / `STADIUMS`, ramos com nome em `passives.py` | Só casos singulares que nenhuma frase genérica cobre. Todo nome citado no código precisa estar em `passive_text.HAND_WRITTEN` (`tests/test_hand_written.py` confere). Evite: é a última opção. |
| Compilador de frases | `text_effects.py` | Ataques, Treinadores (Item/Apoiador), Estádios e Habilidades ativas. Cada frase vira `Step`s. |
| Passivas | `passive_text.py` | Habilidades e Ferramentas contínuas (dano recebido/causado, HP, recuo, travas). Cada frase vira `Passive` (dados). |
| Redação antiga | `wording.py` | Traduz a redação de Black & White a Sol e Lua para a atual ("the Defending Pokémon", "his or her", "Does 30 damage", "times the number of heads"). Só é usada quando a frase original não é reconhecida. |
| Classificação | `cardinfo.py` | Perguntas sobre a carta: Regra, Tera, ACE SPEC, Prism Star, Pokémon V, energia que uma Especial fornece (`special_energy_units`). |
| Dados publicados | `effects.json` (`pack.py`) | `rewrites` (texto original → texto que compila) e `groups` (marcas que a API não traz: Tera, Ancient, Future, ACE SPEC). |
| Cobertura | `coverage.py` | `unimplemented(card)`: a única resposta para "esta carta tem efeito?" (construtor, carta ampliada, relatórios). |

## Fluxo de trabalho

1. **Meça antes.** Descubra as frases que mais faltam:

   ```bash
   uv run python tools/compile_report.py --top 80                    # Standard
   uv run python tools/compile_report.py --format expanded --top 80  # Expanded
   uv run python tools/maintenance.py                                # relatório completo
   ```

   O `compile_report` agrupa frases por forma (números viram `N`, ícones viram
   `{X}`) e também *executa* cada efeito compilado num estado sintético: a
   linha "Execuções com erro" tem de continuar em 0.
2. **Teste a frase moderna primeiro.** Muitas frases "novas" são só redação
   diferente de algo que já existe. Teste rápido:

   ```bash
   uv run python -c "from pokemon_companion.engine.effects import text_effects as te; print(te.parse_sentence('Discard an Energy attached to this Pokémon'))"
   ```

   Se a forma atual compila, a solução é uma regra em `wording.py` (redação
   antiga, várias cartas) ou um `rewrite` no `effects.json` (uma carta nova
   com redação torta). Não escreva frase nova para isso.
3. **Escolha o menor mecanismo.** Na ordem: reescrita → condição/contagem/tipo
   novo (que destrava muitas frases de uma vez) → frase nova → registro à mão.
4. **Escreva um teste por mecanismo** (`tests/test_text_effects.py`,
   `tests/test_passive_text.py`, `tests/test_expanded_effects.py`...). O teste
   usa um texto realista e confere o efeito no estado do jogo, não só que
   compilou.
5. **Meça depois.** A cobertura do Standard não pode cair (hoje 100% dos
   ataques); rode um torneio de fumaça para pegar erros em partida:

   ```bash
   uv run python tools/tournament.py src/pokemon_companion/decks/top --games 2 --level medium
   ```

6. **Tudo verde antes do commit:**

   ```bash
   uv run black --check src tests tools && uv run ruff check src tests tools && uv run mypy src && uv run pytest -q
   ```

## Como escrever cada peça em `text_effects.py`

- **Frase** — `@phrase("padrão", "variante", ...)`. O padrão é casado com
  `fullmatch`, sem diferenciar maiúsculas. Tokens: `{N}` número (ou "a",
  "two"...), `{E}` ícone de energia (`{R}` ou `[R]`), `{X}` texto livre,
  `{S}` condição especial. A função recebe os grupos e devolve `Step`,
  `list[Step]` ou `None` (não reconheço → a frase fica desconhecida).
- **Fases do `Step`** — `pre(...)`: moedas, portões ("If tails, this attack
  does nothing") e mudanças no dano; `Step("before", ...)`: antes do dano
  ("Before doing damage, ..."); `after(...)`: o resto, na ordem do texto.
  Alvo escolhido pelo jogador vira `option` ("opp_bench", "own_bench",
  "opp_any").
- **Estado da execução (`Run`)** — `run.me`, `run.opp`, `run.source` (o
  Pokémon que ataca/usa), `run.defender`, `run.heads`/`run.tails`,
  `run.did` ("If you do, ..."), `run.counted` ("for each card you discarded in
  this way"), `run.last_target` ("that Pokémon"), `run.picked` (cartas
  olhadas). Use esses campos para ligar uma frase à seguinte, em vez de
  procurar a carta pelo nome.
- **Condição** — `@condition("your opponent's Active Pokémon is ...")` devolve
  um `Predicate` (`Run -> bool`). Vale em "If <condição>, <frase>" e em "You
  can use this card only if <condição>".
- **Contagem** — `@count("of your Benched Pokémon")` devolve `Run -> int`.
  Vale em "for each ...".
- **Tipo de carta** — `@kind("Pokémon with {N} HP or less")` devolve
  `Card -> bool`. Vale em buscas, descartes e "reveal a ... you find there".
- **Frases compostas** já são tratadas: "If heads, ...", "For each heads,
  ...", "If you do, ...", "Then, ...", "You may ...", "Before doing damage,
  ...", ", and ...". Não registre a frase inteira com o prefixo; registre só o
  miolo.
- **Escolhas da IA** são heurísticas dentro do `act` (o alvo mais danificado,
  o Básico mais frágil do oponente, a primeira opção de "Choose 1:"). Quando
  simplificar, deixe um comentário `# ponytail:` dizendo o limite e como
  melhorar.

## Passivas (`passive_text.py`)

`@rule("padrão")` com regex direta (sem os tokens acima) devolvendo
`Passive` ou `list[Passive]`. A `Passive` é só dados (tipo, escopo, valor,
filtro); quem aplica é `passives.py`, nos pontos onde as regras já consultam
Habilidades. Para um tipo de passiva novo, acrescente o tipo na `Passive` e a
consulta no ponto certo de `passives.py`, nunca um ramo com nome de carta.

## Redação antiga (`wording.py`)

Cada entrada de `OLD_WORDING` troca uma *forma* (`regex` → texto ou função),
nunca um nome de carta. `modernize(frase)` aplica todas; `parse_sentence`
tenta a frase original, depois a moderna e, por último, a moderna com "the
Defending Pokémon" trocado por "your opponent's Active Pokémon". Antes de
acrescentar uma regra, confira que a frase moderna resultante compila e que a
cobertura do Standard continua igual.

## Energias Especiais

Energia cujo texto só diz o que fornece ("Double Colorless Energy provides
{C}{C} Energy", "While in play, this card provides every type of Energy but
provides only 1 Energy at a time") funciona pelo próprio texto
(`cardinfo.special_energy_units`, consultado por `passives.provided_energy`).
Qualquer outra frase no texto faz a função devolver `None`: a carta fica
marcada como sem efeito até ganhar um mecanismo.

## Dados publicados (`effects.json`)

- `rewrites`: `{"texto original exato": "texto que compila"}`. Use para uma
  carta nova com redação que nenhuma frase conhece, apontando para frases já
  existentes.
- `groups`: assinaturas (`nome|ataques|Habilidades`) com marcas que a API não
  traz. `tools/maintenance.py --refresh` atualiza Tera/Ancient/Future/ACE SPEC.
- Se uma reescrita publicada depender de frase nova no compilador, suba
  `updates.APP_DATA_VERSION` para o app antigo não baixar dados que não
  entende.

## Antes de entregar

- [ ] Nenhum nome de carta no código compartilhado (só em `ATTACKS`/`TRAINERS`/
      `ABILITIES`/`HAND_WRITTEN`, e só se não houver mecanismo possível).
- [ ] Um teste por mecanismo novo, conferindo o efeito no estado.
- [ ] `compile_report` sem erros de execução; Standard sem queda de cobertura.
- [ ] Torneio de fumaça sem erros.
- [ ] black, ruff, mypy e pytest verdes.
- [ ] CHANGELOG ("Não lançado") e PLANO.md atualizados; commit em português.
