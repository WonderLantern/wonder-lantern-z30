"""Resident loader for Z30/WL.PATCH (see card.py for the file and its checks).

Runs from the stock IMU task: the accelerometer call at 0x1021C360 jumps to a
stub that makes the stock call, gives the loader one tick, then takes the stock
branch with the stock result (same contract as imu_hook). The loader waits
START ticks after the task starts, tries to open the file every EVERY ticks
and gives up at LIMIT ticks, so a camera without the file stays stock.

One attempt that finds the file:
  read the header, check magic / format / resident id / sizes;
  allocate body + 64 KiB from the stock pool, read the body onto a 64 KiB
  boundary (the tail is its first part), require end of file, close;
  check CRC32, relocation bounds and every edit's target range and expected
  bytes (code edits through the DRAM alias, scatter edits in RAM);
  relocate the tail, clean / invalidate it on both cores;
  only then relocate and write every edit, syncing each code range.
Any failure before the writes frees the block and changes nothing.

CELL (+0x0C of the unused page-0 menu record) holds the state:
  bits 0-11 tick count while trying; bit 15 done;
  bits 8-11 result code once done; bits 16-31 the tail base on success.
Z30/WL.LOG gets one line whenever the file was found.
"""
import struct

from ..assembly import assemble, constant, invoke, jump
from .imu_hook import ACCEL_READ, FAILURE, SITE as IMU_SITE, STOCK as IMU_STOCK, SUCCESS as IMU_SUCCESS
from .module_cache import sync_shared_code
from .sound_layout import SUCCESS

CELL = 0xC1832D30
PATH = b'A:\\Z30\\WL.PATCH\0'
LOG_PATH = b'A:\\Z30\\WL.LOG\0'
MAGIC = 0x50434C57          # 'WLCP'
FORMAT = 1
MAX_BODY = 0x00800000       # 8 MiB
START, EVERY, LIMIT = 60, 10, 600   # ticks of ~50 ms: first try after ~3 s, give up after ~30 s
DONE = 0x8000
CODE_LO, CODE_HI = 0x10020000, 0x10700000
ALIAS = 0xCC400000 - 0x10000000
SCATTER_LO, SCATTER_HI = 0xC1800000, 0xC1800000 + 0xF8258
MEMSET, MEMCPY = 0x10199C40, 0x10199BDC
SET_PATH, OPEN, READ, CLOSE = 0x1033829E, 0x103382A2, 0x10338414, 0x1033832E
FD_CLOSE, FD_WRITE = 0x102C89EE, 0x102C8A6A
POOL_ALLOC, POOL_FREE = 0x10211F4C, 0x10211F5E
CREATE = 0x301

RESULTS = {1: 'loaded', 2: 'no file', 3: 'read failed', 4: 'bad file', 5: 'other resident firmware',
           6: 'no memory', 7: 'CRC mismatch', 8: 'bad structure', 9: 'memory differs from the patch',
           10: 'close failed', 11: 'cache sync failed (nothing written)',
           12: 'cache sync failed after writing (reboot)'}
SIZES = dict(stub=72, load=1024, read_all=96, crc32=64, check_relocs=64, target=96,
             validate=192, apply_relocs=192, apply_edits=256, write_log=320, sync=160)


def _n(address):
    return invoke(address)


