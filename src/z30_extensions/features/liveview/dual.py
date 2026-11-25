"""Standalone histogram + horizon feature; preserves native eligibility gates."""
import struct
from ...patch import Edit, apply_patch
from ...platform.z30_120 import BASE, CAPACITY, STOCK_SHA256, StockImage
from ...thumb import Thumb

GATE = 0x1044FF70
WIDGET_GATE = 0x1036E8B4
HISTOGRAM = 0xC1826714
SNAPSHOT = 0xC2016A68
HOOKS = (
    (GATE, bytes.fromhex('8d4810b5002490f89601022812d1'), True, 0x1044FF7E, 0x1044FFA4),
    (WIDGET_GATE, bytes.fromhex('90f89601022803d1'), False, 0x1036E8BC, 0x1036E8C4),
)
VIDEO_STOCK_WINDOWS = (
    (0x1016E150, bytes.fromhex('524b103310e0089c')),
    (0x1016E09E, bytes.fromhex('0fd1')),
)


def gate_code(address, prologue, yes, no):
    a = Thumb(address)
    if prologue:
        a.emit('10b5 0024')  # Native push {r4,lr}; movs r4,#0.
        a.load(0, 'snapshot', SNAPSHOT)
    a.emit('90f89601 0228')  # ldrb.w r0,[r0,#0x196]; cmp r0,#2.
    a.branch(0, 'accepted')
    a.emit('0328')
    a.branch(0, 'accepted')
    a.emit('0528')
    a.branch(0, 'accepted')
    a.load(3, 'no', no | 1)
    a.emit('1847')
    a.label('accepted')
    a.load(3, 'yes', yes | 1)
    a.emit('1847')
    return a.finish()


def build(source, tail_base=None):
    """tail_base: assemble the tail for another address (card builds only;
    the returned image is then not bootable and only its edits and tail count)."""
    stock = StockImage(source)
    source = stock.data
    if tail_base is None:
        tail_base = BASE + len(source) - 2
    if stock.words(0x1073F830 + 31 * 4, 1) != (HISTOGRAM,):
        raise ValueError('Unexpected histogram descriptor binding')
    if stock.words(HISTOGRAM + 4, 9) != (0x444,) * 9:
        raise ValueError('Unexpected histogram visibility masks')
    for address, expected in VIDEO_STOCK_WINDOWS:
        if stock.read(address, len(expected)) != expected:
            raise ValueError('Video shutter source differs from stock')
    append = bytearray()
    edits = []
    hooks = []
    for site, expected, prologue, yes, no in HOOKS:
        if site % 4 or stock.read(site, len(expected)) != expected:
            raise ValueError('Unexpected histogram instruction window')
        entry = tail_base + len(append)
        code = gate_code(entry, prologue, yes, no)
        jump = bytes.fromhex('dff800f0') + struct.pack('<I', entry | 1)
        jump += bytes.fromhex('00bf') * ((len(expected) - 8) // 2)
        edits.append(Edit(site - BASE, expected, jump, 'Histogram DISP gate'))
        append.extend(code)
        hooks.append({'site': hex(site), 'entry': hex(entry | 1), 'size': len(code),
                      'resume_accept': hex(yes), 'resume_reject': hex(no)})
    mask_address = stock.rom(HISTOGRAM + 4, 36)
    edits.append(Edit(mask_address - BASE, struct.pack('<9I', *([0x444] * 9)),
                      struct.pack('<9I', *([0xCCC] * 9)), 'Histogram visibility masks'))
    from .full_info import add as add_full_info
    full_info_hooks = add_full_info(stock, append, edits, tail_base)
    output, report = apply_patch(source, edits, append, expected_sha256=STOCK_SHA256,
                                 base=BASE, capacity=CAPACITY)
    for address, expected in VIDEO_STOCK_WINDOWS:
        if output[address - BASE:address - BASE + len(expected)] != expected:
            raise ValueError('Video shutter bytes were modified')
    report.update(feature='liveview_dual', model='Nikon Z30', firmware='C 1.20',
                  hooks=hooks, mask_rom_address=hex(mask_address),
                  full_info_disp=5, full_info_hooks=full_info_hooks,
                  camera_verified=False, video_shutter_stock=True,
                  limitations=[
                      'Offline instruction checks do not verify full camera operation.',
                      'Native display eligibility and output-mode restrictions remain.',
                      'No Camera Status, CFW version marker or shutter sound is included.',
                      'Feature builder uses validated EG; the CLI extracts it internally from the stock container.',
                  ])
    return output, report
