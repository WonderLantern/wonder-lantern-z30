"""SD-card feature patches: a small resident firmware plus Z30/WL.PATCH.

Camera facts this relies on (docs/card-loader-probe.md, measured on a Z30):
- only EG 0x10000000..0x106FFFFF is a DRAM copy; it is written through its
  identity RAM alias at 0xCC400000 (no DACR change);
- the rest of the EG, the tail included, cannot be written;
- main RAM 0xC1800000..0xC79FFFFF (scatter data, bss, heap) is cacheable RAM.

So the feature tail goes to RAM. It is assembled at LINK_BASE; every address
dependence is found by assembling at two more bases and comparing. There are
two kinds: a 32-bit word (ABS32) and the high half of a Thumb MOVT. The loader
places the tail on a 64 KiB boundary, so MOVW halves never change.

The resident firmware carries what boot reads before any loader can run (the
CJK fonts with the common character sets) and the loader. Without a card it
behaves as stock apart from those fonts. Its report lists what the loader
supports (`capabilities`), and card patches are built against the resident
that is on the camera, so newer tools keep serving an older resident as long
as the patch needs nothing it lacks.

File format 2: a header, then sections (type, flags, length, payload). The
TAIL section comes first so the loader can read it straight onto its 64 KiB
boundary. A section the loader does not know is skipped unless its flags mark
it REQUIRED, in which case the whole patch is refused.
"""
import binascii
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .build import build
from .features import card_loader, cjk_fonts, firmware_banner
from .plan import Plan
from .platform.z30_120 import BASE, SCATTER

MAGIC = b'WLCP'
FORMAT = card_loader.FORMAT
LINK_BASE = 0x20000000
CHECK_BASES = (0x31230000, 0x42460000)   # 64 KiB aligned, differing in both MOVT nibbles
ALIGN = 0x10000
DRAM_END = card_loader.CODE_HI            # EG below this is a writable DRAM copy
DRAM_ALIAS = card_loader.ALIAS            # add to a code address to get its RAM alias
RAM_LO, RAM_HI = card_loader.RAM_LO, card_loader.RAM_HI
ABS32, MOVT = 0, 1
HEADER = struct.Struct('<4sIIIII')        # magic, format, resident id, link base, body bytes, body crc32
SECTION = struct.Struct('<HHI')           # type, flags, payload bytes
TAIL, RELOCS, EDITS, INIT = card_loader.SECTIONS
REQUIRED = 1

# Readable at the start of the resident tail (right after the stock image), so a
# hex dump or strings of the flashed EG names the project and its licence.
BANNER = (b'Wonder Lantern Z30 - resident card loader\n'
          b'Copyright (c) 2026 CaCl2 (GitHub: RealCaCl2)\n'
          b'MIT License\0')


@dataclass
class CardEdit:
    address: int            # runtime address: EG code below DRAM_END, or main RAM
    expected: bytes
    replacement: bytes      # linked at LINK_BASE
    relocs: list            # (offset, kind) inside replacement
    name: str = ''


@dataclass
class CardPatch:
    resident_id: int
    tail: bytes
    relocs: list            # (offset, kind) inside tail
    edits: list = field(default_factory=list)
    init: list = field(default_factory=list)     # tail offsets of load-time functions
    link_base: int = LINK_BASE
    extra: list = field(default_factory=list)    # (type, flags, payload) sections loaders may not know


@dataclass
class Resident:
    identity: int
    image: bytes
    symbols: dict
    capabilities: dict
    report: dict = field(default_factory=dict)


def runtime_address(offset, size):
    """Where the loader writes an edit recorded at decoded-EG offset `offset`."""
    address = BASE + offset
    source, destination, length, _ = SCATTER
    if source <= address and address + size <= source + length:
        return destination + address - source
    if BASE <= address and address + size <= DRAM_END:
        return address
    raise ValueError(f'Edit at {address:#x} lies in EG memory the camera cannot write')


def _movt_imm(h1, h2):
    return ((h1 & 15) << 12) | (((h1 >> 10) & 1) << 11) | (((h2 >> 12) & 7) << 8) | (h2 & 255)


def _set_movt_imm(h1, h2, imm):
    h1 = (h1 & ~0x040F) | ((imm >> 12) & 15) | (((imm >> 11) & 1) << 10)
    h2 = (h2 & ~0x70FF) | (((imm >> 8) & 7) << 12) | (imm & 255)
    return h1, h2


