"""Execute the portrait UI switch page: SD save/restore and menu actions; hardware services are fixtures."""
import argparse
import json
import struct
from pathlib import Path
from verify_sound_settings import Fixture
from z30_extensions.container import extract_eg
from z30_extensions.features.portrait_menu import LABEL_SLOT, MAGIC, SETTINGS_FILE, TEXT_A, TEXT_B
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1


def verify(stock, image, report):
    control = report['features']['portrait_ui_control']
    off = control['state_offset']
    functions = {k: int(v, 16) for k, v in control['functions'].items()}
    cases = []
    for value in (0, 1):
        files = {}
        a = Fixture(stock, image, report, files, properties={})
        a.set(off, value)
        assert a.call(functions['portrait_save']) == 1
        b = Fixture(stock, image, report, files, properties={})
        assert b.word(off) == value and not b.opened
        cases.append(dict(fresh_state_restore=value))
    valid = struct.pack('<4I', MAGIC, 0, 1, ~(MAGIC ^ 1) & 0xffffffff)
    for payload in (b'', valid[:8], valid + b'X', valid[:-1] + b'\0',
                    struct.pack('<4I', MAGIC, 0, 2, ~(MAGIC ^ 2) & 0xffffffff)):
        a = Fixture(stock, image, report, {'A:\\' + SETTINGS_FILE: payload}, properties={})
        assert a.word(off) == 0 and not a.opened
    cases.append(dict(invalid_records_fail_closed=5))
    a = Fixture(stock, image, report, {}, properties={})
    navigation = []
    def boundary(u, address, size, data):
        if address == 0x1045f732:
            navigation.append(u.reg_read(UC_ARM_REG_R1))
        a.ret(0)
    for address in (a.f['labels'] & ~1, 0x1045f732, 0x1045d54c, 0x10453fac):
        a.u.hook_add(UC_HOOK_CODE, boundary, begin=address, end=address)
    page = a.state + control['page_offset']
    # A choice clears the hooks' runtime words and the readout; the persisted words stay.
    runtime = off + control['runtime_offset']
    for value in (1, 0, 1):
        for word in range(10):
            a.set(runtime + 4 * word, 0x11 + word)
        a.u.mem_write(page + 22, struct.pack('<2H', value, 0))
        a.call(functions['portrait_action'], (page,))
        assert a.word(off) == value and navigation[-1] == 955, (a.word(off), value, navigation)
        assert all(a.word(runtime + 4 * word) == 0 for word in range(10))
        a.call(functions['portrait_restore'])
        assert a.word(off) == value
    # Readout lines handed to the ASCII text setter.
    texts = {}
    def text(u, address, size, data):
        pointer, slot = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)
        if address == a.f['text_ascii'] & ~1:
            texts[slot] = bytes(u.mem_read(pointer, 28)).split(bytes(1))[0].decode()
        a.ret(0)
    for address in (a.f['text'] & ~1, a.f['text_ascii'] & ~1):
        a.u.hook_add(UC_HOOK_CODE, text, begin=address, end=address)
    words = dict(kinds=0x00000409, targets=0x00000604, phases=0x06, busy=0x5, copies=0x00000200, passes=0x1234, forced=0x3)
    for offset, key in ((36, 'kinds'), (40, 'targets'), (44, 'phases'), (48, 'busy'), (52, 'copies'), (28, 'passes'), (32, 'forced')):
        a.set(off + offset, words[key])
    a.call(functions['portrait_labels'])
    diagnostics = control.get('diagnostics', True)
    want = {LABEL_SLOT + 2: 'K00000409 T00000604', LABEL_SLOT + 3: 'P06B5C00000200R1234F03'} if diagnostics else {}
    assert texts == want, texts  # a release build shows only OFF/ON
    cases.append(dict(readout_lines=list(want.values())))
    if not diagnostics:
        # Opening the page writes nothing to the card.
        files = {}
        b = Fixture(stock, image, report, files, properties={})
        for address in (0x1045f732, 0x1045d54c, 0x10453fac):  # stock page navigation
            b.u.hook_add(UC_HOOK_CODE, lambda u, *_: b.ret(0), begin=address, end=address)
        b.call(functions['portrait_open'])
        assert not files, list(files)
        cases.append(dict(open_writes_no_file=True))
    if diagnostics:
        # Export: the whole block reaches the card and recording resumes afterwards.
        from z30_extensions.features.portrait_menu import LOG, LOG_FILE, STATE_SIZE
        files = {}
        b = Fixture(stock, image, report, files, properties={})
        pattern = bytes((i * 7) & 255 for i in range(STATE_SIZE - LOG))
        b.u.mem_write(b.state + off + LOG, pattern)
        b.set(off + LOG + 8, 0)
        b.call(functions['portrait_export'])
        written = files.get('A:\\' + LOG_FILE)
        block = bytearray(b.u.mem_read(b.state + off, STATE_SIZE))
        block[LOG + 8:LOG + 12] = struct.pack('<I', 1)  # recording is paused while the block is written
        assert written is not None and bytes(written) == block, (written and len(written), STATE_SIZE)
        assert b.word(off + LOG + 8) == 0
        cases.append(dict(export_bytes=STATE_SIZE, export_file=LOG_FILE, recording_resumed=True))
    a.short_write = True
    assert a.call(functions['portrait_save']) == 0 and a.word(52) == 8
    assert a.word(off) == 1
    cases.append(dict(menu_changes=3, runtime_words_cleared=True, save_failure_retains_active_selection=True))
    return cases


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'output'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    report = json.loads(a.report.read_text())
    raw = a.source.read_bytes()
    try:
        stock = extract_eg(raw)
    except Exception:
        stock = raw
    cases = verify(stock, a.image.read_bytes(), report)
    a.output.write_text(json.dumps(dict(cases=cases, scope=__doc__, physical_verified=False), indent=2) + '\n')
    print('PASS portrait switch SD/action/restore cases:', len(cases))
