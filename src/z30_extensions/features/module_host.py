"""Resident, menu-owned SD native-module host for the modular preset.

Only trusted PIC code is supported. Successful modules are pinned until power
off. File reads, pool allocation and shared cache maintenance use Nikon APIs;
installation and ZMOD load/run were verified on camera; this does not validate
every payload or failure path.
"""
import struct
from ..assembly import assemble, constant, invoke
from ..modules import ABI, API_BYTES, MAX_CODE, CONTEXT_BYTES, MAX_TEXT
from .module_validator import validator_code
from .module_cache import sync_shared_code
from .menu_slots import BLOCKS
from .sound_layout import NEW_SIZE, SWITCH_INDEX, SUCCESS

PAGE_INDEX = SWITCH_INDEX + 1
PAGE_OFFSET = NEW_SIZE
STATE_OFFSET = PAGE_OFFSET + 44
STATE_BYTES = 352
ALLOCATION_SIZE = STATE_OFFSET + STATE_BYTES
PHASE, ERROR, ALLOCATION, TOTAL, ENTRY, HANDLER, FD, FILE_STATE = range(0, 32, 4)
VALIDATED = 32
FILE_OBJECT = 48
HEADER = 96
CONTEXT = 160
TEXT = 224
TEMP = 288
ROOT_SLOT = BLOCKS['modules'][0]
TITLE_SLOT, LABEL_SLOT, EMPTY_SLOT, STATUS_SLOT = ROOT_SLOT + 1, ROOT_SLOT + 2, ROOT_SLOT + 6, ROOT_SLOT + 7
STATUS_TEXT = ('Not loaded', 'Loading', 'Loaded', 'Running')
ERROR_TEXT = ('', 'File not found', 'Read failed', 'Bad module', 'Close failed',
              'No memory', 'Cache unavailable', 'Init failed', 'No handler',
              'Reboot to reload', 'Run failed', 'Host busy', 'Memory retained')
FUNCTION_SIZES = dict(module_get=128, module_labels=512, module_open=128,
                      module_action=256, module_load=2048, module_read=256,
                      module_close=256, module_set_text=512,
                      module_set_handler=256, module_run=256,
                      module_validate=512, module_sync=256)


