# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
r"""Linux page: hand the whole camera over to a Linux kernel from the card (branch experimental).

The handoff is one C / assembly payload in WL.PATCH (modules/linuxboot); the
kernel, DTB and initrd are files in A:\Z30\LINUX (tools/build_linux_kit.py).
docs/linux.md has the plan and the evidence. Rows:

  0 Boot Linux   load, park core 1, stop the watchdog, MMU off, jump (no way back)
  1 Dry run      load and patch the DTB only; the value shows the sizes or the error
  2 (info)       GIC distributor and timer frequency this camera reports
  3 (info)       LCD overlay plane and the watchdog's PrimeCell id

Rows 2-3 are label-only rows whose text the payload writes (ASCII).
"""
from ...assembly import constant as c, invoke as n, assemble
from ..menu.ui import list_top
from ..menu.slots import BLOCKS
from . import linuxboot_payload as payload

PAGE_INDEX = 978   # 977 benchmark
ROWS = 4
TITLE_SLOT = BLOCKS['linux_title'][0]
ROOT_SLOT = BLOCKS['linux_root'][0]
LABEL_SLOTS = tuple(BLOCKS[name][0] for name in ('linux_row0', 'linux_row1', 'linux_row2', 'linux_row3'))
LABEL_SLOT = LABEL_SLOTS[0]
STATUS_SLOT = BLOCKS['linux_value'][0]
MARK_SLOT = CURRENT_SLOT = STATUS_SLOT
VALUE_SLOTS = (STATUS_SLOT, None, None, None)
TITLE = ('Linux', ('Linux', 'Linux'))
BOOT = ('Boot Linux', ('启动 Linux', '啟動 Linux'))
DRY = ('Dry run', ('试运行', '試執行'))
SIZES = dict(linux_labels=256, linux_open=128, linux_action=128)
CMD_STATUS, CMD_ACTION, CMD_INFO = 0, 1, 2


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    # 64-byte aligned: lowlevel.S keeps core 1's parking words in a cache line of their
    # own (.p2align 6 inside the payload). The loader puts the tail on a 64 KiB boundary.
    plan.alloc(bytes(-plan.address % 64))
    f['linux_payload'] = plan.alloc(payload.CODE)
    if f['linux_payload'] % 64:
        raise ValueError('Linux handoff payload is not 64-byte aligned')
    page_offset, state_offset = allocation_size, allocation_size + 44
    title, boot, dry = resources.alloc(*TITLE), resources.alloc(*BOOT), resources.alloc(*DRY)
    call = lambda name: n(f[name])
    run = lambda cmd: f"movs r0,#{cmd};movs r1,#0;movs r2,#0;{call('linux_payload')}"
    codes = {}
    codes['linux_labels'] = f'''push {{r4,lr}};
        {c('r0',title)}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',title)}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {c('r0',boot)}movs r1,#{LABEL_SLOTS[0]};movs r2,#0;{call('text')}
        {c('r0',dry)}movs r1,#{LABEL_SLOTS[1]};movs r2,#0;{call('text')}
        {run(CMD_STATUS)}movs r1,#{STATUS_SLOT};movs r2,#0;{call('text_ascii')}
        {run(CMD_INFO)}movs r1,#{LABEL_SLOTS[2]};movs r2,#0;{call('text_ascii')}
        {run(CMD_INFO + 1)}movs r1,#{LABEL_SLOTS[3]};movs r2,#0;{call('text_ascii')}
        movs r0,#0;pop {{r4,pc}};'''
    codes['linux_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        {call('linux_labels')}movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # r0 = the page object; row 0 boots (does not return on success), row 1 is the dry run.
    codes['linux_action'] = f'''push {{r4,lr}};mov r4,r0;
        ldrh r2,[r4,#22];ldrh r1,[r4,#24];adds r2,r2,r1;
        movs r0,#{CMD_ACTION};mov r1,r4;{call('linux_payload')}
        mov r0,r4;{call('render')}
        {c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{n(0x10453fac)}
        movs r0,#0;pop {{r4,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['linux'] = dict(
        page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        payload=hex(f['linux_payload'] | 1), payload_bytes=payload.SIZE, payload_sha256=payload.SHA256,
        files=['Z30/LINUX/ZIMAGE', 'Z30/LINUX/Z30.DTB', 'Z30/LINUX/INITRD'],
        rows=[BOOT[0], DRY[0], 'GIC / timer', 'LCD / watchdog'],
        controls=list(identifiers), functions={k: hex(f[k] | 1) for k in SIZES},
        physical_verified=False)
