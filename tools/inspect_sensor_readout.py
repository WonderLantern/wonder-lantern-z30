"""Locate the Z30 capture/sensor readout layer in the stock firmware; no camera access.

The Z30 firmware ships as three modules. This tool maps each module's role,
recovers the movie mode table and its selector, and reports where sensor readout
control lives (and where it does not). It is a navigation aid for further
reverse engineering, not a capability proof.

Usage:
  python tools/inspect_sensor_readout.py --source Z_30_0120.bin --output build/research/sensor-readout.json
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import CONTAINER_SIZE, CONTAINER_SHA256, extract_eg
from z30_extensions.platform.z30_120 import BASE, sha256

MODULE_HEADER = 144
EG_NAME = b'eg2090_0120a0.bi'
MODE_TABLE = 0x10713154
RECORD = 0x1C
RECORD_COUNT = 30
SELECTOR = 0x1035566C
SERVICE_TABLE = 0x12069BE0
SERVICE_STRIDE = 0x18
SERVICE_COUNT = 156
# Names are truncated to 16 bytes in the registry. Only CaptureSensorSer is an
# imaging sensor service; EyeSensor* entries are the viewfinder proximity sensor.
SENSOR_SERVICES = ('CaptureSensorSer',)
EYE_SENSOR_SERVICES = ('EyeSensorControl', 'EyeSensorDeviceS', 'EyeSensorService')
# EX module: Cortex-M vector table, STM32-family peripheral window.
EX_FLASH = 0x08000000
EX_RAM = 0x20000000
ST_STM32_RCC = (0x40023800, 0x40023900)
ST_STM32_APB = (0x40000000, 0x40030000)

FPS_CODES = {0: '59.94', 1: '50', 2: '29.97', 3: '25', 4: '23.976',
             5: '119.88', 6: '100', 7: 'slow x4 29.97', 8: 'slow x4 25', 9: 'slow x5 23.976'}
RES_CODES = {0: '4K', 1: '1080', 2: '720'}


def split_modules(container):
    """Recover the three decoded modules without relying on the EG extractor's return value."""
    from z30_extensions.container import decode_body
    body = decode_body(container[32:])
    modules = {}
    for index in range(3):
        raw_name, start, size, _, _ = struct.unpack_from('>16s4I', body, 48 + index * 32)
        modules[raw_name.rstrip(b'\0').decode()] = body[start:start + size]
    return modules


def read_mode_table(eg):
    rows = []
    for index in range(RECORD_COUNT):
        record = eg[MODE_TABLE - BASE + index * RECORD:][:RECORD]
        width, height, timebase, duration = struct.unpack_from('<2H2I', record, 0)
        gop1, gop_n = struct.unpack_from('<2H', record, 0x10)
        maximum, average = struct.unpack_from('<2I', record, 0x14)
        rows.append(dict(index=index, width=width, height=height, timescale=timebase,
                         frame_duration=duration, gop_interval=gop1, gop_length=gop_n,
                         maximum_bps=maximum, average_bps=average))
    return rows


def selector_map(eg):
    """Recover the frame-rate codes each resolution branch tests.

    The selector dispatches on the resolution byte (0=4K, 1=1080, 2=720) and each
    branch body compares the frame-rate byte against the codes it supports. The
    stock 4K branch tests only 29.97/25/23.976, so 59.94 and 50 fall through to
    the default record 6: that is the 4K 50p/60p gap.
    """
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    code = eg[SELECTOR - BASE:SELECTOR - BASE + 0xF0]
    instructions = list(md.disasm(code, SELECTOR))
    targets = {}
    previous = None
    for insn in instructions:
        mnemonic, ops = insn.mnemonic, insn.op_str
        # `cbz r4, #x` dispatches resolution 0; `cmp r4,#n` + `beq #x` dispatches n.
        if mnemonic == 'cbz' and ops.startswith('r4, #'):
            targets[0] = int(ops.split('#')[1], 16)
        elif mnemonic == 'cmp' and ops.startswith('r4, #') and previous is not None \
                and previous.mnemonic == 'cmp' and previous.op_str.startswith('r4, #'):
            pass
        elif mnemonic == 'beq' and previous is not None and previous.mnemonic == 'cmp' \
                and previous.op_str.startswith('r4, #'):
            targets[int(previous.op_str.split('#')[1])] = int(ops.split('#')[1], 16)
        previous = insn
    blocks = {}
    for resolution, start in targets.items():
        codes = set()
        for insn in instructions:
            if insn.address < start:
                continue
            if insn.mnemonic == 'cmp' and insn.op_str.startswith('r3, #'):
                codes.add(int(insn.op_str.split('#')[1]))
            elif insn.mnemonic == 'cbz' and insn.op_str.startswith('r3,'):
                codes.add(0)
            elif insn.mnemonic in ('strb', 'pop') or insn.mnemonic == 'b':
                break
        blocks[RES_CODES.get(resolution, str(resolution))] = sorted(codes)
    return blocks


