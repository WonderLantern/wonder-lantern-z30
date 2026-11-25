"""Build and verify isolated DX horizontal-trim experiments for Z30 C1.20.

Changes stock area 0 tables only, without extension menus or injected code.
Native configuration execution is evidence of configuration, not pixel crop.
"""
import argparse
import binascii
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.features.aspect.geometry import dimensions
from z30_extensions.plan import Plan
from z30_extensions.platform.z30_120 import BASE


def build_trial(stock, ratio):
    plan = Plan(stock)
    sizes = dimensions(*ratio)
    trim = (5568 - sizes[0][0]) // 2
    # Read points verified separately for DX size 0/1/2 at 0x10142972
    # (output) and 0x10142928 (trim). Parent/origin remain native DX.
    for index, wh in enumerate(sizes):
        plan.edit(0x115A4DA8 + index * 4, struct.pack('<2H', *wh),
                  f'DX experiment output size {index}')
        plan.edit(0x11571E18 + index * 4, struct.pack('<2H', trim, 0),
                  f'DX experiment horizontal trim {index}', bytes(4))
    image, report = plan.finish()
    report.update(trial='stock-dx-horizontal-trim', ratio=list(ratio),
                  target_jpeg_wh=sizes, target_large_in_dx_xywh=[trim, 0, *sizes[0]],
                  status='native_configuration_verified_camera_crop_unverified',
                  base_area=0, extension_menu=False, preview_modified=False,
                  delivery='EG module in the existing project workflow; not an outer Nikon update container',
                  limitations=['Global DX table experiment; all DX JPEG size choices change.',
                               'Use JPEG L and native DX for the primary camera comparison.',
                               'Embedded previews remain stock; assess the decoded main JPEG.',
                               'RAW, alternate modes and actual ISP crop remain unverified.'])
    return image, report


def verify(stock, image, report, baseline):
    from verify_aspect_trials import measure
    assert len(image) == len(stock) and binascii.crc_hqx(image, 0) == 0
    allowed = {len(stock) - 2, len(stock) - 1}
    for edit in report['edits']:
        offset = int(edit['offset'], 16)
        expected = bytes.fromhex(edit['expected_hex'])
        replacement = bytes.fromhex(edit['replacement_hex'])
        assert stock[offset:offset + len(expected)] == expected
        assert image[offset:offset + len(replacement)] == replacement
        allowed.update(range(offset, offset + len(replacement)))
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(stock, image)))
    cases = measure(stock, image)
    trim, _, width, height = report['target_large_in_dx_xywh']
    for before, case in zip(baseline, cases):
        if case['area'] != 0:
            assert case == before, ('Non-DX regression', case['area'], case['size'])
            continue
        assert case['config_parent_wh'] == (5568, 3712)
        assert case['config_origin_xy'] == (16, 8)
        assert case['config_trim_xy'] == (trim, 0)
        assert case['config_trimmed_wh'] == (width, height)
        assert case['config_output_wh'] == tuple(report['target_jpeg_wh'][case['size']])
        assert case['output_wh'] == case['config_output_wh']
        assert width + 2 * trim == 5568 and height == 3712
    return dict(passed_cases=len(cases), cases=cases, camera_verified=False,
                scope='Native configuration and output buffer; no sensor/ISP pixels')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--deps', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.deps.resolve()))
    from verify_aspect_trials import measure
    if args.output_dir.exists():
        raise ValueError('Use a new output directory')
    stock = extract_eg(args.source.read_bytes())
    # Identity is checked by Plan before baseline execution.
    Plan(stock)
    baseline = measure(stock, stock)
    built = []
    for name, ratio in [('4-3', (4, 3)), ('5-4', (5, 4))]:
        image, report = build_trial(stock, ratio)
        report['verification'] = verify(stock, image, report, baseline)
        built.append((name, image, report))
    args.output_dir.mkdir(parents=True)
    for name, image, report in built:
        folder = args.output_dir / name
        folder.mkdir()
        (folder / 'EG209020.BIN').write_bytes(image)
        (folder / 'EG209020.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        digest = hashlib.sha256(image).hexdigest()
        (folder / 'SHA256SUMS.txt').write_text(f'{digest}  EG209020.BIN\n', encoding='ascii')
        print(name, digest, report['target_jpeg_wh'], report['verification']['passed_cases'])


if __name__ == '__main__':
    main()
