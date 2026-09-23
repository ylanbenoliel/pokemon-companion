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

Executável: `uv run pyinstaller --noconfirm packaging/pokemon_companion.spec`
(gera o app para o sistema em que roda).

## Desenvolver

```bash
uv run pytest             # testes (a UI roda sem tela, em modo offscreen)
uv run black --check .    # formatação
uv run ruff check .       # lint
uv run mypy src           # tipos
uv run python tools/maintenance.py   # cobertura de efeitos e legalidade
```

O [PLANO.md](PLANO.md) descreve a arquitetura, as decisões e o que falta.
Efeitos novos devem ser mecanismos genéricos (uma frase de texto → um passo
do compilador em `engine/effects/text_effects.py`), nunca código para uma
carta específica.

## Licença e aviso

Código sob a [GNU GPL v3 ou posterior](LICENSE).

Projeto de fã, **não oficial** e sem fins lucrativos. Pokémon, os nomes, os
textos e as imagens das cartas são marcas e propriedade de Nintendo, Creatures
Inc., GAME FREAK inc. e The Pokémon Company; este projeto não é afiliado nem
endossado por elas. Os dados das cartas vêm das APIs públicas
[pokemontcg.io](https://pokemontcg.io) e [TCGdex](https://tcgdex.dev), e as
artes dos Pokémon da [PokéAPI](https://pokeapi.co); nada disso é distribuído
junto com o código.
