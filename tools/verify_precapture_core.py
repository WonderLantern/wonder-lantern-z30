"""Execute the experimental Thumb history core and stock writer offline.

No sensor, DMA, task scheduler, card filesystem or camera allocation is modeled.
Generated JPEGs and exposure timestamps are explicit synthetic inputs.
"""
import argparse
import hashlib
import io
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import StockImage, sha256
from z30_extensions.features.research.precapture_core import build_core, CONTEXT_SIZE, OK, BAD_STATE, BAD_FRAME, TOO_LARGE, STALE, EMPTY, BAD_ARGUMENT, WRITE_FAILED
from probe_precapture_writer import WriterFixture, RAM, PAYLOAD, COPY

CODE, CTX, REQUEST, OUTPUT = 0x51000000, RAM + 0x9000, RAM + 0x9100, RAM + 0x9200
ARENA, CAPACITY = 0x52000000, 0x100000


class CoreFixture:
    def __init__(self, stock):
        self.writer = WriterFixture(stock)
        self.context = self.writer.context
        self.u = self.context.u
        code, self.entries = build_core(CODE)
        self.context.map(CODE, len(code))
        self.u.mem_write(CODE, code)
        self.context.map(ARENA, CAPACITY * 3)
        self.context.map(PAYLOAD, CAPACITY + 1)
        self.check(self.call('init', ARENA, CAPACITY, 500), OK)

    @staticmethod
    def check(actual, expected):
        if actual != expected:
            raise AssertionError(f'Expected {expected!r}, got {actual!r}')

    def call(self, entry, *args):
        return self.context.call(self.entries[entry], (CTX, *args), limit=12000000)

    def words(self, address, *values):
        self.u.mem_write(address, struct.pack('<' + 'I' * len(values), *values))

    def state(self):
        return struct.unpack('<16I', self.u.mem_read(CTX, CONTEXT_SIZE))

    def offer(self, data, timestamp, pointer=PAYLOAD):
        self.context.map(pointer, len(data))
        self.u.mem_write(pointer, data)
        self.words(REQUEST, pointer, len(data), timestamp & 0xFFFFFFFF)
        return self.call('offer', REQUEST)

    def peek(self):
        status = self.call('peek', OUTPUT)
        if status != OK:
            return status, None
        pointer, length, timestamp = struct.unpack('<3I', self.u.mem_read(OUTPUT, 12))
        return status, (timestamp, bytes(self.u.mem_read(pointer, length)))


def make_jpeg(color, size=(320, 240)):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4),
                   fill='#eec94d')
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=92)
    return stream.getvalue()


