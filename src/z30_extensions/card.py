"""SD-card feature patches: a small resident firmware plus Z30/WL.PATCH.

Camera facts this relies on (docs/card-loader-probe.md, measured on a Z30):
- only EG 0x10000000..0x106FFFFF is a DRAM copy; it is written through its
  identity RAM alias at 0xCC400000 (no DACR change);
- the rest of the EG, the tail included, cannot be written;
- the initialized-data scatter is RAM at 0xC1800000 after boot.

So the feature tail goes to RAM. It is assembled at LINK_BASE; every address
dependence is found by assembling at two more bases and comparing. There are
two kinds: a 32-bit word (ABS32) and the high half of a Thumb MOVT. The loader
places the tail on a 64 KiB boundary, so MOVW halves never change.

The resident firmware carries what boot reads before any loader can run: the
CJK font tables (and, later, the loader itself). Without a card it behaves as
stock apart from those fonts.
"""
import binascii
import struct
from dataclasses import dataclass, field

from .build import build
from .features import card_loader, cjk_fonts
from .plan import Plan
from .platform.z30_120 import BASE, SCATTER

MAGIC = b'WLCP'
FORMAT = 1
LINK_BASE = 0x20000000
CHECK_BASES = (0x31230000, 0x42460000)   # 64 KiB aligned, differing in both MOVT nibbles
ALIGN = 0x10000
DRAM_END = 0x10700000                     # EG below this is a writable DRAM copy
DRAM_ALIAS = 0xCC400000 - 0x10000000      # add to a code address to get its RAM alias
ABS32, MOVT = 0, 1
HEADER = struct.Struct('<4sIIIIIIII')     # magic, format, resident id, link base, tail size,
                                          # tail relocs, edits, payload bytes, payload crc32


@dataclass
class CardEdit:
    address: int            # runtime address: EG code below DRAM_END or scatter RAM
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
    link_base: int = LINK_BASE


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


def _resident_plan(source, identity):
    plan = Plan(source)
    functions = {}
    cjk_fonts.add(plan, functions, common=True)
    card_loader.add(plan, identity)
    return plan, dict(cjk_font_table=functions['cjk_font_table'])


def build_resident(source):
    """Stock plus everything boot needs before a card can be read, and the loader.

    The resident id is the CRC32 of the resident edits and tail built with id 0;
    the loader then carries it and refuses card patches made for anything else.
    """
    plan, resident = _resident_plan(source, 0)
    identity = binascii.crc32(bytes(plan.tail) + b''.join(
        struct.pack('<I', e.offset) + e.replacement for e in plan.edits))
    plan, resident = _resident_plan(source, identity)
    image, report = plan.finish()
    report.update(card_resident=True, resident_id=hex(identity),
                  resident_symbols={k: hex(v) for k, v in resident.items()})
    return image, report, resident, identity


def build_card(source, preset, resident, resident_id, resident_image):
    """Link the preset at LINK_BASE, derive relocations, check them at a third base."""
    links = {}
    for base in (LINK_BASE,) + CHECK_BASES:
        plan, report = build(source, preset, card=True, tail_base=base, resident=resident)
        links[base] = plan
    first = links[LINK_BASE]
    delta = CHECK_BASES[0] - LINK_BASE
    tail_relocs = find_relocs(first.tail, links[CHECK_BASES[0]].tail, delta)
    edits = []
    for index, edit in enumerate(first.edits):
        other = links[CHECK_BASES[0]].edits[index]
        if (other.offset, other.expected, other.name) != (edit.offset, edit.expected, edit.name):
            raise ValueError('Edits differ between links')
        relocs = find_relocs(edit.replacement, other.replacement, delta, edit.name)
        start = edit.offset
        expected = edit.expected
        if resident_image[start:start + len(expected)] != expected:
            # Only the loader trigger may be taken over (gyro-evr's IMU hook);
            # the loader is past that site when it writes it.
            if start != card_loader.IMU_SITE - BASE or len(expected) != len(card_loader.IMU_STOCK):
                raise ValueError(f'{edit.name}: the resident firmware changed these bytes')
            expected = resident_image[start:start + len(expected)]
        edits.append(CardEdit(runtime_address(edit.offset, len(expected)), expected,
                              edit.replacement, relocs, edit.name))
    # The relocations must reproduce an independent link exactly.
    third = links[CHECK_BASES[1]]
    moved = CHECK_BASES[1] - LINK_BASE
    if relocate(first.tail, tail_relocs, moved) != bytes(third.tail):
        raise ValueError('Tail relocations do not reproduce the third link')
    for edit, other in zip(edits, third.edits):
        if relocate(edit.replacement, edit.relocs, moved) != other.replacement:
            raise ValueError(f'{edit.name}: relocations do not reproduce the third link')
    _check_overlaps(edits)
    patch = CardPatch(resident_id, bytes(first.tail), tail_relocs, edits)
    report.update(card=dict(tail_bytes=len(patch.tail), tail_relocs=len(tail_relocs),
                            edits=len(edits), edit_relocs=sum(len(e.relocs) for e in edits)))
    return patch, report


