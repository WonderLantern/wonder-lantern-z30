# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Frame and selected thirds/quarters grid in the visible photo region.

The hardware exposes one regular line sequence per axis. Borders and interior
lines share that sequence; this is not an independent GPU overlay.
"""
from ...assembly import constant as c, invoke as n


def axis(start, end, lower, upper, divisions, *, include_borders=True):
    """Return clipped span, first visible line, spacing and count, or None."""
    if not 8 <= end - start <= 65535 or divisions not in (3, 4):
        return None
    step = ((end - start - 2) // divisions) & ~1
    if step < 4:
        return None
    anchor = (((start + end - step * divisions - 2) // 2) + 1) & ~1
    span_start = (max(lower, anchor) + 1) & ~1
    span_end = min(upper, anchor + divisions * step + 2) & ~1
    skipped = max(0, (lower - anchor + step - 1) // step)
    first = (anchor + skipped * step + 1) & ~1
    count = min(divisions + 1 - skipped, (upper - 2 - first) // step + 1)
    if count < 1 or span_end - span_start < 8:
        return None
    span_end = max(span_end, first + (count - 1) * step + 2)
    if not include_borders:
        if first == anchor:
            first += step
            count -= 1
        if count and first + (count - 1) * step == anchor + divisions * step:
            count -= 1
        if count < 1:
            return None
    return span_start, span_end, first, step, count


def projection_instructions():
    """Shared viewport/source projection; writes only the caller's stack."""
    return f'''        coords:ldrsh r0,[r5];ldrsh r1,[r5,#2];ldrsh r2,[r5,#4];ldrsh r3,[r5,#6];
        cmp r0,#0;blt bad;cmp r1,#0;blt bad;cmp r2,#8192;bhi bad;cmp r3,#8192;bhi bad;
        subs r8,r2,r0;subs r9,r3,r1;cmp r8,#8;blt bad;cmp r9,#8;blt bad;
        str r0,[sp];str r1,[sp,#4];str r2,[sp,#8];str r3,[sp,#12];
        cmp r7,#2;bls narrow;
        ldr r10,[r6,#8];ldr r11,[r6,#12];mul r0,r8,r11;mul r1,r9,r10;cmp r0,r1;bhs sides;
        mov r2,r8;udiv r3,r0,r10;b centered;
        sides:udiv r2,r1,r11;mov r3,r9;
        centered:bic r2,r2,#3;bic r3,r3,#3;
        sub r0,r8,r2;lsrs r0,#1;ldr r1,[sp];add r0,r1;bic r0,r0,#1;str r0,[sp,#16];
        add r0,r2;str r0,[sp,#24];
        sub r0,r9,r3;lsrs r0,#1;ldr r1,[sp,#4];add r0,r1;bic r0,r0,#1;str r0,[sp,#20];
        add r0,r3;str r0,[sp,#28];b axes;
        narrow:{c('r5',0xc237c380)}ldrh r0,[r5,#0x52];ldrh r1,[r5,#0x54];
        ldrh r2,[r5,#0x56];ldrh r3,[r5,#0x58];cmp r0,#0;bne bad;
        movw r5,#5568;cmp r2,r5;bne bad;cmp r1,#0;bne source_169;
        movw r5,#3712;cmp r3,r5;bne bad;b source_ok;
        source_169:cmp r1,#292;bne bad;movw r5,#3420;cmp r3,r5;bne bad;
        source_ok:subs r2,r2,r0;subs r3,r3,r1;
        cmp r2,#8;blt bad;cmp r3,#8;blt bad;
        str r0,[sp,#116];str r1,[sp,#120];str r2,[sp,#124];str r3,[sp,#128];
        ldr r0,[r6];ldr r1,[sp,#116];subs r0,r0,r1;mul r0,r0,r8;ldr r2,[sp,#124];sdiv r0,r0,r2;
        ldr r1,[sp];add r0,r1;str r0,[sp,#16];
        ldr r0,[r6];ldr r1,[r6,#8];add r0,r1;ldr r1,[sp,#116];subs r0,r0,r1;
        mul r0,r0,r8;sdiv r0,r0,r2;ldr r1,[sp];add r0,r1;str r0,[sp,#24];
        ldr r0,[r6,#4];ldr r1,[sp,#120];subs r0,r0,r1;mul r0,r0,r9;ldr r2,[sp,#128];sdiv r0,r0,r2;
        ldr r1,[sp,#4];add r0,r1;str r0,[sp,#20];
        ldr r0,[r6,#4];ldr r1,[r6,#12];add r0,r1;ldr r1,[sp,#120];subs r0,r0,r1;
        mul r0,r0,r9;sdiv r0,r0,r2;ldr r1,[sp,#4];add r0,r1;str r0,[sp,#28];
'''


