"""Execute the stock XMP camera-raw-defaults writer (0x101B862E) with the crop hook.

The writer context is a synthetic fixture (zero-filled records); the real
Picture Control values, the final packet assembly, NEF/JPEG file writing and
Adobe import are not modeled. Checks, against the stock firmware run:
- no profile selected, or native area not 16:9: text identical to stock;
- profile selected: stock text followed by exactly that profile's crs: block,
  and the whole result is well-formed XML;
- buffer without room for the block: stock text, no crop, no overflow.
"""
import argparse
import json
import struct
import sys
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from z30_extensions.container import extract_eg
from z30_extensions.features.aspect.geometry import PROFILES
from z30_extensions.platform.z30_120 import sha256

WRITER = 0x101B862E


def run(context, capacity=None):
    from native_runtime import RAM
    u = context.u
    ctx, zero, text = RAM + 0x20000, RAM + 0x30000, RAM + 0x40000
    u.mem_write(zero, bytes(0x8000))
    u.mem_write(text, bytes(0x1000))
    # Every context word points at zeroed records; sub-writers read defaults.
    u.mem_write(ctx, struct.pack('<%dI' % (0x900 // 4), *([zero] * (0x900 // 4))))
    u.mem_write(ctx + 0xE8, struct.pack('<I', text))
    for offset in range(0x850, 0x880, 4):
        u.mem_write(ctx + offset, bytes(4))
    if capacity is not None:
        # Simulate a longer stock block: shrink capacity after the stock init.
        from unicorn import UC_HOOK_CODE
        def shrink(uc, address, size, data):
            uc.mem_write(ctx + 0x870, struct.pack('<I', capacity))
        hook = u.hook_add(UC_HOOK_CODE, shrink, begin=0x101B8650, end=0x101B8650)
    try:
        context.call(WRITER, (ctx,))
    finally:
        if capacity is not None:
            u.hook_del(hook)
    raw = bytes(u.mem_read(text, 0x1000))
    cursor, remaining, overflow = struct.unpack('<3I', u.mem_read(ctx + 0x86C, 12))
    return raw.split(b'\0')[0].decode('ascii'), cursor - text, remaining, overflow


def verify(stock, image, report):
    from native_runtime import Context
    from verify_sound_settings import Fixture
    feature = report['features']['aspect_profiles']
    crop = feature['xmp_crop']
    functions = {k: int(v, 16) for k, v in feature['functions'].items()}
    base_text, base_len, _, base_overflow = run(Context(stock, stock))
    assert base_overflow == 0 and base_text.startswith('<rdf:Description') and base_text.endswith('</rdf:Description>')
    properties = {0x501: 2, 0x188c: 0}
    built = Fixture(stock, image, report, {}, properties=properties)
    state = built.call(functions['aspect_get'])
    cases = []
    built.u.mem_write(state, struct.pack('<I', 0))
    off_text = run(built)[0]
    assert off_text == base_text
    cases.append(dict(selection=0, area=2, crop=False))
    for index, name in enumerate(PROFILES, 1):
        built.u.mem_write(state, struct.pack('<I', index))
        for area in (0, 4):
            properties[0x501] = area
            assert run(built)[0] == base_text, (name, area)
            cases.append(dict(selection=index, area=area, crop=False))
        properties[0x501] = 2
        text, length, remaining, overflow = run(built)
        assert text.startswith(base_text) and overflow == 0
        block = text[len(base_text):]
        assert len(block) == crop['text_bytes'] and length == len(text)
        left, top, right, bottom = crop['crops'][name]
        for key, value in (('CropLeft', left), ('CropTop', top), ('CropRight', right), ('CropBottom', bottom)):
            assert f'<crs:{key}>{value:.6f}</crs:{key}>' in block, (name, key)
        assert '<crs:HasCrop>True</crs:HasCrop>' in block
        xml.dom.minidom.parseString(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            + text + '</rdf:RDF></x:xmpmeta>')
        cases.append(dict(selection=index, area=2, crop=True, box=[left, top, right, bottom]))
        # Room left after the stock text: the block (plus terminator and one
        # spare byte) is written only when it fits; never overflow the stock block.
        need = crop['text_bytes'] + 2
        for spare in (1, need - 1, need):
            text, _, _, overflow = run(built, capacity=base_len + spare)
            assert overflow == 0, (name, spare)
            assert text == (base_text + block if spare >= need else base_text), (name, spare)
            cases.append(dict(selection=index, area=2, spare=spare, crop=text != base_text))
    return dict(source_sha256=sha256(stock), image_sha256=sha256(image), stock_text_bytes=base_len,
                stock_room=0x7FF - base_len, passed_cases=len(cases), cases=cases,
                limitations=['Synthetic writer context; Picture Control specific crd lengths vary.',
                             'Packet assembly, NEF/JPEG writing and Adobe import are not executed.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--deps', type=Path)
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    outcome = verify(extract_eg(args.source.read_bytes()), args.image.read_bytes(),
                     json.loads(args.report.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcome, indent=2) + '\n', encoding='utf-8')
    print(f'Passed {outcome["passed_cases"]} XMP crop cases (stock crd text {outcome["stock_text_bytes"]} bytes); saved {args.output}')
