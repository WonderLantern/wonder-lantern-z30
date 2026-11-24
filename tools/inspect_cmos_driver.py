"""Locate the Z30 CMOS serial driver and its drive-mode data source; no camera access.

Corrects the earlier "readout lives in the EX MCU" reading. Every fact below is
asserted against the pinned stock container, so a firmware change fails loudly
instead of printing a stale conclusion.

Usage:
  python tools/inspect_cmos_driver.py --source Z_30_0120.bin --output build/research/cmos-driver.json
"""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.container import CONTAINER_SIZE, CONTAINER_SHA256, extract_eg
from z30_extensions.platform.z30_120 import BASE, sha256
from inspect_sensor_readout import split_modules

# EX: sub-controller RTOS objects (battery, lens, RTC, shutter, switches, power).
EX_EXPECTED = ('BatCmCy1','LnMngSem', 'RtcMgMtx', 'ShterAlm', 'SwichEvF', 'PowMgCyc')
EX_IMAGING = re.compile(rb'(?i)sensor|cmos|readout|sif_|capture')
# SoC interrupt-name table: 32-byte records; SIF = sensor interface.
IRQ_NAMES = ('CA7_CPU0_COM_RX', 'A926_COM_RX', 'SIF_0', 'SIF_7', 'PPC_0', 'RAW', 'RAWRS', 'H264', 'SIO_5_TX')
# CMOS serial driver on SIO channel 5.
CMOS_OPEN = 0x1019D7D6          # opens SIO channel, 5 MHz or 20 MHz
CMOS_WRITE = 0x1019D868         # chunked write, <=0xC0 bytes per transfer
CMOS_READ = 0x1019D91E          # read: 0x82, addr_hi, addr_lo
SEQ_WRITER = 0x101401F6         # writes concatenated packets from a u16 length list
CTX = 0xC18E4FEC                # driver context; +0x14 = drive data blob
SET_BLOB = 0x10139E84           # stores r0 into CTX+0x14
SET_BLOB_EXPORT = 0x101139A0    # b.w SET_BLOB
BLOB_INIT = 0x100F0DC8          # movs r0,#0x4c; bl region_addr; bl SET_BLOB_EXPORT
MODE_INDEX = 0x11C57A84         # [command*14 + drive_mode] -> blob record index
# Data-area and NOR-bank descriptors (initialized scatter data).
SCATTER_SRC, SCATTER_DST = 0x11F81C94, 0xC1800000
AREA_TABLE, AREA_STRIDE = 0xC1880D2C, 28
BANK_TABLE, BANK_STRIDE = 0xC1880888, 44
# Audio codec on another SIO channel: ruled out as the sensor.
AUDIO_DEFAULTS, AUDIO_COUNT = 0x10690518, 0x109B


def u32(eg, address):
    return struct.unpack_from('<I', eg, address - BASE)[0]


def init_data(eg, ram, size):
    offset = SCATTER_SRC + (ram - SCATTER_DST) - BASE
    return eg[offset:offset + size]


def thumb_bl_targets(eg, start, end):
    out = []
    for address in range(start, end, 2):
        h1, h2 = struct.unpack_from('<HH', eg, address - BASE)
        if (h1 & 0xF800) != 0xF000 or (h2 & 0xD000) not in (0xD000, 0x9000):
            continue
        s = (h1 >> 10) & 1
        i1 = 1 - (((h2 >> 13) & 1) ^ s)
        i2 = 1 - (((h2 >> 11) & 1) ^ s)
        offset = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
        if s:
            offset -= 1 << 25
        out.append((address, address + 4 + offset))
    return out