def relocate(data, relocs, delta):
    """Apply relocations for a move by `delta` (a multiple of ALIGN)."""
    if delta % ALIGN:
        raise ValueError('Relocation delta must be a multiple of 64 KiB')
    out = bytearray(data)
    for offset, kind in relocs:
        if kind == ABS32:
            word, = struct.unpack_from('<I', out, offset)
            struct.pack_into('<I', out, offset, (word + delta) & 0xFFFFFFFF)
        elif kind == MOVT:
            h1, h2 = struct.unpack_from('<HH', out, offset)
            struct.pack_into('<HH', out, offset, *_set_movt_imm(h1, h2, (_movt_imm(h1, h2) + (delta >> 16)) & 0xFFFF))
        else:
            raise ValueError('Unknown relocation kind')
    return bytes(out)


def _is_movt(h1):
    return h1 & 0xFBF0 == 0xF2C0


def find_relocs(a, b, delta, where='tail'):
    """Relocations explaining every difference between two links `delta` apart."""
    if len(a) != len(b):
        raise ValueError(f'{where}: size differs between links')
    relocs, i = [], 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        for k in (i & ~1, (i & ~1) - 2):
            if k < 0 or k + 4 > len(a) or a[k:i] != b[k:i]:
                continue
            if relocate(a[k:k + 4], [(0, ABS32)], delta) == b[k:k + 4]:
                relocs.append((k, ABS32))
                break
            h1, h2 = struct.unpack_from('<HH', a, k)
            if _is_movt(h1) and relocate(a[k:k + 4], [(0, MOVT)], delta) == b[k:k + 4]:
                relocs.append((k, MOVT))
                break
        else:
            raise ValueError(f'{where}+{i:#x}: address dependence is neither ABS32 nor MOVT')
        i = k + 4
    return relocs


# --- resident firmware -------------------------------------------------------

def _resident_plan(source, identity):
    plan = Plan(source)
    banner = plan.alloc(BANNER)
    functions = {}
    cjk_fonts.add(plan, functions, common=True)
    card_loader.add(plan, identity)
    firmware_banner.add(plan)
    return plan, dict(cjk_font_table=functions['cjk_font_table'], banner=banner)


def _capabilities():
    from .platform.fonts import CJK_FONTS, extra_glyphs
    return dict(format=FORMAT, sections=list(card_loader.SECTIONS), init_abi=card_loader.INIT_ABI,
                code=[hex(card_loader.CODE_LO), hex(card_loader.CODE_HI)],
                ram=[hex(RAM_LO), hex(RAM_HI)],
                glyphs={str(font): ''.join(sorted(extra_glyphs(font))) for font in CJK_FONTS})


def build_resident(source):
    """Stock plus everything boot needs before a card can be read, and the loader.

    The resident id is the CRC32 of the resident edits and tail built with id 0;
    the loader then carries it and refuses card patches made for anything else.
    """
    plan, symbols = _resident_plan(source, 0)
    identity = binascii.crc32(bytes(plan.tail) + b''.join(
        struct.pack('<I', e.offset) + e.replacement for e in plan.edits))
    plan, symbols = _resident_plan(source, identity)
    image, report = plan.finish()
    capabilities = _capabilities()
    report.update(card_resident=True, resident_id=hex(identity), banner=BANNER[:-1].decode(),
                  resident_symbols={k: hex(v) for k, v in symbols.items()},
                  capabilities=capabilities)
    return Resident(identity, image, symbols, capabilities, report)


def load_resident(image_path, report_path):
    """The resident EG that is on the camera, from its build output."""
    report = json.loads(Path(report_path).read_text(encoding='utf-8'))
    if not report.get('card_resident'):
        raise ValueError(f'{report_path} is not a resident EG report')
    capabilities = report.get('capabilities')
    if not capabilities or capabilities.get('format') != FORMAT:
        raise ValueError('The resident EG on the camera has an older card loader: build a new resident EG '
                         '(without --patch-only) and flash it')
    image = Path(image_path).read_bytes()
    if hashlib.sha256(image).hexdigest() != report['sha256']:
        raise ValueError(f'{image_path} does not match {report_path}')
    symbols = {k: int(v, 16) for k, v in report['resident_symbols'].items()}
    return Resident(int(report['resident_id'], 16), image, symbols, capabilities, report)