def source_codes(f, *, include_borders=True):
    call = lambda key: n(f[key])
    # Input: start/end/lower/upper/divisions (five signed words).
    # Output: span start/end/first line/step/count at +20..+36. No writes to
    # the live grid occur until both axes have passed all checks.
    code = {}
    interior_only = '' if include_borders else '''
        cmp r6,r11;bne check_far_border;add r6,r10;subs r9,#1;
        check_far_border:cmp r9,#1;blt bad;
        mov r5,r0;subs r1,r9,#1;mul r1,r1,r10;add r1,r6;
        ldr r0,[r4,#16];mul r0,r0,r10;add r0,r11;cmp r0,r1;
        it eq;subeq r9,#1;cmp r9,#1;blt bad;mov r0,r5;
    '''
    code['aspect_frame_axis'] = '''push {r4-r11,lr};sub sp,#4;mov r4,r0;
        ldr r5,[r4];ldr r6,[r4,#4];ldr r7,[r4,#8];ldr r8,[r4,#12];ldr r9,[r4,#16];
        subs r0,r6,r5;cmp r0,#8;blt bad;movw r1,#65535;cmp r0,r1;bhi bad;
        cmp r9,#3;beq divide;cmp r9,#4;bne bad;
        divide:subs r0,#2;udiv r10,r0,r9;bic r10,r10,#1;cmp r10,#4;blo bad;
        mul r0,r10,r9;add r1,r5,r6;subs r1,r1,r0;subs r1,#2;
        asrs r1,#1;adds r1,#1;bic r11,r1,#1;
        cmp r11,r7;ite ge;movge r0,r11;movlt r0,r7;adds r0,#1;bic r0,r0,#1;str r0,[r4,#20];
        mul r0,r10,r9;add r0,r11;adds r0,#2;cmp r0,r8;it gt;movgt r0,r8;
        bic r0,r0,#1;str r0,[r4,#24];
        movs r5,#0;cmp r11,r7;bge first;
        sub r0,r7,r11;add r0,r10;subs r0,#1;udiv r5,r0,r10;
        first:mul r0,r5,r10;add r6,r11,r0;adds r6,#1;bic r6,r6,#1;
        sub r0,r8,#2;subs r0,r0,r6;cmp r0,#0;blt bad;udiv r0,r0,r10;adds r0,#1;
        adds r9,#1;subs r9,r9,r5;cmp r9,r0;it gt;movgt r9,r0;cmp r9,#1;blt bad;
        ldr r0,[r4,#24];ldr r1,[r4,#20];subs r1,r0,r1;cmp r1,#8;blt bad;
        subs r1,r9,#1;mul r1,r1,r10;add r1,r6;adds r1,#2;cmp r0,r1;it lt;movlt r0,r1;
        ''' + interior_only + '''
        str r0,[r4,#24];str r6,[r4,#28];str r10,[r4,#32];str r9,[r4,#36];
        movs r0,#1;b done;bad:movs r0,#0;done:add sp,#4;pop {r4-r11,pc};'''
    code['aspect_frame_prepare'] = f'''push {{r4-r11,lr}};sub sp,#132;
        mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;
        ldrh r0,[r4,#10];cmp r0,#2;beq hc;cmp r0,#3;bne bad;
        hc:ldrh r0,[r4,#22];cmp r0,#2;beq coords;cmp r0,#3;bne bad;
        {projection_instructions()}
        axes:ldr r0,[sp,#16];str r0,[sp,#32];ldr r0,[sp,#24];str r0,[sp,#36];
        ldr r0,[sp];str r0,[sp,#40];ldr r0,[sp,#8];str r0,[sp,#44];
        ldrh r0,[r4,#22];adds r0,#1;str r0,[sp,#48];add r0,sp,#32;{call('aspect_frame_axis')}cmp r0,#0;beq bad;
        ldr r0,[sp,#20];str r0,[sp,#72];ldr r0,[sp,#28];str r0,[sp,#76];
        ldr r0,[sp,#4];str r0,[sp,#80];ldr r0,[sp,#12];str r0,[sp,#84];
        ldrh r0,[r4,#10];adds r0,#1;str r0,[sp,#88];add r0,sp,#72;{call('aspect_frame_axis')}cmp r0,#0;beq bad;
        ldr r0,[sp,#52];strh r0,[r4];ldr r1,[sp,#56];subs r1,r1,r0;str r1,[r4,#4];
        ldr r0,[sp,#100];strh r0,[r4,#2];ldr r0,[sp,#104];strh r0,[r4,#8];ldr r0,[sp,#108];strh r0,[r4,#10];
        ldr r0,[sp,#60];strh r0,[r4,#12];ldr r0,[sp,#92];strh r0,[r4,#14];
        ldr r1,[sp,#96];subs r1,r1,r0;str r1,[r4,#16];
        ldr r0,[sp,#64];strh r0,[r4,#20];ldr r0,[sp,#68];strh r0,[r4,#22];
        movs r0,#1;b done;bad:movs r0,#0;done:add sp,#132;pop {{r4-r11,pc}};'''
    return code
