# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
r"""Benchmark page: measures the camera's application CPU (branch experimental).

The whole benchmark is one C / assembly payload in WL.PATCH (modules/bench),
with no change to the resident EG. Rows:

  0 Run benchmark   start a run (about 3-4 s); the value shows progress and the result
  1 CPU             core type and revision, cores in the cluster, measured clock
  2 Integer         adds per cycle (4 chains) and table CRC32 throughput
  3 FPU / NEON      NEON and scalar VFP single-precision GFLOPS
  4 Memory          DRAM read / write bandwidth and load latency

Rows 1-4 are label-only rows whose label text the payload writes (ASCII). The
full numbers go to A:\Z30\BENCH.TXT.
"""
from ...assembly import constant as c, invoke as n, assemble
from ..menu.ui import list_top
from ..menu.slots import BLOCKS
from . import bench_payload as payload

PAGE_INDEX = 977   # 976 handheld
ROWS = 5
TITLE_SLOT = BLOCKS['bench_title'][0]
ROOT_SLOT = BLOCKS['bench'][0]
# The free text slots are scattered, so the page names each row's label slot.
LABEL_SLOTS = (BLOCKS['bench'][0] + 1, BLOCKS['bench'][0] + 2, *range(BLOCKS['bench_rows'][0], BLOCKS['bench_rows'][0] + 3))
LABEL_SLOT = LABEL_SLOTS[0]
STATUS_SLOT = BLOCKS['bench_value'][0]
MARK_SLOT = CURRENT_SLOT = STATUS_SLOT
# Per row: the value column (None: label only).
VALUE_SLOTS = (STATUS_SLOT, None, None, None, None)
TITLE = ('Benchmark', ('跑分', '跑分'))
RUN = ('Run benchmark', ('开始跑分', '開始跑分'))
SIZES = dict(bench_labels=256, bench_open=128, bench_action=128)
CMD_STATUS, CMD_ACTION, CMD_ROW = 0, 1, 2


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    f['bench_payload'] = plan.alloc(payload.CODE)
    page_offset, state_offset = allocation_size, allocation_size + 44
    title = resources.alloc(*TITLE)
    run_label = resources.alloc(*RUN)
    call = lambda name: n(f[name])
    run = lambda cmd: f"movs r0,#{cmd};movs r1,#0;movs r2,#0;{call('bench_payload')}"
    codes = {}
    codes['bench_labels'] = f'''push {{r4,lr}};
        {c('r0',title)}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',title)}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {c('r0',run_label)}movs r1,#{LABEL_SLOTS[0]};movs r2,#0;{call('text')}
        {run(CMD_STATUS)}movs r1,#{STATUS_SLOT};movs r2,#0;{call('text_ascii')}
        ''' + ''.join(f"{run(CMD_ROW + i)}movs r1,#{slot};movs r2,#0;{call('text_ascii')}"
                      for i, slot in enumerate(LABEL_SLOTS[1:])) + '''
        movs r0,#0;pop {r4,pc};'''
    codes['bench_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        {call('bench_labels')}movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # r0 = the page object; row 0 starts a run, which then advances on a menu timer.
    codes['bench_action'] = f'''push {{r4,lr}};mov r4,r0;
        ldrh r2,[r4,#22];ldrh r1,[r4,#24];adds r2,r2,r1;
        movs r0,#{CMD_ACTION};mov r1,r4;{call('bench_payload')}
        mov r0,r4;{call('render')}
        {c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{n(0x10453fac)}
        movs r0,#0;pop {{r4,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['bench'] = dict(
        page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        payload=hex(f['bench_payload'] | 1), payload_bytes=payload.SIZE, payload_sha256=payload.SHA256,
        report='Z30/BENCH.TXT', rows=[RUN[0], 'CPU', 'Integer', 'FPU / NEON', 'Memory'],
        controls=list(identifiers), functions={k: hex(f[k] | 1) for k in SIZES},
        physical_verified=False)
