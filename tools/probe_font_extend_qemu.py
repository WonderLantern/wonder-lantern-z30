"""QEMU-only check that the native font engine draws glyphs from rebuilt fonts.

Rebuilds stock fonts 0 and 2 with `extend_font`: 设/設 (setup-menu title) is
remapped to a new, horizontally mirrored copy of 定 and 橙 is added. Before
boot, the fonts and a copied font pointer array are written to the image tail
and the array literal 0x104530F4 is redirected. A mirrored title glyph proves
the redirected array, the inserted map entry, the new index range and the
appended record; unchanged neighbours prove the shifted offsets.

QEMU-only; the stock image itself is not modified.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.fonts import (CJK_FONTS, FONT_COUNT, FONT_SIZES, FONT_TABLE, FONT_TABLE_LITERAL,
                                           HANS_FONT, Glyph,
                                           character_map, decode_glyph, extend_font, font_blob,
                                           glyph_addresses)
from z30_extensions.platform.z30_120 import BASE, StockImage

FONTS = FONT_COUNT


def mirrored(glyph):
    return Glyph(glyph.flags, [[(2048 - x, y, on) for x, y, on in reversed(c)] for c in glyph.contours],
                 glyph.advance, glyph.vertical, glyph.size, 2048 - glyph.x_min - glyph.size[0],
                 glyph.y_max, glyph.bearings, glyph.extra)


def stock_glyph(image, font, char):
    glyph = next(g + ord(char) - first for first, g, run in character_map(image, font)
                 if first <= ord(char) < first + run)
    return decode_glyph(image.read(glyph_addresses(image, font)[glyph], 4096))[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--firmware', type=Path, default=ROOT / 'Z_30_0120.bin')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--language', choices=('zh-Hans', 'zh-Hant'), default='zh-Hans')
    p.add_argument('--glyphs', type=Path, help='JSON {font: {char: record hex}} replacing the mirrored test')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session

    eg = extract_eg(a.firmware.read_bytes())
    image = StockImage(eg)
    tail = (BASE + len(eg) - 2 + 3) & ~3
    pointers = list(image.words(FONT_TABLE, FONTS))
    sizes = list(image.words(FONT_SIZES, FONTS))
    blobs, cursor = {}, tail + FONTS * 8
    supplied = json.loads(a.glyphs.read_text(encoding='utf-8')) if a.glyphs else None
    for font in CJK_FONTS:
        title = '设' if font == HANS_FONT else '設'
        if supplied:
            glyphs = {ord(char): decode_glyph(bytes.fromhex(record))[0]
                      for char, record in supplied[str(font)].items()}
        else:
            glyphs = {ord(title): mirrored(stock_glyph(image, font, '定')),
                      ord('橙'): stock_glyph(image, font, '定')}
        blob = extend_font(font_blob(image, font), glyphs)
        blobs[cursor], pointers[font], sizes[font] = blob, cursor, len(blob)
        cursor = (cursor + len(blob) + 3) & ~3
    blobs[tail] = struct.pack(f'<{FONTS}I', *pointers) + struct.pack(f'<{FONTS}I', *sizes)

    s = Session(str(a.output_dir.resolve()))
    def write(address, data):
        for off in range(0, len(data), 1024):
            part = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(part):x}:' + part.hex()) == 'OK'
    try:
        for address, data in blobs.items():
            write(address, data)
            assert s.remote.memory(address, len(data)) == data
        write(FONT_TABLE_LITERAL, struct.pack('<2I', tail, tail + FONTS * 4))
        s.audit.append(dict(font_array=hex(tail), fonts={str(f): hex(pointers[f]) for f in CJK_FONTS},
                            bytes=cursor - tail))
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        code, ui_font = {'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, code])
        s.input(0x10452cc0, [ui_font])
        # The getter must now hand out the copied array.
        global_array = int.from_bytes(s.remote.memory(int.from_bytes(s.remote.memory(0x104ed808, 4), 'little') - 0x2c, 4), 'little')
        s.audit.append(dict(runtime_font_array=hex(global_array)))
        s.input(0x1045f732, [2, 397])
        s.run_for(1.5)
        s.frame('setup-' + a.language)
        s.write_audit()
        print(f'font array in RAM global: {global_array:#x} (copied array {tail:#x})')
    finally:
        s.close()


if __name__ == '__main__':
    main()
