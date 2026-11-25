"""Native automatic-orientation checks on an existing Z30 QEMU Linux/WSL build.

Uses synthetic mux objects and native pose RAM. The BIOS stock guard remains
active; checked edits, appended wrappers and CRC are overlaid through the paused
debugger. Executes native configuration and full video tkhd construction.
"""
import argparse
import binascii
import importlib.util
import json
import socket
import struct
import subprocess
import sys
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.build import build
from z30_extensions.platform.z30_120 import BASE, SCATTER, sha256
from z30_extensions.features.video.rotation import CONFIGURE, ORIENTATION_ROOT, ORIENTATION_STATE, VIDEO_MATRIX_OFFSET, POSE_MATRICES, EXPECTED_MATRIX
from video_rot_fixture import parameters, object_bytes, OBJECT_SIZE, VIDEO_CONSTRUCTOR

STACK, OBJECT, PARAM, RETURN = 0xC7A08000, 0xC7A10000, 0xC7A40000, BASE + 0xAC
MMU_CODE, PAGE_TABLE = 0xC7A60000, 0xC7A80000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(remote, address, data):
    for offset in range(0, len(data), 1024):
        part = data[offset:offset + 1024]
        require(remote.command(f'M{address + offset:x},{len(part):x}:' + part.hex()) == 'OK',
                f'Cannot write diagnostic memory at {address + offset:#x}')


