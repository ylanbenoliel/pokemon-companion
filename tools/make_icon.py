"""Gera o ícone do app em todos os tamanhos e formatos (QPainter, vetorial).

    uv run python tools/make_icon.py

Saídas:
- `src/pokemon_companion/ui/icons/app_<N>.png` (16–1024): o ícone da janela
  e da barra de tarefas/Dock em tempo de execução (`QApplication.setWindowIcon`);
- `packaging/app.icns` (macOS, via `iconutil`) e `packaging/app.ico`
  (Windows, via Pillow) para o executável do PyInstaller.

Desenho (seguindo as regras de design-playful-app-icons, skills.sh): um
objeto só — duas cartas em leque sobre o feltro do tapete —, com um gancho:
o orbe de energia amarelo do próprio jogo na carta da frente. Paleta do app
(feltro, osso, volt, coral). Nada de marca de terceiros: nem Poké Ball, nem
silhueta de Pokémon, nem texto. Cada tamanho é redesenhado (não reduzido):
abaixo de 64 px some a sombra e o detalhe da carta, para ler em 16 px.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "src" / "pokemon_companion" / "ui" / "icons"
PACKAGING = ROOT / "packaging"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)

FELT_TOP = QColor("#236e61")
FELT_BOTTOM = QColor("#0a2b2b")
BONE = QColor("#f7efe1")
INK = QColor("#20242b")
VOLT = QColor("#ffe03d")
FLARE = QColor("#ff5a36")


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_icon(painter: QPainter, size: int) -> None:
    """Desenha num quadro de `size` px (coordenadas pensadas em 1024)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 1024, size / 1024)
    small = size < 64
    tiny = size <= 24

    # placa (grade do macOS: corpo de 824 px, margem para a sombra); nos
    # tamanhos pequenos a placa cresce para aproveitar cada pixel
    inset = 36 if tiny else 60 if small else 100
    plate = QRectF(inset, inset, 1024 - 2 * inset, 1024 - 2 * inset)
    radius = plate.width() * 0.225
    if not small:
        for grow, alpha in ((22, 20), (12, 34), (5, 48)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawPath(
                _rounded(
                    plate.adjusted(-grow / 2, grow / 2, grow / 2, grow * 1.2), radius + grow / 2
                )
            )
    felt = QLinearGradient(plate.topLeft(), plate.bottomLeft())
    felt.setColorAt(0.0, FELT_TOP)
    felt.setColorAt(1.0, FELT_BOTTOM)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(felt)
    painter.drawPath(_rounded(plate, radius))
    # luz do abajur sobre a mesa: um brilho largo e suave no alto
    lamp = QRadialGradient(QPointF(512, plate.top() + plate.height() * 0.28), plate.width() * 0.62)
    lamp.setColorAt(0.0, QColor(255, 207, 138, 70))
    lamp.setColorAt(1.0, QColor(255, 207, 138, 0))
    painter.setBrush(lamp)
    painter.drawPath(_rounded(plate, radius))

    # as duas cartas (proporção 63:88), em leque
    card_w = 370 if small else 330
    card_h = card_w * 88 / 63
    card_radius = card_w * 0.11

    def card(center: QPointF, angle: float, fill: QColor, face: bool) -> None:
        painter.save()
        painter.translate(center)
        painter.rotate(angle)
        rect = QRectF(-card_w / 2, -card_h / 2, card_w, card_h)
        if not small:  # sombra da carta sobre o feltro
            painter.setBrush(QColor(0, 0, 0, 60))
            painter.drawPath(_rounded(rect.translated(10, 18), card_radius))
        if face:
            painter.setBrush(fill)
            painter.drawPath(_rounded(rect, card_radius))
            # moldura interna da carta
            if not small:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(32, 36, 43, 40), 8))
                painter.drawPath(_rounded(rect.adjusted(22, 22, -22, -22), card_radius * 0.6))
                painter.setPen(Qt.PenStyle.NoPen)
            _orb(painter, QPointF(0, -card_h * 0.07), card_w * (0.36 if small else 0.3), small)
            if not small:  # linhas de texto da carta, discretas
                painter.setBrush(QColor(32, 36, 43, 55))
                for i, width in enumerate((0.56, 0.42)):
                    bar = QRectF(
                        -card_w * width / 2, card_h * (0.25 + i * 0.09), card_w * width, 18
                    )
                    painter.drawRoundedRect(bar, 9, 9)
        else:
            back = QLinearGradient(rect.topLeft(), rect.bottomRight())
            back.setColorAt(0.0, fill.lighter(112))
            back.setColorAt(1.0, fill.darker(118))
            painter.setBrush(back)
            painter.drawPath(_rounded(rect, card_radius))
            if not small:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 70), 8))
                painter.drawPath(_rounded(rect.adjusted(22, 22, -22, -22), card_radius * 0.6))
                painter.setPen(Qt.PenStyle.NoPen)
        painter.restore()

    card(QPointF(430, 540), -14, FLARE, face=False)
    card(QPointF(592, 500), 9, BONE, face=True)


def _orb(painter: QPainter, center: QPointF, radius: float, small: bool) -> None:
    """O orbe de energia do jogo: amarelo, com brilho e aro branco."""
    gradient = QRadialGradient(center - QPointF(radius * 0.3, radius * 0.35), radius * 1.3)
    gradient.setColorAt(0.0, QColor("#fff7b8"))
    gradient.setColorAt(0.45, VOLT)
    gradient.setColorAt(1.0, QColor("#e0a000"))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor(255, 255, 255, 235), radius * (0.16 if small else 0.11)))
    painter.drawEllipse(center, radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)
    if not small:  # reflexo
        painter.setBrush(QColor(255, 255, 255, 150))
        painter.drawEllipse(
            center - QPointF(radius * 0.36, radius * 0.4), radius * 0.26, radius * 0.18
        )


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_icon(painter, size)
    painter.end()
    return image


def build_icns(pngs: dict[int, Path], out: Path) -> bool:
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "app.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            shutil.copy(pngs[base], iconset / f"icon_{base}x{base}.png")
            shutil.copy(pngs[base * 2], iconset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    return True


def build_ico(pngs: dict[int, Path], out: Path) -> None:
    from PIL import Image

    sizes = [s for s in (16, 24, 32, 48, 64, 128, 256) if s in pngs]
    images = [Image.open(pngs[s]).convert("RGBA") for s in sizes]
    images[-1].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])


def main() -> None:
    QGuiApplication(sys.argv)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, Path] = {}
    for size in SIZES:
        path = ICON_DIR / f"app_{size}.png"
        render(size).save(str(path))
        pngs[size] = path
    print(f"PNG: {', '.join(str(s) for s in SIZES)} → {ICON_DIR.relative_to(ROOT)}")
    build_ico(pngs, PACKAGING / "app.ico")
    print("ICO: packaging/app.ico")
    if build_icns(pngs, PACKAGING / "app.icns"):
        print("ICNS: packaging/app.icns")
    else:
        print("ICNS: pulado (precisa do iconutil do macOS)")


if __name__ == "__main__":
    main()
