"""Research-only dormant e-VR result hooks for SD modules.

The VrMx task (entry 0x100B9862) has two e-VR paths, both running the image
algorithm 0x1010E044 and leaving a 0x78-byte result:

* path 1, 0x100B9768: result at sp+0x84 is packed into the 0x84-byte warp
  command (corners). Site 0x100B97B8 is where the "ran" and "skipped" stock
  branches meet.
* path 2, 0x100B9708: result at sp+0 is converted by 0x100B9FB2 into the
  0x18-byte translation-only command (mailbox channel 1). Site 0x100B9738
  directly follows the algorithm call.

Each site reads one RAM word, HOOK_CELL. While it is zero (boot value, and
the only value without a loaded module) the displaced stock instructions run
unchanged. A module may store a Thumb callback there:

    void frame(const uint8_t *input /* 0x68 */, uint8_t *result /* 0x78 */,
               const uint8_t *frame_meta /* or NULL */, uint32_t path /* 1|2 */);

The callback runs in the stock VrMx task and may rewrite result. HOOK_CELL is
+0x20 of the unused page-0 menu record at 0xC1832D24 (sound, aspect and LUT
use +0x10, +0x14, +0x18; the IMU hook uses +0x24).
"""
from ...assembly import assemble, jump

SITE = 0x100B97B8
RETURN = 0x100B97C0
# ldr r1,[r4,#0x34]; movw r0,#0x100; lsls r1,r1,#8
STOCK = bytes.fromhex('616b 40f20010 0902')
SITE2 = 0x100B9738
RETURN2 = 0x100B9740
LOGGER = 0x100BD46A
# ldr r1,[r5]; mov r0,sp; bl 0x100BD46A
STOCK2 = bytes.fromhex('2968 6846 03f095fe')
HOOK_CELL = 0xC1832D44
# e-VR context holder: per-channel context pointers, metadata at ctx+0x144
# (0x100B995C indexes it by input channel; 0x100B978E uses channel 1).
CONTEXT_HOLDER = 0xC2A01D08


def cell_load(idle='stock'):
    return (f'movw r12,#{HOOK_CELL & 0xFFFF}; movt r12,#{HOOK_CELL >> 16}; '
            f'ldr.w r12,[r12]; cmp.w r12,#0; beq {idle};')


def source(request=False):
    # request (evr_builtin): with no module armed, ask once for the built-in
    # module; only movie live view with e-VR on ever reaches this site.
    idle = ''
    if request:
        from ..gyro.builtin import AUTO_CELL, REQUESTED
        idle = f'''
        idle: movw r2,#{AUTO_CELL & 0xFFFF}; movt r2,#{AUTO_CELL >> 16};
        ldr r3,[r2]; cbnz r3, stock; movs r3,#{REQUESTED}; str r3,[r2]; b stock;'''
    return f'''
        {cell_load('idle' if request else 'stock')}
        ldr r2,[r5,#4]; ldr.w r2,[r2,#0x144];
        mov r0,r4; add r1,sp,#0x84; movs r3,#1; blx r12;
        {'b stock;' if request else ''}{idle}
        stock: ldr r1,[r4,#0x34]; movw r0,#0x100; lsls r1,r1,#8;
        ldr.w pc, ={RETURN | 1};
    '''


def source2():
    return f'''
        {cell_load()}
        mov r1,sp; mov r0,r5;
        ldr r2,[r5]; cmp r2,#3; bhs no_meta;
        movw r3,#{CONTEXT_HOLDER & 0xFFFF}; movt r3,#{CONTEXT_HOLDER >> 16};
        ldr.w r2,[r3,r2,lsl #2]; cbz r2, no_meta;
        ldr.w r2,[r2,#0x144]; b call;
        no_meta: movs r2,#0;
        call: movs r3,#2; blx r12;
        stock: ldr r1,[r5]; mov r0,sp;
        ldr.w r12, ={LOGGER | 1}; blx r12;
        ldr.w pc, ={RETURN2 | 1};
    '''


def _check(raw, targets, what):
    # Keystone has silently dropped malformed literal loads before; refuse a
    # hook that cannot reach its stock call or return.
    for target in targets:
        if (target | 1).to_bytes(4, 'little') not in raw:
            raise ValueError(f'{what} lost a stock call or return target')


def add(plan, request=False):
    if plan.image.read(SITE, len(STOCK)) != STOCK:
        raise ValueError('Unexpected stock bytes at the e-VR hook site')
    if plan.image.read(SITE2, len(STOCK2)) != STOCK2:
        raise ValueError('Unexpected stock bytes at the e-VR path-2 hook site')
    if plan.image.words(HOOK_CELL, 1) != (0,):
        raise ValueError('e-VR hook cell is not zero in stock data')
    for literal_site, pc_offset in ((0x100B977E, 0x3A0), (0x100B9726, 0x3F8)):
        if plan.image.words(((literal_site + 4) & ~3) + pc_offset, 1) != (CONTEXT_HOLDER,):
            raise ValueError('Unexpected e-VR context holder literal')
    size = 96 if request else 64
    code = plan.alloc(bytes(size))
    raw = assemble(source(request), code, size)
    _check(raw, (RETURN,), 'e-VR hook')
    plan.put(code, raw)
    plan.edit(SITE, jump(SITE, code, 8), 'evr-result-hook', expected=STOCK)
    code2 = plan.alloc(bytes(96))
    raw2 = assemble(source2(), code2, 96)
    _check(raw2, (LOGGER, RETURN2), 'e-VR path-2 hook')
    plan.put(code2, raw2)
    plan.edit(SITE2, jump(SITE2, code2, 8), 'evr-result-hook-path2', expected=STOCK2)
    plan.features['evr_hook'] = dict(
        sites=[hex(SITE), hex(SITE2)], code=[hex(code), hex(code2)], cell=hex(HOOK_CELL),
        callback='frame(input r0, result r1, frame_meta r2 or 0, path r3: 1 corners, 2 translation) in VrMx',
        dormant='Stock instructions run while the cell is zero (boot value)')
    return code