def run_case(root, stock, image, report, patched, log_path):
    launcher = load_module('z30_qemu_run', root / 'tools/run.py')
    Remote = load_module('z30_qemu_rsp', root / 'tools/rsp.py').Remote
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = launcher.command(root / '.build/build/qemu-system-arm', stock, 'assisted', 1, port, True)
    remote = None
    with log_path.open('w') as log:
        proc = subprocess.Popen(args, stdout=log, stderr=log)
        try:
            remote = Remote(port)
            remote.command('qSupported:multiprocess+;qXfer:features:read+')
            remote.command('qXfer:features:read:target.xml:0,fff')
            require(remote.command('Hg1') == 'OK', 'Cannot select CPU')
            saved = bytes.fromhex(remote.command('g'))
            require(len(saved) == 68, 'Expected negotiated ARM registers')
            overlays = []
            if patched:
                for edit in report['edits']:
                    offset = int(edit['offset'], 16)
                    expected = bytes.fromhex(edit['expected_hex'])
                    replacement = bytes.fromhex(edit['replacement_hex'])
                    require(remote.memory(BASE + offset, len(expected)) == expected, 'Guest expected bytes differ')
                    write(remote, BASE + offset, replacement)
                    require(remote.memory(BASE + offset, len(replacement)) == replacement, 'Hook readback differs')
                    overlays.append({'address': hex(BASE + offset), 'length': len(replacement)})
                append_start = len(stock.read_bytes()) - 2
                tail = image[append_start:]
                write(remote, BASE + append_start, tail)
                require(remote.memory(BASE + append_start, len(tail)) == tail, 'Tail/CRC readback differs')
                overlays.append({'address': hex(BASE + append_start), 'length': len(tail)})
            # Reset scatter RAM explicitly; this is diagnostic context, not boot.
            source, destination, length, _ = SCATTER
            write(remote, destination, image[source - BASE:source - BASE + length])
            write(remote, PARAM, parameters())
            require(remote.command(f'Z1,{RETURN:x},4') == 'OK', 'Cannot set return breakpoint')

            def pose(value, available=True):
                write(remote, ORIENTATION_ROOT + 0xC, struct.pack('<I', 1 if available else 0))
                write(remote, ORIENTATION_STATE + 0x90, bytes([value]))
                write(remote, ORIENTATION_STATE + 0x238, struct.pack('<I', 5))

            def call(address, arguments, *, thumb=True):
                registers = bytearray(saved)
                for index in range(4, 12):
                    struct.pack_into('<I', registers, index * 4, 0x11220000 + index)
                for index, value in enumerate(arguments):
                    struct.pack_into('<I', registers, index * 4, value)
                struct.pack_into('<I', registers, 13 * 4, STACK)
                struct.pack_into('<I', registers, 14 * 4, RETURN | 1)
                struct.pack_into('<I', registers, 15 * 4, address)
                cpsr = struct.unpack_from('<I', saved, 64)[0]
                struct.pack_into('<I', registers, 64,
                                 (cpsr | 0x20 | 0xC0) if thumb else ((cpsr | 0xC0) & ~0x20))
                require(remote.command('G' + registers.hex()) == 'OK', 'Cannot supply call context')
                remote.sock.settimeout(5)
                remote.send('c')
                packet = remote.receive()
                if 'thread:' in packet:
                    thread = packet.split('thread:')[1].split(';')[0]
                    require(remote.command('Hg' + thread) == 'OK', 'Cannot select stopped CPU')
                state = remote.registers()
                require(int(state['r15'], 16) == RETURN and int(state['r13'], 16) == STACK,
                        f'Call did not return cleanly: {state}')
                require(all(int(state[f'r{i}'], 16) == 0x11220000 + i for i in range(4, 12)),
                        'Preserved registers changed')
                return int(state['r0'], 16)

            # With MMU off, reset memory attributes can reject the native
            # unaligned ARM word copies. Supply an explicit diagnostic normal
            # non-cacheable identity mapping, rather than bypassing the copy.
            table = b''.join(struct.pack('<I', (n << 20) | 0x10C02) for n in range(4096))
            write(remote, PAGE_TABLE, table)
            code, _ = Ks(KS_ARCH_ARM, KS_MODE_ARM).asm(f'''
                movw r0,#{PAGE_TABLE & 65535}; movt r0,#{PAGE_TABLE >> 16};
                mcr p15,0,r0,c2,c0,0;
                mov r0,#0; mcr p15,0,r0,c2,c0,2;
                mvn r0,#0; mcr p15,0,r0,c3,c0,0;
                dsb; mrc p15,0,r0,c1,c0,0;
                bic r0,r0,#2; orr r0,r0,#1;
                mcr p15,0,r0,c1,c0,0; isb; bx lr;
            ''', as_bytes=True)
            write(remote, MMU_CODE, code)
            call(MMU_CODE, (), thumb=False)

            cases = []
            for index, native_pose in enumerate([0, 1, 2, 3, 4, 255]):
                obj = OBJECT + index * 0x3000
                write(remote, obj, bytes(0x2000))
                write(remote, obj, object_bytes(obj))
                pose(native_pose)
                require(call(CONFIGURE, (obj, PARAM)) == 0, 'Native configuration failed')
                expected = POSE_MATRICES[native_pose] if patched and native_pose < 4 else EXPECTED_MATRIX
                if patched:
                    require(remote.memory(obj + VIDEO_MATRIX_OFFSET, 36) == expected, 'Snapshot differs')
                pose((native_pose + 1) % 4)
                require(call(VIDEO_CONSTRUCTOR, (obj,)) == 0, 'Native video tkhd build failed')
                header = remote.memory(obj, OBJECT_SIZE)
                require(header[VIDEO_MATRIX_OFFSET:VIDEO_MATRIX_OFFSET + 36] == expected, 'Final tkhd matrix differs')
                pose((native_pose + 2) % 4)
                require(call(VIDEO_CONSTRUCTOR, (obj,)) == 0, 'Repeated finalization failed')
                require(remote.memory(obj + VIDEO_MATRIX_OFFSET, 36) == expected, 'Orientation was resampled')
                cases.append({'pose': native_pose, 'matrix_hex': expected.hex(), 'header_hex': header.hex(),
                              'locked_after_pose_change': True, 'repeat_finalization': True})
            pose(2, available=False)
            write(remote, OBJECT + 0x18000, bytes(0x2000))
            write(remote, OBJECT + 0x18000, object_bytes(OBJECT + 0x18000))
            require(call(CONFIGURE, (OBJECT + 0x18000, PARAM)) == 0, 'Unavailable configuration failed')
            if patched:
                require(remote.memory(OBJECT + 0x18000 + VIDEO_MATRIX_OFFSET, 36) == EXPECTED_MATRIX,
                        'Unavailable pose must fall back to landscape')
            require(call(CONFIGURE, (0, PARAM)) == 3, 'Null object failure changed')
            require(call(CONFIGURE, (OBJECT, 0)) == 3, 'Null parameters failure changed')
            require(call(VIDEO_CONSTRUCTOR, (0,)) == 3, 'Null video header failure changed')
            before = remote.memory(OBJECT + VIDEO_MATRIX_OFFSET, 36)
            write(remote, PARAM + 0x19, b'\x01')
            require(call(CONFIGURE, (OBJECT, PARAM)) == 3, 'Native validation failure changed')
            require(remote.memory(OBJECT + VIDEO_MATRIX_OFFSET, 36) == before, 'Failed configuration latched a pose')
            if patched:
                require(remote.memory(OBJECT + 0x3000 + VIDEO_MATRIX_OFFSET, 36) == POSE_MATRICES[1],
                        'Another recording overwrote an existing snapshot')
            return {'image': 'video-rot' if patched else 'stock', 'passed': True, 'cases': cases,
                    'unavailable_and_failure_checks': True, 'per_object_isolation': patched,
                    'command': args, 'log': str(log_path), 'diagnostic_rom_writes': overlays,
                    'mmu_fixture': 'Normal non-cacheable identity sections; synthetic page table and CP15 setup.'}
        finally:
            if remote:
                remote.sock.close()
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-root', type=Path, required=True)
    parser.add_argument('--stock-eg', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    stock, candidate = args.stock_eg.resolve(), args.candidate.resolve()
    source = stock.read_bytes()
    expected, build_report = build(source, 'video-rot')
    actual = candidate.read_bytes()
    require(actual == expected, 'Candidate must exactly match standalone video-rot build')
    require(binascii.crc_hqx(actual, 0) == 0, 'Candidate CRC mismatch')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = [run_case(args.qemu_root.resolve(), stock, source, build_report, False, out / 'stock.log'),
             run_case(args.qemu_root.resolve(), stock, actual, build_report, True, out / 'video-rot.log')]
    for baseline, patched in zip(cases[0]['cases'], cases[1]['cases']):
        old, new = bytes.fromhex(baseline['header_hex']), bytes.fromhex(patched['header_hex'])
        require(old[:VIDEO_MATRIX_OFFSET] == new[:VIDEO_MATRIX_OFFSET] and
                old[VIDEO_MATRIX_OFFSET + 36:] == new[VIDEO_MATRIX_OFFSET + 36:],
                'Native video header changed outside its matrix')
        baseline.pop('header_hex')
        patched.pop('header_hex')
    result = {'passed': True, 'candidate_sha256': sha256(actual), 'crc_residue': 0,
              'cases': cases, 'camera_verified': False, 'pose_direction_device_verified': False,
              'scope': 'Native configure, orientation getter, memcpy and complete video tkhd; supplied pose RAM and mux objects.',
              'limitations': 'Debugger ROM overlay and synthetic RAM; no natural boot, sensor or physical recording.'}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
