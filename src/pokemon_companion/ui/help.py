"""Como jogar: as regras do Pokémon TCG e os controles do app, em abas.

O texto segue o livro de regras oficial na forma em que o motor
(`engine/rules.py`) o aplica — se uma regra mudar lá, mude aqui também.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from pokemon_companion.ui.theme import ui_font
from pokemon_companion.ui.widgets import make_button, style_dialog, title_label

_CSS = """
<style>
  h3 { color: #ffe03d; font-size: 15px; margin: 14px 0 4px 0; }
  p, li { color: #f7efe1; font-size: 14px; line-height: 145%; }
  b { color: #ffffff; }
  .muted { color: #b9c9c2; }
  kbd { color: #20242b; background: #f7efe1; }
</style>
"""

PAGES: tuple[tuple[str, str], ...] = (
    (
        "Objetivo",
        """
<h3>Como vencer</h3>
<p>Você vence a partida quando acontece qualquer uma destas três coisas:</p>
<ul>
  <li>você pega a sua <b>última carta de Prêmio</b>;</li>
  <li>o oponente fica <b>sem Pokémon em jogo</b> (o Ativo foi nocauteado e o Banco está vazio);</li>
  <li>o oponente precisa <b>comprar no início do turno e o deck dele acabou</b>.</li>
</ul>
<h3>Preparação</h3>
<p>Cada jogador embaralha o deck de 60 cartas e compra 7. Escolha um
<b>Pokémon Básico</b> para o <b>Ativo</b> e, se quiser, até 5 para o
<b>Banco</b>. Quem não tiver Básico mostra a mão, embaralha e compra de novo
(o oponente pode comprar uma carta a mais por isso). Depois, cada um separa
<b>6 cartas de Prêmio</b> viradas para baixo. Cara ou coroa decide quem começa.</p>
""",
    ),
    (
        "Seu turno",
        """
<h3>1. Compre uma carta</h3>
<h3>2. Faça o que quiser, na ordem que quiser</h3>
<ul>
  <li>Ponha <b>Pokémon Básicos</b> no Banco (no máximo 5).</li>
  <li><b>Evolua</b> um Pokémon pondo a carta de Estágio 1 ou 2 sobre ele. Não vale no
      seu primeiro turno nem num Pokémon que entrou em jogo neste turno.</li>
  <li>Ligue <b>uma</b> carta de Energia da mão a um dos seus Pokémon (uma por turno).</li>
  <li>Jogue <b>Itens</b> à vontade, <b>um Apoiador</b> por turno, <b>um Estádio</b> por
      turno e <b>Ferramentas</b> (uma por Pokémon). Quem começa não joga Apoiador no
      primeiro turno.</li>
  <li><b>Recue</b> o Ativo uma vez por turno: descarte Energia igual ao custo de recuo e
      troque-o por um Pokémon do Banco.</li>
  <li>Use as <b>Habilidades</b> dos seus Pokémon.</li>
</ul>
<h3>3. Ataque (e o turno acaba)</h3>
<p>Para atacar, o Ativo precisa ter as Energias do custo do ataque. Atacar encerra
o turno; você também pode passar sem atacar. <b>Quem começa não ataca no primeiro
turno.</b></p>
<p class="muted">Entre os turnos acontece o <i>Pokémon Checkup</i>: as condições
especiais fazem efeito (veja a aba Combate).</p>
""",
    ),
    (
        "Combate",
        """
<h3>Dano</h3>
<p>O dano do ataque vai no Ativo do oponente. <b>Fraqueza</b> dobra o dano (×2) e
<b>Resistência</b> tira 30. Dano no Banco não sofre Fraqueza nem Resistência.</p>
<h3>Nocaute e Prêmios</h3>
<p>Um Pokémon com dano igual ou maior que o HP é nocauteado e vai para o descarte com
todas as cartas ligadas a ele. Quem nocauteou pega Prêmios: <b>1</b> por Pokémon
comum, <b>2</b> por Pokémon ex, <b>3</b> por Mega Evolução ex. O dono escolhe um
Pokémon do Banco para ser o novo Ativo.</p>
<h3>Condições especiais</h3>
<ul>
  <li><b>Envenenado</b>: 1 contador de dano (10) a cada Checkup.</li>
  <li><b>Queimado</b>: 2 contadores (20) a cada Checkup; depois, cara cura.</li>
  <li><b>Adormecido</b>: não ataca nem recua; a cada Checkup, cara acorda.</li>
  <li><b>Paralisado</b>: não ataca nem recua; passa no fim do próximo turno do dono.</li>
  <li><b>Confuso</b>: ao atacar, jogue uma moeda; coroa = o ataque falha e o Pokémon
      leva 30 de dano.</li>
</ul>
<p>Recuar ou evoluir cura todas as condições. Adormecido, Paralisado e Confuso se
substituem entre si; Envenenado e Queimado podem somar-se a elas.</p>
""",
    ),
    (
        "Controles",
        """
<h3>Jogar cartas</h3>
<p><b>Arraste</b> uma carta da mão até onde ela vai (o Ativo, o Banco, um Pokémon para
ligar Energia ou evoluir). Os destinos possíveis <b>pulsam</b>. Se só houver um destino,
basta <b>clicar</b> na carta. Cartas que você pode jogar agora brilham em verde.</p>
<h3>Atacar, recuar, passar</h3>
<p>Os botões de ataque ficam ao lado do seu Ativo, com o custo em orbes de Energia.
O botão de recuo mostra o custo; o botão grande passa o turno.</p>
<h3>Ver uma carta</h3>
<p>Passe o mouse sobre qualquer carta ou Pokémon para ver o texto completo, o HP e os
ataques. Treinadores mostram em português o que fazem.</p>
<h3>Atalhos</h3>
<ul>
  <li><kbd>&nbsp;Esc&nbsp;</kbd> pausa (regras, configurações, desistir)</li>
  <li><kbd>&nbsp;F1&nbsp;</kbd> esta ajuda</li>
  <li><kbd>&nbsp;M&nbsp;</kbd> liga/desliga o som &nbsp; <kbd>&nbsp;+&nbsp;</kbd>
      <kbd>&nbsp;−&nbsp;</kbd> volume</li>
</ul>
""",
    ),
)


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, page: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("Como jogar")
        self.resize(640, 620)
        style_dialog(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(title_label("Como jogar"))
        self.tabs = QTabWidget()
        self.tabs.setFont(ui_font(10.5))
        for name, body in PAGES:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(_CSS + body)
            self.tabs.addTab(browser, name)
        self.tabs.setCurrentIndex(page)
        layout.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = make_button("Fechar", primary=True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
