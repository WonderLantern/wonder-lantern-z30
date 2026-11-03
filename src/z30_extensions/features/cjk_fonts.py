"""Stock CJK UI fonts 0/2 extended with the glyphs the owned labels need.

The rebuilt fonts and copies of the 25-entry font pointer and size tables
live in the owned tail; the literals at 0x104530F4 and 0x104530F8, which boot
code hands to the font engine, are redirected to the copies. Stock font data
stays untouched. QEMU evidence: docs/ui-qemu.md.
"""
import struct

from ..platform.fonts import (CJK_FONTS, FONT_COUNT, FONT_SIZES, FONT_SIZES_LITERAL, FONT_TABLE,
                              FONT_TABLE_LITERAL, decode_glyph, extend_font, extra_glyphs, font_blob)


def add(plan, functions):
    if 'cjk_font_table' in functions:
        return functions['cjk_font_table']
    pointers = list(plan.image.words(FONT_TABLE, FONT_COUNT))
    sizes = list(plan.image.words(FONT_SIZES, FONT_COUNT))
    added = {}
    for font in CJK_FONTS:
        glyphs = {ord(char): decode_glyph(bytes.fromhex(record))[0]
                  for char, record in extra_glyphs(font).items()}
        blob = extend_font(font_blob(plan.image, font), glyphs)
        pointers[font], sizes[font] = plan.alloc(blob), len(blob)
        added[str(font)] = ''.join(extra_glyphs(font))
    table = plan.alloc(struct.pack(f'<{FONT_COUNT}I', *pointers))
    size_table = plan.alloc(struct.pack(f'<{FONT_COUNT}I', *sizes))
    plan.edit(FONT_TABLE_LITERAL, struct.pack('<2I', table, size_table), 'cjk-font-tables',
              expected=struct.pack('<2I', FONT_TABLE, FONT_SIZES))
    functions['cjk_font_table'] = table
    plan.features['cjk_fonts'] = dict(font_table=hex(table), size_table=hex(size_table), added_glyphs=added,
                                      source='Noto Sans CJK / Noto Sans Mono CJK (SIL OFL 1.1)')
    return table
