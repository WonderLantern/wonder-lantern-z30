"""Execute native highlight derivation callbacks for the photo-zebra candidate.

Dependency snapshots and the observer index are modeled. All calculation
helpers, queue construction and the callbacks execute original instructions,
except for the two declared candidate mode-gate edits. No camera access.
"""
import argparse
import binascii
import itertools
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.build import build, resolve_presets
from z30_extensions.container import extract_eg
from z30_extensions.features.photo.zebra import GATES
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage, sha256


class Context:
    def __init__(self, image):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
        from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC,
                                       UC_ARM_REG_R0, UC_ARM_REG_R4,
                                       UC_ARM_REG_R5, UC_ARM_REG_R7)
        self.u = u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 4095) & ~4095)
        u.mem_write(BASE, image)
        u.mem_map(0xC1800000, 0x100000)
        u.mem_map(0xC238A000, 0x1000)
        source, destination, size, _ = SCATTER
        u.mem_write(destination, image[source-BASE:source-BASE+size])
        u.mem_map(0x20000000, 0x10000)
        self.notifications = []
        self.gate_inputs = None
        self.building = False
        self.requests = []
        # Only the two property pages read by the native request builder.
        u.mem_map(0xC2501000, 0x1000)
        u.mem_map(0xC2534000, 0x1000)

        def hook(machine, address, _size, _context):
            if self.building and self.builder_service(machine, address):
                return
            if address == 0x101644A4:
                self.notifications.append(machine.reg_read(UC_ARM_REG_R0))
            if address in (0x101777F6, 0x10177922):
                self.gate_inputs = dict(movie=machine.reg_read(UC_ARM_REG_R5),
                                        peaking=machine.reg_read(UC_ARM_REG_R4 if address == 0x101777F6 else UC_ARM_REG_R7),
                                        scene=machine.reg_read(UC_ARM_REG_R0))
        u.hook_add(UC_HOOK_CODE, hook)

    def builder_service(self, machine, address):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_PC, UC_ARM_REG_LR)
        if address == 0x10465984:
            # Bypass high-level readiness validation; execute the native
            # property descriptor lookup and getter instead of returning values.
            machine.reg_write(UC_ARM_REG_PC, 0x101993DF)
            return True
        if address not in (0x10199C40, 0x10199CF0, 0x1045B654, 0x10455074, 0x1045580C):
            return False
        r0, r1, r2 = [machine.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        result = 0
        if address == 0x10199C40:
            machine.mem_write(r0, bytes(r1))
        elif address == 0x10199CF0:
            machine.mem_write(r0, bytes(machine.mem_read(r1, r2)))
        elif address == 0x10455074:
            result = 1
        elif address == 0x1045580C:
            self.requests.append(dict(route=r0, payload=bytes(machine.mem_read(r2, 36)).hex()))
        machine.reg_write(UC_ARM_REG_R0, result)
        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
        return True

    def request(self, pattern, threshold, action):
        from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_PC)
        # Explicit handoff of the callback's queued property to native storage;
        # observer scheduling is not claimed to be emulated.
        self.u.mem_write(0xC2501928, struct.pack('<I', pattern))
        self.u.mem_write(0xC2534734, struct.pack('<I', threshold))
        self.u.mem_write(0xC1821E60, bytes(4))
        self.u.reg_write(UC_ARM_REG_SP, 0x2000F000)
        self.u.reg_write(UC_ARM_REG_LR, 0x2000FF01)
        self.u.reg_write(UC_ARM_REG_R0, action)
        self.requests.clear()
        self.building = True
        try:
            self.u.emu_start(0x1044F399, 0x2000FF00, count=20000)
        finally:
            self.building = False
        if self.u.reg_read(UC_ARM_REG_PC) != 0x2000FF00 or self.u.reg_read(UC_ARM_REG_SP) != 0x2000F000:
            raise AssertionError('Request builder did not return normally')
        return list(self.requests)

    def run(self, fields, availability=False):
        from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR,
                                       UC_ARM_REG_PC, UC_ARM_REG_R0,
                                       UC_ARM_REG_R1, UC_ARM_REG_R3)
        u = self.u
        self.notifications.clear()
        self.gate_inputs = None
        u.mem_write(0x20000000, struct.pack('<34I', *fields))
        u.mem_write(0xC1800000 + self.availability_offset, b'\0')
        u.mem_write(0x20001000, struct.pack('<4I', 8, 0, 0x20002000, 0x20003000))
        u.mem_write(0x20001044, struct.pack('<3I', 8, 0, 0x20004000))
        u.mem_write(0x2000115C, struct.pack('<I', 0x20001500))
        u.mem_write(0x20001500, struct.pack('<2I', 1, 0x20001600))
        u.mem_write(0x20001600, struct.pack('<2I', 0x189A, 0))
        u.mem_write(0x20002000, bytes(64))
        u.mem_write(0x20003000, bytes(64))
        for reg, value in ((UC_ARM_REG_SP, 0x2000F000), (UC_ARM_REG_LR, 0x2000FF01),
                           (UC_ARM_REG_R0, 0x114E8F8C), (UC_ARM_REG_R1, 0x20000000),
                           (UC_ARM_REG_R3, 0x20001000)):
            u.reg_write(reg, value)
        u.emu_start(0x1017784B if availability else 0x101776FF, 0x2000FF00, count=20000)
        if u.reg_read(UC_ARM_REG_PC) != 0x2000FF00 or u.reg_read(UC_ARM_REG_SP) != 0x2000F000:
            raise AssertionError('Callback did not return with balanced stack')
        if u.reg_read(UC_ARM_REG_R0) != 1:
            raise AssertionError('Native callback reported failure')
        count, = struct.unpack('<I', u.mem_read(0x20001004, 4))
        if availability:
            # Native availability callback writes the shared control byte and
            # appends a record using native 0x1017ddf0, rather than a property.
            count, = struct.unpack('<I', u.mem_read(0x20001048, 4))
            return dict(unavailable=u.mem_read(0xC1800000 + self.availability_offset, 1)[0],
                        queue_count=count, gate=self.gate_inputs)
        keys = struct.unpack('<'+'I'*count, u.mem_read(0x20002000, count*4))
        values = struct.unpack('<'+'I'*count, u.mem_read(0x20003000, count*4))
        if self.notifications != list(keys):
            raise AssertionError('Native property queue notification mismatch')
        dirty, = struct.unpack('<I', u.mem_read(0x20001604, 4))
        if dirty != int(bool(count)):
            raise AssertionError('Native dependency dirty flag mismatch')
        return dict(properties=list(zip(keys, values)), gate=self.gate_inputs)

    @property
    def availability_offset(self):
        # Literal used by 0x10177950; native code writes base + 0x7b.
        literal, = struct.unpack('<I', self.u.mem_read(0x10177D34, 4))
        return literal + 0x7B - 0xC1800000


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('../Z_30_0120.bin'))
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--preset', nargs='+', default=['photo-zebra'],
                   help='Presets to verify, e.g. full or photo-zebra; selection must resolve to photo-zebra')
    args = p.parse_args()
    raw = args.source.read_bytes()
    stock = StockImage(raw if len(raw) == 33939514 else extract_eg(raw))
    _, features = resolve_presets(args.preset)
    if 'photo-zebra' not in features:
        p.error('Selected presets must include photo-zebra')
    candidate, metadata = build(stock.data, args.preset)
    baseline = bytearray(candidate)
    for address, expected, _ in GATES:
        baseline[address-BASE:address-BASE+len(expected)] = expected
    if len(candidate) < len(stock.data) or binascii.crc_hqx(candidate, 0):
        raise AssertionError('Candidate size/CRC mismatch')
    if 'photo-zebra' not in resolve_presets('full')[1]:
        raise AssertionError('Photo zebra missing from full')
    original, patched = Context(bytes(baseline)), Context(candidate)
    control = metadata['features']['photo_zebra_control']
    def switch(value):
        from z30_extensions.features.menu.layout import STATE_ANCHOR, MAGIC
        state = 0xC1840000
        patched.u.mem_write(STATE_ANCHOR+16, struct.pack('<I',state))
        patched.u.mem_write(state+56, struct.pack('<I',MAGIC))
        patched.u.mem_write(state+control['state_offset'], struct.pack('<I',value))
    switch(1)
    count = 0
    photo_enabled = 0
    video_unchanged = 0
    examples = []
    # Peaking, scene restrictions, blocked state bits, movie selection,
    # dirty-flag skip behavior, current effective setting and suppression.
    cases = itertools.product(range(3), (0, 3), (0, 29, 30, 31, 32, 33),
                              (0, 1, 0x20, 0x80, 0x200, 0x2000, 0x4000, 0x10000),
                              range(2), range(2), range(3), range(2))
    for pattern, peaking, scene, flags, movie, dirty, current, suppress in cases:
        fields = [0]*34
        fields[0:7] = [dirty, pattern, current, movie, peaking, scene, flags]
        fields[16] = movie
        fields[27] = suppress
        a = original.run(fields)
        av_a = original.run(fields, True)
        switch(0)
        off, off_av = patched.run(fields), patched.run(fields, True)
        if {k:v for k,v in a.items() if k!='gate'} != {k:v for k,v in off.items() if k!='gate'} or {k:v for k,v in av_a.items() if k!='gate'} != {k:v for k,v in off_av.items() if k!='gate'}:
            raise AssertionError('Photo switch OFF differs from stock')
        switch(1)
        b = patched.run(fields)
        for result in (a, b):
            if any(key != 0x189A for key, _ in result['properties']):
                raise AssertionError('Unexpected derived property')
        av_b = patched.run(fields, True)
        # Gate capture inside displaced windows is present only in stock.
        a_gate = a.pop('gate')
        b.pop('gate')
        av_a.pop('gate')
        av_b.pop('gate')
        if movie:
            if a != b or av_a != av_b:
                raise AssertionError('Video behavior changed')
            video_unchanged += 1
        elif a != b or av_a != av_b:
            gate = a_gate
            if not (gate['movie'] == 0 and gate['peaking'] == 3 and gate['scene'] == 0
                    and not flags & (0x2000 | 0x4000 | 0x10000) and not suppress):
                raise AssertionError('Candidate bypassed a non-mode restriction')
        value = b['properties'][0][1] if b['properties'] else current
        if not movie and value:
            photo_enabled += 1
            if len(examples) < 4 and (scene, dirty, current) == (29, 0, 0):
                examples.append(dict(pattern=pattern, peaking=peaking, scene=scene,
                                     flags=flags, suppress=suppress, stock=a, candidate=b,
                                     availability=av_b))
        count += 1
    THRESHOLDS = (255, 248, 235, 224, 213, 202, 191, 180)
    handoffs = []
    for pattern, threshold, action in itertools.product(range(3), range(8), range(2)):
        fields = [0]*34
        fields[1], fields[4], fields[5] = pattern, 3, 29
        derived = patched.run(fields)['properties']
        if derived != [(0x189A, pattern)]:
            raise AssertionError('Photo pattern derivation failed')
        requests = patched.request(derived[0][1], threshold, action)
        if [r['route'] for r in requests] != [1, 4]:
            raise AssertionError('Unexpected native builder outputs')
        for request in requests:
            payload = bytes.fromhex(request['payload'])
            enabled, = struct.unpack_from('<I', payload)
            value, = struct.unpack_from('<H', payload, 8)
            if enabled != int(bool(pattern and action)) or value != THRESHOLDS[threshold]:
                raise AssertionError('Native property handoff/request mismatch')
        handoffs.append(dict(pattern=pattern, threshold=threshold, action=action, requests=requests))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = dict(stock_eg_sha256=sha256(stock.data), baseline_sha256=sha256(bytes(baseline)), baseline_kind='Candidate with both original mode windows restored',
                  selected_presets=args.preset, candidate_sha256=sha256(candidate),
                  cases=count, native_callback_executions=count*6, switch_off_comparisons=count,
                  video_comparisons_passed=video_unchanged, photo_nonzero_results=photo_enabled,
                  examples=examples, build=metadata, camera_verified=False,
                  native_property_handoff_cases=handoffs,
                  assumptions=['34-property dependency snapshots are synthetic.',
                               'The dependency index contains one synthetic 0x189a entry; native notification sets its dirty flag.',
                               'Queued 0x189a is explicitly transferred into native property RAM.',
                               'Request builder uses native descriptor/getter code with readiness validation bypassed.',
                               'Request builder output queries, memory helpers and submission are modeled.',
                               'No sensor, GPU, display-service consumer or capture file is executed.'])
    (args.output_dir / 'verification.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (args.output_dir / 'candidate.EG.BIN').write_bytes(candidate)
    print(json.dumps({k: report[k] for k in ('cases', 'native_callback_executions',
                                           'video_comparisons_passed', 'photo_nonzero_results',
                                           'candidate_sha256', 'camera_verified')}, indent=2))


if __name__ == '__main__':
    main()
