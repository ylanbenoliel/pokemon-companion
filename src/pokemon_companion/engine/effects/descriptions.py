"""Descrições curtas em português do que cada carta faz.

O texto impresso nas cartas vem em inglês da API, e ler um parágrafo no meio
da partida atrapalha. Aqui fica uma linha por carta, no que ela realmente faz
— é o que a interface mostra na mão, no tabuleiro e no painel de detalhes
(`describe_card`); sem tradução cadastrada, cai no texto original da carta.

Também cobre Ferramentas e Estádios cujo efeito é contínuo e as Habilidades,
que aparecem no painel do Pokémon.
"""

from __future__ import annotations

from pokemon_companion.cards_db.models import Card

TRAINERS_PT: dict[str, str] = {
    # Apoiadores
    "Bianca's Devotion": "Cura todo o dano de 1 Pokémon seu com 30 de HP ou menos.",
    "Black Belt's Training": "Neste turno, seus ataques dão +40 no Pokémon ex Ativo do oponente.",
    "Boss's Orders": "Puxa 1 Pokémon do Banco do oponente para o Ativo.",
    "Briar": "Só com o oponente em 2 prêmios: nocaute com Pokémon Tera dá 1 prêmio a mais.",
    "Brock's Scouting": "Busca 2 Pokémon Básicos (ou 1 de Evolução) no deck.",
    "Ciphermaniac's Codebreaking": "Escolhe 2 cartas do deck e as deixa no topo.",
    "Crispin": "Busca 2 energias básicas de tipos diferentes: 1 anexa, 1 vai para a mão.",
    "Cyrano": "Busca até 3 Pokémon ex no deck.",
    "Dawn": "Busca 1 Básico, 1 Estágio 1 e 1 Estágio 2 no deck.",
    "Eri": "Descarta até 2 Itens da mão do oponente.",
    "Explorer's Guidance": "Olha as 6 do topo, pega 2 e descarta o resto.",
    "Gladion's Final Battle": "Só como última carta da mão: +80 de dano com Pokémon sem regra.",
    "Gwynn": "Descarta até 2 Pokémon sem regra da mão e compra 3 por carta descartada.",
    "Hassel": "Só após um nocaute seu: olha as 8 do topo e pega até 3.",
    "Hilda": "Busca 1 Pokémon de Evolução e 1 Energia no deck.",
    "Judge": "Os dois embaralham a mão no deck e compram 4.",
    "Kieran": "Troca o seu Ativo, ou dá +30 de dano contra Pokémon ex e V neste turno.",
    "Lana's Aid": "Recupera até 3 Pokémon sem regra e energias básicas do descarte.",
    "Lillie's Determination": "Embaralha a mão no deck e compra 6 (8 se você tem 6 prêmios).",
    "Pokémon Center Lady": "Cura 60 de dano de 1 Pokémon seu e tira as condições especiais.",
    "Rosa's Encouragement": "Com mais prêmios que o oponente: anexa 2 energias do descarte num Estágio 2.",
    "Surfer": "Troca o seu Ativo e compra até ficar com 5 cartas na mão.",
    "Team Rocket's Archer": "Após um nocaute seu: os dois embaralham a mão; você compra 5, ele 3.",
    "Team Rocket's Ariana": "Compra até 5 cartas (8 se todos os seus Pokémon forem da Equipe Rocket).",
    "Team Rocket's Giovanni": "Troca seus Pokémon da Equipe Rocket e puxa 1 do Banco do oponente.",
    "Team Rocket's Petrel": "Busca qualquer carta de Treinador no deck.",
    "Team Rocket's Proton": "Busca até 3 Básicos da Equipe Rocket (pode ser usada no 1º turno).",
    "Wally's Compassion": "Cura todo o dano de 1 Mega ex seu e devolve as energias dele à mão.",
    "Xerosic's Machinations": "O oponente descarta até ficar com 3 cartas na mão.",
    # Itens
    "Buddy-Buddy Poffin": "Busca até 2 Básicos com 70 de HP ou menos e põe no Banco.",
    "Bug Catching Set": "Olha as 7 do topo e pega até 2 Pokémon/energias de Planta.",
    "Crushing Hammer": "Cara: descarta 1 energia de 1 Pokémon do oponente.",
    "Energy Recycler": "Embaralha até 5 energias básicas do descarte no deck.",
    "Energy Search": "Busca 1 energia básica no deck.",
    "Energy Switch": "Move 1 energia básica de um Pokémon seu para outro.",
    "Enhanced Hammer": "Descarta 1 Energia Especial de 1 Pokémon do oponente.",
    "Fighting Gong": "Busca 1 energia de Luta ou 1 Básico de Luta no deck.",
    "Glass Trumpet": "Com Tera em jogo: anexa 1 energia do descarte em até 2 Incolores do Banco.",
    "Jumbo Ice Cream": "Cura 80 do seu Ativo, se ele tiver 3 ou mais energias.",
    "Miracle Headset": "Recupera até 2 Apoiadores do descarte.",
    "N's PP Up": "Anexa 1 energia básica do descarte num Pokémon do N no Banco.",
    "Night Stretcher": "Recupera 1 Pokémon ou energia básica do descarte.",
    "Poké Pad": "Busca 1 Pokémon sem regra no deck.",
    "Pokégear 3.0": "Olha as 7 do topo e pega 1 Apoiador.",
    "Precious Trolley": "Busca vários Pokémon Básicos no deck e põe no Banco.",
    "Premium Power Pro": "Neste turno, seus Pokémon de Luta dão +30 de dano.",
    "Prime Catcher": "Puxa 1 Pokémon do Banco do oponente e troca o seu Ativo.",
    "Rare Candy": "Evolui um Básico direto para o Estágio 2, pulando o Estágio 1.",
    "Roto-Stick": "Olha as 4 do topo e pega os Apoiadores que achar.",
    "Sacred Ash": "Embaralha até 5 Pokémon do descarte no deck.",
    "Secret Box": "Descarta 3 cartas e busca 1 Item, 1 Ferramenta, 1 Apoiador e 1 Estádio.",
    "Special Red Card": "Com o oponente em 3 prêmios ou menos: ele troca a mão por 3 cartas.",
    "Strange Timepiece": "Desevolui 1 Pokémon Psíquico seu (a evolução volta para a mão).",
    "Switch": "Troca o seu Ativo por 1 Pokémon do Banco.",
    "Team Rocket's Transceiver": "Busca 1 Apoiador da Equipe Rocket no deck.",
    "Tool Scrapper": "Descarta até 2 Ferramentas em jogo.",
    "Ultra Ball": "Descarta 2 cartas e busca qualquer Pokémon no deck.",
    "Unfair Stamp": "Após um nocaute seu: os dois embaralham a mão; você compra 5, ele 2.",
    "Wondrous Patch": "Anexa 1 energia Psíquica do descarte num Psíquico do Banco.",
    # Estádios
    "Academy at Night": "Uma vez por turno, cada jogador pode pôr 1 carta da mão no topo do deck.",
    "Area Zero Underdepths": "Quem tem Pokémon Tera em jogo pode ter 8 no Banco.",
    "Battle Cage": "Impede contadores de dano no Banco vindos de ataques e Habilidades.",
    "Festival Grounds": "Pokémon com energia anexada não sofrem condições especiais.",
    "Forest of Vitality": "Pokémon de Planta podem evoluir no mesmo turno em que entram.",
    "Gravity Mountain": "Todo Pokémon de Estágio 2 em jogo perde 30 de HP.",
    "Jamming Tower": "Nenhuma Ferramenta anexada tem efeito.",
    "Lumiose City": "Uma vez por turno: busca 1 Básico e põe no Banco, mas encerra o turno.",
    "Nighttime Mine": "Ataques de Pokémon Tera custam 1 energia Incolor a mais.",
    "Prism Tower": "Uma vez por turno, cada jogador pode descartar 2 cartas e comprar 1.",
    "Risky Ruins": "Básico que não é de Escuridão entra no Banco com 2 contadores de dano.",
    "Spikemuth Gym": "Uma vez por turno: busca 1 Pokémon da Marnie no deck.",
    "Team Rocket's Factory": "Com um Apoiador da Equipe Rocket jogado no turno: compre 2.",
    "Team Rocket's Watchtower": "Pokémon Incolores em jogo perdem as Habilidades.",
    # Ferramentas
    "Air Balloon": "O Pokémon equipado recua com 2 energias a menos.",
    "Binding Mochi": "Se o Pokémon equipado está Envenenado, os ataques dele dão +40.",
    "Brave Bangle": "Pokémon sem regra equipado dá +30 contra Pokémon ex.",
    "Cynthia's Power Weight": "Pokémon da Cynthia equipado ganha +70 de HP.",
    "Handheld Fan": "Ao ser atacado, move 1 energia do atacante para o Banco dele.",
    "Hero's Cape": "O Pokémon equipado ganha +100 de HP.",
    "Lillie's Pearl": "Pokémon da Lillie equipado dá 1 prêmio a menos ao ser nocauteado.",
    "Lucky Helmet": "Ao ser atacado, você compra 2 cartas.",
    "Powerglass": "No fim do seu turno, anexa 1 energia básica do descarte no Ativo equipado.",
}

