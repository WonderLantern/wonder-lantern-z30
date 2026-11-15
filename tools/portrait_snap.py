"""Snapshot UI RAM and the UI canvas over USB (0x9FF1 ReadMemory), and diff snapshots.

Research aid for finding UI state on the camera (e.g. the i menu and its cursor):
take a snapshot per step, then diff two of them.

  python tools/portrait_snap.py --report EG209020.json snap --name live --dir build/research/imenu-camera
  python tools/portrait_snap.py diff build/research/imenu-camera/live.bin build/research/imenu-camera/open.bin
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools')]

# UI state windows: the C18xxxxx globals (view kind, phase, render target, ...)
# and the C201xxxx GUI objects (overlay words, live-view container).
REGIONS = ((0xC1800000, 0x40000), (0xC2010000, 0x20000))
CHUNK = 0x10000


def snap(live, path):
    with path.open('wb') as f:
        for base, size in REGIONS:
            f.write(struct.pack('<2I', base, size))
            for o in range(0, size, CHUNK):
                data = live.read(base + o, CHUNK)
                if len(data) != CHUNK:
                    raise OSError(f'short read at {base + o:#x}: {len(data)}')
                f.write(data)


def load(path):
    data, out, o = path.read_bytes(), {}, 0
    while o < len(data):
        base, size = struct.unpack_from('<2I', data, o)
        out[base] = data[o + 8:o + 8 + size]
        o += 8 + size
    return out


def diff(a, b, ignore=()):
    rows = []
    for base, old in a.items():
        new = b[base]
        for o in range(0, len(old), 4):
            if old[o:o + 4] != new[o:o + 4] and base + o not in ignore:
                rows.append((base + o, struct.unpack_from('<I', old, o)[0], struct.unpack_from('<I', new, o)[0]))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--report', type=Path)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snap')
    s.add_argument('--name', required=True)
    s.add_argument('--dir', type=Path, required=True)
    d = sub.add_parser('diff')
    d.add_argument('a', type=Path)
    d.add_argument('b', type=Path)
    d.add_argument('--noise', type=Path, nargs='*', default=[], help='pairs a1 b1 a2 b2 ...: words changing between these are ignored')
    d.add_argument('--limit', type=int, default=200)
    a = p.parse_args()
    if a.command == 'snap':
        from portrait_live import Live
        live = Live(json.loads(a.report.read_text()))
        a.dir.mkdir(parents=True, exist_ok=True)
        snap(live, a.dir / f'{a.name}.bin')
        # The UI canvas as the viewer sees it (same as portrait_live.py screen).
        from PIL import Image
        canvas = live.word(0xC18200BC)
        size = 720 * 480 * 4
        data = b''.join(live.read(canvas + o, min(CHUNK, size - o)) for o in range(0, size, CHUNK))
        picture = Image.new('RGBA', (720, 480), (60, 60, 60, 255))
        picture.alpha_composite(Image.frombytes('RGBA', (720, 480), data))
        picture.convert('RGB').save(a.dir / f'{a.name}.png')
        print(f'{a.name}: snapshot and canvas saved in {a.dir}')
        return
    noise = set()
    pairs = a.noise
    for i in range(0, len(pairs) - 1, 2):
        noise |= {row[0] for row in diff(load(pairs[i]), load(pairs[i + 1]))}
    rows = diff(load(a.a), load(a.b), noise)
    print(f'{len(rows)} words differ ({len(noise)} noisy words ignored)')
    for address, old, new in rows[:a.limit]:
        print(f'{address:#x}  {old:#010x} -> {new:#010x}')


if __name__ == '__main__':
    main()
