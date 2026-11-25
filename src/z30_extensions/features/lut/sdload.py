# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Bounded Thumb-2 SD LUT loader with caller-owned writable workspace.

Offline component; not installed into an effect worker. Candidate and input
memory must be explicitly allocated as RAM. Activation follows read, close and
validation. Text supports case-insensitive directives before data, three
decimal [0,1] channels per line, at most nine fractional digits, no exponent.
WL3L v1 supports sizes 2..65. Runtime allocator and I/O context remain unproven.
"""
import struct
from ...assembly import assemble, invoke

BLOCK_FLAG = 0x574C5554
FS_OPEN, FS_READ, FS_CLOSE = 0x102C89C4, 0x102C8A3A, 0x102C89EE
FS_SEEK = 0x102C8A98  # (fd, offset, whence, out_ptr); whence 2 reports the size
WL3L_MAGIC = 0x4C334C57
MAX_LUT_SIZE = 65
SD_SLOT_SIZE = (22 + 6 * MAX_LUT_SIZE ** 3 + 7) & ~7
READ_BUF_SIZE = 8 * 1024 * 1024
LUT_NAME = b'A:\\WL-LUT.CUBE\x00'
DECIMAL_SCALE = 1_000_000_000


def build_layout(binary_len):
    """Offsets within an explicitly allocated RAM arena, not ROM addresses."""
    sd = (16 + binary_len + 7) & ~7
    read = sd + SD_SLOT_SIZE
    path = read + READ_BUF_SIZE
    descriptor = (path + len(LUT_NAME) + 3) & ~3
    return dict(embed=16, sd_slot=sd, sd_cap=SD_SLOT_SIZE, read_buf=read,
                read_len=READ_BUF_SIZE, path=path, workspace=descriptor, total=descriptor + 20)


def build_block(binary, address, flag=BLOCK_FLAG):
    """Build RAM arena: control, embedded payload, candidate, input, path, workspace.

    Control: flag, absolute active pointer, last status, active length.
    Workspace: input pointer/capacity, candidate pointer/capacity, path pointer.
    """
    from ...host.lut import unpack_binary
    unpack_binary(binary)
    layout = build_layout(len(binary))
    if address & 7 or address < 0 or address + layout['total'] > 0x100000000:
        raise ValueError('Arena address must be aligned and fit in 32 bits')
    block = bytearray(layout['total'])
    struct.pack_into('<4I', block, 0, flag, address + 16, 0, len(binary))
    block[16:16 + len(binary)] = binary
    block[layout['path']:layout['path'] + len(LUT_NAME)] = LUT_NAME
    struct.pack_into('<5I', block, layout['workspace'], address + layout['read_buf'],
                     layout['read_len'], address + layout['sd_slot'], layout['sd_cap'],
                     address + layout['path'])
    return bytes(block), layout


def _keyword(name, target, following):
    code = f'cmp r0, #{len(name)}; bne {following};'
    for offset, char in enumerate(name.lower()):
        code += (f'ldrb r1, [r4, #{offset}]; orr r1, r1, #32;'
                 f'cmp r1, #{ord(char) | 32}; bne {following};')
    return code + f'adds r4, #{len(name)}; b {target}; {following}: '


def _triple():
    code = ''
    for offset in (24, 28, 32):
        code += ('mov r0, r4; ldr r1, [sp, #40]; bl decimal;'
                 'cmp r2, #0; bne pc_error; mov r4, r0;'
                 f'str r1, [sp, #{offset}];')
    return code + 'bl horizontal; ldr r0, [sp, #40]; cmp r4, r0; bne pc_error;'


def parse_cube_source():
    """parse_cube(in, end, output, capacity) -> length or 0xffffffff.

    Reads and writes are bounded. Exact rational round-even normalization on
    a 1e9 decimal grid. Host float rounding at exact ties may differ; verifier
    covers supported decimal fixtures against pack_binary and rational oracle.
    """
    code = (
        'parse_cube: push {r4-r11, lr}; sub sp, #52;'
        'mov r4, r0; mov r5, r1; mov r6, r2; mov r11, r2;'
        'cmp r4, r5; bhi pc_error; adds r7, r2, r3; bcs pc_error;'
        'movs r8, #0; movs r9, #0; movs r10, #0;'
        'movs r0, #0; str r0, [sp]; str r0, [sp, #4]; str r0, [sp, #8];'
        f'ldr r0, ={DECIMAL_SCALE};'
        'str r0, [sp, #12]; str r0, [sp, #16]; str r0, [sp, #20];'
        'pc_line: cmp r4, r5; bhs pc_done;'
        'ldrb r0, [r4]; cmp r0, #10; beq pc_advance; cmp r0, #13; beq pc_advance;'
        'cmp r0, #32; beq pc_advance; cmp r0, #9; beq pc_advance;'
        'mov r0, r4; pc_find_end: cmp r0, r5; bhs pc_end_found;'
        'ldrb r1, [r0]; cmp r1, #0; beq pc_error;'
        'cmp r1, #10; beq pc_end_found; cmp r1, #13; beq pc_end_found;'
        'adds r0, #1; b pc_find_end;'
        'pc_end_found: str r0, [sp, #40];'
        'ldrb r1, [r4]; cmp r1, #35; beq pc_next_line;'
        'mov r0, r4; pc_token: ldr r1, [sp, #40]; cmp r0, r1; bhs pc_token_end;'
        'ldrb r1, [r0]; cmp r1, #32; beq pc_token_end; cmp r1, #9; beq pc_token_end;'
        'adds r0, #1; b pc_token; pc_token_end: subs r0, r0, r4;')
    code += _keyword('LUT_3D_SIZE', 'pc_size', 'pc_not_size')
    code += _keyword('DOMAIN_MIN', 'pc_min', 'pc_not_min')
    code += _keyword('DOMAIN_MAX', 'pc_max', 'pc_not_max')
    code += _keyword('TITLE', 'pc_title', 'pc_row')
    code += ('cmp r8, #2; blo pc_error;'
             'mul r0, r8, r8; mul r0, r0, r8; cmp r9, r0; bhs pc_error;'
             + _triple() + 'tst r10, #1; bne pc_emit;'
             'mul r0, r8, r8; mul r0, r0, r8; movs r1, #6; mul r0, r0, r1;'
             'adds r0, #22; subs r1, r7, r11; cmp r0, r1; bhi pc_error;'
             f'ldr r0, ={WL3L_MAGIC}; str r0, [r11];'
             'movs r0, #1; strh r0, [r11, #4]; strh r8, [r11, #6];'
             'movs r0, #0; strh r0, [r11, #8];')
    for i in range(3):
        code += (f'ldr r0, [sp, #{i * 4}]; ldr r1, [sp, #{12 + i * 4}];'
                 'cmp r0, r1; bhs pc_error;'
                 f'ldr r1, ={DECIMAL_SCALE}; bl quantize; strh r0, [r11, #{10 + i * 2}];'
                 f'ldr r0, [sp, #{12 + i * 4}]; ldr r1, ={DECIMAL_SCALE};'
                 f'bl quantize; ldrh r1, [r11, #{10 + i * 2}]; cmp r1, r0; bhs pc_error;'
                 f'strh r0, [r11, #{16 + i * 2}];')
    code += 'add r6, r11, #22; orr r10, r10, #1; pc_emit: '
    for i in range(3):
        code += (f'ldr r0, [sp, #{24 + i * 4}]; ldr r2, [sp, #{i * 4}];'
                 f'ldr r1, [sp, #{12 + i * 4}]; cmp r0, r2; blo pc_error;'
                 'cmp r0, r1; bhi pc_error; subs r0, r0, r2; subs r1, r1, r2;'
                 'bl quantize; strh r0, [r6]; adds r6, #2;')
    code += (
        'adds r9, #1; b pc_next_line;'
        'pc_size: cmp r8, #0; bne pc_error; tst r10, #1; bne pc_error;'
        'bl horizontal; movs r0, #0; movs r2, #0;'
        'pc_size_digit: ldr r1, [sp, #40]; cmp r4, r1; bhs pc_size_end;'
        'ldrb r1, [r4]; subs r1, #48; cmp r1, #9; bhi pc_size_end;'
        'movs r3, #10; mla r0, r0, r3, r1; cmp r0, #65; bhi pc_error;'
        'adds r2, #1; adds r4, #1; b pc_size_digit;'
        'pc_size_end: cmp r2, #0; beq pc_error; cmp r0, #2; blo pc_error;'
        'mov r8, r0; bl horizontal; ldr r0, [sp, #40]; cmp r4, r0; bne pc_error; b pc_next_line;'
        'pc_title: tst r10, #1; bne pc_error; b pc_next_line;'
        'pc_min: tst r10, #3; bne pc_error; orr r10, r10, #2;'
        + _triple() +
        'ldr r0, [sp, #24]; str r0, [sp]; ldr r0, [sp, #28]; str r0, [sp, #4];'
        'ldr r0, [sp, #32]; str r0, [sp, #8]; b pc_next_line;'
        'pc_max: tst r10, #5; bne pc_error; orr r10, r10, #4;'
        + _triple() +
        'ldr r0, [sp, #24]; str r0, [sp, #12]; ldr r0, [sp, #28]; str r0, [sp, #16];'
        'ldr r0, [sp, #32]; str r0, [sp, #20];'
        'pc_next_line: ldr r4, [sp, #40]; b pc_line;'
        'pc_advance: adds r4, #1; b pc_line;'
        'pc_done: tst r10, #1; beq pc_error;'
        'mul r0, r8, r8; mul r0, r0, r8; cmp r9, r0; bne pc_error;'
        'subs r0, r6, r11; b pc_return; pc_error: mov.w r0, #-1;'
        'pc_return: add sp, #52; pop {r4-r11, pc};'
        'horizontal: ldr r1, [sp, #40]; h_loop: cmp r4, r1; bhs h_done;'
        'ldrb r2, [r4]; cmp r2, #32; beq h_step; cmp r2, #9; bne h_done;'
        'h_step: adds r4, #1; b h_loop; h_done: bx lr;'
        + _decimal_source() + _quantize_source())
    return code


def _decimal_source():
    return (
        'decimal: push {r4-r8, lr}; mov r4, r0; mov r5, r1;'
        'movs r6, #0; movs r7, #0; movs r8, #0;'
        'd_space: cmp r4, r5; bhs d_error; ldrb r0, [r4];'
        'cmp r0, #32; beq d_skip; cmp r0, #9; bne d_sign;'
        'd_skip: adds r4, #1; b d_space;'
        'd_sign: cmp r0, #45; bne d_plus; movs r8, #1; adds r4, #1; b d_int;'
        'd_plus: cmp r0, #43; bne d_int; adds r4, #1;'
        'd_int: cmp r4, r5; bhs d_end; ldrb r0, [r4];'
        'subs r0, #48; cmp r0, #9; bhi d_dot;'
        'movs r1, #10; mla r6, r6, r1, r0; cmp r6, #1; bhi d_error;'
        'adds r7, #1; adds r4, #1; b d_int;'
        f'd_dot: ldr r1, ={DECIMAL_SCALE}; mul r6, r6, r1;'
        'ldrb r0, [r4]; cmp r0, #46; bne d_delimiter;'
        'adds r4, #1; movs r3, #0; ldr r1, =100000000;'
        'd_frac: cmp r4, r5; bhs d_value; ldrb r0, [r4]; subs r0, #48;'
        'cmp r0, #9; bhi d_value; cmp r3, #9; bhs d_error;'
        'mla r6, r0, r1, r6; movs r2, #10; udiv r1, r1, r2;'
        'adds r7, #1; adds r3, #1; adds r4, #1; b d_frac;'
        f'd_end: ldr r1, ={DECIMAL_SCALE}; mul r6, r6, r1;'
        'd_value: cmp r7, #0; beq d_error; cmp r4, r5; bhs d_checked;'
        'd_delimiter: ldrb r0, [r4]; cmp r0, #32; beq d_checked; cmp r0, #9; bne d_error;'
        f'd_checked: cmp r7, #0; beq d_error; ldr r0, ={DECIMAL_SCALE};'
        'cmp r6, r0; bhi d_error; cmp r8, #0; beq d_ok; cmp r6, #0; bne d_error;'
        'd_ok: mov r0, r4; mov r1, r6; movs r2, #0; pop {r4-r8, pc};'
        'd_error: mov r0, r4; movs r1, #0; movs r2, #1; pop {r4-r8, pc};')


def _quantize_source():
    return (
        'quantize: push {r4-r7, lr}; mov r5, r1; movw r2, #65535;'
        'umull r6, r4, r0, r2; movs r0, #0; movs r7, #32;'
        'q_loop: lsls r6, r6, #1; adcs r4, r4, r4; lsls r0, r0, #1;'
        'cmp r4, r5; blo q_next; subs r4, r4, r5; adds r0, #1;'
        'q_next: subs r7, #1; bne q_loop; lsls r4, r4, #1;'
        'cmp r4, r5; bhi q_up; blo q_done; tst r0, #1; beq q_done;'
        'q_up: adds r0, #1; q_done: pop {r4-r7, pc};')


def sd_load_source():
    """sd_load(control, workspace) -> 0 success, 1 open, 2 I/O, 3 parse, 4 bounds.

    Exclusive ownership required. Failed loads preserve active pointer/length.
    The length comes from seek(END) through its out pointer; the call's return
    register is not a status and is ignored, as the library's own reader does.
    A zero length means seek is unavailable, so reads then stream in 1,024-byte
    requests until one comes back empty. A file larger than the input buffer
    reports 4. Each open gets one close attempt. r1 returns -1 when closed/no
    open, otherwise the handle a caller must retain and close later.
    Read/close success must be 0; close failure always reports I/O, not capacity.
    """
    return (
        'sd_load: push {r4-r11, lr}; sub sp, #28;'
        'mov r4, r0; mov r5, r1; movs r11, #4; mov.w r10, #-1;'
        'ldr r6, [r5]; ldr r7, [r5, #4]; ldr r8, [r5, #8]; ldr r9, [r5, #12];'
        'cmp r7, #22; blo sd_return; cmp r9, #22; blo sd_return;'
        'tst r6, #3; bne sd_return; tst r8, #3; bne sd_return;'
        'adds r0, r6, r7; bcs sd_return; adds r1, r8, r9; bcs sd_return;'
        'cmp r6, r1; bhs sd_disjoint; cmp r8, r0; blo sd_return;'
        'sd_disjoint: ldr r2, [r4, #4]; ldr r3, [r4, #12]; adds r3, r2, r3; bcs sd_return;'
        'cmp r2, r1; bhs sd_active_input; cmp r8, r3; blo sd_return;'
        'sd_active_input: cmp r2, r0; bhs sd_active_ok; cmp r6, r3; blo sd_return;'
        'sd_active_ok: adds r2, r4, #16; bcs sd_return;'
        'cmp r4, r1; bhs sd_ctl_out; cmp r8, r2; blo sd_return;'
        'sd_ctl_out: cmp r4, r0; bhs sd_checked; cmp r6, r2; blo sd_return;'
        'sd_checked: ldr r0, [r5, #16]; movs r1, #0; movs r2, #0;'
        + invoke(FS_OPEN) +
        'movs r11, #1; cmp r0, #0; blt sd_return; mov r10, r0;'
        'movs r5, #0; movs r11, #2;'
        # seek(END) reports the length through the out pointer; its return value
        # is not a status, and the stock reader at 0x10595570 ignores it. A zero
        # length means the call is unsupported, so fall back to streaming reads
        # that stop at the first empty transfer.
        'mov r0, r10; movs r1, #0; movs r2, #2; add r3, sp, #8; movs r12, #0;'
        'str r12, [sp, #8];' + invoke(FS_SEEK) +
        'ldr r0, [sp, #8]; cmp r0, #0; beq sd_stream;'
        'ldr r3, =8388608; cmp r0, r3; bhi sd_close_error;'
        'cmp r0, r7; bhi sd_buffer_full; str r0, [sp, #12];'
        'sd_read: ldr r1, [sp, #12]; subs r2, r1, r5; beq sd_read_done;'
        # One request covers exactly what is left, matching the library's own
        # reader; the position is re-established first in case a transfer was
        # short.
        'mov r0, r10; mov r1, r5; movs r2, #0; add r3, sp, #8;' + invoke(FS_SEEK) +
        'ldr r2, [sp, #12]; subs r2, r2, r5; b sd_request;'
        'sd_stream: movs r2, #1024;'
        'sd_request: str r2, [sp, #4]; add r1, r6, r5; mov r0, r10; mov r3, sp;'
        'movs r12, #0; str r12, [sp];' + invoke(FS_READ) +
        'cmp r0, #0; bne sd_close_error; ldr r0, [sp]; ldr r1, [sp, #4];'
        'cmp r0, r1; bhi sd_close_error; cmp r0, #0; bne sd_progress;'
        'ldr r1, [sp, #12]; cmp r1, #0; bne sd_close_error; b sd_read_done;'
        'sd_progress: adds r5, r5, r0; ldr r1, [sp, #12]; cmp r1, #0; bne sd_read;'
        'cmp r5, r7; blo sd_stream;'
        'sd_buffer_full: movs r11, #4; b sd_close_error;'
        'sd_read_done: mov r0, r10;' + invoke(FS_CLOSE) +
        'cmp r0, #0; bne sd_return; mov.w r10, #-1; movs r11, #3;'
        'cmp r5, #4; blo sd_parse_text; ldr r0, [r6];'
        f'ldr r1, ={WL3L_MAGIC}; cmp r0, r1; bne sd_parse_text;'
        'cmp r5, #22; blo sd_return; ldrh r0, [r6, #4]; cmp r0, #1; bne sd_return;'
        'ldrh r0, [r6, #8]; cmp r0, #0; bne sd_return;'
        'ldrh r0, [r6, #6]; cmp r0, #2; blo sd_return; cmp r0, #65; bhi sd_return;'
        'mul r1, r0, r0; mul r1, r1, r0; movs r0, #6; mul r1, r1, r0; adds r1, #22;'
        'cmp r1, r5; bne sd_return; cmp r1, r9; bhi sd_return;'
        'ldrh r0, [r6, #10]; ldrh r1, [r6, #16]; cmp r0, r1; bhs sd_return;'
        'ldrh r0, [r6, #12]; ldrh r1, [r6, #18]; cmp r0, r1; bhs sd_return;'
        'ldrh r0, [r6, #14]; ldrh r1, [r6, #20]; cmp r0, r1; bhs sd_return;'
        'movs r0, #0; sd_copy: cmp r0, r5; bhs sd_activate;'
        'ldrb r1, [r6, r0]; strb r1, [r8, r0]; adds r0, #1; b sd_copy;'
        'sd_parse_text: mov r0, r6; adds r1, r6, r5; mov r2, r8; mov r3, r9; bl parse_cube;'
        'cmp r0, #22; blo sd_return; cmp r0, r9; bhi sd_return; mov r5, r0;'
        'sd_activate: dmb sy; str r8, [r4, #4]; str r5, [r4, #12]; movs r11, #0; b sd_return;'
        'sd_close_error: mov r0, r10;' + invoke(FS_CLOSE) +
        'cmp r0, #0; beq sd_closed_error; movs r11, #2; b sd_return;'
        'sd_closed_error: mov.w r10, #-1;'
        'sd_return: str r11, [r4, #8]; mov r0, r11; mov r1, r10; add sp, #28; pop {r4-r11, pc};'
        + parse_cube_source())


def build_loader(address):
    return assemble(sd_load_source(), address, 0x4000)
