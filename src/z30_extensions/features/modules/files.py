"""Version-bound PIC module files for the SD extension host.

Payloads are trusted ARM Thumb code, not sandboxed scripts. The source digest
identifies the required stock platform; CRC32 detects damage, not authenticity.
"""
import binascii
import struct
from ...platform.z30_120 import STOCK_SHA256

HEADER = struct.Struct('<4s7I32s')
MAX_CODE = 16384
MODEL = 2090
FIRMWARE = 0x120
ABI = 2
API_BYTES = 20
CONTEXT_BYTES = 64
MAX_TEXT = 40


def pack(code, entry=0):
    code = bytes(code)
    if not code or len(code) > MAX_CODE or len(code) % 4:
        raise ValueError('Module code must be 4-byte aligned and 1..16384 bytes')
    if entry % 2 or not 0 <= entry < len(code):
        raise ValueError('Invalid Thumb entry offset')
    return HEADER.pack(b'ZMOD', 1, ABI, MODEL, FIRMWARE, len(code), entry,
                       binascii.crc32(code), bytes.fromhex(STOCK_SHA256)) + code


def parse(payload):
    if len(payload) < HEADER.size:
        raise ValueError('Truncated module header')
    magic, fmt, abi, model, fw, size, entry, crc, source = HEADER.unpack_from(payload)
    if (magic, fmt, abi, model, fw, source) != (
            b'ZMOD', 1, ABI, MODEL, FIRMWARE, bytes.fromhex(STOCK_SHA256)):
        raise ValueError('Module platform or ABI mismatch')
    if not 0 < size <= MAX_CODE or size % 4 or entry % 2 or not 0 <= entry < size:
        raise ValueError('Invalid module bounds')
    if len(payload) != HEADER.size + size:
        raise ValueError('Truncated or trailing module data')
    code = payload[HEADER.size:]
    if binascii.crc32(code) != crc:
        raise ValueError('Module CRC mismatch')
    return dict(code=code, entry=entry, abi=abi)


