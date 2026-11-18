# Pokémon Companion

Treine Pokémon Estampas Ilustradas (TCG) contra uma IA, com as regras oficiais
e os decks do formato Standard atual. Feito em Python e Qt para Windows, macOS
e Linux (até aqui testado no macOS), sem conta e sem internet depois da
primeira abertura de cada deck.

- **Regras completas**: turno, Apoiador por turno, Estádios, Ferramentas,
  Habilidades, energias especiais, condições especiais, Fraqueza/Resistência,
  Morte Súbita. Os efeitos de ~99% dos textos do Standard (marcas H–J) estão
  implementados, parte escrita à mão e parte compilada do texto em inglês da
  carta.
- **IA em três níveis** (fácil, média, difícil) e modo espectador (IA × IA).
- **Os 25 decks do meta** vêm prontos; importe qualquer lista do
  [Limitless](https://limitlesstcg.com) ou monte a sua no construtor de deck.
- **Tabuleiro animado** com efeitos sonoros por tipo de ataque e de energia,
  música, replays, estatísticas e dicas para quem está começando.
- **Câmera (experimental)**: módulos para reconhecer as cartas de uma mesa
  física; ainda não ligados à partida.

## Jogar

Requer [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m pokemon_companion.ui.app
```

Controles: arraste cartas da mão para o campo (ou clique para escolher o
destino), clique nos botões de ataque, recuo e passar o turno. **Esc** abre o
menu da partida, **F1** as regras, **M** liga/desliga o som.

Outros modos:

```bash
# IA contra IA
uv run python -m pokemon_companion.ui.app --spectate --difficulty hard \
  --player-deck src/pokemon_companion/decks/originais/mega_charizard_x.txt \
  --opponent-deck src/pokemon_companion/decks/originais/mega_gardevoir.txt

# Partida pelo terminal
uv run pokemon-companion --player-deck MEU_DECK.txt
```

Os releases do GitHub trazem o app para Windows, macOS (Apple Silicon) e
Linux; no Linux, rode `./install.sh` de dentro da pasta extraída para pôr o
app no menu, com ícone. Todos os comandos estão em [Comandos](#comandos).

## Desenvolver

```bash
uv sync                   # dependências em .venv
uv run pytest -q          # testes (a UI roda sem tela, em modo offscreen)
uv run black --check src tests tools
uv run ruff check src tests tools
uv run mypy src           # tipos (o CI roda os quatro)
```

O [PLANO.md](PLANO.md) descreve a arquitetura, as decisões e o que falta.
Efeitos novos devem ser mecanismos genéricos (uma frase de texto → um passo
do compilador em `engine/effects/text_effects.py`), nunca código para uma
carta específica.

## Comandos

Todos rodam da raiz do projeto com `uv run`. Cada script de `tools/` também
aceita `--help`, exceto `publish_data.py` e `make_icon.py`, que não leem
argumentos e executam direto.

### Jogar

| Comando | O que faz |
|---|---|
| `python -m pokemon_companion.ui.app` | Abre o app na tela de seleção de decks |
| `… ui.app --player-deck A.txt --opponent-deck B.txt --difficulty hard` | Vai direto para uma partida (`easy`, `medium`, `hard`) |
| `… ui.app --spectate --player-difficulty hard --speed 2` | IA contra IA; `--speed` acelera as animações |
| `… ui.app --record-history partida.json` | Grava as ações da partida em JSON |
| `pokemon-companion --player-deck A.txt --opponent-deck B.txt` | Partida pelo terminal (sem deck: deck de demonstração) |

Decks prontos: `src/pokemon_companion/decks/top/` (meta atual),
`originais/`, `worlds2026/` e os da raiz de `decks/`.

### Manutenção das cartas

| Comando | O que faz |
|---|---|
| `python tools/maintenance.py` | Relatório de cobertura e legalidade com os dados já baixados (`data/maintenance_report.md`); avisa set lançado que ainda não entrou na rotação |
| `python tools/maintenance.py --refresh` | Antes do relatório, baixa de novo a rotação (TCGdex), o catálogo e os 25 decks do meta |
| `python tools/effect_coverage.py PASTA` | Textos sem efeito nos decks de uma pasta |
| `python tools/compile_report.py [--top N] [--explain N]` | Frases que o compilador de efeitos ainda não reconhece, por frequência |
| `python tools/fetch_top_decks.py --top 25 --clean [--out PASTA]` | Baixa os decks do meta do Limitless; `--clean` remove os que saíram do ranking |
| `python tools/publish_data.py` | **Publica no GitHub** (release `data`) a rotação, o catálogo, as reescritas e os decks do meta, que o app instalado baixa ao abrir |

### IA e simulação

| Comando | O que faz |
|---|---|
| `python tools/tournament.py PASTA [--games N] [--level hard] [--workers N] [--seed N] [--out PASTA]` | Torneio IA contra IA entre todos os decks da pasta, em paralelo; resumo de vitórias, deck-outs e erros |
| `python tools/replay_top_cut.py [--tournament ID] [--labs ID] [--series N] [--level hard]` | Reproduz o top cut de um torneio real e compara com quem venceu (padrão: Mundial 2026) |

### Recursos e empacotamento

| Comando | O que faz |
|---|---|
| `python tools/make_sounds.py [nomes] [--play]` | Gera os efeitos sonoros por síntese (`--play` toca no macOS) |
| `python tools/make_music.py [nomes] [--play]` | Gera as músicas de fundo |
| `python tools/make_icon.py` | **Regera** o ícone em todos os tamanhos (PNG, `.icns`, `.ico`) |
| `pyinstaller --noconfirm packaging/pokemon_companion.spec` | Executável para o sistema atual (os releases `vX.Y.Z` saem do GitHub Actions para Windows, macOS e Linux) |

## Licença e aviso

Código sob a [GNU GPL v3 ou posterior](LICENSE).

Projeto de fã, **não oficial** e sem fins lucrativos. Pokémon, os nomes, os
textos e as imagens das cartas são marcas e propriedade de Nintendo, Creatures
Inc., GAME FREAK inc. e The Pokémon Company; este projeto não é afiliado nem
endossado por elas. Os dados das cartas vêm das APIs públicas
[pokemontcg.io](https://pokemontcg.io) e [TCGdex](https://tcgdex.dev), e as
artes dos Pokémon da [PokéAPI](https://pokeapi.co); nada disso é distribuído
junto com o código.
