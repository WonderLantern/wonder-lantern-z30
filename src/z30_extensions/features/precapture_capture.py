"""One opt-in half-press capture on a dedicated native event task.

Uses the current camera image settings and requires silent photography already
enabled. No setting writes, continuous acquisition, save suppression or claim
that callback 0x46 drains all image pools. Native event/task objects are pinned
in the extension's existing lifetime allocation until shutdown.
"""
from ..assembly import constant as c, invoke as n

TASK, CONFIG, REQUEST = 1744, 1796, 1824
PHASE, READY, TICKET, RESULT = 1844, 1848, 1852, 1856
TASK_INFO, QUERY_RESULT, ENTRY_HIT = 1860, 1924, 1928
STATE_BYTES = 1932
SIZES = dict(pre_task=512, pre_entry=192, pre_inspect=256, pre_wake=384, pre_worker=1280,
             pre_gates=384, pre_complete=320)
# v10: poll the stock 0x1020A630 admission getters (all mutex-guarded reads,
# no writes) while the half press is still held, like the Z7 II worker's
# admission wait, then submit at most one request exactly as v9 did.
GATE_POLLS, GATE_DELAY_MS = 15, 100


def codes(f):
    call = lambda name: n(f[name])
    # Split the original 0x10199CAE constructor into its existing event-create
    # and task-create stages, so event failure cannot start an invalid wait loop.
    task = f'''push {{r4-r8,lr}};mov r4,r0;
        ldr r0,[r4,#{READY}];cmp r0,#1;beq ready;cmp r0,#2;beq failed;
        add r5,r4,#{CONFIG};mov r0,r5;movs r1,#28;{n(0x10199C40)}
        movs r0,#1;str r0,[r5];movs r0,#32;str r0,[r5,#4];
        movw r0,#16384;str r0,[r5,#8];
        {c('r0',0x502d4c57)}str r0,[r5,#16];movw r0,#0x4552;str r0,[r5,#20];
        add r6,r4,#{TASK};add r0,r6,#32;add r1,r5,#16;{n(0x10199D78)}
        ldr r7,[r6,#32];cmp r7,#0;ble failure;
        {c('r0',f['pre_worker']|1)}str r0,[r6,#44];movs r0,#1;str r0,[r6,#48];
        mov r0,r6;{c('r1',f['pre_entry']|1)}mov r2,r5;{n(0x10219876)}
        mov r7,r0;cmp r7,#0;bne failure;ldr r7,[r6];cmp r7,#0;ble failure;
        mov r0,r6;{n(0x10199CD6)}mov r7,r0;cmp r7,#0;bne failure;
        ldr r7,[r6];
        movs r0,#1;dmb ish;str r0,[r4,#{READY}];
        mov r0,r4;movs r1,#5;movs r2,#1;mov r3,r7;{call('pre_log')}
    ready:movs r0,#1;pop {{r4-r8,pc}};
    failure:str r7,[r4,#{RESULT}];movs r0,#2;str r0,[r4,#{READY}];
        movs r0,#12;str r0,[r4,#36];
        mov r0,r4;movs r1,#5;movs r2,#6;mov r3,r7;{call('pre_log')}
    failed:movs r0,#12;str r0,[r4,#36];movs r0,#0;pop {{r4-r8,pc}};'''
    # Record actual OS entry arguments, then supply the dispatcher object
    # explicitly. The stock trampoline preserves r0 and branches via [r1+4].
    # Do not assume the task start code in r0 is an object pointer.
    entry = f'''push {{r4-r6,lr}};mov r5,r0;mov r6,r1;
        {call('bitrate_get')}cmp r0,#0;beq entry_done;mov r4,r0;
        movs r1,#1;str r1,[r4,#{ENTRY_HIT}];dmb ish;
        movs r1,#7;mov r2,r5;mov r3,r6;{call('pre_log')}
        add r0,r4,#{TASK};{n(0x10199C90)}
    entry_done:pop {{r4-r6,pc}};'''
    # v7's task query hung a physical camera at export. Never call the kernel
    # query here. Keep the tail layout, marking the query as deliberately absent.
    inspect = f'''push {{r4-r6,lr}};mov r4,r0;
        ldr r0,[r4,#{READY}];cmp r0,#1;bne inspect_done;
        add r5,r4,#{TASK_INFO};mov r0,r5;movs r1,#64;{n(0x10199C40)}
        mvn r0,#1;str r0,[r4,#{QUERY_RESULT}];
    inspect_done:pop {{r4-r6,pc}};'''
    # Arguments: state, current SW0, previous SW0. Never run a capture here.
    wake = f'''push {{r4-r6,lr}};mov r4,r0;
        ldr r0,[r4,#8];cmp r0,#1;bne done;
        ldr r0,[r4,#{READY}];cmp r0,#1;bne done;
        tst r1,#0x40000000;bne done;
        eor r2,r1,r2;and r2,r1,r2;tst r2,#0x80000000;beq done;
        add r3,r4,#{TICKET};ldrex r0,[r3];cmp r0,#1;bne locked;
        movs r0,#0;strex r1,r0,[r3];cmp r1,#0;bne done;
        ldr r0,[r4,#{PHASE}];cmp r0,#1;blo queue;cmp r0,#3;bls done;
    queue:movs r0,#1;str r0,[r4,#{PHASE}];dmb ish;
        add r0,r4,#{TASK};movs r1,#1;{n(0x10199CDC)}
        mov r5,r0;cmp r5,#0;beq posted;
        str r5,[r4,#{RESULT}];movs r0,#5;str r0,[r4,#{PHASE}];movs r0,#12;str r0,[r4,#36];
    posted:mov r0,r4;movs r1,#5;movs r2,#2;mov r3,r5;{call('pre_log')}
        b done;
    locked:clrex;
    done:movs r0,#0;pop {{r4-r6,pc}};'''
    worker = f'''push {{r4-r8,lr}};mov r5,r0;
        {call('bitrate_get')}cmp r0,#0;beq done;mov r4,r0;
        movs r1,#5;movs r2,#11;mov r3,r5;{call('pre_log')}
        tst r5,#1;beq done;
        mov r0,r4;movs r1,#5;movs r2,#12;ldr r3,[r4,#{PHASE}];{call('pre_log')}
        ldr r0,[r4,#{PHASE}];cmp r0,#1;bne done;
        movs r0,#80;{n(0x1021994A)}
        mov r0,r4;movs r1,#5;movs r2,#13;ldr r3,[r4,#8];{call('pre_log')}
        ldr r0,[r4,#8];cmp r0,#1;bne disabled;
        {n(0x101E389C)}ldr r0,[r0,#4];tst r0,#0x40000000;bne stale;
        tst r0,#0x80000000;beq stale;
        movs r7,#0;
    gate_loop:
        mov r0,r4;movs r1,#0;cmp r7,#0;it eq;moveq r1,#1;{call('pre_gates')}
        cmp r0,#1;beq gates_clear;
        adds r7,#1;cmp r7,#{GATE_POLLS};bhs gates_timeout;
        movs r0,#{GATE_DELAY_MS};{n(0x1021994A)}
        ldr r0,[r4,#8];cmp r0,#1;bne disabled;
        {n(0x101E389C)}ldr r0,[r0,#4];tst r0,#0x40000000;bne stale;
        tst r0,#0x80000000;beq stale;
        b gate_loop;
    gates_timeout:movs r6,#14;b gates_logged;
    gates_clear:movs r6,#15;
    gates_logged:
        cmp r7,#0;beq gates_first;
        mov r0,r4;movs r1,#1;{call('pre_gates')}
    gates_first:
        mov r0,r4;movs r1,#5;mov r2,r6;mov r3,r7;{call('pre_log')}
        movw r0,#0x57b;{n(0x10188776)}cmp r0,#1;bne silent_required;
        {n(0x1020BB8A)}cmp r0,#0;bne native_active;
        movs r0,#0x46;{n(0x10061064)}cmp r0,#0;bne occupied;
        movs r0,#0x47;{n(0x10061064)}cmp r0,#0;bne occupied;
        add r5,r4,#{REQUEST};mov r0,r5;movs r1,#20;{n(0x10199C40)}
        movs r0,#1;str r0,[r5,#16];movs r0,#2;str r0,[r4,#{PHASE}];
        mov r0,r4;movs r1,#5;movs r2,#3;movs r3,#1;{call('pre_log')}
        mov r0,r5;{c('r1',f['pre_complete']|1)}{n(0x1020A630)}mov r6,r0;
        str r6,[r4,#{RESULT}];cmp r6,#0;bne rejected;
        ldr r0,[r4,#{PHASE}];cmp r0,#2;bne returned;
        movs r0,#3;str r0,[r4,#{PHASE}];
        ldr r0,[r4,#36];cmp r0,#1;bne returned;movs r0,#14;str r0,[r4,#36];b returned;
    rejected:movs r0,#5;str r0,[r4,#{PHASE}];movs r0,#13;str r0,[r4,#36];
    returned:mov r0,r4;movs r1,#5;movs r2,#4;mov r3,r6;{call('pre_log')}b done;
    silent_required:movs r5,#11;movs r6,#7;b refuse;
    occupied:movs r5,#13;movs r6,#9;b refuse;
    native_active:movs r5,#13;movs r6,#10;b refuse;
    stale:movs r5,#1;movs r6,#8;
    refuse:movs r0,#5;str r0,[r4,#{PHASE}];str r5,[r4,#36];
        mov r0,r4;movs r1,#5;mov r2,r6;movs r3,#0;{call('pre_log')}
        b done;
    disabled:movs r0,#5;str r0,[r4,#{PHASE}];
    done:movs r0,#0;pop {{r4-r8,pc}};'''
    # Snapshot the stock capture-entry admission inputs in entry order:
    # attr 0x1B2E (!=0 -> 0x20), 0x1020BBC4 (==0x12 -> 0x20), 0x1020BC2E,
    # 0x1006316E, 0x10063166, 0x10065964 (!=0 -> 0x1D), 0x1020BAEC (!=0 -> 0xD).
    # 0x101F5204(0x11B)==1 makes the entry ignore the request and only
    # register slot 0x46, so it is logged but never waited on.
    # Args: state, log flag. Returns 1 only when every waited gate is clear.
    gates = f'''push {{r4-r9,lr}};sub sp,#4;mov r4,r0;mov r9,r1;
        {n(0x1020BBC4)}uxtb r5,r0;
        {n(0x1020BC2E)}uxtb r0,r0;orr r5,r5,r0,lsl #8;
        {n(0x1020BAEC)}uxtb r0,r0;orr r5,r5,r0,lsl #16;
        {n(0x10065964)}uxtb r0,r0;orr r5,r5,r0,lsl #24;
        movw r0,#0x1b2e;{n(0x10188776)}mov r6,r0;
        {n(0x1006316E)}mov r7,r0;{n(0x10063166)}mov r8,r0;
        cmp r9,#0;beq judge;
        mov r0,r4;movs r1,#8;mov r2,r5;mov r3,r6;{call('pre_log')}
        mov r0,r4;movs r1,#9;mov r2,r7;mov r3,r8;{call('pre_log')}
        movw r0,#0x11b;{n(0x101F5204)}mov r9,r0;{n(0x1020BB8A)}mov r3,r0;
        mov r0,r4;movs r1,#10;mov r2,r9;{call('pre_log')}
    judge:movs r0,#0;
        cmp r6,#0;bne gates_done;cmp r7,#0;bne gates_done;cmp r8,#0;bne gates_done;
        and r1,r5,#0xff;cmp r1,#0x12;beq gates_done;
        bics r1,r5,#0xff;bne gates_done;
        movs r0,#1;
    gates_done:add sp,#4;pop {{r4-r9,pc}};'''
    complete = f'''push {{r4-r6,lr}};mov r5,r0;mov r6,r1;
        {call('bitrate_get')}cmp r0,#0;beq done;mov r4,r0;
        {c('r0',0x04100046)}cmp r5,r0;bne record;
        ldr r0,[r4,#{PHASE}];cmp r0,#2;beq completed;cmp r0,#3;bne record;
    completed:movs r0,#4;dmb ish;str r0,[r4,#{PHASE}];
    record:mov r0,r4;movs r1,#6;mov r2,r5;mov r3,r6;{call('pre_log')}
    done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(pre_task=task, pre_entry=entry, pre_inspect=inspect, pre_wake=wake, pre_worker=worker,
                pre_gates=gates, pre_complete=complete)