def inspect(container):
    assert len(container) == CONTAINER_SIZE and sha256(container) == CONTAINER_SHA256
    eg = extract_eg(container)
    ex = split_modules(container)['ex2090_012000.bi']

    ex_names = sorted({m.decode() for m in re.findall(rb'[A-Z][A-Za-z]{4}[A-Za-z0-9]{2,3}(?=\0)', ex)})
    assert all(name in ex_names for name in EX_EXPECTED), ex_names
    assert b'Z 30 Firmware Ex' in ex
    assert not EX_IMAGING.search(ex), 'EX now mentions imaging'

    irq = {}
    for name in IRQ_NAMES:
        hits = [m.start() for m in re.finditer(b'\0' + name.encode() + b'\0', eg)]
        assert hits, name
        irq[name] = hex(hits[0] + 1 + BASE)

    # CMOS read builds 0x82, addr_hi, addr_lo; both rates are literal-pool words.
    read_body = eg[CMOS_READ - BASE:CMOS_READ - BASE + 0x50]
    assert b'\x82\x20' in read_body          # movs r0,#0x82
    open_body = eg[CMOS_OPEN - BASE:CMOS_OPEN - BASE + 0x200]
    assert struct.pack('<I', 5_000_000) in open_body and struct.pack('<I', 20_000_000) in open_body
    calls = thumb_bl_targets(eg, 0x10139000, 0x10141000)
    assert any(t == CMOS_WRITE for _, t in calls), 'sequence writer no longer calls CMOS write'
    assert sum(1 for _, t in calls if t == SEQ_WRITER) >= 3

    # Blob pointer: region 0x4C -> CTX+0x14 (SET_BLOB is `ldr r1,=CTX; str r0,[r1,#0x14]`).
    assert eg[SET_BLOB + 2 - BASE:SET_BLOB + 4 - BASE] == b'\x48\x61'  # str r0,[r1,#0x14]
    tail = thumb_bl_targets(eg, SET_BLOB_EXPORT, SET_BLOB_EXPORT + 4)
    assert tail and tail[0][1] == SET_BLOB, tail
    init = eg[BLOB_INIT - BASE:BLOB_INIT - BASE + 2]
    assert init == b'\x4c\x20', init.hex()  # movs r0,#0x4c
    init_calls = [t for _, t in thumb_bl_targets(eg, BLOB_INIT, BLOB_INIT + 0x10)]
    assert SET_BLOB_EXPORT in init_calls, [hex(t) for t in init_calls]

    area = struct.unpack('<7I', init_data(eg, AREA_TABLE + 0x4C * AREA_STRIDE, AREA_STRIDE))
    assert area[0] == 0x4C and area[2] == 0 and area[3] == 0x168000, area
    bank_index = area[5]
    bank = struct.unpack('<11I', init_data(eg, BANK_TABLE + bank_index * BANK_STRIDE, BANK_STRIDE))
    eg_bank = struct.unpack('<11I', init_data(eg, BANK_TABLE + 2 * BANK_STRIDE, BANK_STRIDE))
    eg_end = eg_bank[0] + eg_bank[2]
    assert eg_bank[0] == BASE and BASE + len(eg) <= eg_end
    assert bank[0] >= eg_end, 'drive data bank now overlaps the updatable EG bank'

    rows = eg[MODE_INDEX - BASE:MODE_INDEX - BASE + 0x80]
    drive_modes = max(rows[0x38:0x46]) + 1
    assert drive_modes == 14, rows.hex()

    audio = [struct.unpack_from('<HH', eg, AUDIO_DEFAULTS - BASE + 4 * i) for i in range(AUDIO_COUNT)]
    assert audio[0] == (5, 8) and max(a for a, _ in audio) < 0x6000

    return dict(
        source_sha256=CONTAINER_SHA256,
        ex_module=dict(role='sub-controller: battery, lens, RTC, shutter, switches, power',
                       rtos_objects=ex_names, imaging_strings=0),
        soc_interrupt_names=irq,
        cmos_serial_driver=dict(sio_channel=5, open=hex(CMOS_OPEN), write=hex(CMOS_WRITE),
                                read=hex(CMOS_READ), read_prefix='0x82 addr_hi addr_lo',
                                spi_hz=[5_000_000, 20_000_000], sequence_writer=hex(SEQ_WRITER),
                                context=hex(CTX)),
        drive_data=dict(area_id=0x4C, area_size=hex(area[3]), nor_bank=bank_index,
                        nor_address=hex(bank[0]), nor_bank_size=hex(bank[2]),
                        payload_size=hex(bank[3]), eg_bank_end=hex(eg_end),
                        in_update_package=False, drive_modes=drive_modes,
                        mode_index_table=hex(MODE_INDEX)),
        ruled_out=dict(audio_codec_defaults=hex(AUDIO_DEFAULTS), audio_registers=AUDIO_COUNT,
                       arm926_image='wireless/Bluetooth/SD co-processor at 0xC1000000'),
        limitations=['No camera access: drive data contents, sensor model and register '
                     'semantics are unknown.',
                     'Drive-mode to movie-mode mapping is not yet recovered.'],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', default='Z_30_0120.bin')
    parser.add_argument('--output', default='build/research/cmos-driver.json')
    args = parser.parse_args()
    report = inspect(Path(args.source).read_bytes())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report['drive_data'], indent=2))


if __name__ == '__main__':
    main()