def _check_capabilities(plan, resident):
    caps = resident.capabilities
    if plan.init_functions and INIT not in caps['sections']:
        raise ValueError('The patch has load-time functions; the resident loader cannot run them')
    lo, hi = (int(v, 16) for v in caps['ram'])
    for address, expected, _, name in plan.ram_edits:
        if not (lo <= address and address + len(expected) <= hi):
            raise ValueError(f'{name}: {address:#x} is outside the RAM the resident loader writes')
    from .platform.fonts import CJK_FONTS, coverage
    for font in CJK_FONTS:
        have = coverage(font, False) | set(map(ord, caps['glyphs'].get(str(font), '')))
        missing = ''.join(sorted(c for c in cjk_fonts.label_chars(font) if ord(c) not in have))
        if missing:
            raise ValueError(f'Labels need characters the resident fonts lack ({missing}): '
                             'build a new resident EG and flash it')


# --- card patch ---------------------------------------------------------------

def build_card(source, preset, resident, menu_tab=False, build_tag='dev', ready_banner=True, banner_debug=False):
    """Link the preset at LINK_BASE, derive relocations, check them at a third base."""
    links = {}
    for base in (LINK_BASE,) + CHECK_BASES:
        plan, report = build(source, preset, card=True, tail_base=base, resident=resident.symbols,
                             menu_tab=menu_tab, build_tag=build_tag, resident_id=resident.identity,
                             ready_banner=ready_banner, banner_debug=banner_debug)
        links[base] = plan
    first, second, third = (links[b] for b in (LINK_BASE,) + CHECK_BASES)
    _check_capabilities(first, resident)
    delta = CHECK_BASES[0] - LINK_BASE
    moved = CHECK_BASES[1] - LINK_BASE
    tail_relocs = find_relocs(first.tail, second.tail, delta)
    edits = []
    for edit, other in zip(first.edits, second.edits):
        if (other.offset, other.expected, other.name) != (edit.offset, edit.expected, edit.name):
            raise ValueError('Edits differ between links')
        relocs = find_relocs(edit.replacement, other.replacement, delta, edit.name)
        start, expected = edit.offset, edit.expected
        if resident.image[start:start + len(expected)] != expected:
            # Only the loader trigger may be taken over (gyro-evr's IMU hook);
            # the loader is past that site when it writes it.
            if start != card_loader.IMU_SITE - BASE or len(expected) != len(card_loader.IMU_STOCK):
                raise ValueError(f'{edit.name}: the resident firmware changed these bytes')
            expected = resident.image[start:start + len(expected)]
        edits.append(CardEdit(runtime_address(edit.offset, len(expected)), expected,
                              edit.replacement, relocs, edit.name))
    if len(first.edits) != len(second.edits) or len(first.ram_edits) != len(second.ram_edits):
        raise ValueError('Edits differ between links')
    for (address, expected, replacement, name), other in zip(first.ram_edits, second.ram_edits):
        if other[:2] != (address, expected) or other[3] != name:
            raise ValueError('RAM edits differ between links')
        edits.append(CardEdit(address, expected, replacement,
                              find_relocs(replacement, other[2], delta, name), name))
    init = []
    for a, b, c in zip(first.init_functions, second.init_functions, third.init_functions):
        offset = (a & ~1) - LINK_BASE
        if (b & ~1) - CHECK_BASES[0] != offset or (c & ~1) - CHECK_BASES[1] != offset:
            raise ValueError('Load-time functions differ between links')
        if offset % 2 or not 0 <= offset < len(first.tail):
            raise ValueError('Load-time function outside the tail')
        init.append(offset)
    # The relocations must reproduce an independent link exactly.
    if relocate(first.tail, tail_relocs, moved) != bytes(third.tail):
        raise ValueError('Tail relocations do not reproduce the third link')
    third_edits = [e.replacement for e in third.edits] + [r[2] for r in third.ram_edits]
    for edit, other in zip(edits, third_edits):
        if relocate(edit.replacement, edit.relocs, moved) != other:
            raise ValueError(f'{edit.name}: relocations do not reproduce the third link')
    _check_overlaps(edits)
    patch = CardPatch(resident.identity, bytes(first.tail), tail_relocs, edits, init)
    report.update(card=dict(tail_bytes=len(patch.tail), tail_relocs=len(tail_relocs),
                            edits=len(edits), ram_edits=len(first.ram_edits), init_functions=len(init),
                            edit_relocs=sum(len(e.relocs) for e in edits)))
    return patch, report


def _check_overlaps(edits):
    end = 0
    for edit in sorted(edits, key=lambda e: e.address):
        if edit.address < end:
            raise ValueError(f'{edit.name}: overlaps another edit')
        end = edit.address + len(edit.expected)


def _words(values):
    return b''.join(struct.pack('<I', v) for v in values)


def _reloc_words(relocs):
    return _words((kind << 28) | offset for offset, kind in relocs)