def verify(stock, output):
    from PIL import Image
    f = CoreFixture(stock)
    cases = []

    def passed(name, **details):
        cases.append(dict(name=name, passed=True, **details))

    # Reject bad integer ranges/aliasing without modifying a valid context.
    before = bytes(f.u.mem_read(CTX, CONTEXT_SIZE))
    for arena, capacity, window in (
        (ARENA, 3, 500), (ARENA, 0x1000001, 500), (ARENA, CAPACITY, 0),
        (ARENA, CAPACITY, 60001), (CTX, CAPACITY, 500),
        (0xFFF00000, CAPACITY, 500), (ARENA + 1, CAPACITY, 500),
    ):
        f.check(f.call('init', arena, capacity, window), BAD_ARGUMENT)
        f.check(bytes(f.u.mem_read(CTX, CONTEXT_SIZE)), before)
    passed('invalid_initialization_is_atomic')
    jpeg = make_jpeg('#293b50')
    f.check(f.offer(jpeg, 0), BAD_STATE)
    f.check(f.call('arm'), OK)
    f.check(f.call('arm'), BAD_STATE)
    f.check(f.call('peek', OUTPUT), BAD_STATE)
    passed('idle_and_armed_state_guards')

    # Rejected frames must preserve previous history and byte contents.
    f.check(f.offer(jpeg, 100), OK)
    before = bytes(f.u.mem_read(CTX, CONTEXT_SIZE))
    for data, timestamp, expected in (
        (b'bad', 200, BAD_FRAME), (b'broken jpeg', 200, BAD_FRAME),
        (b'\xff\xd8xx\xff\xd9' + bytes(CAPACITY), 200, TOO_LARGE),
        (jpeg, 100, STALE), (jpeg, 99, STALE),
    ):
        f.check(f.offer(data, timestamp), expected)
        f.check(bytes(f.u.mem_read(CTX, CONTEXT_SIZE)), before)
    # Do not permit copies from the core's own slots.
    f.words(REQUEST, ARENA, len(jpeg), 200)
    f.check(f.call('offer', REQUEST), BAD_FRAME)
    f.check(bytes(f.u.mem_read(CTX, CONTEXT_SIZE)), before)
    f.words(REQUEST, 0xFFFFFFFE, 6, 200)
    f.check(f.call('offer', REQUEST), BAD_FRAME)
    f.check(bytes(f.u.mem_read(CTX, CONTEXT_SIZE)), before)
    passed('invalid_oversized_stale_alias_and_wrapping_frames_preserve_history')
    f.check(f.call('cancel'), OK)
    f.check(f.state()[1], 0)
    f.check(f.state()[6], 0)
    passed('half_release_clears_history')

    # A new frame uses the oldest slot only after the complete input is validated.
    f.check(f.call('arm'), OK)
    history = []
    for index, timestamp in enumerate((0, 125, 250, 375, 500, 625)):
        data = make_jpeg(('#24384c', '#743d38', '#365d4b')[index % 3])
        f.check(f.offer(data, timestamp), OK)
        history.append((timestamp, data))
        f.u.mem_write(PAYLOAD, bytes(len(data)))
    f.check(f.call('freeze', 625), OK)
    f.check(f.call('cancel'), BAD_STATE)
    f.check(f.offer(jpeg, 750), BAD_STATE)
    f.check(f.call('arm'), BAD_STATE)
    for expected in history[-3:]:
        f.check(f.peek(), (OK, expected))
        f.check(f.call('ack', 2), WRITE_FAILED)
        f.check(f.peek(), (OK, expected))
        f.check(f.call('ack', 0), OK)
    f.check(f.state()[1], 0)
    passed('three_slot_rotation_producer_overwrite_frozen_batch_and_failed_ack')

    # At 4 fps, retain both endpoints of a 500 ms exposure-time window.
    f.check(f.call('arm'), OK)
    for timestamp in (1000, 1250, 1500):
        f.check(f.offer(jpeg, timestamp), OK)
    f.check(f.call('freeze', 1750), OK)
    f.check(f.peek(), (OK, (1250, jpeg)))
    f.check(f.state()[6], 2)
    f.check(f.call('discard'), OK)
    f.check(f.call('arm'), OK)
    f.check(f.offer(jpeg, 2000), OK)
    f.check(f.call('freeze', 2501), OK)
    f.check(f.peek(), (EMPTY, None))
    f.check(f.call('discard'), OK)
    passed('freeze_prunes_by_exposure_time_including_idle_gap')

    f.check(f.call('arm'), OK)
    for timestamp in (100, 350, 600):
        f.check(f.offer(jpeg, timestamp), OK)
    f.check(f.call('freeze', 400), OK)
    f.check(f.state()[6], 2)
    f.check(f.peek(), (OK, (100, jpeg)))
    f.check(f.call('discard'), OK)
    passed('exposures_after_trigger_excluded_even_if_callback_delivered_first')

    f.check(f.call('arm'), OK)
    for timestamp in (0xFFFFFF00, 0xFFFFFFFA, 244):
        f.check(f.offer(jpeg, timestamp), OK)
    f.check(f.call('freeze', 244), OK)
    f.check(f.state()[6], 3)
    f.check(f.peek(), (OK, (0xFFFFFF00, jpeg)))
    f.check(f.call('discard'), OK)
    passed('millisecond_counter_wraparound')

    # Differential state testing: independent list model vs actual ARM code.
    rng = random.Random(0x303120)
    tick = 0xFFFF0000
    frames = [make_jpeg(c, (64, 48)) for c in ('#20364c', '#753a36', '#31604c')]
    for session in range(150):
        f.check(f.call('arm'), OK)
        model = []
        for _ in range(rng.randrange(1, 24)):
            tick += rng.randrange(1, 900)
            data = rng.choice(frames)
            f.check(f.offer(data, tick), OK)
            model = [(t, b) for t, b in model if tick - t <= 500]
            model = (model + [(tick, data)])[-3:]
            f.check(f.state()[6], len(model))
        if session % 4 == 0:
            f.check(f.call('cancel'), OK)
            continue
        trigger = tick + rng.randrange(-300, 901)
        model = [(t, b) for t, b in model if 0 <= trigger - t <= 500]
        f.check(f.call('freeze', trigger & 0xFFFFFFFF), OK)
        f.check(f.state()[6], len(model))
        for timestamp, data in model:
            f.check(f.peek(), (OK, (timestamp & 0xFFFFFFFF, data)))
            f.check(f.call('ack', 1), WRITE_FAILED)
            f.check(f.peek(), (OK, (timestamp & 0xFFFFFFFF, data)))
            f.check(f.call('ack', 0), OK)
        if not model:
            f.check(f.peek(), (EMPTY, None))
            f.check(f.call('discard'), OK)
    passed('differential_randomized_lifecycle', sessions=150, seed=0x303120)

    # Full-resolution fixtures are encoded by Pillow, never by Nikon firmware.
    originals = [make_jpeg(c, (5568, 3712)) for c in ('#23394c', '#774036', '#32634d')]
    f.check(f.call('arm'), OK)
    for timestamp, data in zip((5000, 5250, 5500), originals):
        f.check(f.offer(data, timestamp), OK)
        f.u.mem_write(PAYLOAD, bytes(len(data)))
    f.check(f.call('freeze', 5500), OK)
    saved = []
    for index, original in enumerate(originals):
        status, (timestamp, snapshot) = f.peek()
        f.check(status, OK)
        f.check(snapshot, original)
        # Construct only the synthetic descriptor subset; this does not bind
        # stock mode 1 to camera JPEG or produce a real capture/DCF object.
        f.writer.configure([snapshot, b'', b'', b''], COPY)
        result, short, _ = f.writer.write(short_success=True)
        f.check(result, 0)
        if short == snapshot:
            raise AssertionError('Short-write control did not truncate')
        f.check(f.call('ack', WRITE_FAILED), WRITE_FAILED)
        f.check(f.peek()[1][1], snapshot)
        result, emitted, _ = f.writer.write()
        f.check(result, 0)
        f.check(emitted, original)
        with Image.open(io.BytesIO(emitted)) as decoded:
            decoded.load()
            f.check(decoded.size, (5568, 3712))
        name = f'history-{index + 1}.jpg'
        (output / name).write_bytes(emitted)
        saved.append(dict(file=name, exposure_ms=timestamp, bytes=len(emitted),
                          sha256=hashlib.sha256(emitted).hexdigest()))
        f.check(f.call('ack', 0), OK)
    f.check(f.state()[1], 0)
    passed('full_resolution_owned_history_replayed_through_stock_writer', files=saved)
    return dict(
        source_sha256=sha256(stock), passed=len(cases), cases=cases,
        code_kind='freestanding ARM Thumb, executed with Unicorn',
        context_bytes=CONTEXT_SIZE, slots=3, synthetic_slot_capacity=CAPACITY,
        window_ms=500, native_core_implemented=True,
        camera_precapture_implemented=False, installable_firmware_generated=False,
        camera_capture_verified=False, camera_ram_allocation_verified=False,
        camera_jpeg_mode_binding_verified=False, camera_ownership_verified=False,
        real_sd_write_verified=False,
        substitutions=['Pillow-generated JPEG inputs and synthetic exposure timestamps',
                       'caller-owned mapped RAM, serialized function calls',
                       'stock low-level file service intercepted into Python bytes',
                       'synthetic mode-1 descriptor subset for writer replay'],
        limitations=['No camera input/half-press capture adapter or JPEG completion hook.',
                     'No stock frame release, metadata, DCF or completion-event binding.',
                     'No scheduler, DMA/cache-coherency, throughput or thermal evidence.',
                     'JPEG markers do not establish decodability; full decoding is external.',
                     'Short-write detection here compares fixture bytes; real file verification is unresolved.'],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output-dir', type=Path, default=Path('build/research/precapture-core'))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.source.resolve() in [output / name for name in
                                ('report.json', 'core.bin', 'history-1.jpg', 'history-2.jpg', 'history-3.jpg')]:
        raise ValueError('Outputs must not overwrite firmware source')
    source = args.source.read_bytes()
    stock = source if len(source) == 33939514 else extract_eg(source)
    StockImage(stock)
    output.mkdir(parents=True, exist_ok=True)
    report = verify(stock, output)
    code, entries = build_core(CODE)
    (output / 'core.bin').write_bytes(code)
    report['native_entries'] = {name: hex(address) for name, address in entries.items()}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], output=str(output),
                         native_core_implemented=True, camera_precapture_implemented=False), indent=2))


if __name__ == '__main__':
    main()
