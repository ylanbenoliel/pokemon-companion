"""Widget que renderiza um `PlayerState` como texto — board da IA (sempre
virtual) e board do jogador (por clique manual até a Fase 4 trocar por
leitura de câmera) usam o mesmo widget."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from pokemon_companion.engine.game_state import PlayerState, PokemonInPlay


def _format_pokemon(label: str, mon: PokemonInPlay | None) -> str:
    if mon is None:
        return f"{label}: (vazio)"
    energies = ", ".join(mon.attached_energies) or "nenhuma"
    status = "-" if mon.status.name == "NONE" else mon.status.name
    return (
        f"{label}: {mon.card.name}  HP {mon.current_hp}/{mon.card.hp}  "
        f"Energia: {energies}  Status: {status}"
    )


def format_board(title: str, player: PlayerState, show_hand: bool = False) -> str:
    lines = [f"{title} — prêmios restantes: {len(player.prizes)}"]
    lines.append(_format_pokemon("Ativo", player.active))
    for i, mon in enumerate(player.bench):
        lines.append(_format_pokemon(f"Banco {i}", mon))
    if show_hand:
        lines.append("Mão: " + (", ".join(c.name for c in player.hand) or "(vazia)"))
    return "\n".join(lines)


class BoardView(QLabel):
    def __init__(self, title: str, show_hand: bool = False) -> None:
        super().__init__()
        self._title = title
        self._show_hand = show_hand
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStyleSheet("font-family: Menlo, Consolas, monospace; padding: 8px;")
        self.setWordWrap(True)

    def update_state(self, player: PlayerState) -> None:
        self.setText(format_board(self._title, player, self._show_hand))