def sources(f, resident_id, strings):
    c, call = constant, lambda name: invoke(f[name])
    code = {}
    code['stub'] = f'''
        push {{r4,r5}}; ldr.w r12,={ACCEL_READ | 1}; blx r12; mov r4,r0;
        movw r3,#{CELL & 0xFFFF}; movt r3,#{CELL >> 16}; ldr r3,[r3]; tst r3,#{DONE}; bne stock;
        ldr.w r12,={f['load'] | 1}; blx r12;
        stock: mov r0,r4; pop {{r4,r5}}; cmp r0,#1; bne failed;
        ldr.w pc,={IMU_SUCCESS | 1};
        failed: ldr.w pc,={FAILURE | 1};
    '''
    # r0 obj, r1 buffer, r2 length -> 1 when exactly length bytes arrived.
    code['read_all'] = f'''
        push {{r4-r8,lr}}; sub sp,#8; mov r4,r0; mov r5,r1; mov r6,r2;
        more: cmp r6,#0; beq good; movs r0,#0; str r0,[sp];
        mov r0,r4; mov r1,r5; mov r2,r6; mov r3,sp; {_n(READ)}
        {c('r1', SUCCESS)} cmp r0,r1; bne bad; ldr r0,[sp]; cmp r0,#0; beq bad;
        cmp r0,r6; bhi bad; add r5,r5,r0; sub r6,r6,r0; b more;
        good: movs r0,#1; b done;
        bad: movs r0,#0; done: add sp,#8; pop {{r4-r8,pc}};
    '''
    code['crc32'] = f'''
        push {{r4,lr}}; mvn r2,#0; {c('r4', 0xEDB88320)}
        byte: cmp r1,#0; beq done; ldrb r3,[r0],#1; eors r2,r3; movs r3,#8;
        bit: lsrs r2,r2,#1; bcc next; eors r2,r4; next: subs r3,#1; bne bit;
        subs r1,#1; b byte;
        done: mvn r0,r2; pop {{r4,pc}};
    '''
    # r0 relocs, r1 count, r2 limit -> 1 when every offset+4 <= limit, even, kind 0/1.
    code['check_relocs'] = '''
        push {r4,lr};
        loop: cmp r1,#0; beq good; ldr r3,[r0],#4; lsrs r4,r3,#28; cmp r4,#1; bhi bad;
        bic r3,r3,#0xF0000000; tst r3,#1; bne bad; adds r3,#4; cmp r3,r2; bhi bad;
        subs r1,#1; b loop;
        good: movs r0,#1; pop {r4,pc};
        bad: movs r0,#0; pop {r4,pc};
    '''
    # r0 address, r1 size -> writable alias / RAM address, or 0.
    code['target'] = f'''
        adds r2,r0,r1; bcs bad;
        {c('r3', CODE_LO)} cmp r0,r3; blo scatter; {c('r3', CODE_HI)} cmp r2,r3; bhi scatter;
        {c('r3', ALIAS)} add r0,r0,r3; bx lr;
        scatter: {c('r3', SCATTER_LO)} cmp r0,r3; blo bad; {c('r3', SCATTER_HI)} cmp r2,r3; bhi bad; bx lr;
        bad: movs r0,#0; bx lr;
    '''
    # r0 first record, r1 count, r2 end -> 0, 8 (structure) or 9 (memory differs).
    code['validate'] = f'''
        push {{r4-r10,lr}}; mov r4,r0; mov r5,r1; mov r6,r2;
        loop: cmp r5,#0; beq good;
        add r0,r4,#8; cmp r0,r6; bhi bad; ldr r7,[r4]; ldrh r8,[r4,#4]; ldrh r9,[r4,#6];
        cmp r8,#0; beq bad;
        lsl r0,r8,#1; adds r0,#3; bic r0,r0,#3; adds r0,#8; add r10,r4,r0;
        add r0,r10,r9,lsl #2; cmp r0,r6; bhi bad; cmp r0,r4; bls bad;
        mov r0,r10; mov r1,r9; mov r2,r8; {call('check_relocs')} cmp r0,#1; bne bad;
        mov r0,r7; mov r1,r8; {call('target')} cmp r0,#0; beq bad;
        add r1,r4,#8; mov r2,r8;
        compare: ldrb r3,[r0],#1; ldrb r12,[r1],#1; cmp r3,r12; bne differs; subs r2,#1; bne compare;
        add r4,r10,r9,lsl #2; subs r5,#1; b loop;
        good: movs r0,#0; pop {{r4-r10,pc}};
        bad: movs r0,#8; pop {{r4-r10,pc}};
        differs: movs r0,#9; pop {{r4-r10,pc}};
    '''
    # r0 data, r1 relocs, r2 count, r3 delta (multiple of 64 KiB). ABS32 and Thumb MOVT.
    code['apply_relocs'] = '''
        push {r4-r9,lr}; mov r4,r0; mov r5,r1; mov r6,r2; mov r7,r3;
        loop: cmp r6,#0; beq done; ldr r0,[r5],#4; lsrs r1,r0,#28; bic r0,r0,#0xF0000000; add r8,r4,r0;
        cmp r1,#0; bne movt;
        ldrh r0,[r8]; ldrh r1,[r8,#2]; orr r0,r0,r1,lsl #16; add r0,r0,r7;
        strh r0,[r8]; lsrs r0,r0,#16; strh r0,[r8,#2]; b next;
        movt: ldrh r0,[r8]; ldrh r1,[r8,#2];
        uxtb r2,r1; ubfx r3,r1,#12,#3; bfi r2,r3,#8,#3; ubfx r3,r0,#10,#1; bfi r2,r3,#11,#1;
        ubfx r3,r0,#0,#4; bfi r2,r3,#12,#4;
        add r2,r2,r7,lsr #16; uxth r2,r2;
        ubfx r3,r2,#12,#4; bfi r0,r3,#0,#4; ubfx r3,r2,#11,#1; bfi r0,r3,#10,#1;
        ubfx r3,r2,#8,#3; bfi r1,r3,#12,#3; bfi r1,r2,#0,#8;
        strh r0,[r8]; strh r1,[r8,#2];
        next: subs r6,#1; b loop;
        done: dsb sy; pop {r4-r9,pc};
    '''
    # r0 first record, r1 count, r2 delta -> 0, or 12 when a code range failed to sync.
    code['apply_edits'] = f'''
        push {{r4-r11,lr}}; mov r4,r0; mov r5,r1; mov r6,r2; movs r11,#0;
        loop: cmp r5,#0; beq done;
        ldr r7,[r4]; ldrh r8,[r4,#4]; ldrh r9,[r4,#6];
        lsl r0,r8,#1; adds r0,#3; bic r0,r0,#3; adds r0,#8; add r10,r4,r0;
        add r0,r4,#8; add r0,r0,r8; mov r1,r10; mov r2,r9; mov r3,r6; {call('apply_relocs')}
        mov r0,r7; mov r1,r8; {call('target')}
        add r1,r4,#8; add r1,r1,r8; mov r2,r8;
        orr r3,r0,r1; orr r3,r3,r2; tst r3,#1; bne bytes;
        halves: ldrh r3,[r1],#2; strh r3,[r0],#2; subs r2,#2; bne halves; b copied;
        bytes: ldrb r3,[r1],#1; strb r3,[r0],#1; subs r2,#1; bne bytes;
        copied: dsb sy;
        {c('r3', CODE_HI)} cmp r7,r3; bhs data;
        mov r0,r7; mov r1,r8; {call('sync')} cmp r0,#1; beq data; movs r11,#12;
        data: add r4,r10,r9,lsl #2; subs r5,#1; b loop;
        done: dsb sy; isb sy; mov r0,r11; pop {{r4-r11,pc}};
    '''
    # r0 result, r1 base, r2 tail size, r3 edit count: one line in Z30/WL.LOG.
    code['write_log'] = f'''
        push {{r4-r8,lr}}; sub sp,#120; mov r4,r0; mov r5,r1; mov r6,r2; mov r7,r3;
        add r8,sp,#56; {c('r0', strings['log_text'])}
        copy: ldrb r1,[r0],#1; cbz r1,copied; strb r1,[r8],#1; b copy;
        copied: mov r0,r4; movs r2,#4; bl hex; movs r0,#66; bl label;
        mov r0,r5; movs r2,#28; bl hex; movs r0,#84; bl label;
        mov r0,r6; movs r2,#28; bl hex; movs r0,#69; bl label;
        mov r0,r7; movs r2,#28; bl hex; movs r1,#13; strb r1,[r8],#1; movs r1,#10; strb r1,[r8],#1;
        mov r0,sp; movs r1,#48; {_n(MEMSET)} mvn r0,#0; str r0,[sp,#8];
        mov r0,sp; {c('r1', strings['log'])} {_n(SET_PATH)}
        mov r0,sp; {c('r1', CREATE)} movs r2,#0; {_n(OPEN)} {c('r1', SUCCESS)} cmp r0,r1; bne closefd;
        movs r0,#0; str r0,[sp,#48]; ldr r0,[sp,#8]; add r1,sp,#56; sub r2,r8,r1; add r3,sp,#48; {_n(FD_WRITE)}
        mov r0,sp; {_n(CLOSE)} b done;
        closefd: ldr r0,[sp,#8]; cmp r0,#0; blt done; {_n(FD_CLOSE)}
        done: add sp,#120; pop {{r4-r8,pc}};
        label: movs r1,#32; strb r1,[r8],#1; strb r0,[r8],#1; movs r1,#61; strb r1,[r8],#1; bx lr;
        hex: lsr.w r1,r0,r2; and r1,r1,#15; cmp r1,#10; blo digit; adds r1,#7; digit: adds r1,#48;
        strb r1,[r8],#1; subs r2,#4; bpl hex; bx lr;
    '''
    code['load'] = f'''
        push {{r4-r11,lr}}; sub sp,#104;
        {c('r11', CELL)} ldr r0,[r11]; tst r0,#{DONE}; bne out;
        adds r0,#1; str r0,[r11]; ubfx r1,r0,#0,#12;
        cmp r1,#{START}; blo out; cmp r1,#{LIMIT}; bhs give_up;
        sub r1,r1,#{START}; movs r2,#{EVERY}; udiv r3,r1,r2; mls r3,r3,r2,r1; cmp r3,#0; bne out;
        movs r0,#0; str r0,[sp,#88];
        mov r0,sp; movs r1,#48; {_n(MEMSET)} mvn r0,#0; str r0,[sp,#8];
        mov r0,sp; {c('r1', strings['path'])} {_n(SET_PATH)}
        mov r0,sp; movs r1,#0; movs r2,#0; {_n(OPEN)} {c('r1', SUCCESS)} cmp r0,r1; bne not_open;
        mov r10,#3; mov r0,sp; add r1,sp,#48; movs r2,#36; {call('read_all')} cmp r0,#1; bne close_fail;
        mov r10,#4; ldr r0,[sp,#48]; {c('r1', MAGIC)} cmp r0,r1; bne close_fail;
        ldr r0,[sp,#52]; cmp r0,#{FORMAT}; bne close_fail;
        mov r10,#5; ldr r0,[sp,#56]; {c('r1', resident_id)} cmp r0,r1; bne close_fail;
        mov r10,#4; ldr r5,[sp,#76]; cmp r5,#0; beq close_fail; {c('r1', MAX_BODY)} cmp r5,r1; bhi close_fail;
        tst r5,#3; bne close_fail; ldr r6,[sp,#64]; cmp r6,r5; bhi close_fail; tst r6,#3; bne close_fail;
        ldr r0,[sp,#60]; lsls r1,r0,#16; bne close_fail;
        mov r10,#6; add r0,r5,#0x10000; movs r1,#0; {_n(POOL_ALLOC)} cmp r0,#0; beq close_fail;
        str r0,[sp,#88]; movw r1,#0xFFFF; add r4,r0,r1; bfc r4,#0,#16; str r4,[sp,#92];
        mov r10,#3; mov r0,sp; mov r1,r4; mov r2,r5; {call('read_all')} cmp r0,#1; bne close_fail;
        movs r0,#0; str r0,[sp,#84]; mov r0,sp; add r1,sp,#96; movs r2,#1; add r3,sp,#84; {_n(READ)}
        {c('r1', SUCCESS)} cmp r0,r1; bne close_fail; mov r10,#4; ldr r0,[sp,#84]; cmp r0,#0; bne close_fail;
        mov r10,#10; mov r0,sp; {_n(CLOSE)} {c('r1', SUCCESS)} cmp r0,r1; bne fail;
        mov r10,#7; mov r0,r4; mov r1,r5; {call('crc32')} ldr r1,[sp,#80]; cmp r0,r1; bne fail;
        mov r10,#8; ldr r8,[sp,#68]; add r7,r4,r6; sub r1,r5,r6; lsrs r1,r1,#2; cmp r8,r1; bhi fail;
        mov r0,r7; mov r1,r8; mov r2,r6; {call('check_relocs')} cmp r0,#1; bne fail;
        add r9,r7,r8,lsl #2;
        mov r0,r9; ldr r1,[sp,#72]; add r2,r4,r5; {call('validate')} mov r10,r0; cmp r0,#0; bne fail;
        ldr r3,[sp,#60]; sub r3,r4,r3; mov r0,r4; mov r1,r7; mov r2,r8; {call('apply_relocs')}
        mov r10,#11; mov r0,r4; mov r1,r6; {call('sync')} cmp r0,#1; bne fail;
        ldr r2,[sp,#60]; sub r2,r4,r2; mov r0,r9; ldr r1,[sp,#72]; {call('apply_edits')}
        mov r10,r0; cmp r10,#0; bne status; mov r10,#1;
        status: orr r0,r4,#{DONE}; orr r0,r0,r10,lsl #8; str r0,[r11];
        mov r0,r10; mov r1,r4; mov r2,r6; ldr r3,[sp,#72]; {call('write_log')} b out;
        close_fail: mov r0,sp; {_n(CLOSE)}
        fail: ldr r0,[sp,#88]; cmp r0,#0; beq failed; {_n(POOL_FREE)}
        failed: mov r0,#{DONE}; orr r0,r0,r10,lsl #8; str r0,[r11];
        mov r0,r10; movs r1,#0; movs r2,#0; movs r3,#0; {call('write_log')} b out;
        not_open: ldr r0,[sp,#8]; cmp r0,#0; blt out; {_n(FD_CLOSE)} b out;
        give_up: mov r0,#{DONE | (2 << 8)}; str r0,[r11];
        out: add sp,#104; pop {{r4-r11,pc}};
    '''
    return code


def add(plan, resident_id):
    """Install the loader into a resident plan (stock source, normal tail)."""
    if plan.image.read(IMU_SITE, len(IMU_STOCK)) != IMU_STOCK:
        raise ValueError('Unexpected stock bytes at the IMU hook site')
    if plan.image.words(CELL, 1) != (0,):
        raise ValueError('Card loader cell is not zero in stock data')
    strings = dict(path=plan.alloc(PATH), log=plan.alloc(LOG_PATH), log_text=plan.alloc(b'WL.PATCH S=\0'))
    f = {name: plan.alloc(bytes(size)) for name, size in SIZES.items()}
    plan.put(f['sync'], sync_shared_code(f['sync'], MAX_BODY))
    for name, source in sources(f, resident_id, strings).items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.edit(IMU_SITE, jump(IMU_SITE, f['stub'], 8), 'card-loader IMU trigger', expected=IMU_STOCK)
    plan.features['card_loader'] = dict(site=hex(IMU_SITE), cell=hex(CELL), path=PATH[:-1].decode(),
                                        log=LOG_PATH[:-1].decode(), resident_id=hex(resident_id),
                                        functions={k: hex(v) for k, v in f.items()})
    return f
