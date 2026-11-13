# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Read-only vendor PTP operation for research: 0x9FF1 ReadMemory(address, length).

The stock USB PTP layer finds an operation record through 0x102F8C74(opcode).
A wrapper returns an owned record for OPCODE and otherwise runs the stock
prologue unchanged. The record's first callback does the whole transaction:
validate two parameters, copy the range into the stock data-in buffer
(0x1045AD3C, capacity 0x1045AD50), clean it, send the data phase
(0x1045AF5A) and the response (0x1045AEB4), then return 0 so the stock
framework neither runs its permission checkers nor a second callback.

Responses: 0x2001 OK (data = the bytes), 0x2007 data phase incomplete,
0x201D invalid parameter (fewer than two parameters or a range outside the
windows below). Lengths above the buffer capacity are truncated; the host sees
the actual length from the data phase. Peripheral windows are read with aligned
32-bit loads only (address and length multiples of 4, at most 4 KiB), never
memcpy; an unaligned or oversized peripheral request is invalid. Nothing is
written anywhere else.
"""
import struct
from ..assembly import assemble, constant as c, invoke as n, jump

OPCODE = 0x9FF1
LOOKUP = 0x102F8C74
LOOKUP_PROLOGUE = bytes.fromhex('70b5002406460df028fd')  # push {r4-r6,lr}; movs r4,#0; mov r6,r0; bl 0x103066CE
LOOKUP_CALL, LOOKUP_RESUME = 0x103066CE, 0x102F8C7E
DATA_BUFFER, DATA_CAPACITY = 0x1045AD3C, 0x1045AD50
SEND_DATA, RESPOND = 0x1045AF5A, 0x1045AEB4
MEMCPY, CACHE_CLEAN = 0x10199CF0, 0x10204B0C
OK, INCOMPLETE, INVALID_PARAMETER = 0x2001, 0x2007, 0x201D
WINDOWS = ((0x10000000, 0x14000000), (0x80000000, 0xE0000000))  # NOR XIP, DDR: memcpy
# Sensor front-end, CMOS/TG driver, capture gates and timer channels: 32-bit loads only,
# word-aligned, at most MMIO_LIMIT bytes per request. The 0x20380000 block repeats per
# channel every 0x40000 (writers add channel << 18); channel 1 is 0x203C0000.
MMIO_WINDOWS = ((0x20380000, 0x20390000), (0x20200000, 0x20206000), (0x20068000, 0x2006C000),
                (0x2C011000, 0x2C012000), (0x203C0000, 0x203D0000))
MMIO_LIMIT = 0x1000
SIZES = dict(wrapper=96, execute=640, idle=8)


def add(plan):
    f = {name: plan.alloc(bytes(size)) for name, size in SIZES.items()}
    record = plan.alloc(struct.pack('<7I', OPCODE, 1, 0, 0, f['execute'] | 1, f['idle'] | 1, f['idle'] | 1))
    checks = ''.join(f'''{c('r0', low)}cmp r5,r0;blo w{i};{c('r0', high)}cmp r1,r0;bls inside;w{i}:'''
                     for i, (low, high) in enumerate(WINDOWS))
    mmio_checks = ''.join(f'''{c('r0', low)}cmp r5,r0;blo m{i};{c('r0', high)}cmp r1,r0;bls mmio;m{i}:'''
                          for i, (low, high) in enumerate(MMIO_WINDOWS))
    codes = {}
    codes['wrapper'] = f'''movw r1,#{OPCODE};cmp r0,r1;bne stock;{c('r0', record)}bx lr;
        stock:push {{r4,r5,r6,lr}};movs r4,#0;mov r6,r0;{n(LOOKUP_CALL)}
        {c('r12', LOOKUP_RESUME | 1)}bx r12;'''
    # execute(context, request): request +0 length, +6 code, +0xC parameters.
    codes['execute'] = f'''push {{r4-r8,lr}};sub sp,#8;mov r4,r1;
        ldr r0,[r4];subs r0,#12;lsrs r0,r0,#2;cmp r0,#2;blo invalid;
        ldr r5,[r4,#12];ldr r6,[r4,#16];
        {n(DATA_CAPACITY)}cmp r6,r0;it hi;movhi r6,r0;
        adds r1,r5,r6;bcs invalid;
        {checks}{mmio_checks}b invalid;
    mmio:
        orr r0,r5,r6;tst r0,#3;bne invalid;{c('r0', MMIO_LIMIT)}cmp r6,r0;bhi invalid;
        {n(DATA_BUFFER)}mov r7,r0;movs r2,#0;
    words:cmp r2,r6;bhs mmio_clean;ldr r0,[r5,r2];str r0,[r7,r2];adds r2,#4;b words;
    mmio_clean:
        cmp r6,#0;beq send;mov r0,r7;mov r1,r6;{n(CACHE_CLEAN)}b send;
    inside:
        {n(DATA_BUFFER)}mov r7,r0;
        cmp r6,#0;beq send;
        mov r0,r7;mov r1,r5;mov r2,r6;{n(MEMCPY, thumb=False)}
        mov r0,r7;mov r1,r6;{n(CACHE_CLEAN)}
    send:
        mov r0,r4;mov r1,r7;mov r2,r6;mov r3,#-1;{n(SEND_DATA)}
        movw r1,#{OK};cmp r0,#0;bne respond;movw r1,#{INCOMPLETE};b respond;
    invalid:movw r1,#{INVALID_PARAMETER};
    respond:movs r0,#0;str r0,[sp];mov r2,r0;mov r3,r0;mov r0,r4;{n(RESPOND)}
        movs r0,#0;add sp,#8;pop {{r4-r8,pc}};'''
    codes['idle'] = 'movs r0,#0;bx lr;'
    for name, source in codes.items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    actual = plan.image.read(LOOKUP, len(LOOKUP_PROLOGUE))
    if actual != LOOKUP_PROLOGUE:
        raise ValueError(f'Unexpected PTP record lookup prologue: {actual.hex()}')
    plan.edit(LOOKUP, jump(LOOKUP, f['wrapper'], length=len(LOOKUP_PROLOGUE)),
              'PTP ReadMemory operation record', LOOKUP_PROLOGUE)
    plan.features['ptp_read'] = dict(
        opcode=hex(OPCODE), record=hex(record), functions={k: hex(v | 1) for k, v in f.items()},
        lookup=hex(LOOKUP), displaced=LOOKUP_PROLOGUE.hex(), parameters=['address', 'length'],
        windows=[[hex(a), hex(b)] for a, b in WINDOWS],
        mmio_windows=[[hex(a), hex(b)] for a, b in MMIO_WINDOWS], mmio_rule='aligned 32-bit loads, <= 0x1000 bytes',
        buffer=hex(DATA_BUFFER), capacity_getter=hex(DATA_CAPACITY),
        responses=dict(ok=hex(OK), incomplete=hex(INCOMPLETE), invalid_parameter=hex(INVALID_PARAMETER)),
        writes=False, permission_checkers='bypassed (read-only operation answered in the first callback)',
        camera_verified=False)
