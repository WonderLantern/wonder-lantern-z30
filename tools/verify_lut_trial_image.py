"""End-to-end Unicorn verification of the built LUT trial image.

Builds the trial firmware exactly as a user would, then extracts the gate,
core and LUT block from the patched image at the addresses the report names
and runs the format-1 parity case against the host reference. This closes the
loop between the feature assembly, the plan layout and the verified core.
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z30_extensions.host import lut
from z30_extensions.container import extract_eg
from native_runtime import Context, RAM, STOP
from trace_lut_retouch import put, returning
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP
from build_lut_trial import build
from verify_lut_core_native import (PARAMS, DESC_IN, DESC_OUT, Y_IN, C_IN, Y_OUT, C_OUT,
                                    image_descriptor, gradient_image)

BASE = 0x10020000


def main():
    stock = extract_eg(Path('Z_30_0120.bin').read_bytes())
    image, report = build(stock)
    feature = report['features']['lut_retouch']
    gate = int(feature['gate'], 16)
    entry = int(feature['gate_entry'], 16) & ~1
    block = int(feature['block'], 16)

    # The patch landed: gate window replaced, expected source bytes recorded.
    edits = {e['name']: e for e in report['edits']}
    patch = next(e for name, e in edits.items() if name.startswith('Index-6'))
    assert patch['expected_hex'] == '002810b50ad00446fff7'
    assert struct.unpack_from('<H', image, int(patch['offset'], 16))[0] == 0xf8df  # ldr.w pc

    c = Context(stock, image)
    u = c.u

    def record(uc, address, size, unused):
        if address in (0x10204B48, 0x10204B0C):
            returning(uc, 0)

    u.hook_add(UC_HOOK_CODE, record)
    binary = bytes(u.mem_read(block + 8, feature['embedded_bytes']))
    assert binary[:4] == b'WL3L' and lut.unpack_binary(binary)['size'] == feature['lut_size']

    y_in, c_in = gradient_image(16, 8, 16, 16)
    u.mem_write(Y_IN, y_in)
    u.mem_write(C_IN, c_in)
    u.mem_write(Y_OUT, bytes(0x8000))
    u.mem_write(C_OUT, bytes(0x8000))
    image_descriptor(u, DESC_IN, 16, 16, 16, 8, Y_IN, C_IN)
    image_descriptor(u, DESC_OUT, 16, 16, 16, 8, Y_OUT, C_OUT)
    u.mem_write(PARAMS, bytes(0x18))
    put(u, PARAMS + 4, DESC_IN)
    put(u, PARAMS + 0x10, DESC_OUT)
    result = c.call(entry, (PARAMS,))
    status = u.mem_read(PARAMS + 0x14, 1)[0]
    assert result == 1 and status == 1, (result, status)
    y_got = bytes(u.mem_read(Y_OUT, 16 * 8))
    c_got = bytes(u.mem_read(C_OUT, 16 * 8))
    y_want, c_want = lut.apply_frame_int(binary, y_in, c_in, 16, 8, 16, 16)
    assert y_got == y_want and c_got == c_want, 'image core diverged from host reference'

    # Disabled flag must fall through to the stock path: the gate replicates
    # the displaced window and calls the stock validator (hooked here).
    fallthrough = []
    def stock_probe(uc, address, size, unused):
        if address == 0x1025FB00:
            fallthrough.append(uc.reg_read(UC_ARM_REG_SP))
            returning(uc, 0)
    u.hook_add(UC_HOOK_CODE, stock_probe)
    u.mem_write(block, struct.pack('<I', 0))
    u.mem_write(PARAMS, bytes(0x18))
    put(u, PARAMS + 4, DESC_IN)
    put(u, PARAMS + 0x10, DESC_OUT)
    result = c.call(entry, (PARAMS,))
    assert len(fallthrough) == 1 and result == 0
    assert u.mem_read(PARAMS + 0x14, 1)[0] == 0
    print('PASS: built-image core matches host reference; kill switch falls through to stock.')
    print(json.dumps(dict(gate=hex(gate), entry=hex(entry | 1), block=hex(block),
                          edits=report['edits'], sha256=report['sha256'],
                          size=hex(report['size']), append=report['append_address']),
                     indent=2))
    print(json.dumps(dict(gate=hex(gate), entry=hex(entry | 1), block=hex(block),
                          edits=report['edits'], sha256=report['sha256'],
                          size=report['size'], append=report['append_address']),
                     indent=2))


if __name__ == '__main__':
    main()