def service_registry(eg):
    entries = []
    for index in range(SERVICE_COUNT):
        offset = SERVICE_TABLE - BASE + index * SERVICE_STRIDE
        name = eg[offset:offset + 16].split(b'\0')[0].decode('ascii', 'replace')
        identifier = struct.unpack_from('<H', eg, offset + 0x16)[0]
        entries.append(dict(name=name, id=identifier))
    return entries


def ex_module_role(ex):
    vector = struct.unpack_from('<2I', ex, 0)
    words = struct.unpack_from('<%dI' % (len(ex) // 4), ex)
    flash_ptrs = sum(1 for word in words if EX_FLASH <= word < EX_FLASH + 0x100000)
    ram_ptrs = sum(1 for word in words if EX_RAM <= word < EX_RAM + 0x100000)
    rcc = sum(1 for word in words if ST_STM32_RCC[0] <= word < ST_STM32_RCC[1])
    apb = sum(1 for word in words if ST_STM32_APB[0] <= word < ST_STM32_APB[1])
    return dict(initial_stack=hex(vector[0]), reset_vector=hex(vector[1]),
                flash_pointer_words=flash_ptrs, ram_pointer_words=ram_ptrs,
                stm32_rcc_words=rcc, stm32_apb_words=apb,
                interpretation='Cortex-M firmware for a separate STM32-family MCU'
                if rcc and vector[0] >> 24 == 0x20 else 'unrecognized')


def inspect(container):
    assert len(container) == CONTAINER_SIZE and sha256(container) == CONTAINER_SHA256
    eg = extract_eg(container)
    modules = split_modules(container)
    ex = modules['ex2090_012000.bi']
    registry = service_registry(eg)
    sensor = [entry for entry in registry if entry['name'] in SENSOR_SERVICES]
    assert len(sensor) == len(SENSOR_SERVICES), sensor
    eye = [entry for entry in registry if entry['name'] in EYE_SENSOR_SERVICES]
    assert len(eye) == len(EYE_SENSOR_SERVICES), eye

    # Every movie record is reachable from the selector; assert the 4K gap.
    branches = selector_map(eg)
    assert branches['4K'] == [2, 3, 4], branches
    assert set(branches['1080']) >= {0, 1, 2, 3, 4}, branches
    assert 0 in branches['720'] and 1 in branches['720'], branches

    rows = read_mode_table(eg)
    four_k = [row for row in rows if row['width'] == 3840]
    assert {row['frame_duration'] for row in four_k} <= {1000, 1001}
    assert len(four_k) == 6 and all(row['height'] == 2160 for row in four_k)

    role = ex_module_role(ex)
    assert role['stm32_rcc_words'] > 0, role

    return dict(stock_eg_sha256=sha256(eg), camera_access=False,
                modules={name: dict(size=len(data), sha256=sha256(data))
                         for name, data in modules.items()},
                ex_module=role,
                mode_table=dict(address=hex(MODE_TABLE), record_size=RECORD, count=RECORD_COUNT,
                                records=rows),
                selector=dict(address=hex(SELECTOR), frame_rate_codes_per_resolution=branches,
                              finding='4K branch tests only 29.97/25/23.976; '
                                      '59.94 and 50 have no 4K target record'),
                sensor_services=sensor,
                eye_sensor_services=eye,
                readout_control_location='Not in the EG main module. The EG carries the movie '
                                          'mode table and the protocol-service registry only; '
                                          'sensor readout timing/mode selection is executed by '
                                          'the separate Cortex-M MCU (ex2090_012000.bi).',
                limitations=['Static mapping only; no readout mode table was recovered.',
                             'Service ids are registry indices, not call addresses.',
                             'The EX module is not disassembled into a driver here.'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = inspect(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('modules:', {k: v['size'] for k, v in result['modules'].items()})
    print('EX module:', result['ex_module']['interpretation'])
    print('selector frame-rate codes:', result['selector']['frame_rate_codes_per_resolution'])
    print('sensor services:', [(s['name'], hex(s['id'])) for s in result['sensor_services']])
    print('readout control:', result['readout_control_location'])


if __name__ == '__main__':
    main()
