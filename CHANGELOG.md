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
- Exportar deck: "Copiar lista" no menu de decks (copia o seu deck) e no
  construtor põe a lista na área de transferência, pronta para colar no
  Pokémon TCG Live ou no Limitless.
- Relógio de turno opcional (Configurações → Partida: 1, 2 ou 3 minutos). O
  tempo aparece no botão de passar o turno, só corre na sua vez (para nas
  animações e na pausa) e, ao zerar, passa o turno sozinho.
- Formato **Expandido** (Black & White em diante, sem as banidas) ao lado do
  Standard. O seletor Standard | Expandido fica no construtor de deck e na
  importação (a busca no Limitless passa a trazer os arquétipos do
  Expandido). O formato fica gravado no deck, que ganha um selo
  "EXPANDIDO" na tela inicial e é validado com as regras dele: só uma cópia
  de cada carta Prism Star (◇), e as ACE SPEC antigas também contam no
  limite de uma. Muitas cartas antigas ainda não têm efeito no jogo.
- No construtor, as cartas do deck mostram ⛔ quando não valem no formato
  escolhido e ⚠ quando algum efeito delas ainda não está implementado; o
  motivo aparece ao passar o mouse. Trocar de formato mantém o deck e só
  marca o que deixou de valer.

- Muito mais cartas do Expandido com efeito: a cobertura de textos subiu de
  68% para 78%. O jogo agora entende a redação antiga das cartas (Black & White
  a Sol e Lua), Energias Especiais que só fornecem energia (Double Colorless,
  Unit Energy) e Treinadores do meta do Expandido como N, Quick Ball, Trainers'
  Mail, Max Elixir, Field Blower, Hypnotoxic Laser, Colress, Acerola, Rescue
  Stretcher e Counter Catcher.

### Corrigido

- Listas antigas importadas do Limitless agora acham as cartas: nomes GX/EX
  com hífen ("Zoroark-GX"), itens da Equipe Flare ("Battle Compressor"),
  Prism Star escrito sem o ◇, promos como "SMP 45" e energia básica com a
  impressão no fim ("7 Psychic Energy SSH P").
- Prêmios das cartas do Expandido: TAG TEAM e V-UNION valem 3 (TAG TEAM
  valia 1), Pokémon BREAK vale 1 (valia 2) e os Mega de XY ("M …-EX") valem 2.
  TAG TEAM, V-UNION, BREAK, Mega de XY, LEGEND e Prism Star contam como
  Pokémon com Regra.

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
