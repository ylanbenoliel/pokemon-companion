# Changelog

Mudanças que valem para quem joga. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e as versões
seguem [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Novo

- Registro da partida: tecla **L** (ou "Registro da partida" no menu de
  pausa) lista tudo o que aconteceu, agrupado por turno e jogador.
- Clicar num descarte, seu ou do oponente, mostra as cartas dele na ordem em
  que caíram.

## [0.2.0] - 2026-11-18

### Novo

- Cartas em jogo redesenhadas em zonas fixas: nome e tipo no topo, arte sem
  nada por cima, HP como "restante / máximo", status numa faixa própria e a
  lista de Habilidade e ataques com dano — dá para ver de relance quanto o
  Pokémon do oponente bate.
- A Habilidade pronta para usar fica destacada na própria linha, com o nome
  dela (antes era um selo genérico "Habilidade").
- A Ferramenta ligada aparece numa linha própria, com o nome inteiro.
- Pokémon na mão mostram a Habilidade e os ataques com dano.
- Selos de status com texto legível em qualquer cor (Paralisado era branco
  sobre amarelo).
- Efeitos das cartas do set 30C, incluindo Fraqueza ×3, "não pode ser
  curado", cura ao continuar dormindo e moedas em Treinadores.
- Construtor de deck com o catálogo da rotação nova (cartas do 30C).

### Mudado

- A arte vem da imagem oficial da própria carta, recortada na ilustração
  (antes vinha da PokeAPI). As imagens antigas podem ser apagadas da pasta
  `card_art` do app.
- IA difícil mais forte: avalia o dano acumulado no lado do oponente e o
  potencial de nocaute; venceu 59,5% de 390 partidas contra a versão anterior.
- IA calcula o dano de ataques que descartam toda a energia (Steel Burst valia
  60 em vez de 150).
- Downloads tentam de novo quando a rede falha ou o servidor limita pedidos;
  a importação de decks usa a TCGdex primeiro.

### Corrigido

- A partida travava, com a carta parada pequena no meio do caminho, quando o
  mouse passava pela mão logo depois de soltar uma carta no banco.
- Selos de status e "Habilidade" se sobrepunham no mesmo Pokémon.
- Textos de carta da TCGdex com HTML e símbolos de energia (set 30C) agora
  são lidos certo.
- Torneio entre IAs quebrava com ataques emprestados (Memory Helix).

## [0.1.0] - 2026-11-17

Primeiro release: partidas contra a IA (fácil, médio, difícil), modo
espectador, decks do meta, construtor e importação de decks, e executáveis
para macOS, Windows e Linux.

[0.2.0]: https://github.com/ylanbenoliel/pokemon-companion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ylanbenoliel/pokemon-companion/releases/tag/v0.1.0
