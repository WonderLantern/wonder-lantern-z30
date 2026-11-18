"""Research-only dormant IMU-task hook for SD modules.

The stock IMU task loop (around 0x1021C2A2..0x1021C580) sleeps one period
(0x1021C358) and reads the accelerometer with 0x1021BC38 at 0x1021C360. A
failed read branches to 0x1021C2F8, which parks the loop for good, so the
stock call, its result and both branches are kept exactly:

    0x1021C360  bl  0x1021BC38      ; r0 = [r4+4] + 0x20
    0x1021C364  cmp r0, #1
    0x1021C366  bne 0x1021C2F8
    0x1021C368  ...                  ; success path

These eight bytes jump to a tail stub that performs the stock call, then,
only while IMU_CELL is non-zero, calls the module as cb(stock_result) in the
IMU task, restores r0 and takes the stock branch. The callback may use the
I2C bus (this task already does) but must not change the stock result.
IMU_CELL is +0x24 of the unused page-0 menu record (evr_hook uses +0x20).
"""
from ..assembly import assemble, jump

SITE = 0x1021C360
SUCCESS = 0x1021C368
FAILURE = 0x1021C2F8
ACCEL_READ = 0x1021BC38
# bl 0x1021BC38; cmp r0,#1; bne 0x1021C2F8
STOCK = bytes.fromhex('fff76afc 0128 c7d1')
IMU_CELL = 0xC1832D48


def source(autostart=None, boot=False):
    # autostart (evr_builtin): while the module cell is still zero, a good stock
    # read with the built-in request pending starts the built-in module here.
    auto = ''
    if autostart:
        from .evr_builtin import AUTO_CELL, REQUESTED
        auto = f'''
        idle: cmp r4, #1; bne stock;
        movw r3, #{AUTO_CELL & 0xFFFF}; movt r3, #{AUTO_CELL >> 16}; ldr r3, [r3];
        cmp r3, #{0 if boot else REQUESTED}; bne stock;
        ldr.w r12, ={autostart | 1}; blx r12;'''
    return f'''
        push {{r4, r5}};
        ldr.w r12, ={ACCEL_READ | 1}; blx r12;
        mov r4, r0;
        movw r3, #{IMU_CELL & 0xFFFF}; movt r3, #{IMU_CELL >> 16}; ldr r3, [r3];
        cbz r3, {'idle' if autostart else 'stock'};
        blx r3;
        {'b stock;' if autostart else ''}{auto}
        stock: mov r0, r4;
        pop {{r4, r5}};
        cmp r0, #1; bne failed;
        ldr.w pc, ={SUCCESS | 1};
        failed: ldr.w pc, ={FAILURE | 1};
    '''


def add(plan, autostart=None, boot=False):
    # boot (auto_arm): start the built-in copy on the first good read after power-on
    # (cell still 0) instead of waiting for an e-VR request.
    if plan.image.read(SITE, len(STOCK)) != STOCK:
        raise ValueError('Unexpected stock bytes at the IMU hook site')
    if plan.image.words(IMU_CELL, 1) != (0,):
        raise ValueError('IMU hook cell is not zero in stock data')
    size = 128 if autostart else 96
    code = plan.alloc(bytes(size))
    raw = assemble(source(autostart, boot), code, size)
    for target in (ACCEL_READ, SUCCESS, FAILURE) + ((autostart,) if autostart else ()):
        if (target | 1).to_bytes(4, 'little') not in raw:
            raise ValueError('IMU hook lost a stock call or branch target')
    plan.put(code, raw)
    plan.edit(SITE, jump(SITE, code, 8), 'imu-task-hook', expected=STOCK)
    plan.features['imu_hook'] = dict(
        site=hex(SITE), code=hex(code), cell=hex(IMU_CELL),
        callback='cb(stock_accel_result r0) in the stock IMU task after its accel read',
        dormant='Stock call and branches run unchanged while the cell is zero',
        autostart=('boot' if boot else 'request') if autostart else None)
    return code
