"""Build the in-camera LUT trial firmware (research image, not camera-verified).

Embeds a packed 3D LUT (identity by default, or converted from a .cube file)
and the Thumb-2 LUT core, gated over the stock index-6 retouch processing
entry. Playback retouch option 0/1/2 then runs the embedded LUT on the decoded
planes and the stock encode/save path produces the copy.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.features.lut import retouch as lut_retouch
from z30_extensions.features.sound import shutter_sound


def build(stock, cube_text=None, size=17, sd=False):
    plan = Plan(stock)
    if sd:
        shutter_sound.add(plan, lut_sd=True, lut_cube_text=cube_text, lut_size=size)
    else:
        lut_retouch.add(plan, cube_text=cube_text, size=size)
    image, report = plan.finish()
    report.update(preset='lut-sd-research' if sd else 'lut-retouch-research',
                  enabled_features=['lut-sd-menu', 'shutter-sound-framework'] if sd else ['lut-retouch'],
                  research=True, includes_full_features=False)
    if sd:
        report['limitations'] += [
            'SD LUT loading uses the Settings Extensions menu; fixed path A:\\WL-LUT.CUBE, no file browser.',
            'Default disabled. Original index-6 playback retouch jobs use the LUT only after a menu action.',
            'Menu routes/session/processing verified with replaced heap, file, cache and display endpoints.',
            'Actual task scheduling, card I/O, free heap, rendered UI and JPEG save remain unverified.',
            'Color matrix/range/CbCr order and full-frame processing time require device measurement.',
            'Includes the existing shutter-sound menu framework and its WL-SOUND.CFG restore behavior.',
        ]
    return image, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cube', type=Path, default=None,
                        help='optional .cube file to embed (default: identity 17)')
    parser.add_argument('--size', type=int, default=17)
    parser.add_argument('--sd', action='store_true', help='independent SD LUT menu research build')
    args = parser.parse_args()
    metadata = args.output.with_suffix('.json')
    if args.output.exists() or metadata.exists():
        parser.error('Choose new output paths')
    cube_text = args.cube.read_text(encoding='utf-8') if args.cube else None
    image, report = build(extract_eg(args.source.read_bytes()),
                          cube_text=cube_text, size=args.size, sd=args.sd)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(image)
    with metadata.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    if args.sd:
        feature = report['features']['lut_sd_menu']
        print(f"Built SD LUT research image: page {feature['page_index']}, path {feature['path']}; camera validation pending")
        return
    feature = report['features']['lut_retouch']
    print(f"Built LUT trial image: gate {feature['gate']} -> {feature['gate_entry']}, "
          f"embedded {feature['embedded_bytes']} byte LUT; validation pending")


if __name__ == '__main__':
    main()
