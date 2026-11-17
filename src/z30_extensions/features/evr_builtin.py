"""Research-only: a built-in copy of the gyro stabilizer module (EVRSTAB) that
starts by itself, without an SD module.

Trigger and escape hatch
------------------------
AUTO_CELL (+0x28 of the unused page-0 menu record at 0xC1832D24; the e-VR and
IMU hooks use +0x20/+0x24) is zero at boot. The e-VR path-1 hook, which only
runs in movie live view with electronic VR on, sets it to REQUESTED once. The
IMU-task hook sees REQUESTED after a good stock accelerometer read and calls
autostart() in the IMU task, so all I2C stays in the task that already owns
it. Photo mode (or e-VR off) never runs the e-VR hook, so a camera that boots
there never starts the module: the stock Setup -> Firmware version page stays
reachable even if the built-in module misbehaves. With the Gyro e-VR menu page
(gyro_ui) built in, autostart first asks the menu choice through a get_property
shim (GYRO_KEY) and drops the request when it is OFF.

autostart()
-----------
Allocates one pool block [code | context 64 | api 20 | handler slot], copies
the module code out of the EG image, publishes the block in AUTO_CELL, makes
the copy visible to both cores (the same shared cache service the SD host
uses), calls the module entry with a private API (set_text is a no-op,
set_handler records the run handler in the block), then runs the handler
twice: arm (gyro, hooks, OFF) and OFF -> first gyro mode. Any failure leaves
AUTO_CELL = FAILED and the stock paths untouched.

The SD host's Run row drives the built-in handler while no SD module is
loaded (module_host builtin=True), and refuses to load CORE.ZMOD while the
built-in copy is active.
"""
import struct
from ..assembly import assemble, constant, invoke
from ..modules import ABI, API_BYTES, CONTEXT_BYTES
# The built-in copy is copied out of the EG image, not loaded by the SD module host,
# so the 16 KB CORE.ZMOD limit does not apply; 32 KB keeps the pool block small.
BUILTIN_MAX = 32768
from .module_cache import sync_shared_code

AUTO_CELL = 0xC1832D4C
REQUESTED = 1
BUSY = 2
FAILED = 0xFFFF
POOL_ALLOC = 0x10211F4C
MEMCPY = 0x10199BDC          # ARM
BZERO = 0x10199C40
GET_PROPERTY = 0x10465985
# Private property key: the module asks for the Gyro e-VR menu choice through
# api->get_property(GYRO_KEY) (0 Standard, 1 Enhanced, 2 Native crop, 3 OFF);
# every other key goes to the stock getter.
GYRO_KEY = 0x47595230
CHOICE_OFF = 3


def layout(code_size):
    ctx = (code_size + 7) & ~7
    api = ctx + CONTEXT_BYTES
    slot = api + 32
    return dict(ctx=ctx, api=api, slot=slot, total=slot + 32)


def add(plan, code, choice=None):
    code = bytes(code)
    if not code or len(code) > BUILTIN_MAX or len(code) % 4:
        raise ValueError(f'Built-in module code must be 4-byte aligned and 1..{BUILTIN_MAX} bytes')
    if plan.image.words(AUTO_CELL, 1) != (0,):
        raise ValueError('Built-in module cell is not zero in stock data')
    lay = layout(len(code))
    blob = plan.alloc(code)
    noop = plan.alloc(bytes(8))
    plan.put(noop, assemble('movs r0,#1; bx lr;', noop, 8))
    sethandler = plan.alloc(bytes(32))
    plan.put(sethandler, assemble(f'''
        {constant('r1', AUTO_CELL)} ldr r1,[r1];
        {constant('r2', lay['slot'])} str r0,[r1,r2]; movs r0,#1; bx lr;
    ''', sethandler, 32))
    getter = GET_PROPERTY
    if choice:
        getter = plan.alloc(bytes(32))
        plan.put(getter, assemble(f'''
            {constant('r12', GYRO_KEY)} cmp r0,r12; beq mine;
            ldr.w pc,={GET_PROPERTY | 1};
            mine: ldr.w pc,={choice | 1};
        ''', getter, 32))
        getter |= 1
    sync = plan.alloc(bytes(256))
    raw = sync_shared_code(sync, BUILTIN_MAX)
    if len(raw) > 256:
        raise ValueError('Shared cache routine exceeds its slot')
    plan.put(sync, raw)
    c = constant
    auto = plan.alloc(bytes(384))
    plan.put(auto, assemble(f'''
        push {{r4-r7,lr}};
        {c('r4', AUTO_CELL)} movs r0,#{BUSY}; str r0,[r4];
        {f"{c('r0', GYRO_KEY)} {invoke(getter)} cmp r0,#{CHOICE_OFF}; beq idle;" if choice else ''}
        {c('r0', lay['total'])} movs r1,#0; {invoke(POOL_ALLOC)}
        cmp r0,#0; beq fail; mov r5,r0; tst r5,#7; bne fail;
        mov r0,r5; {c('r1', blob)} {c('r2', len(code))} {invoke(MEMCPY, thumb=False)}
        {c('r0', lay['ctx'])} add r0,r5,r0; {c('r1', lay['total'] - lay['ctx'])} {invoke(BZERO)}
        {c('r6', lay['api'])} add r6,r5,r6;
        movs r0,#{ABI}; str r0,[r6]; movs r0,#{API_BYTES}; str r0,[r6,#4];
        {c('r0', getter)} str r0,[r6,#8];
        {c('r0', noop | 1)} str r0,[r6,#12]; {c('r0', sethandler | 1)} str r0,[r6,#16];
        dmb ish; str r5,[r4];
        mov r0,r5; {c('r1', len(code))} {invoke(sync)}
        cmp r0,#1; bne fail;
        {c('r7', lay['ctx'])} add r7,r5,r7;
        mov r0,r6; mov r1,r7; orr r3,r5,#1; blx r3;
        cmp r0,#1; bne fail;
        {c('r0', lay['slot'])} ldr r3,[r5,r0]; tst r3,#1; beq fail;
        mov r0,r6; mov r1,r7; blx r3;
        {c('r0', lay['slot'])} ldr r3,[r5,r0];
        mov r0,r6; mov r1,r7; blx r3;
        pop {{r4-r7,pc}};
        fail: {c('r0', FAILED)} str r0,[r4];
        pop {{r4-r7,pc}};
        idle: movs r0,#0; str r0,[r4];
        pop {{r4-r7,pc}};
    ''', auto, 384))
    plan.features['evr_builtin'] = dict(
        cell=hex(AUTO_CELL), code=hex(blob), code_bytes=len(code), autostart=hex(auto),
        layout={k: hex(v) for k, v in lay.items()},
        trigger='e-VR path-1 hook requests; IMU task starts unless the menu says OFF',
        property_shim=hex(getter) if choice else None, gyro_key=hex(GYRO_KEY),
        escape='Boot in photo mode (or menu OFF): the module never starts')
    return dict(autostart=auto, layout=lay)
