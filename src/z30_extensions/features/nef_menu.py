from ..assembly import assemble, invoke, jump, constant

MODE_UNCOMPRESSED = 0
MODE_COMPRESSED = 2
SNAPSHOT_MODE_OFFSET = 0x22C

SITES = (
    dict(kind='register', address=0x101E77B8, expected=bytes.fromhex('c1788af85a120179'),
         head='ldrb r1,[r0,#3];', tail='strb.w r1,[sl,#0x25a];ldrb r1,[r0,#4];',
         slot=8, resume=0x101E77C0, name='NEF compression menu: source record byte'),
    dict(kind='register', address=0x101536A0, expected=bytes.fromhex('90f85a1280f82c12'),
         head='ldrb.w r1,[r0,#0x25a];', tail='strb.w r1,[r0,#0x22c];',
         slot=8, resume=0x101536A8, name='NEF compression menu: shot path'),
    dict(kind='register', address=0x10153A06, expected=bytes.fromhex('95f85a0286f8240095f85902'),
         head='ldrb.w r0,[r5,#0x25a];', tail='strb.w r0,[r6,#0x24];ldrb.w r0,[r5,#0x259];',
         slot=4, resume=0x10153A12, name='NEF compression menu: pipeline request'),
    dict(kind='register', address=0x1015F956, expected=bytes.fromhex('90f85a1280f82c120198'),
         head='ldrb.w r1,[r0,#0x25a];', tail='strb.w r1,[r0,#0x22c];ldr r0,[sp,#4];',
         slot=8, resume=0x1015F960, name='NEF compression menu: alternate shot path'),
    dict(kind='snapshot', address=0x101436C2, expected=bytes.fromhex('70b500250c46deb00646'),
         head='', tail='push {r4,r5,r6,lr};movs r5,#0;mov r4,r1;sub sp,#0x178;mov r6,r0;',
         slot=8, resume=0x101436CC, name='NEF compression menu: capture config creation'),
)


def selection_code(site, get, state_offset):
    if site['kind'] == 'register':
        apply = f"str r3,[sp,#{site['slot']}];"
    else:
        apply = f"ldr r1,[sp,#{site['slot']}];cmp r1,#0;beq stock;strb.w r3,[r1,#{SNAPSHOT_MODE_OFFSET}];"
    return f'''
        {invoke(get)}cmp r0,#0;beq stock;
        {constant('r1',state_offset)}ldr r0,[r0,r1];
        cmp r0,#1;beq compressed;cmp r0,#2;bne stock;
        movs r3,#{MODE_UNCOMPRESSED};b put;
        compressed:movs r3,#{MODE_COMPRESSED};
        put:{apply}
    '''


def cave_source(site, get, state_offset):
    return f'''
        {site['head']}
        push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0}};
        {selection_code(site, get, state_offset)}
        stock:pop {{r0}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
        {site['tail']}
        ldr.w pc, ={site['resume'] | 1};
    '''


def add(plan):
    control = plan.features['nef_compression_control']
    get = int(plan.features['shutter_sound']['functions']['get'], 16)
    hooks = []
    for site in SITES:
        entry = plan.address
        code = assemble(cave_source(site, get, control['state_offset']), entry, 192)
        plan.alloc(code)
        plan.edit(site['address'], jump(site['address'], entry, len(site['expected'])),
                  site['name'], site['expected'])
        hooks.append(dict(site=hex(site['address']), kind=site['kind'], entry=hex(entry | 1), size=len(code)))
    plan.features['nef_menu'] = dict(
        revision=2, hooks=hooks,
        selections={'0': 'stock', '1': 'compressed (mode 2)', '2': 'uncompressed (mode 0)'},
        state_offset=control['state_offset'], settings_file='WL-NEF.CFG',
        stock_when_uninitialized=True, camera_verified=False)
