"""Local firmware build CLI; never connects to a camera or writes an SD card."""
import argparse
import json
import sys
from pathlib import Path
from .build import build, PRESETS, resolve_presets
from .container import extract_eg, CONTAINER_SHA256

PRESET_LABELS = {
    'full': '全部十一项功能，包含自定义画幅与半透明遮幅、胶片风格日期水印和 NEF 压缩菜单',
    'liveview-dual': '直方图与水平仪同屏，新增全信息 DISP 页',
    'shutter-sound': '自定义电子快门声音',
    'm900': 'M 档 900 秒曝光',
    'mtp-fast': 'MTP 属性列表加速',
    'video-rot': '视频自动方向；锁定开始录制时的方向',
    'bitrate-slowmo-uncap': '机内码率档位与记忆；放宽慢动作时长和文件限制',
    'aspect-profiles': '自定义照片画幅与半透明遮幅，保留原厂内部网格',
    'photo-zebra': '照片取景斑马纹；独立开关，复用原厂样式和阈值',
    'wifi-5ghz': 'Wi-Fi 5 GHz 地区解锁；全部有效配置请求 US/-1，包含在 full 中',
    'nef-uncompressed': '实验性：NEF 固定为未压缩（实机已确认变为 Packed 14-bit）；不包含在 full 中，与 full 同选时替换其中的 nef-menu',
    'nef-compressed': '实验性：NEF 固定为压缩（有损）（实机已确认变为 Lossy type 2）；不包含在 full 中，与 full 同选时替换其中的 nef-menu',
    'nef-menu': '实验性：扩展菜单中选择 NEF 压缩（无失真压缩/压缩/未压缩），保存到 WL-NEF.CFG；包含在 full 中，菜单选择的效果尚未实机验证',
    'date-stamp': '在 JPEG 上打胶片风格日期水印，可选五种格式、四种颜色和柔光晕，竖拍自动转向，默认关闭；包含在 full 中',
}


def choose_presets():
    """Read a runtime selection without changing firmware on the camera."""
    print('Select presets (combine numbers or names, separated by spaces or commas):')
    for index, name in enumerate(PRESETS, 1):
        print(f'  {index}. {name} — {PRESET_LABELS[name]}')
    while True:
        try:
            answer = input('Presets [full]: ').strip()
        except (EOFError, KeyboardInterrupt):
            raise ValueError('Preset selection cancelled') from None
        tokens = answer.replace(',', ' ').split() if answer else ['full']
        selected = [PRESETS[int(t) - 1] if t.isdigit() and 1 <= int(t) <= len(PRESETS)
                    else t for t in tokens]
        try:
            return resolve_presets(selected)[0]
        except ValueError as error:
            print(error)


def build_to_files(source_path, selected, output_path, extracted_eg=False):
    """Build one image and its report, shared by the CLI and the GUI."""
    metadata = output_path.with_suffix('.json')
    supplied = source_path.read_bytes()
    source = supplied if extracted_eg else extract_eg(supplied)
    output, report = build(source, selected)
    report['input_kind'] = 'extracted_eg' if extracted_eg else 'stock_container'
    if not extracted_eg:
        report['container_sha256'] = CONTAINER_SHA256
    # Create exclusively; never overwrite an existing image or report.
    with metadata.open('x', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(report, indent=2) + '\n')
    try:
        with output_path.open('xb') as f:
            f.write(output)
    except OSError:
        metadata.unlink()
        raise
    return report, metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('feature', nargs='*', metavar='PRESET',
                        help='Combine presets with spaces: ' + ', '.join(PRESETS) +
                             '; full enables eleven features including wifi-5ghz (all valid profiles US/-1), date-stamp and nef-menu; '
                             'nef-uncompressed and nef-compressed are optional, and replace the nef-menu inside full; '
                             'aspect-profiles includes a translucent live-view mask; '
                             'photo-zebra includes an independent photo live-view switch; '
                             'date-stamp is off until chosen in the camera menu; legacy alias: modular')
    parser.add_argument('--interactive', action='store_true', help='Prompt for presets even when stdin is redirected')
    parser.add_argument('--source', type=Path, required=True, help='Original Z_30_0120.bin, SHA-256 checked before decoding')
    parser.add_argument('--extracted-eg', action='store_true', help='Developer mode: input is an extracted stock EG module')
    parser.add_argument('--output', type=Path, required=True, help='New output path; existing files are refused')
    args = parser.parse_args(argv)
    metadata = args.output.with_suffix('.json')
    if args.output.resolve() == metadata.resolve():
        parser.error('Output path must not have a .json suffix')
    if args.output.exists() or metadata.exists():
        parser.error('Output or report already exists; choose fresh paths')
    try:
        selected = args.feature
        if args.interactive or (not selected and sys.stdin.isatty()):
            selected = choose_presets()
        if not selected:
            selected = ['full']
        selected, features = resolve_presets(selected)
        print('Enabled features: ' + ', '.join(features))
        report, metadata = build_to_files(args.source, selected, args.output, args.extracted_eg)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Build failed: {error}\n')
    print(f'Built {args.output}: {report["sha256"]}')
    print(f'Report: {metadata}')
    return 0
