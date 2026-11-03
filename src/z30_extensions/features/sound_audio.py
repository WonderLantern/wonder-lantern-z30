# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Native Z30 sound routines and menu support."""
from .sound_layout import *
from ..assembly import constant, invoke

def audio_code(addresses, path_table, anchor_table):
    codes = {}
    c = constant
    def call(name): return invoke(addresses[name])
    native = lambda address: invoke(address, thumb=address != 0x10199bdc)
    def guard(else_label):
        return f"""{c('r0',0xc21c0e28)} ldrb r1,[r0,#28]; cmp r1,#18; bne {else_label};
            ldr r1,[r0,#32]; {c('r2',addresses['complete']|1)} cmp r1,r2; bne {else_label};
            {call('get')} cmp r0,#0; beq {else_label}; ldr r1,[r0,#{LENGTH}]; cmp r1,#0; beq {else_label};"""
    codes['get']=f"""
            {c('r0',STATE_ANCHOR)} ldr r0,[r0,#16]; {c('r1',anchor_table)}
            cmp r0,r1; beq absent; cmp r0,#0; beq absent;
            ldr r1,[r0,#56]; {c('r2',MAGIC)} cmp r1,r2; bne absent; bx lr;
            absent: movs r0,#0; bx lr;
        """

    codes['cas']="""
            movs r12,#8;
            retry: ldrex r3,[r0]; cmp r3,r1; bne mismatch;
            strex r3,r2,[r0]; cmp r3,#0; beq good;
            subs r12,r12,#1; bne retry;
            mismatch: clrex; movs r0,#0; bx lr;
            good: dmb ish; movs r0,#1; bx lr;
        """

    codes['validate']=f"""
            push {{r4,lr}}; cmp r1,#46; blo bad;
            {c('r2',17324)} cmp r1,r2; bhi bad;
            ldr r2,[r0]; {c('r3',0x46464952)} cmp r2,r3; bne bad;
            ldr r2,[r0,#4]; adds r2,#8; cmp r2,r1; bne bad;
            ldr r2,[r0,#8]; {c('r3',0x45564157)} cmp r2,r3; bne bad;
            ldr r2,[r0,#12]; {c('r3',0x20746d66)} cmp r2,r3; bne bad;
            ldr r2,[r0,#16]; cmp r2,#16; bne bad;
            ldr r2,[r0,#20]; {c('r3',0x00010001)} cmp r2,r3; bne bad;
            ldr r2,[r0,#24]; {c('r3',8000)} cmp r2,r3; bne bad;
            ldr r2,[r0,#28]; {c('r3',16000)} cmp r2,r3; bne bad;
            ldr r2,[r0,#32]; {c('r3',0x00100002)} cmp r2,r3; bne bad;
            ldr r2,[r0,#36]; {c('r3',0x61746164)} cmp r2,r3; bne bad;
            ldr r4,[r0,#40]; cmp r4,#0; beq bad; tst r4,#1; bne bad;
            {c('r2',MAX_DATA)} cmp r4,r2; bhi bad;
            add r2,r4,#44; cmp r2,r1; bne bad; mov r0,r4; pop {{r4,pc}};
            bad: movs r0,#0; pop {{r4,pc}};
        """

    codes['convert']="""
            push {r4-r11,lr}; sub sp,#4; mov r4,r0; mov r5,r1; lsrs r6,r2,#1;
            add r11,r2,r2,lsl #1; lsl r11,r11,#2; movs r10,#6;
            outer: ldrsh r7,[r4]; mov r8,r7; cmp r6,#1; beq last;
            ldrsh r8,[r4,#2]; last: sub r8,r8,r7; mul r7,r7,r10; movs r9,#0;
            inner: mla r0,r8,r9,r7; sdiv r0,r0,r10;
            strh r0,[r5],#2; strh r0,[r5],#2; adds r9,#1; cmp r9,#6; bne inner;
            adds r4,#2; subs r6,#1; bne outer;
            mov r0,r11; add sp,#4; pop {r4-r11,pc};
        """

    codes['load']=f"""
            push {{r4-r8,lr}}; sub sp,#48; mov r7,r0; {call('get')} mov r4,r0;
            cmp r4,#0; beq failed; cmp r7,#4; bhi failed;
            add r0,r4,#{BUSY}; movs r1,#0; movs r2,#1; {call('cas')} cmp r0,#1; bne busy;
            ldr r0,[r4,#112]; cmp r0,#0; blt fd_clear; {native(0x102c89ee)}
            cmp r0,#0; bne close_failed; mvn r0,#0; str r0,[r4,#112];
            fd_clear: mov r0,sp; movs r1,#48; {native(0x10199c40)}
            mvn r0,#0; str r0,[sp,#8]; {c('r0',path_table)} ldr r1,[r0,r7,lsl #2];
            mov r0,sp; {native(0x1033829e)} mov r0,sp; movs r1,#0; movs r2,#0; {native(0x103382a2)}
            {c('r1',SUCCESS)} cmp r0,r1; bne open_failed;
            ldr r8,[sp,#8]; movs r0,#0; str r0,[sp,#40];
            mov r0,sp; add r1,r4,#{WAV_OFFSET}; {c('r2',17325)} add r3,sp,#40; {native(0x10338414)}
            mov r6,r0; mov r0,sp; {native(0x1033832e)} {c('r1',SUCCESS)} cmp r0,r1; beq closed;
            mov r0,r8; {native(0x102c89ee)} cmp r0,#0; beq closed;
            str r8,[r4,#112]; b close_failed;
            closed: {c('r1',SUCCESS)} cmp r6,r1; bne read_failed;
            add r0,r4,#{WAV_OFFSET}; ldr r1,[sp,#40]; {call('validate')} cmp r0,#0; beq invalid;
            mov r2,r0; add r0,r4,#{WAV_OFFSET+44}; {c('r1',PCM_OFFSET)} add r1,r4,r1; {call('convert')}
            str r0,[r4,#{LENGTH}]; str r7,[r4,#{SELECTED}]; movs r0,#0; str r0,[r4,#{RESULT}];
            dmb ish; str r0,[r4,#{BUSY}]; movs r0,#1; b done;
            open_failed: ldr r8,[sp,#8]; cmp r8,#0; blt missing;
            mov r0,sp; {native(0x1033832e)} {c('r1',SUCCESS)} cmp r0,r1; beq missing;
            str r8,[r4,#112]; b close_failed;
            missing: movs r5,#1; b unlock;
            read_failed: movs r5,#2; b unlock;
            invalid: movs r5,#3; b unlock;
            close_failed: movs r5,#4;
            unlock: str r5,[r4,#{RESULT}]; movs r0,#0; dmb ish; str r0,[r4,#{BUSY}]; b done;
            busy: movs r0,#5; str r0,[r4,#{RESULT}];
            failed: movs r0,#0;
            done: add sp,#48; pop {{r4-r8,pc}};
        """

    codes['start']=f"""
            push {{r4-r8,lr}}; mov r4,r0;
            movs r0,#12; movs r1,#0; {native(0x10211f4c)} mov r5,r0; cmp r5,#0; beq failure;
            movs r0,#36; movs r1,#0; {native(0x10211f4c)} mov r6,r0; cmp r6,#0; beq free_job;
            mov r0,r6; movs r1,#36; {native(0x10199c40)}
            movs r0,#1; str r0,[r6]; str r0,[r6,#24]; movs r0,#31; strb r0,[r6,#4];
            movs r0,#18; strb r0,[r6,#28]; {c('r0',addresses['complete']|1)} str r0,[r6,#32];
            str r6,[r5]; {c('r0',0x101db84f)} str r0,[r5,#4]; movs r0,#0; str r0,[r5,#8];
            {c('r0',0x0418001e)} mov r1,r5; {native(0x10096ca0)} cmp r0,#0; beq free_record;
            movs r0,#0; str r0,[r4,#{RESULT}]; movs r0,#1; pop {{r4-r8,pc}};
            free_record: mov r0,r6; {native(0x10211f5e)}
            free_job: mov r0,r5; {native(0x10211f5e)}
            failure: movs r0,#6; str r0,[r4,#{RESULT}]; movs r0,#0;
            str r0,[r4,#{PENDING}]; dmb ish; str r0,[r4,#{BUSY}]; pop {{r4-r8,pc}};
        """

    codes['stop']=f"""
            push {{r4,lr}}; mov r4,r0;
            add r0,r4,#{BUSY}; movs r1,#3; movs r2,#4; {call('cas')} cmp r0,#1; bne done;
            {native(0x101dba7c)} cmp r0,#0; bne done;
            movs r0,#3; dmb ish; str r0,[r4,#{BUSY}]; movs r0,#0;
            done: pop {{r4,pc}};
        """

    codes['trigger']=f"""
            push {{r4-r6,lr}}; {call('ensure')} mov r4,r0; cmp r4,#0; beq no;
            ldr r0,[r4,#{ENABLED}]; cmp r0,#1; bne no;
            ldr r0,[r4,#{LENGTH}]; cmp r0,#0; beq no;
            {c('r0',0x188c)} {native(0x101993de)} cmp r0,#0; bne no;
            {c('r0',0x57b)} {native(0x101993de)} cmp r0,#1; bne no;
            ldr r0,[r4,#{BUSY}]; cmp r0,#1; beq no;
            cmp r0,#0; bne pending;
            {c('r0',0xc1885a50)} ldrb r0,[r0]; cmp r0,#0; bne no;
            add r0,r4,#{BUSY}; movs r1,#0; movs r2,#2; {call('cas')} cmp r0,#1; bne no;
            mov r0,r4; {call('start')} b done;
            pending: cmp r0,#4; bhi no;
            movs r0,#1; str r0,[r4,#{PENDING}]; mov r0,r4; {call('stop')} b done;
            no: movs r0,#0; done: pop {{r4-r6,pc}};
        """

    codes['complete']=f"""
            push {{r4-r6,lr}}; mov r5,r0; mov r6,r1; {call('get')} mov r4,r0;
            cmp r4,#0; beq done; {c('r0',0x04180022)} cmp r5,r0; beq started;
            adds r0,#1; cmp r5,r0; bne done;
            ldr r0,[r6,#4]; cmp r0,#0; bne failed;
            ldr r0,[r4,#{PENDING}]; cmp r0,#1; bne idle;
            ldr r0,[r4,#{ENABLED}]; cmp r0,#1; bne idle;
            movs r0,#0; str r0,[r4,#{PENDING}]; movs r0,#2; str r0,[r4,#{BUSY}];
            mov r0,r4; {call('start')} b done;
            failed: movs r0,#7; str r0,[r4,#{RESULT}];
            idle: movs r0,#0; str r0,[r4,#{PENDING}]; dmb ish; str r0,[r4,#{BUSY}]; b done;
            started: ldr r0,[r6,#4]; cmp r0,#0; bne done;
            movs r0,#3; dmb ish; str r0,[r4,#{BUSY}];
            ldr r0,[r4,#{PENDING}]; cmp r0,#1; bne done; mov r0,r4; {call('stop')}
            done: movs r0,#1; pop {{r4-r6,pc}};
        """

    # The root page object persists, and its list cursor (+22) and first visible row
    # (+24) with it. The camera can draw a re-entered list from the top while the
    # cursor stays on the old row, so each entry starts both at the first row.
    codes['open']=f"""
            push {{r4,lr}}; {call('ensure')} cmp r0,#0; beq done;
            movs r1,#0; strh r1,[r0,#{PAGE_OFFSET + 22}]; strh r1,[r0,#{PAGE_OFFSET + 24}]; {call('labels')}
            movs r0,#2; {c('r1',PAGE_INDEX)} {native(0x1045f732)}
            done: movs r0,#0; pop {{r4,pc}};
        """

    codes['config']=f"""
            push {{r0-r4,lr}}; {guard('stock')}
            ldr r1,[r0,#{LENGTH}]; {c('r0',0x01010502)} str r0,[sp]; str r1,[sp,#4];
            movs r0,#0; str r0,[sp,#8]; mov r0,sp; {native(0x1006b5f4)}
            add sp,#4; pop {{r1-r4,pc}};
            stock: pop {{r0-r4,lr}}; push {{r1-r3,lr}}; {c('r2',0xc21c0e28)} movs r3,#2;
            {c('r1',0x114d506c)} strb r3,[sp];
            .balign 4; ldr.w pc,[pc,#0]; .word 0x1006beeb;
        """

    codes['data']=f"""
            push {{r0-r4,lr}}; sub sp,#24; {guard('stock')}
            {c('r1',PCM_OFFSET)} add r1,r0,r1; str r1,[sp]; ldr r1,[r0,#{LENGTH}]; str r1,[sp,#4];
            movs r1,#1; str r1,[sp,#8]; movs r1,#0; str r1,[sp,#12]; str r1,[sp,#16]; str r1,[sp,#20];
            ldr r1,[sp,#24]; mov r0,sp; {native(0x1006b7b2)}
            add sp,#28; pop {{r1-r4,pc}};
            stock: add sp,#24; pop {{r0-r4,lr}}; push {{r4-r6,lr}};
            {c('r4',0xc21c0e28)} mov r6,r0; {c('r5',0x114d506c)}
            .balign 4; ldr.w pc,[pc,#0]; .word 0x1006be99;
        """

    codes['shot']=f"""
            {c('r0',0x04190001)} mov r1,sp; adds r0,#3; blx r2;
            push {{r0-r3,r12,lr}}; mrs r0,apsr; push {{r0,r1}};
            {call('trigger')} pop {{r0,r1}}; msr apsr_nzcvq,r0;
            pop {{r0-r3,r12,lr}}; {c('lr',0x100725a1)}
            .balign 4; ldr.w pc,[pc,#0]; .word 0x100725a1;
        """
    return codes
