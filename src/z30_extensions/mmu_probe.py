"""Research ZMOD that answers whether the EG code region can be patched in RAM.

The stock boot code (0x1002BCF0..0x1002BD14) loads TTBR0 = 0x10030000, a
short-descriptor table inside the EG image, and DACR = 0x55555555. That table
maps 0x10000000.. as cacheable normal memory with AP=0b101 (privileged
read-only) in domain 1, and 0xC1800000.. RAM with AP=0b001. Whether the code
region is DRAM (a copy the loader made) or memory-mapped flash cannot be read
from the image, so this module measures it on the camera.

Each Run shows one line of at most 24 characters (the Chinese UI limit):

    1 M=<mode> S=<SCTLR>        CPU mode and SCTLR
    2 D=<DACR> C=<TTBCR>
    3 A=<TTBR0> B=<TTBR1>
    4 E=<L1 0x10020000> T=<L1 api>
    5 W=<PAR write> R=<PAR read>  ATS1CPW/ATS1CPR on the host API table
    6 O=<old> V=<readback>      the only step that writes, see below
    7 F=<final>                 must equal O

Steps 1-5 only read. Step 6 masks interrupts, sets domain 1 to manager,
writes the inverted value over the host's get_property word (an API table in
the EG tail), cleans and invalidates that line to the point of coherency,
reads it back from memory, restores the word and DACR. V == ~O means writes
reach memory (the region is RAM). V == O means they are dropped. If the
region is flash, the clean may raise an external abort and hang the camera:
remove the battery. The host refuses User-mode callers (shows "USR mode").

Camera result of v1: VA 0x10000000 maps to DRAM 0xCC400000 (the EG is copied),
while the tail section 0x12100000 stays identity and drops writes. build(2)
measures how far the DRAM copy reaches and tests writes to stock code, both
with DACR manager and through the DRAM identity alias (docs/card-loader-probe.md).
"""
from .assembly import assemble
from .modules import ABI, API_BYTES, MAX_CODE, pack

SAVE = 52  # context words 52/56/60 keep step 6 results for step 7


def _line(label, *values):
    """Text label then 8-hex-digit values, each preceded by its own label."""
    parts = [f'adr.w r0,{label}; bl put_str;']
    for name, load in values:
        parts.append(f'adr.w r0,{name}; bl put_str; {load} bl put_hex;')
    return ' '.join(parts)


L1 = ('mrc p15,#0,r1,c2,c0,#0; bic r1,r1,#0x3f00; bic r1,r1,#0xff; '
      'lsrs r2,r0,#20; ldr r0,[r1,r2,lsl #2];')
BOOT_WORD = 0x10031000  # stock boot L1 table entry for VA 0x40000000 (a fault entry, unused at runtime)
ALIAS_RAM = 0xCC400000  # boot table maps this DRAM identity, privileged read/write


def _write_test(target, label):
    """Invert the word at `target` (r0) with domain 1 set to manager, read it back
    from memory, restore it. Leaves old/readback in r2/r3."""
    return f'''{target} mrs r8,cpsr; cpsid i; mrc p15,#0,r6,c3,c0,#0; orr r12,r6,#0xc;
        mcr p15,#0,r12,c3,c0,#0; isb sy;
        ldr r2,[r0]; mvns r1,r2; str r1,[r0]; dsb sy;
        mcr p15,#0,r0,c7,c14,#1; dsb sy; ldr r3,[r0];
        str r2,[r0]; dsb sy; mcr p15,#0,r0,c7,c14,#1; dsb sy; ldr r1,[r0];
        mcr p15,#0,r6,c3,c0,#0; isb sy; msr cpsr_c,r8;
        str r2,[r5,#{SAVE}]; str r3,[r5,#{SAVE + 4}]; str r1,[r5,#{SAVE + 8}];
        ''' + _line(label, ('k_o', f'ldr r0,[r5,#{SAVE}];'), ('k_v', f'ldr r0,[r5,#{SAVE + 4}];'))


def _steps_v1():
    return [
        'adr.w r0,line1; bl put_str; mrs r0,cpsr; and r0,r0,#0x1f; bl put_hex2;'
        ' adr.w r0,k_s; bl put_str; mrc p15,#0,r0,c1,c0,#0; bl put_hex;',
        _line('line2', ('k_d', 'mrc p15,#0,r0,c3,c0,#0;'), ('k_c', 'mrc p15,#0,r0,c2,c0,#2;')),
        _line('line3', ('k_a', 'mrc p15,#0,r0,c2,c0,#0;'), ('k_b', 'mrc p15,#0,r0,c2,c0,#1;')),
        _line('line4', ('k_e', f'movw r0,#0; movt r0,#0x1002; {L1}'), ('k_t', f'mov r0,r4; {L1}')),
        _line('line5', ('k_w', 'add r0,r4,#8; mcr p15,#0,r0,c7,c8,#1; isb sy; mrc p15,#0,r0,c7,c4,#0;'),
              ('k_r', 'add r0,r4,#8; mcr p15,#0,r0,c7,c8,#0; isb sy; mrc p15,#0,r0,c7,c4,#0;')),
        _write_test('add r0,r4,#8;', 'line6'),
        _line('line7', ('k_f', f'ldr r0,[r5,#{SAVE + 8}];')),
    ]


