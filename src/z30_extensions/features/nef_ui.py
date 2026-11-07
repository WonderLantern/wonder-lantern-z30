import struct
from ..assembly import constant as c, invoke as n, assemble
from .menu_slots import page_slots

PAGE_INDEX = 965
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('nef', 3)
MAGIC = 0x314E4C57
OPTION_COUNT = 3
SELECTION_LOSSLESS, SELECTION_COMPRESSED, SELECTION_UNCOMPRESSED = 0, 1, 2
SIZES = dict(nef_get=96, nef_labels=320, nef_open=160,
             nef_action=320, nef_save=640, nef_restore=640, nef_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='NEF compression', lossless='Lossless', compressed='Compressed',
                    uncompressed='Uncompressed', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<3I', strings['lossless'], strings['compressed'],
                                    strings['uncompressed']))
    path = plan.alloc(b'A:\\WL-NEF.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='nef_get', close='nef_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=OPTION_COUNT - 1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'nef_save'), ('restore_settings', 'nef_restore'),
        ('settings_close', 'nef_close'))}
    codes['nef_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['nef_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('nef_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#{OPTION_COUNT - 1};it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{OPTION_COUNT};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['nef_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('nef_get')}ldr r4,[r0];cmp r4,#{OPTION_COUNT - 1};it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        NEF_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['nef_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{OPTION_COUNT - 1};bhi done;
        {call('nef_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('nef_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{OPTION_COUNT}I', *identifiers))
    extension['codes']['nef_open'] = extension['codes']['nef_open'].replace('NEF_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['nef_compression_control'] = dict(
        default=SELECTION_LOSSLESS, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-NEF.CFG',
        selections={'0': 'lossless compressed (stock)', '1': 'compressed', '2': 'uncompressed'},
        restore_policy='First custom-menu initialization; stock lossless before initialization',
        functions={k: hex(f[k] | 1) for k in SIZES}, physical_verified=False)
