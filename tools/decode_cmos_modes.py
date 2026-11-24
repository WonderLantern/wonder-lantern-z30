"""Decode the CMOS register packets of every drive command and drive type.

Runs the stock selector 0x10139F44(command, request) in Unicorn over drive data
saved from a camera (see read_cmos_dump.py) and intercepts the stock sequence
writer 0x101401F6, so the packet choice is the firmware's own. Also tabulates
the stock upper-mode mapper 0x1013D7DE. Output stays local: it is derived from
private per-body data.

Usage:
  python tools/decode_cmos_modes.py --dump-dir build/research/cmos-ab --output build/research/cmos-modes.json
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.container import extract_eg
from z30_extensions.features import cmos_dump as cd
from native_runtime import Context, STACK
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC, UC_ARM_REG_LR

SELECTOR = 0x10139F44     # (command, request) -> status; 1 = no record for this type
SEQ_WRITER = 0x101401F6   # (packets, u16 length list ending in 0)
MAPPER = 0x1013D7DE       # (p0, p1, p2, 0, [sp]=p[0x27], [sp+4]=flag) -> drive type 0..12
COMMANDS = range(0x1D)
TYPES = range(13)
REQUEST = 0x50020000
WRITE_CHIP = 0x81


def decode(packet):
    if len(packet) < 4 or packet[0] != WRITE_CHIP:
        return dict(raw=packet.hex())
    return dict(register=f'{packet[1] << 8 | packet[2]:04x}', data=packet[3:].hex())


class Emulator:
    def __init__(self, stock, blob, context, pointer):
        self.cx = Context(stock, stock)
        self.u = self.cx.u
        self.cx.map(pointer, len(blob))
        self.u.mem_write(pointer, blob)
        self.u.mem_write(cd.CTX, context)
        self.packets = []
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED, self.unmapped)
        self.u.hook_add(UC_HOOK_CODE, self.writer, begin=SEQ_WRITER, end=SEQ_WRITER)

    def unmapped(self, u, access, address, size, value, data):
        if 0x80000000 <= address < 0xE0000000:
            self.cx.map(address & ~0xFFF, 0x1000)
            return True
        return False

    def writer(self, u, address, size, data):
        source, lengths = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)
        for index in range(4096):
            length = struct.unpack('<H', u.mem_read(lengths + 2 * index, 2))[0]
            if not length:
                break
            self.packets.append(bytes(u.mem_read(source, length)))
            source += length
        u.reg_write(UC_ARM_REG_R0, 0)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def select(self, command, drive_type):
        request = bytearray(0x100)
        request[0] = drive_type
        self.cx.map(REQUEST, 0x1000)
        self.u.mem_write(REQUEST, bytes(request))
        self.packets = []
        status = self.cx.call(SELECTOR, (command, REQUEST), limit=2_000_000)
        return status, list(self.packets)

    def mapper(self, p0, p1, p2, rate, flag):
        self.u.mem_write(STACK + 0xF0000, struct.pack('<2I', rate, flag))
        return self.cx.call(MAPPER, (p0, p1, p2, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--dump-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    header = json.loads((args.dump_dir / 'header.json').read_text(encoding='utf-8'))
    if not header.get('sum_matches'):
        raise SystemExit('Dump failed its checksum; save a v2 dump first')
    emulator = Emulator(extract_eg(args.source.read_bytes()), (args.dump_dir / 'drive-data.bin').read_bytes(),
                        (args.dump_dir / 'context.bin').read_bytes(), int(header['pointer'], 16))
    commands = {}
    for command in COMMANDS:
        groups = {}
        for drive_type in TYPES:
            status, packets = emulator.select(command, drive_type)
            if status:
                continue
            groups.setdefault(tuple(packets), []).append(drive_type)
        commands[command] = [dict(types=types, packets=[decode(p) for p in packets])
                             for packets, types in groups.items()]
    mapping = {}
    for p0 in range(8):
        for p1 in range(4):
            for p2 in range(4):
                for rate in range(8):
                    for flag in range(2):
                        code = emulator.mapper(p0, p1, p2, rate, flag)
                        mapping.setdefault(code, []).append([p0, p1, p2, rate, flag])
    report = dict(dump_sha256=header['blob_sha256'], commands=commands,
                  mapper={str(k): v for k, v in sorted(mapping.items())},
                  mapper_inputs='p0 = message+0x18 via 0x100F0EA4, p1/p2 = message+0x19/+0x1A; '
                                'at the request fill 0x10114364 the first stack argument ("rate") is 0 '
                                'and the second ("flag") is p[0x27]',
                  special_types='types 2 and 3 are set directly when p[0x30] != 0 (p[0x34] 1 -> 2, 0 -> 3)',
                  limitations=['Mapping of p0/p1/p2 to camera menu modes is not yet established.',
                               'Request fields other than byte 0 are zero; commands that read them '
                               '(gain, exposure) show placeholder values.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1), encoding='utf-8')
    for command, groups in commands.items():
        print(f'cmd {command:2}: ' + ' | '.join(f"types {g['types']}: {len(g['packets'])} pkts" for g in groups))


if __name__ == '__main__':
    main()