def _section(kind, payload, flags=REQUIRED):
    if len(payload) % 4:
        raise ValueError('Section payloads are word aligned')
    return SECTION.pack(kind, flags, len(payload)) + payload


def pack(patch):
    if len(patch.tail) % 4:
        raise ValueError('Tail must be word aligned')
    edits = bytearray(struct.pack('<I', len(patch.edits)))
    for edit in patch.edits:
        size = len(edit.expected)
        edits += struct.pack('<IHH', edit.address, size, len(edit.relocs))
        edits += edit.expected + edit.replacement + bytes((-2 * size) % 4)
        edits += _reloc_words(edit.relocs)
    body = _section(TAIL, patch.tail) + _section(RELOCS, _reloc_words(patch.relocs)) + _section(EDITS, bytes(edits))
    if patch.init:
        body += _section(INIT, _words(patch.init))
    for kind, flags, payload in patch.extra:
        body += _section(kind, payload, flags)
    return HEADER.pack(MAGIC, FORMAT, patch.resident_id, patch.link_base, len(body), binascii.crc32(body)) + body


def _relocs(data, offset, count, limit):
    relocs = []
    for i in range(count):
        word, = struct.unpack_from('<I', data, offset + 4 * i)
        reloc = (word & 0x0FFFFFFF, word >> 28)
        if reloc[1] not in (ABS32, MOVT) or reloc[0] % 2 or reloc[0] + 4 > limit:
            raise ValueError('Bad relocation')
        relocs.append(reloc)
    return relocs, offset + 4 * count


def _edits(payload, base):
    count, = struct.unpack_from('<I', payload, 0)
    at, edits = 4, []
    for _ in range(count):
        address, length, nrel = struct.unpack_from('<IHH', payload, at)
        at += 8
        expected = payload[at:at + length]
        replacement = payload[at + length:at + 2 * length]
        at += 2 * length + (-2 * length) % 4
        relocs, at = _relocs(payload, at, nrel, length)
        if not length or len(replacement) != length or at > len(payload):
            raise ValueError('Bad edit record')
        edits.append(CardEdit(address, bytes(expected), bytes(replacement), relocs))
    if at != len(payload):
        raise ValueError('Trailing edit data')
    return edits


def parse(blob, known=card_loader.SECTIONS):
    """Read a patch the way a loader that knows `known` section types would."""
    if len(blob) < HEADER.size:
        raise ValueError('Truncated card patch')
    magic, fmt, ident, link, size, crc = HEADER.unpack_from(blob)
    if magic != MAGIC or fmt != FORMAT:
        raise ValueError('Not a card patch of this format')
    body = blob[HEADER.size:]
    if len(body) != size or binascii.crc32(body) != crc:
        raise ValueError('Card patch damaged')
    at, sections = 0, []
    while at < len(body):
        if at + SECTION.size > len(body):
            raise ValueError('Truncated section')
        kind, flags, length = SECTION.unpack_from(body, at)
        payload = body[at + SECTION.size:at + SECTION.size + length]
        if length % 4 or len(payload) != length:
            raise ValueError('Bad section length')
        sections.append((kind, flags, payload))
        at += SECTION.size + length
    if not sections or sections[0][0] != TAIL:
        raise ValueError('The tail section must come first')
    patch = CardPatch(ident, bytes(sections[0][2]), [], link_base=link)
    for kind, flags, payload in sections[1:]:
        if kind == RELOCS and kind in known:
            patch.relocs, _ = _relocs(payload, 0, len(payload) // 4, len(patch.tail))
        elif kind == EDITS and kind in known:
            patch.edits = _edits(payload, 0)
        elif kind == INIT and kind in known:
            patch.init = list(struct.unpack(f'<{len(payload) // 4}I', payload))
            if any(o % 2 or o >= len(patch.tail) for o in patch.init):
                raise ValueError('Bad load-time function offset')
        elif kind == TAIL or flags & REQUIRED:
            raise ValueError(f'Section {kind} needs a newer resident loader')
        else:
            patch.extra.append((kind, flags, bytes(payload)))
    return patch


def target_address(address, size):
    """Where the loader writes `size` bytes for runtime `address`, or None."""
    if card_loader.CODE_LO <= address and address + size <= card_loader.CODE_HI:
        return address + DRAM_ALIAS
    if RAM_LO <= address and address + size <= RAM_HI:
        return address
    return None


def load(patch, memory, base, resident_id, call=None):
    """Reference loader: what the camera does, on a byte memory.

    memory.read(address, size) / memory.write(address, data) see runtime
    addresses; code edits go through the DRAM alias like on the camera.
    Nothing is written unless every check passes. `call(address)` runs a
    load-time function and returns its result.
    """
    if patch.resident_id != resident_id:
        raise ValueError('Card patch was built for another resident firmware')
    if base % ALIGN:
        raise ValueError('Tail base must be 64 KiB aligned')
    delta = base - patch.link_base
    writes = []
    for edit in patch.edits:
        target = target_address(edit.address, len(edit.expected))
        if target is None:
            raise ValueError(f'{edit.address:#x}: not memory the loader writes')
        if memory.read(target, len(edit.expected)) != edit.expected:
            raise ValueError(f'{edit.address:#x}: memory differs from what the patch expects')
        writes.append((target, relocate(edit.replacement, edit.relocs, delta)))
    memory.write(base, relocate(patch.tail, patch.relocs, delta))
    for target, data in writes:
        memory.write(target, data)
    results = [call(base + offset) for offset in patch.init] if call else []
    return results


# --- kit on disk ----------------------------------------------------------------

RESIDENT_NAME, REPORT_NAME, PATCH_PATH = 'EG209020.BIN', 'EG209020.json', Path('Z30') / 'WL.PATCH'


def default_build_tag():
    """MM-DD and the short git commit, '+' when the tree has changes: shown on the status page.

    The packaged program has no git checkout and says 'app' instead."""
    import datetime
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    commit = None
    if not getattr(sys, 'frozen', False):
        def git(*args):
            try:
                return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True,
                                      check=True).stdout.strip()
            except (OSError, subprocess.CalledProcessError):
                return None
        commit = git('rev-parse', '--short=7', 'HEAD')
        if commit and git('status', '--porcelain', '--untracked-files=no'):
            commit += '+'
    return f'{datetime.date.today():%m-%d} {commit or "app"}'