ABILITIES_PT: dict[str, str] = {
    "ACE Nullifier": "Com Ferramenta anexada, o oponente não pode jogar cartas ACE SPEC.",
    "Adrena-Brain": "Com energia de Escuridão: move até 3 contadores de dano seus para o oponente.",
    "Attract Customers": "No Ativo: olha as 6 do topo e pega 1 Apoiador.",
    "Boom Boom Groove": "Com Festival Lead no Ativo: busca qualquer carta no deck.",
    "Champion's Call": "Busca 1 Pokémon da Cynthia no deck.",
    "Charging Up": "Anexa 1 energia básica do descarte neste Pokémon.",
    "Cheer On to Glory": "Ataques dos seus Pokémon da Cynthia dão +30 de dano.",
    "Cobalt Command": "Ataques dos seus Pokémon do Futuro dão +20 de dano.",
    "Cursed Blast": "Põe contadores de dano num Pokémon do oponente, mas este é nocauteado.",
    "Damp": "Anula Habilidades que exigem nocautear o próprio Pokémon.",
    "Durable Body": "Ao ser nocauteado por ataque, cara: sobrevive com 10 de HP.",
    "Fairy Zone": "Pokémon de Dragão do oponente ficam fracos a Psíquico.",
    "Festival Lead": "Com Festival Grounds em jogo, este Pokémon ataca duas vezes.",
    "Flip the Script": "Após um nocaute seu no turno passado: compra 3 cartas.",
    "Flower Curtain": "Seus Pokémon sem regra no Banco não recebem dano de ataques.",
    "Heave-Ho Catcher": "Ao evoluir: puxa 1 Pokémon do Banco do oponente para o Ativo.",
    "Hide 'n' Sneak": "Efeitos de ataques e Habilidades do oponente não afetam este Pokémon.",
    "Last-Ditch Catch": "Ao entrar no Banco: busca 1 Apoiador no deck.",
    "Lunar Cycle": "Com Solrock em jogo, descarta 1 energia de Luta e compra 3.",
    "Metal Maker": "Olha as 4 do topo e anexa as energias de Metal que achar.",
    "Metallic Signal": "Busca até 2 Pokémon de Evolução de Metal no deck.",
    "Mysterious Rock Inn": "Este Pokémon não recebe dano de ataques de Pokémon ex.",
    "Plasma Bane": "Com carta do Colress no descarte do oponente, Trifrost custa 1 Incolor.",
    "Power Saver": "Só ataca com 4 ou mais Pokémon da Equipe Rocket em jogo.",
    "Psychic Draw": "Ao evoluir: compra cartas.",
    "Punk Up": "Ao evoluir: busca até 5 energias de Escuridão para os Pokémon da Marnie.",
    "Rapid Vernier": "Ao entrar no Banco: vai para o Ativo levando energias dos outros.",
    "Recon Directive": "Olha as 2 do topo, pega 1 e manda a outra para o fundo.",
    "Repelling Veil": "Efeitos de ataques não afetam seus Básicos da Equipe Rocket.",
    "Ripening Charge": "Anexa 1 energia de Planta da mão e cura 30 desse Pokémon.",
    "Run Away Draw": "Compra 3 cartas e embaralha este Pokémon de volta no deck.",
    "Run Errand": "No Ativo: compra 2 cartas (uma vez por turno).",
    "Seasoned Skill": "Blood Moon custa 1 Incolor a menos por prêmio que o oponente pegou.",
    "Sinister Surge": "Busca 1 energia de Escuridão para o Banco (com 2 contadores de dano).",
    "Skyliner": "Seus Pokémon Básicos recuam de graça.",
    "Snow Sink": "Ao entrar no Banco: descarta o Estádio em jogo.",
    "Spherical Shield": "Seus Pokémon no Banco não recebem dano nem efeitos de ataques.",
    "Subjugating Chains": "Troca o Ativo por 1 Pokémon de Escuridão do Banco, que fica Envenenado.",
    "Teal Dance": "Anexa 1 energia de Planta da mão neste Pokémon e compra 1 carta.",
    "Teleporter": "No Ativo: embaralha este Pokémon e tudo que está nele de volta no deck.",
    "Toxic Subjugation": "No Ativo: o veneno do oponente causa 50 de dano a mais no Checkup.",
    "Trade": "Descarta 1 carta da mão e compra 2.",
    "Watchful Eye": "Contadores de dano não podem ser movidos entre Pokémon.",
    "Wild Growth": "Cada energia de Planta sua fornece 2 energias.",
    "Freezing Shroud": "No Checkup, põe 1 contador em cada Pokémon com Habilidade.",
}


def describe_card(card: Card) -> str:
    """Uma linha em português do que a carta faz; sem tradução cadastrada,
    devolve o texto original impresso."""
    known = TRAINERS_PT.get(card.name)
    if known:
        return known
    return " ".join(card.rules).strip()


def describe_ability(name: str, fallback: str = "") -> str:
    return ABILITIES_PT.get(name, fallback)
