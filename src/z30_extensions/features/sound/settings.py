# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Versioned SD sound preferences; corrupt/missing settings leave sound OFF.

Native file operations run in task context, never in an interrupt. The 16-byte
record contains WLS1, enabled, file index, and the complement of their XOR.
Saving uses the native create/truncate mode, checks short writes and close,
and reports failure without disabling the working in-memory sound.
"""
from ..menu.layout import CONFIG_MAGIC, ENABLED, SELECTED, RESULT, SUCCESS, SETTINGS_FD
from ...assembly import constant, invoke


def settings_code(f, path, *, state_get='get', close='settings_close',
                  magic=CONFIG_MAGIC, enabled_offset=ENABLED, selected_offset=SELECTED,
                  fd_offset=SETTINGS_FD, enabled_max=1, selected_max=4,
                  load_sound=True, restore_enabled=False, flag_offset=None):
    """Emit a checked SD record; optional layout supports independent video state.

    flag_offset: a 0/1 state word kept in bit 8 of the record's enabled word.
    Records without the bit (written before it existed) restore it as 0; a
    module without flag_offset rejects a record that has it set.
    """
    CONFIG_MAGIC, ENABLED, SELECTED, SETTINGS_FD = magic, enabled_offset, selected_offset, fd_offset
    c = constant
    call = lambda name: invoke(f[state_get if name == 'get' else close if name == 'settings_close' else name])
    n = invoke
    report_success = (f'movs r0,#0;str r0,[r4,#{RESULT}];' if state_get == 'get' else
                      f"{invoke(f['get'])}movs r1,#0;str r1,[r0,#{RESULT}];")
    report_failure = (f'movs r0,#8;str r0,[r4,#{RESULT}];' if state_get == 'get' else
                      f"{invoke(f['get'])}movs r1,#8;str r1,[r0,#{RESULT}];")
    restore = (f'''str r6,[r4,#{SELECTED}];cmp r5,#0;beq done;
        mov r0,r6;{call('load')}cmp r0,#1;bne done;str r5,[r4,#{ENABLED}];b done;'''
        if load_sound else (f'str r5,[r4,#{ENABLED}];' if restore_enabled else '') + f'str r6,[r4,#{SELECTED}];b done;')
    if flag_offset is None:
        save_enabled = f'ldr r1,[r4,#{ENABLED}];'
        check_enabled = f'cmp r5,#{enabled_max};bhi done;'
    else:
        if load_sound or enabled_max > 255:
            raise ValueError('A flag bit needs an enabled value below 256 without sound loading')
        save_enabled = (f'ldr r1,[r4,#{ENABLED}];ldr r2,[r4,#{flag_offset}];cmp r2,#0;beq flag_clear;'
                        'orr r1,r1,#256;flag_clear:')
        check_enabled = f'bic r1,r5,#256;cmp r1,#{enabled_max};bhi done;'
        restore = (f'ubfx r1,r5,#8,#1;str r1,[r4,#{flag_offset}];and r5,r5,#255;' + restore)
    initialize = f"""
        mov r0,sp;movs r1,#48;{n(0x10199c40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',path)}{n(0x1033829e)}
    """
    # Native wrapper 10338414 converts raw zero-success to SUCCESS.
    # Raw write 102c8a6a is operation 9, with the same count-out convention.
    return {
        'settings_close': f"""
            push {{r4,lr}};mov r4,r0;ldr r0,[r4,#{SETTINGS_FD}];cmp r0,#0;blt good;
            {n(0x102c89ee)}cmp r0,#0;bne bad;
            good:mvn r0,#0;str r0,[r4,#{SETTINGS_FD}];movs r0,#1;pop {{r4,pc}};
            bad:movs r0,#0;pop {{r4,pc}};
        """,
        'save_settings': f"""
            push {{r4-r8,lr}};sub sp,#64;{call('get')}mov r4,r0;cmp r4,#0;beq failed;
            {initialize}
            mov r0,r4;{call('settings_close')}cmp r0,#1;bne failed;
            mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382a2)}
            ldr r1,[sp,#8];str r1,[r4,#{SETTINGS_FD}];
            {c('r1',SUCCESS)}cmp r0,r1;bne failed;
            ldr r0,[sp,#8];str r0,[r4,#{SETTINGS_FD}];
            {c('r0',CONFIG_MAGIC)}str r0,[sp,#48];{save_enabled}str r1,[sp,#52];
            eor r0,r0,r1;ldr r1,[r4,#{SELECTED}];str r1,[sp,#56];eor r0,r0,r1;mvn r0,r0;str r0,[sp,#60];
            movs r0,#0;str r0,[sp,#40];ldr r0,[sp,#8];add r1,sp,#48;movs r2,#16;add r3,sp,#40;
            {n(0x102c8a6a)}mov r5,r0;ldr r6,[sp,#40];mov r0,sp;{n(0x1033832e)}
            {c('r1',SUCCESS)}cmp r0,r1;bne failed;
            mvn r0,#0;str r0,[r4,#{SETTINGS_FD}];cmp r5,#0;bne failed;cmp r6,#16;bne failed;
            {report_success}movs r0,#1;b done;
            failed:cmp r4,#0;beq no_state;
            mov r0,r4;{call('settings_close')}
            report:{report_failure}
            no_state:movs r0,#0;done:add sp,#64;pop {{r4-r8,pc}};
        """,
        'restore_settings': f"""
            push {{r4-r8,lr}};sub sp,#72;{call('get')}mov r4,r0;cmp r4,#0;beq done;
            {initialize}
            mov r0,r4;{call('settings_close')}cmp r0,#1;bne done;
            mov r0,sp;movs r1,#0;movs r2,#0;{n(0x103382a2)}
            ldr r1,[sp,#8];str r1,[r4,#{SETTINGS_FD}];
            {c('r1',SUCCESS)}cmp r0,r1;bne cleanup;
            ldr r0,[sp,#8];str r0,[r4,#{SETTINGS_FD}];
            mov r0,sp;add r1,sp,#48;movs r2,#17;add r3,sp,#40;{n(0x10338414)}mov r5,r0;
            mov r0,sp;{n(0x1033832e)}{c('r1',SUCCESS)}cmp r0,r1;bne cleanup;
            mvn r0,#0;str r0,[r4,#{SETTINGS_FD}];
            cmp r5,r1;bne done;ldr r0,[sp,#40];cmp r0,#16;bne done;
            ldr r0,[sp,#48];{c('r1',CONFIG_MAGIC)}cmp r0,r1;bne done;
            ldr r5,[sp,#52];{check_enabled}ldr r6,[sp,#56];cmp r6,#{selected_max};bhi done;
            eor r0,r0,r5;eor r0,r0,r6;mvn r0,r0;ldr r1,[sp,#60];cmp r0,r1;bne done;
            {restore}
            cleanup:mov r0,r4;{call('settings_close')}
            done:movs r0,#0;add sp,#72;pop {{r4-r8,pc}};
        """,
    }