def _steps_v2():
    """Where the DRAM copy of the EG ends, and whether stock code can be written."""
    table = 'mrc p15,#0,r1,c2,c0,#0; bic r1,r1,#0x3f00; bic r1,r1,#0xff;'
    scan = f'''{table} movw r2,#0x100; ldr r3,[r1,r2,lsl #2]; bfc r3,#0,#20;
        scan: adds r2,#1; cmp r2,#0x200; bhs scan_done;
        ldr r0,[r1,r2,lsl #2]; and r12,r0,#3; cmp r12,#2; bne scan_done;
        bfc r0,#0,#20; add r3,r3,#0x100000; cmp r0,r3; beq scan;
        scan_done: str r2,[r5,#36]; subs r0,r2,#1;'''
    boot = f'movw r0,#{BOOT_WORD & 0xFFFF}; movt r0,#{BOOT_WORD >> 16};'
    alias_par = (f'{boot} mcr p15,#0,r0,c7,c8,#0; isb sy; mrc p15,#0,r0,c7,c4,#0; str r0,[r5,#44];')
    alias_w = ('ldr r0,[r5,#44]; bfc r0,#0,#12; str r0,[r5,#40];'
               ' mcr p15,#0,r0,c7,c8,#1; isb sy; mrc p15,#0,r0,c7,c4,#0; str r0,[r5,#48];')
    # Alias test: write ~old through the RAM alias, read through the code VA.
    alias_test = f'''ldr r0,[r5,#48]; tst r0,#1; beq alias_ok; adr.w r0,line5; bl put_str;
        adr.w r0,k_skip; bl put_str; b.w finish;
        alias_ok: ldr r0,[r5,#40]; {boot.replace('r0', 'r12')}
        mrs r8,cpsr; cpsid i; ldr r2,[r0]; mvns r1,r2; str r1,[r0]; dsb sy;
        mcr p15,#0,r0,c7,c14,#1; dsb sy; ldr r3,[r12];
        str r2,[r0]; dsb sy; mcr p15,#0,r0,c7,c14,#1; dsb sy; ldr r1,[r12]; msr cpsr_c,r8;
        str r3,[r5,#{SAVE + 4}]; str r1,[r5,#{SAVE + 8}];
        ''' + _line('line5', ('k_v', f'ldr r0,[r5,#{SAVE + 4}];'), ('k_f', f'ldr r0,[r5,#{SAVE + 8}];'))
    return [
        f'adr.w r0,line1; bl put_str; mrc p15,#0,r0,c2,c0,#0; bl put_hex; adr.w r0,k_l; bl put_str; {scan} bl put_hex;',
        _line('line2', ('k_n', f'{table} ldr r2,[r5,#36]; ldr r0,[r1,r2,lsl #2];'),
              ('k_p', f'movw r0,#0; movt r0,#{ALIAS_RAM >> 16}; {L1}')),
        _line('line3', ('k_q', alias_par), ('k_g', alias_w)),
        _write_test(boot, 'line4'),
        alias_test,
    ]


TEXTS = {
    1: dict(ready='mmu-probe ready', line1='1 M=', k_s=' S=',
            line2='2', k_d=' D=', k_c=' C=', line3='3', k_a=' A=', k_b=' B=',
            line4='4', k_e=' E=', k_t=' T=', line5='5', k_w=' W=', k_r=' R=',
            line6='6', k_o=' O=', k_v=' V=', line7='7', k_f=' F='),
    2: dict(ready='mmu-probe v2 ready', line1='1 A=', k_l=' L=',
            line2='2', k_n=' N=', k_p=' P=', line3='3', k_q=' Q=', k_g=' G=',
            line4='4', k_o=' O=', k_v=' V=', line5='5', k_f=' F=', k_skip=' skip'),
}


def source(version=1):
    steps = _steps_v1() if version == 1 else _steps_v2()
    texts = dict(TEXTS[version], usr='USR mode')
    dispatch = ' '.join(f'cmp r6,#{i}; beq.w step{i};' for i in range(len(steps)))
    bodies = ' '.join(f'step{i}: {body} b.w finish;' for i, body in enumerate(steps))
    data = ' '.join(f'{name}: .asciz "{text}";' for name, text in texts.items())
    return f'''
        push {{r4-r6,lr}}; mov r4,r0; mov r5,r1;
        ldr r2,[r4]; cmp r2,#{ABI}; bne reject;
        ldr r2,[r4,#4]; cmp r2,#{API_BYTES}; blo reject;
        adr.w r0,run; orr r0,r0,#1; ldr r3,[r4,#16]; blx r3;
        cmp r0,#1; bne reject;
        movs r0,#0; str r0,[r5];
        adr.w r0,ready; ldr r3,[r4,#12]; blx r3;
        movs r0,#1; pop {{r4-r6,pc}};
        reject: movs r0,#0; pop {{r4-r6,pc}};
        .balign 4;
        run: push {{r4-r8,lr}}; mov r4,r0; mov r5,r1; add r7,r5,#8;
        mrs r0,cpsr; and r0,r0,#0x1f; cmp r0,#0x10; bne privileged;
        adr.w r0,usr; bl put_str; b.w finish;
        privileged: ldr r6,[r5]; adds r0,r6,#1; cmp r0,#{len(steps)}; blo store; movs r0,#0;
        store: str r0,[r5];
        {dispatch} b.w finish;
        {bodies}
        finish: movs r0,#0; strb r0,[r7]; add r0,r5,#8; ldr r3,[r4,#12]; blx r3;
        pop {{r4-r8,pc}};
        put_str: ldrb r1,[r0],#1; cbz r1,put_done; strb r1,[r7],#1; b put_str;
        put_done: bx lr;
        put_hex2: movs r2,#4; b hex_loop;
        put_hex: movs r2,#28;
        hex_loop: lsr.w r1,r0,r2; and r1,r1,#15; cmp r1,#10; blo hex_digit; adds r1,#7; hex_digit: adds r1,#48;
        strb r1,[r7],#1; subs r2,#4; bpl hex_loop; bx lr;
        .balign 4;
        {data}
        .balign 4;
    '''


def build(version=1):
    return pack(assemble(source(version), 0, MAX_CODE))
