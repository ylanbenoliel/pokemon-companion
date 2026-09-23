"""Peças visuais comuns às janelas do app (feltro, botões, campos, rótulos),
para os diálogos novos falarem a mesma língua do menu e da importação."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QWidget

from pokemon_companion.ui.theme import FELT_DEEP, FELT_LIT, display_font, ui_font

FIELD_STYLE = """
QLineEdit, QPlainTextEdit, QSpinBox { background: rgba(247,239,225,14); color: #f7efe1;
    border: 1px solid rgba(247,239,225,45); border-radius: 8px; padding: 7px 10px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border: 1px solid #ffe03d; }
QListWidget, QTableWidget { background: rgba(247,239,225,10); color: #f7efe1;
    border-radius: 8px; border: 1px solid rgba(247,239,225,30); padding: 4px;
    gridline-color: rgba(247,239,225,20); }
QListWidget::item { padding: 7px 8px; border-radius: 6px; }
QListWidget::item:selected, QTableWidget::item:selected { background: #ffe03d; color: #20242b; }
QHeaderView::section { background: transparent; color: rgba(247,239,225,150); border: none;
    padding: 4px 6px; }
QCheckBox { color: #f7efe1; spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid rgba(247,239,225,90); background: rgba(247,239,225,10); }
QCheckBox::indicator:checked { background: #ffe03d; border: 1px solid #ffe03d; }
QComboBox { color: #f7efe1; background: rgba(247,239,225,14); padding: 6px 10px;
    border: 1px solid rgba(247,239,225,45); border-radius: 8px; }
QComboBox QAbstractItemView { background: #124540; color: #f7efe1;
    selection-background-color: #ffe03d; selection-color: #20242b; }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: rgba(247,239,225,35); }
QSlider::sub-page:horizontal { border-radius: 3px; background: #ffe03d; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; border-radius: 8px;
    background: #f7efe1; }
QTabWidget::pane { border: none; }
QTabBar::tab { color: rgba(247,239,225,170); background: transparent; padding: 7px 14px;
    border-radius: 12px; margin-right: 4px; }
QTabBar::tab:selected { color: #20242b; background: #f7efe1; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QTextBrowser { background: transparent; color: #f7efe1; border: none; }
"""


def felt_background(widget_class: str) -> str:
    return (
        f"{widget_class} {{ background: qlineargradient(x1:0.5, y1:0, x2:0.5, y2:1,"
        f" stop:0 {FELT_LIT.name()}, stop:1 {FELT_DEEP.name()}); }}"
    )


def style_dialog(dialog: QDialog) -> None:
    dialog.setStyleSheet(
        felt_background("QDialog")
        + "QLabel { color: #f7efe1; background: transparent; }"
        + FIELD_STYLE
    )


def title_label(text: str, size: float = 17) -> QLabel:
    label = QLabel(text)
    label.setFont(display_font(size))
    return label


def hint_label(text: str, size: float = 9.5) -> QLabel:
    label = QLabel(text)
    label.setFont(ui_font(size))
    label.setWordWrap(True)
    label.setStyleSheet("color: rgba(247,239,225,150); background: transparent;")
    return label


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    font = ui_font(9, QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    label.setFont(font)
    label.setStyleSheet("color: rgba(247,239,225,120); background: transparent;")
    return label


def make_button(text: str, primary: bool = False, parent: QWidget | None = None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setFont(ui_font(11, QFont.Weight.DemiBold))
    button.setMinimumHeight(38)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        button.setStyleSheet(
            "QPushButton { color: #20242b; background: #ffe03d; border: none;"
            " border-radius: 8px; padding: 8px 20px; }"
            "QPushButton:hover { background: #fff06f; }"
            "QPushButton:focus { border: 2px solid #f7efe1; }"
            "QPushButton:disabled { color: rgba(247,239,225,110); background: rgba(247,239,225,22); }"
        )
    else:
        button.setStyleSheet(
            "QPushButton { color: #f7efe1; background: rgba(247,239,225,16);"
            " border: 1px solid rgba(247,239,225,45); border-radius: 8px; padding: 8px 16px; }"
            "QPushButton:hover { background: rgba(247,239,225,30); }"
            "QPushButton:focus { border: 1px solid #ffe03d; }"
            "QPushButton:disabled { color: rgba(247,239,225,90); }"
        )
    return button