def example(kind='counter'):
    """Generate a PIC module; every host call comes from the imported API.

    init(api, 64-byte context) registers run(api, context), which displays a
    persistent run count or a native grid property. No absolute Nikon addresses
    occur in the payload; ADR and local branches remain position independent.
    """
    from ...assembly import assemble
    if kind not in ('counter', 'grid', 'menu-fix'):
        raise ValueError('Unknown example module')
    operation = ('ldr r0,[r5]; adds r0,#1; str r0,[r5];' if kind != 'grid'
                 else 'movw r0,#0x1129; ldr r3,[r4,#8]; blx r3;')
    prefix = 'Runs: ' if kind == 'counter' else 'Grid: ' if kind == 'grid' else 'Menus fixed. Runs: '
    extra_prepare = extra_commit = extra_data = ''
    init_push, init_pop = 'push {r4-r6,lr};', 'pop {r4-r6,pc};'
    ready_text = kind + ' module ready'
    if kind == 'menu-fix':
        from . import host as mh
        from ..menu import layout as sl
        # Version-specific adapter for the known ABI-2 host layout. Shadows are
        # module data, pinned by that host until shutdown. Never place them on
        # the init stack or publish them before all failure paths have passed.
        pages = [(sl.PAGE_OFFSET, sl.PAGE_INDEX, 5), (sl.FILES_OFFSET, sl.FILES_INDEX, 5),
                 (sl.SWITCH_OFFSET, sl.SWITCH_INDEX, 2), (mh.PAGE_OFFSET, mh.PAGE_INDEX, 4)]
        init_push = 'push {r4-r8,lr}; sub sp,#8;'
        init_pop = 'add sp,#8; pop {r4-r8,pc};'
        ready_text = 'Menus fixed'
        delta = mh.STATE_OFFSET + mh.CONTEXT
        extra_prepare = f"""
            movw r2,#{delta & 65535}; movt r2,#{delta >> 16}; subs r6,r5,r2; bcc reject;
            adr.w r7,metadata_shadow; adr.w r8,page_bindings; movs r0,#4; str r0,[sp];
            prepare_page: ldr r0,[r8]; add r2,r6,r0; ldr r0,[r2]; ldr r1,[r8,#4]; cmp r0,r1; bne reject;
            ldr r0,[r2,#40]; cmp r0,#0; beq reject; ldr r1,[r0]; movw r3,#0x1008; cmp r1,r3; bne reject;
            ldrh r1,[r0,#20]; ldr r3,[r8,#8]; cmp r1,r3; bne reject;
            ldrh r1,[r0,#22]; cmp r1,#3; beq copy_metadata; cmp r1,r3; bne reject;
            copy_metadata: mov r2,r7; movs r3,#7;
            metadata_word: ldr r1,[r0],#4; str r1,[r2],#4; subs r3,#1; bne metadata_word;
            ldr r1,[r8,#8]; orr r1,r1,r1,lsl #16; str r1,[r7,#20];
            adds r7,#28; add r8,r8,#12; ldr r0,[sp]; subs r0,#1; str r0,[sp]; bne prepare_page;
        """
        extra_commit = """
            cmp r0,#1; bne reject;
            adr.w r0,metadata_shadow; movs r1,#112; movs r2,#1; movs r3,#2;
            movw r12,#0x5aac; movt r12,#0x1003; blx r12; cmp r0,#0; ble reject;
            dsb sy; dmb ish; adr.w r7,metadata_shadow; adr.w r8,page_bindings;
            movs r0,#4; str r0,[sp];
            publish_page: ldr r0,[r8]; add r0,r6,r0; str r7,[r0,#40];
            ldrh r1,[r0,#22]; ldrh r2,[r0,#24]; adds r1,r1,r2;
            ldr r2,[r8,#8]; cmp r1,r2; blo cursor_valid; movs r1,#0;
            cursor_valid: strh r1,[r0,#22]; movs r1,#0; strh r1,[r0,#24];
            adds r7,#28; add r8,r8,#12; ldr r0,[sp]; subs r0,#1; str r0,[sp]; bne publish_page;
            dmb ish; movs r0,#1;
        """
        extra_data = 'page_bindings: ' + '; '.join(
            f'.word {offset}, {(index << 16) | 2}, {count}' for offset,index,count in pages)
        extra_data += '; .balign 4; metadata_shadow: .space 112; .balign 4;'
    source = f'''
        {init_push} mov r4,r0; mov r5,r1;
        ldr r2,[r4]; cmp r2,#{ABI}; bne reject;
        ldr r2,[r4,#4]; cmp r2,#{API_BYTES}; blo reject;
        {extra_prepare} adr.w r0,run; orr r0,r0,#1; ldr r3,[r4,#16]; blx r3;
        cmp r0,#1; bne reject;
        movs r0,#0; str r0,[r5];
        adr.w r0,ready; ldr r3,[r4,#12]; blx r3;
        {extra_commit} {init_pop}
        reject: movs r0,#0; {init_pop}
        .balign 4;
        run: push {{r4-r8,lr}}; mov r4,r0; mov r5,r1;
        {operation} mov r6,r0;
        add r7,r5,#8; adr.w r1,prefix;
        copy_prefix: ldrb r2,[r1],#1; cmp r2,#0; beq digits;
        strb r2,[r7],#1; b copy_prefix;
        digits: movs r8,#10; add r1,r5,#48; movs r2,#0;
        digit_loop: udiv r0,r6,r8; mls r3,r0,r8,r6; adds r3,#48;
        strb r3,[r1],#1; adds r2,#1; mov r6,r0; cmp r6,#0; bne digit_loop;
        reverse: ldrb r3,[r1,#-1]!; strb r3,[r7],#1; subs r2,#1; bne reverse;
        movs r0,#0; strb r0,[r7]; add r0,r5,#8;
        ldr r3,[r4,#12]; blx r3; pop {{r4-r8,pc}};
        .balign 4;
        ready: .asciz "{ready_text}";
        prefix: .asciz "{prefix}";
        .balign 4; {extra_data}
    '''
    return pack(assemble(source, 0, MAX_CODE))
