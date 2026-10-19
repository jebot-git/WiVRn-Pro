#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Render native WGUI layout measurements with the production palette/SVG assets.

This is a layout preview, not a headset/GPU screenshot. First export layout.json
with the grouped_watch_native_layout_and_preview Rust test (see fork/SPATIAL.md).
Run with PySide6 available and QT_QPA_PLATFORM=offscreen.
"""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('layout', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
data = json.loads(args.layout.read_text())
root = Path(__file__).resolve().parents[1]
assets = root / 'server/wayvr/upstream/wayvr/src/assets'
app = QGuiApplication([])
colors = {key: QColor.fromRgbF(*value) for key, value in data['colors'].items()}
matte = QColor('#00131c')


def text(painter, rect, value, size=18, color='foreground', bold=False, align=Qt.AlignCenter):
    font = QFont('DejaVu Sans')
    font.setPixelSize(size)
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    painter.setFont(font)
    painter.setPen(colors[color])
    painter.drawText(QRectF(*rect), align | Qt.TextWordWrap, value)


def rect(painter, box, fill, border='outline', radius=22, width=2):
    painter.setBrush(colors[fill])
    painter.setPen(QPen(colors[border], width))
    painter.drawRoundedRect(QRectF(*box), radius, radius)


def icon(painter, name, box):
    path = assets / name
    if not path.exists():
        path = root / 'server/wayvr/icons' / Path(name).name
    if not path.exists():
        raise FileNotFoundError(name)
    renderer = QSvgRenderer(str(path))
    layer = QImage(104, 104, QImage.Format_ARGB32_Premultiplied)
    layer.fill(Qt.transparent)
    paint = QPainter(layer)
    paint.setRenderHint(QPainter.Antialiasing)
    renderer.render(paint, QRectF(0, 0, 104, 104))
    paint.setCompositionMode(QPainter.CompositionMode_SourceIn)
    paint.fillRect(layer.rect(), colors['foreground'])
    paint.end()
    painter.drawImage(QRectF(*box), layer)


def draw_watch(painter, state):
    group = state['group']
    if group is None:
        rect(painter, [56, 396, 752, 340], 'background', radius=28)
        for i, (name, charge) in enumerate([('hmd', '96%'), ('controller_l', '83%'), ('controller_r', '87%'), ('track', '72%')]):
            x = 80 + i * 148
            rect(painter, [x, 416, 136, 46], 'button', radius=8)
            icon(painter, f'watch/{name}.svg', [x+8, 425, 28, 28])
            text(painter, [x+44, 420, 78, 38], charge, size=22, bold=True)
        text(painter, [80, 508, 232, 58], '09:41', size=46, bold=True)
        text(painter, [80, 566, 232, 26], 'Mon  ·  19 Oct', size=16, color='muted')
        text(painter, [80, 606, 110, 24], 'Paris', size=14, color='muted')
        text(painter, [200, 606, 112, 24], 'New York', size=14, color='muted')
        text(painter, [80, 630, 110, 28], '09:41', size=22)
        text(painter, [200, 630, 112, 28], '03:41', size=22)
        text(painter, [80, 684, 700, 24], 'Remembered floor: active', size=14, color='muted', align=Qt.AlignLeft)
        for i in range(2):
            x = 56 + i * 72
            rect(painter, [x, 752, 60, 60], 'button', radius=8)
            icon(painter, 'watch/set2.svg', [x+11, 760, 38, 38])
            text(painter, [x, 778, 60, 30], str(i+1), size=18, bold=True)
    else:
        text(painter, [48, 116, 904, 46], group, size=32, bold=True)
        text(painter, [48, 166, 904, 30], 'Choose an action · tap the group again to close', size=18, color='muted')
    for button in state['buttons']:
        x, y = button['x'], button['y']
        parent = 'Parent' in button['id']
        selected = parent and group is not None
        rect(painter, [x, y, data['button_size'], data['button_size']],
             'background' if selected else 'button', 'primary' if selected else 'outline', width=3 if selected else 2)
        icon(painter, button['icon'], [x+38, y+17, 52, 52])
        text(painter, [x+10, y+78, 108, 42], button['title'], size=18, bold=True)


# Export each state at its native logical resolution for close inspection.
args.output.parent.mkdir(parents=True, exist_ok=True)
for state in data['states']:
    image = QImage(int(data['width']), int(data['height']), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    draw_watch(p, state)
    p.end()
    image.save(str(args.output.parent / f"watch-{(state['group'] or 'collapsed').lower()}.png"))

# Compact overview: crop only unpainted margins, preserving all measured button sizes.
scale, cell_w, cell_h, margin = 0.83, 850, 620, 28
image = QImage(cell_w*2+margin*3, cell_h*2+margin*3+92, QImage.Format_ARGB32_Premultiplied)
image.fill(matte)
p = QPainter(image)
p.setRenderHint(QPainter.Antialiasing)
text(p, [margin, 16, image.width()-2*margin, 42], 'WayVR watch · grouped controls', size=28, bold=True, align=Qt.AlignLeft)
text(p, [margin, 58, image.width()-2*margin, 28], '128 × 128 targets  ·  matching press + release  ·  native WGUI geometry / default palette', size=17, color='muted', align=Qt.AlignLeft)
for i, state in enumerate(data['states']):
    x, y = margin+(i%2)*(cell_w+margin), 104+margin+(i//2)*(cell_h+margin)
    p.setPen(QPen(colors['outline'], 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(x, y, cell_w, cell_h), 20, 20)
    text(p, [x+24, y+14, cell_w-48, 32], 'Collapsed' if state['group'] is None else state['group']+' open', size=20, bold=True, align=Qt.AlignLeft)
    p.save()
    p.translate(x+12, y+68)
    p.scale(scale, scale)
    # Collapsed watch uses the lower half of the transparent reserved fan canvas.
    p.translate(0, -370 if state['group'] is None else -100)
    draw_watch(p, state)
    p.restore()
p.end()
assert image.save(str(args.output)), args.output
print(args.output)