def prepare(plan, f, text_resources):
    for name, size in FUNCTION_SIZES.items():
        f[name] = plan.alloc(bytes(size))
    api = plan.alloc(struct.pack('<5I', ABI, API_BYTES, 0x10465985,
                                 f['module_set_text'] | 1, f['module_set_handler'] | 1))
    path = plan.alloc(b'A:\\Z30\\CORE.ZMOD\0')
    strings = {name: text_resources.alloc(value) for name, value in
               dict(title='SD Modules', load='Load CORE.ZMOD', run='Run module',
                    state='State', empty='', output='No module output').items()}
    statuses = [text_resources.alloc(s) for s in STATUS_TEXT]
    errors = [text_resources.alloc(s) for s in ERROR_TEXT]
    status_table = plan.alloc(struct.pack('<4I', *statuses))
    error_table = plan.alloc(struct.pack('<' + 'I' * len(errors), *errors))
    fixed = plan.alloc(struct.pack('<7I', strings['title'], strings['title'],
                                   strings['load'], strings['run'], strings['state'],
                                   strings['output'], strings['empty']))
    c = constant
    call = lambda name: invoke(f[name])
    native = lambda address: invoke(address, thumb=address != 0x10199BDC)
    code = {}
    code['module_get'] = f'''
        push {{r4,lr}}; {call('get')} cmp r0,#0; beq done;
        {c('r1', STATE_OFFSET)} add r0,r0,r1;
        done: pop {{r4,pc}};
    '''
    code['module_labels'] = f'''
        push {{r4-r8,lr}}; {c('r4',fixed)} movs r5,#{ROOT_SLOT}; movs r6,#7;
        again: ldr r0,[r4],#4; mov r1,r5; movs r2,#0; {call('text')}
        adds r5,#1; subs r6,#1; bne again;
        {call('module_get')} mov r4,r0; {c('r0',statuses[0])}
        cmp r4,#0; beq put_status;
        ldr r1,[r4,#{ERROR}]; cmp r1,#0; beq phase;
        cmp r1,#{len(errors)-1}; bhi phase; {c('r0',error_table)} ldr r0,[r0,r1,lsl #2]; b put_status;
        phase: ldr r1,[r4,#{PHASE}]; cmp r1,#3; bhi put_status;
        {c('r0',status_table)} ldr r0,[r0,r1,lsl #2];
        put_status: movs r1,#{STATUS_SLOT}; movs r2,#0; {call('text')}
        cmp r4,#0; beq done; ldrb r0,[r4,#{TEXT}]; cmp r0,#0; beq done;
        add r0,r4,#{TEXT}; movs r1,#{LABEL_SLOT+3}; movs r2,#0; {call('text_ascii')}
        done: movs r0,#0; pop {{r4-r8,pc}};
    '''
    code['module_open'] = f'''
        push {{r4,lr}}; {call('ensure')} cmp r0,#0; beq done;
        {call('labels')} movs r0,#2; {c('r1',PAGE_INDEX)} {native(0x1045F732)}
        done: movs r0,#0; pop {{r4,pc}};
    '''
    code['module_action'] = f'''
        push {{r4,lr}}; mov r4,r0; ldrh r1,[r4,#22]; ldrh r2,[r4,#24]; adds r1,r1,r2;
        cmp r1,#0; bne run; {call('module_load')} b redraw;
        run: cmp r1,#1; bne redraw; {call('module_run')}
        redraw: mov r0,r4; {call('render')}
        {c('r0',0x10453AC3)} movs r1,#3; movs r2,#7; {native(0x10453FAC)}
        movs r0,#0; pop {{r4,pc}};
    '''
    # A native read returns status and writes the count through R3. Accumulate
    # short reads; zero before completion is a truncated file, never success.
    code['module_read'] = f'''
        push {{r4-r8,lr}}; sub sp,#8; mov r4,r0; mov r5,r1; mov r6,r2;
        more: cmp r6,#0; beq good; movs r0,#0; str r0,[sp];
        mov r0,r4; mov r1,r5; mov r2,r6; mov r3,sp; {native(0x10338414)}
        {c('r1',SUCCESS)} cmp r0,r1; bne bad; ldr r0,[sp]; cmp r0,#0; beq bad;
        cmp r0,r6; bhi bad; add r5,r5,r0; sub r6,r6,r0; b more;
        good: movs r0,#1; b done;
        bad: movs r0,#0; done: add sp,#8; pop {{r4-r8,pc}};
    '''
    code['module_close'] = f'''
        push {{r4,lr}}; mov r4,r0; ldr r0,[r4,#{FD}]; cmp r0,#0; blt good;
        ldr r1,[r4,#{FILE_STATE}]; cmp r1,#1; bne retry;
        add r0,r4,#{FILE_OBJECT}; {native(0x1033832E)}
        {c('r1',SUCCESS)} cmp r0,r1; beq good; movs r0,#2; str r0,[r4,#{FILE_STATE}]; b bad;
        retry: {native(0x102C89EE)} cmp r0,#0; bne bad;
        good: mvn r0,#0; str r0,[r4,#{FD}]; movs r0,#0; str r0,[r4,#{FILE_STATE}]; movs r0,#1; b done;
        bad: movs r0,#0; done: pop {{r4,pc}};
    '''
    code['module_load'] = f'''
        push {{r4-r11,lr}}; sub sp,#12; {call('module_get')} mov r4,r0; cmp r4,#0; beq bad_return;
        ldr r0,[r4,#{PHASE}]; cmp r0,#2; beq already;
        cmp r0,#0; bne bad_return;
        ldr r0,[r4,#{ALLOCATION}]; cmp r0,#0; beq close_pending;
        {native(0x10211F5E)} cmp r0,#0; bne retained;
        movs r0,#0; str r0,[r4,#{ALLOCATION}]; str r0,[r4,#{TOTAL}]; str r0,[r4,#{ENTRY}];
        close_pending: mov r0,r4; {call('module_close')} cmp r0,#1; bne close_error;
        movs r0,#1; str r0,[r4,#{PHASE}]; movs r0,#0; str r0,[r4,#{ERROR}]; str r0,[r4,#{HANDLER}];
        strb r0,[r4,#{TEXT}]; add r0,r4,#{CONTEXT}; movs r1,#{CONTEXT_BYTES}; {native(0x10199C40)}
        add r0,r4,#{FILE_OBJECT}; movs r1,#40; {native(0x10199C40)}
        mvn r0,#0; str r0,[r4,#{FILE_OBJECT+8}];
        add r0,r4,#{FILE_OBJECT}; {c('r1',path)} {native(0x1033829E)}
        add r0,r4,#{FILE_OBJECT}; movs r1,#0; movs r2,#0; {native(0x103382A2)}
        mov r6,r0; ldr r0,[r4,#{FILE_OBJECT+8}]; str r0,[r4,#{FD}]; cmp r0,#0; blt opened_status;
        movs r0,#1; str r0,[r4,#{FILE_STATE}];
        opened_status: {c('r1',SUCCESS)} cmp r6,r1; bne missing;
        add r0,r4,#{FILE_OBJECT}; add r1,r4,#{HEADER}; movs r2,#64; {call('module_read')}
        cmp r0,#1; bne read_error;
        ldr r5,[r4,#{HEADER+20}]; cmp r5,#0; beq invalid;
        {c('r0',MAX_CODE)} cmp r5,r0; bhi invalid; tst r5,#3; bne invalid;
        add r0,r5,#64; str r0,[r4,#{TOTAL}]; movs r1,#0; {native(0x10211F4C)}
        cmp r0,#0; beq no_memory; str r0,[r4,#{ALLOCATION}]; mov r7,r0; tst r7,#3; bne invalid;
        add r1,r4,#{HEADER}; movs r2,#64; {native(0x10199BDC)}
        add r0,r4,#{FILE_OBJECT}; add r1,r7,#64; mov r2,r5; {call('module_read')}
        cmp r0,#1; bne read_error;
        movs r0,#0; str r0,[sp]; add r0,r4,#{FILE_OBJECT}; add r1,r4,#{TEMP}; movs r2,#1; mov r3,sp;
        {native(0x10338414)} {c('r1',SUCCESS)} cmp r0,r1; bne read_error;
        ldr r0,[sp]; cmp r0,#0; bne invalid;
        mov r0,r4; {call('module_close')} cmp r0,#1; bne close_error;
        mov r0,r7; ldr r1,[r4,#{TOTAL}]; add r2,r4,#{VALIDATED}; {call('module_validate')}
        cmp r0,#1; bne invalid;
        ldr r0,[r4,#{VALIDATED}]; ldr r1,[r4,#{VALIDATED+4}]; {call('module_sync')}
        cmp r0,#1; bne cache_error;
        ldr r3,[r4,#{VALIDATED+8}]; str r3,[r4,#{ENTRY}]; {c('r0',api)} add r1,r4,#{CONTEXT}; blx r3;
        cmp r0,#1; bne init_error; ldr r0,[r4,#{HANDLER}]; cmp r0,#0; beq handler_error;
        movs r0,#2; str r0,[r4,#{PHASE}]; movs r0,#0; str r0,[r4,#{ERROR}]; movs r0,#1; b done;
        missing: movs r8,#1; b cleanup;
        read_error: movs r8,#2; b cleanup;
        invalid: movs r8,#3; b cleanup;
        close_error: movs r8,#4; b cleanup;
        no_memory: movs r8,#5; b cleanup;
        cache_error: movs r8,#6; b cleanup;
        init_error: movs r8,#7; b cleanup;
        handler_error: movs r8,#8;
        cleanup: movs r0,#0; str r0,[r4,#{HANDLER}]; str r0,[r4,#{ENTRY}]; strb r0,[r4,#{TEXT}];
        mov r0,r4; {call('module_close')} cmp r0,#1; beq release; movs r8,#4;
        release: ldr r0,[r4,#{ALLOCATION}]; cmp r0,#0; beq failed;
        {native(0x10211F5E)} cmp r0,#0; bne retain_failed;
        movs r0,#0; str r0,[r4,#{ALLOCATION}]; str r0,[r4,#{TOTAL}]; b failed;
        retain_failed: movs r8,#12;
        failed: str r8,[r4,#{ERROR}]; movs r0,#0; str r0,[r4,#{PHASE}]; b bad_return;
        already: movs r0,#9; str r0,[r4,#{ERROR}]; b bad_return;
        retained: movs r0,#12; str r0,[r4,#{ERROR}];
        bad_return: movs r0,#0;
        done: add sp,#12; pop {{r4-r11,pc}};
    '''
    code['module_set_handler'] = f'''
        push {{r4,lr}}; mov r4,r0; {call('module_get')} cmp r0,#0; beq bad;
        ldr r1,[r0,#{PHASE}]; cmp r1,#1; bne bad;
        tst r4,#1; beq bad; bic r1,r4,#1;
        ldr r2,[r0,#{ALLOCATION}]; cmp r2,#0; beq bad; add r3,r2,#64; cmp r1,r3; blo bad;
        ldr r3,[r0,#{TOTAL}]; adds r2,r2,r3; bcs bad; cmp r1,r2; bhs bad;
        str r4,[r0,#{HANDLER}]; movs r0,#1; b done;
        bad: movs r0,#0; done: pop {{r4,pc}};
    '''
    code['module_set_text'] = f'''
        push {{r4-r8,lr}}; mov r5,r0; {call('module_get')} mov r4,r0; cmp r4,#0; beq bad;
        ldr r0,[r4,#{PHASE}]; cmp r0,#1; beq pointer; cmp r0,#3; bne bad;
        pointer: ldr r1,[r4,#{ALLOCATION}]; cmp r1,#0; beq bad; add r2,r1,#64;
        cmp r5,r2; blo context; ldr r2,[r4,#{TOTAL}]; adds r1,r1,r2; bcs bad;
        cmp r5,r1; blo copy;
        context: add r2,r4,#{CONTEXT}; cmp r5,r2; blo bad; add r1,r2,#{CONTEXT_BYTES}; cmp r5,r1; bhs bad;
        copy: add r6,r4,#{TEMP}; movs r7,#0;
        next: cmp r5,r1; bhs bad; ldrb r0,[r5],#1; cmp r0,#0; beq commit;
        cmp r0,#32; blo bad; cmp r0,#126; bhi bad; cmp r7,#{MAX_TEXT}; bhs bad;
        strb r0,[r6],#1; adds r7,#1; b next;
        commit: strb r0,[r6]; add r0,r4,#{TEXT}; add r1,r4,#{TEMP}; add r2,r7,#1; {native(0x10199BDC)}
        movs r0,#1; b done;
        bad: movs r0,#0; done: pop {{r4-r8,pc}};
    '''
    code['module_run'] = f'''
        push {{r4,lr}}; {call('module_get')} mov r4,r0; cmp r4,#0; beq bad;
        ldr r0,[r4,#{PHASE}]; cmp r0,#2; bne bad;
        ldr r3,[r4,#{HANDLER}]; cmp r3,#0; beq bad;
        movs r0,#3; str r0,[r4,#{PHASE}]; {c('r0',api)} add r1,r4,#{CONTEXT}; blx r3;
        cmp r0,#1; ite eq; moveq r1,#0; movne r1,#10;
        str r1,[r4,#{ERROR}]; movs r1,#2; str r1,[r4,#{PHASE}]; pop {{r4,pc}};
        bad: movs r0,#0; pop {{r4,pc}};
    '''
    for name, source in code.items():
        raw = assemble(source, f[name], FUNCTION_SIZES[name])
        plan.put(f[name], raw)
    validator = validator_code(f['module_validate'])
    sync = sync_shared_code(f['module_sync'])
    if len(validator) > FUNCTION_SIZES['module_validate'] or len(sync) > FUNCTION_SIZES['module_sync']:
        raise ValueError('Module validation/cache function exceeds owned slot')
    plan.put(f['module_validate'], validator)
    plan.put(f['module_sync'], sync)
    plan.features['module_host'] = dict(
        abi=ABI, api_bytes=API_BYTES, api=hex(api), page_index=PAGE_INDEX,
        page_offset=PAGE_OFFSET, state_offset=STATE_OFFSET, state_bytes=STATE_BYTES,
        module_path='Z30/CORE.ZMOD', max_code_bytes=MAX_CODE,
        context_bytes=CONTEXT_BYTES, max_text_chars=MAX_TEXT,
        functions={k: hex(f[k] | 1) for k in FUNCTION_SIZES},
        loading='Explicit menu action after card and menu services are ready',
        unloading='None; successful modules remain allocated until shutdown',
        shared_cache_service=True, camera_sd_module_execution_verified=True)
    return dict(api=api, page_offset=PAGE_OFFSET, state_offset=STATE_OFFSET, fd_offset=FD,
                allocation_size=ALLOCATION_SIZE)
