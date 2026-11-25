"""Research-only dormant hook on the stock subject-detection result writer, for SD modules.

0x101944B0(result /* 0xF0 bytes */, xy /* int16[2] */, wh /* int16[2] */) is
called from 0x10071D36 on every update of the face / pet detection (about
10-20 Hz in live view, also with no subject). It copies the result to
0xC237E2E8 and the face box to the debug record (0xC237F578 + 0xAC..0xB8).
See docs/af-tracking-research.md.

Its first eight bytes (push {r4-r6,lr}; mov r5,r1; mov r4,r2; movs r2,#0xF0)
are position independent; they move to a stub that first, only while
FACE_CELL is non-zero, calls cb(result, xy, wh) in the caller's task (before
the stock copy: the module sees the previous update in RAM and the new one in
the arguments), then runs them and returns to 0x101944B8.

FACE_CELL is +0x1C of the unused page-0 menu record at 0xC1832D24 (sound,
aspect and LUT use +0x10/+0x14/+0x18, e-VR +0x20, IMU +0x24, the built-in
autostart +0x28); +0x1C was the removed recording hook's cell.
"""
from ...assembly import assemble, jump

SITE = 0x101944B0
RETURN = 0x101944B8
STOCK = bytes.fromhex('70b5 0d46 1446 f022')
FACE_CELL = 0xC1832D40


def source():
    # eight registers pushed: the callback gets an 8-byte aligned stack
    return f'''
        push {{r0, r1, r2, r3, r4, r5, r12, lr}};
        movw r3, #{FACE_CELL & 0xFFFF}; movt r3, #{FACE_CELL >> 16}; ldr r3, [r3];
        cbz r3, skip;
        blx r3;
        skip: pop {{r0, r1, r2, r3, r4, r5, r12, lr}};
        push {{r4, r5, r6, lr}};
        mov r5, r1;
        mov r4, r2;
        movs r2, #0xF0;
        ldr.w pc, ={RETURN | 1};
    '''


def add(plan):
    if plan.image.read(SITE, len(STOCK)) != STOCK:
        raise ValueError('Unexpected stock bytes at the detection result hook site')
    if plan.image.words(FACE_CELL, 1) != (0,):
        raise ValueError('Detection hook cell is not zero in stock data')
    code = plan.alloc(bytes(64))
    raw = assemble(source(), code, 64)
    if (RETURN | 1).to_bytes(4, 'little') not in raw:
        raise ValueError('Detection hook lost its return')
    plan.put(code, raw)
    plan.edit(SITE, jump(SITE, code, 8), 'detection-result-hook', expected=STOCK)
    plan.features['face_hook'] = dict(
        site=hex(SITE), code=hex(code), cell=hex(FACE_CELL),
        callback='cb(result 0xF0 bytes, xy int16[2], wh int16[2]) before the stock copy, in the caller task',
        dormant='The stock writer runs unchanged while the cell is zero')
    return code