def _check_overlaps(edits):
    end = 0
    for edit in sorted(edits, key=lambda e: e.address):
        if edit.address < end:
            raise ValueError(f'{edit.name}: overlaps another edit')
        end = edit.address + len(edit.expected)


def _reloc_words(relocs):
    return b''.join(struct.pack('<I', (kind << 28) | offset) for offset, kind in relocs)


def pack(patch):
    body = bytearray(patch.tail)
    body += _reloc_words(patch.relocs)
    for edit in patch.edits:
        size = len(edit.expected)
        body += struct.pack('<IHH', edit.address, size, len(edit.relocs))
        body += edit.expected + edit.replacement + bytes((-2 * size) % 4)
        body += _reloc_words(edit.relocs)
    if len(patch.tail) % 4:
        raise ValueError('Tail must be word aligned')
    return HEADER.pack(MAGIC, FORMAT, patch.resident_id, patch.link_base, len(patch.tail),
                       len(patch.relocs), len(patch.edits), len(body), binascii.crc32(body)) + bytes(body)


def _relocs(data, offset, count, limit):
    relocs = []
    for i in range(count):
        word, = struct.unpack_from('<I', data, offset + 4 * i)
        reloc = (word & 0x0FFFFFFF, word >> 28)
        if reloc[1] not in (ABS32, MOVT) or reloc[0] + 4 > limit:
            raise ValueError('Bad relocation')
        relocs.append(reloc)
    return relocs, offset + 4 * count


def parse(blob):
    if len(blob) < HEADER.size:
        raise ValueError('Truncated card patch')
    magic, fmt, ident, link, tail_size, tail_relocs, count, size, crc = HEADER.unpack_from(blob)
    if magic != MAGIC or fmt != FORMAT:
        raise ValueError('Not a card patch of this format')
    body = blob[HEADER.size:]
    if len(body) != size or binascii.crc32(body) != crc:
        raise ValueError('Card patch damaged')
    if tail_size % 4 or tail_size > size:
        raise ValueError('Bad tail size')
    tail = body[:tail_size]
    relocs, at = _relocs(body, tail_size, tail_relocs, tail_size)
    edits = []
    for _ in range(count):
        address, length, nrel = struct.unpack_from('<IHH', body, at)
        at += 8
        expected = body[at:at + length]
        replacement = body[at + length:at + 2 * length]
        at += 2 * length + (-2 * length) % 4
        erelocs, at = _relocs(body, at, nrel, length)
        edits.append(CardEdit(address, bytes(expected), bytes(replacement), erelocs))
    if at != len(body):
        raise ValueError('Trailing card patch data')
    return CardPatch(ident, bytes(tail), relocs, edits, link)


def load(patch, memory, base, resident_id):
    """Reference loader: what the camera does, on a dict-like byte memory.

    memory.read(address, size) / memory.write(address, data) see runtime
    addresses; code edits go through the DRAM alias like on the camera.
    Nothing is written unless every check passes.
    """
    if patch.resident_id != resident_id:
        raise ValueError('Card patch was built for another resident firmware')
    if base % ALIGN:
        raise ValueError('Tail base must be 64 KiB aligned')
    delta = base - patch.link_base
    writes = []
    for edit in patch.edits:
        target = edit.address + DRAM_ALIAS if edit.address < DRAM_END else edit.address
        if memory.read(target, len(edit.expected)) != edit.expected:
            raise ValueError(f'{edit.address:#x}: memory differs from what the patch expects')
        writes.append((target, relocate(edit.replacement, edit.relocs, delta)))
    memory.write(base, relocate(patch.tail, patch.relocs, delta))
    for target, data in writes:
        memory.write(target, data)
