"""Diálogo de confirmação manual para reconhecimentos de câmera com baixa
confiança (Fase 4). Recebe candidatos genéricos (carta, distância de
Hamming) em vez do tipo `RecognitionCandidate` do módulo `vision` para
manter `ui` livre de acoplamento direto com a implementação de visão."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pokemon_companion.cards_db.models import Card


class ConfirmationDialog(QDialog):
    def __init__(
        self,
        context_description: str,
        candidates: list[tuple[Card, int]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirme a carta detectada")
        self.selected_card: Card | None = None
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Reconhecimento incerto: {context_description}"))

        self._list = QListWidget()
        for card, distance in candidates:
            self._list.addItem(f"{card.name}  (distância: {distance})")
        if candidates:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        confirm_button = QPushButton("Confirmar")
        confirm_button.clicked.connect(self._on_confirm)
        skip_button = QPushButton("Nenhuma destas (pular)")
        skip_button.clicked.connect(self.reject)
        buttons.addWidget(confirm_button)
        buttons.addWidget(skip_button)
        layout.addLayout(buttons)

    def _on_confirm(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._candidates):
            self.selected_card = self._candidates[row][0]
        self.accept()
