#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild bitmap text/chart assets from a genuine GNU Unifont BDF, without vector fonts."""
import argparse
import math
from pathlib import Path
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument('bdf', type=Path)
args = parser.parse_args()
out = Path(__file__).resolve().parents[1] / 'assets'
out.mkdir(exist_ok=True)
glyphs = {}
for block in args.bdf.read_text().split('STARTCHAR ')[1:]:
    lines = [line.strip() for line in block.splitlines()]
    code = int(next(x for x in lines if x.startswith('ENCODING ')).split()[1])
    advance = int(next(x for x in lines if x.startswith('DWIDTH ')).split()[1])
    box = tuple(map(int, next(x for x in lines if x.startswith('BBX ')).split()[1:]))
    begin = lines.index('BITMAP') + 1
    rows = lines[begin:lines.index('ENDCHAR')]
    width, height, xoff, yoff = box
    image = Image.new('RGBA', (max(1, width), max(1, height)))
    for y, line in enumerate(rows):
        value = int(line, 16)
        bits = len(line) * 4
        for x in range(width):
            if value & (1 << (bits - 1 - x)):
                image.putpixel((x, y), (255, 255, 255, 255))
    glyphs[code] = (image, advance, xoff, 14 - height - yoff)

codes = [c for c in range(32, 0x530) if c in glyphs]
codes += [ord(c) for c in '日本語文字色→←↑↓±×•' if ord(c) in glyphs]
codes = sorted(set(codes))
cols, cell = 32, 20
atlas = Image.new('RGBA', (cols * cell, math.ceil(len(codes) / cols) * cell))
records = []
for index, code in enumerate(codes):
    image, advance, xoff, yoff = glyphs[code]
    x, y = index % cols * cell + 1, index // cols * cell + 1
    atlas.paste(image, (x, y))
    records.append(f'char id={code} x={x} y={y} width={image.width} height={image.height} xoffset={xoff} yoffset={yoff} xadvance={advance} page=0 chnl=15')
atlas.save(out / 'unifont.png')
(out / 'unifont.fnt').write_text('\n'.join([
    'info face="Unifont" size=16 bold=0 italic=0 unicode=1 stretchH=100 smooth=0 aa=1 padding=0,0,0,0 spacing=0,0',
    f'common lineHeight=18 base=14 scaleW={atlas.width} scaleH={atlas.height} pages=1 packed=0',
    'page id=0 file="unifont.png"', f'chars count={len(records)}', *records, 'kernings count=0', '']))

def text(image, pos, value, color=(225, 235, 244), scale=1):
    x, y = pos
    for c in value:
        glyph, advance, xoff, yoff = glyphs[ord(c)]
        mask = glyph.getchannel('A')
        if scale != 1:
            mask = mask.resize((mask.width * scale, mask.height * scale), Image.Resampling.NEAREST)
        image.paste(color, (x + xoff * scale, y + yoff * scale), mask)
        x += advance * scale

chart = Image.new('RGB', (1024, 768), '#0c151f')
d = ImageDraw.Draw(chart)
text(chart, (32, 24), 'CHROMA / SMALL DETAIL', scale=3)
text(chart, (32, 91), 'SOURCE TEXELS: 1 / 2 / 4 / 8 PIXELS', (109, 198, 220))
for row, period in enumerate((1, 2, 4, 8)):
    y0 = 136 + row * 64
    # Equal BT.709 luma in gamma-encoded RGB, within one 8-bit rounding step.
    for x in range(32, 992):
        c = (255, 0, 0) if (x - 32) // period % 2 == 0 else (0, 76, 0)
        d.line((x, y0, x, y0 + 27), fill=c)
    for x in range(32, 992):
        c = (0, 0, 255) if (x - 32) // period % 2 == 0 else (0, 26, 0)
        d.line((x, y0 + 32, x, y0 + 55), fill=c)
text(chart, (32, 412), 'Y-MATCHED RED/GREEN + BLUE/GREEN', scale=2)
text(chart, (32, 460), 'White: 0123456789 Il1 O0 rn m / / /', scale=2)
text(chart, (32, 504), 'CHROMA 0123456789 Il1 O0 rn m / / /', (255, 60, 80), 2)
text(chart, (32, 548), 'CHROMA 0123456789 Il1 O0 rn m / / /', (30, 220, 255), 2)
text(chart, (32, 597), '1x pixel text: 0123456789 abcdefghijklmnopqrstuvwxyz')
text(chart, (32, 628), 'Unicode: Příliš žluťoučký kůň / Ελληνικά / 日本語', scale=2)
text(chart, (32, 704), 'Nearest filtering. Source pixels != encoded pixels.', (140, 164, 184))
chart.save(out / 'chroma.png')
print(f'Generated {len(codes)} genuine bitmap glyphs and 1024x768 chroma chart')