def kit_outputs(folder, patch_only=False, loader_only=False):
    folder = Path(folder)
    resident = [folder / RESIDENT_NAME, folder / REPORT_NAME]
    patch = [folder / PATCH_PATH]
    return patch if patch_only else resident if loader_only else resident + patch


def write_kit(source_path, presets, folder, patch_only=False, menu_tab=False, build_tag=None,
              ready_banner=True, banner_debug=False, loader_only=False):
    """Write FOLDER/EG209020.BIN + .json (resident loader EG) and FOLDER/Z30/WL.PATCH.

    With patch_only only WL.PATCH is written, built for the resident EG already in
    FOLDER (the one flashed on the camera); with loader_only only the resident EG
    and its report (presets is ignored). Never overwrites a file."""
    from .container import CONTAINER_SHA256, extract_eg
    folder = Path(folder)
    eg, meta, patch_path = folder / RESIDENT_NAME, folder / REPORT_NAME, folder / PATCH_PATH
    if patch_only and loader_only:
        raise ValueError('patch_only and loader_only exclude each other')
    existing = [str(path) for path in kit_outputs(folder, patch_only, loader_only) if path.exists()]
    if existing:
        raise ValueError(f'Refusing to overwrite: {", ".join(existing)}')
    stock = extract_eg(Path(source_path).read_bytes())
    if patch_only:
        if not (eg.exists() and meta.exists()):
            raise ValueError(f'Updating only the card file needs the flashed {RESIDENT_NAME} '
                             f'and {REPORT_NAME} in {folder}')
        resident = load_resident(eg, meta)
    else:
        resident = build_resident(stock)
    blob = card_report = None
    if not loader_only:
        presets = [presets] if isinstance(presets, str) else list(presets)
        preset = presets[0] if len(presets) == 1 else presets
        patch, card_report = build_card(stock, preset, resident, menu_tab=menu_tab,
                                        build_tag=build_tag or default_build_tag(),
                                        ready_banner=ready_banner, banner_debug=banner_debug)
        blob = pack(patch)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    if not patch_only:
        report = resident.report
        report['container_sha256'] = CONTAINER_SHA256
        if card_report:
            report['card_patch'] = dict(presets=presets, menu_tab=menu_tab, **card_report['card'])
        with meta.open('x', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        with eg.open('xb') as f:
            f.write(resident.image)
        written += [eg, meta]
    if blob is not None:
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        with patch_path.open('xb') as f:
            f.write(blob)
        written.append(patch_path)
    return dict(resident=resident, resident_sha256=hashlib.sha256(resident.image).hexdigest(),
                patch_bytes=None if blob is None else len(blob),
                card=card_report and card_report['card'],
                enabled=card_report.get('enabled_features', []) if card_report else [], written=written)
