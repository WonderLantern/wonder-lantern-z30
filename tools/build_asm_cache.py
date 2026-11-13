"""Pre-assemble every preset combination so the GUI program can ship without Keystone.

Writes src/z30_extensions/asm_cache.gz, then rebuilds every combination from that
file with Keystone blocked and requires identical image hashes.
"""
import argparse
import gzip
import itertools
import json
import multiprocessing
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions import assembly
from z30_extensions.build import FEATURE_ORDER, build, resolve_presets
from z30_extensions.container import extract_eg

CACHE = ROOT / 'src' / 'z30_extensions' / assembly.CACHE_NAME
_source = None


def start_worker(firmware, block_keystone):
    global _source
    # Keystone's assembler warnings would otherwise flood the console.
    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
    if block_keystone:
        sys.modules['keystone'] = None
    else:
        assembly._cache = {}
    _source = extract_eg(Path(firmware).read_bytes())


def is_valid(combo):
    try:
        resolve_presets(list(combo))
    except ValueError:
        return False
    return True


def build_combo(combo):
    assembly.recorder = {}
    _, report = build(_source, list(combo))
    return combo, report['sha256'], assembly.recorder


def run(pool_args, combos):
    with multiprocessing.Pool(initializer=start_worker, initargs=pool_args) as pool:
        for index, result in enumerate(pool.imap_unordered(build_combo, combos, chunksize=4), 1):
            if index % 50 == 0:
                print(f'{index}/{len(combos)}', flush=True)
            yield result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'Z_30_0120.bin')
    args = parser.parse_args()
    combos = [c for n in range(1, len(FEATURE_ORDER) + 1)
              for c in itertools.combinations(FEATURE_ORDER, n) if is_valid(c)]
    CACHE.unlink(missing_ok=True)
    code, hashes = {}, {}
    for combo, digest, recorded in run((str(args.source), False), combos):
        code.update(recorded)
        hashes[combo] = digest
    lines = ''.join(f'{key} {code[key].hex()}\n' for key in sorted(code))
    with CACHE.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as f:
        f.write(lines.encode('ascii'))
    print(f'{len(combos)} combinations, {len(code)} entries, {CACHE.stat().st_size} bytes')
    mismatches = [combo for combo, digest, _ in run((str(args.source), True), combos)
                  if digest != hashes[combo]]
    if mismatches:
        CACHE.unlink()
        raise SystemExit(f'Cache removed, {len(mismatches)} combinations differ: {json.dumps(mismatches[:5])}')
    print('Every combination matches without Keystone')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
