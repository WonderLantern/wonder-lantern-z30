"""Stock CJK UI fonts 0/2 extended with the glyphs the owned labels need.

The glyph table also holds the common characters (GB2312 level 1 for font 2,
Big5 level 1 for font 0). Normal builds add only what the labels use; the
card resident firmware (common=True) adds all of them so card patches can
bring new labels without a reflash (docs/card-loader.md).

The rebuilt fonts and copies of the 25-entry font pointer and size tables
live in the owned tail; the literals at 0x104530F4 and 0x104530F8, which boot
code hands to the font engine, are redirected to the copies. Stock font data
stays untouched. QEMU evidence: docs/ui-qemu.md.
"""
import struct

from ...platform.fonts import CJK_FONTS, FONT_COUNT, FONT_SIZES, FONT_SIZES_LITERAL, FONT_TABLE, FONT_TABLE_LITERAL, decode_glyph, extend_font, extra_glyphs, font_blob


def label_chars(font):
    """Printable ASCII plus every character the Chinese labels use in `font`."""
    from ...platform.fonts import HANS_FONT
    from .text import TRANSLATIONS
    column = 0 if font == HANS_FONT else 1
    return set(map(chr, range(0x20, 0x7F))) | {c for pair in TRANSLATIONS.values() for c in pair[column]}


def add(plan, functions, common=False):
    if 'cjk_font_table' in functions:
        return functions['cjk_font_table']
    if getattr(plan, 'card', False):
        # Boot code hands these tables to the font engine before the card
        # loader runs, so card builds use the copies the resident firmware has.
        functions['cjk_font_table'] = plan.resident['cjk_font_table']
        return functions['cjk_font_table']
    pointers = list(plan.image.words(FONT_TABLE, FONT_COUNT))
    sizes = list(plan.image.words(FONT_SIZES, FONT_COUNT))
    added = {}
    for font in CJK_FONTS:
        wanted = None if common else label_chars(font)
        chosen = {char: record for char, record in extra_glyphs(font).items()
                  if wanted is None or char in wanted}
        glyphs = {ord(char): decode_glyph(bytes.fromhex(record))[0] for char, record in chosen.items()}
        blob = extend_font(font_blob(plan.image, font), glyphs)
        pointers[font], sizes[font] = plan.alloc(blob), len(blob)
        added[str(font)] = len(chosen) if common else ''.join(chosen)
    table = plan.alloc(struct.pack(f'<{FONT_COUNT}I', *pointers))
    size_table = plan.alloc(struct.pack(f'<{FONT_COUNT}I', *sizes))
    plan.edit(FONT_TABLE_LITERAL, struct.pack('<2I', table, size_table), 'cjk-font-tables',
              expected=struct.pack('<2I', FONT_TABLE, FONT_SIZES))
    functions['cjk_font_table'] = table
    plan.features['cjk_fonts'] = dict(font_table=hex(table), size_table=hex(size_table), added_glyphs=added,
                                      source='Noto Sans CJK / Noto Sans Mono CJK (SIL OFL 1.1)')
    return table
