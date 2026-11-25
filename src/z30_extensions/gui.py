"""Local firmware build GUI (pywebview page in gui_page.html); never connects to a camera
or writes an SD card."""
import hashlib
import locale
import os
import subprocess
import sys
import threading
from importlib.resources import files
from pathlib import Path

from . import card
from .build import FULL_FEATURES, PRESETS, resolve_presets
from .cli import PRESET_LABELS
from .container import CONTAINER_SHA256, CONTAINER_SIZE

SOURCE_NAME = 'Z_30_0120.bin'
KIT_STEM = 'card'
MODES = ('loader', 'patch')
PAGE = 'gui_page.html'
# Selectable next to full instead of being implied by it.
OPT_IN_PRESETS = tuple(name for name in PRESETS if name != 'full' and name not in FULL_FEATURES)

PRESET_LABELS_EN = {
    'full': 'All sixteen features, including aspect profiles, the film-style date stamp, the portrait layout, the NEF compression menu, gyro e-VR, 3D tracking, the AF frame colours and the focal length',
    'liveview-dual': 'Histogram and level together on a new full-info DISP page',
    'shutter-sound': 'Custom electronic shutter sound',
    'm900': '900-second exposure in M mode',
    'mtp-fast': 'Faster MTP property list queries',
    'video-rot': 'Automatic video orientation, locked when recording starts',
    'bitrate-slowmo-uncap': 'In-camera bitrate profiles; longer slow-motion recording',
    'aspect-profiles': 'Custom photo aspect ratios with a translucent mask, keeps stock grid',
    'photo-zebra': 'Zebra pattern in photo live view, with its own switch',
    'wifi-5ghz': 'Wi-Fi 5 GHz region unlock; all valid profiles request US/-1, included in full',
    'portrait-ui': 'Portrait layout for photo live-view info when held upright, redrawn after each turn, off by default; included in full',
    'nef-uncompressed': 'Experimental: NEF fixed to uncompressed (confirmed on the camera as Packed 14-bit); not in full, replaces the nef-menu inside full',
    'nef-compressed': 'Experimental: NEF fixed to compressed (lossy), confirmed on the camera as Lossy type 2; not in full, replaces the nef-menu inside full',
    'focal-length': 'Show the focal length for every CPU lens in photo live view (stock: power zoom only); always on; included in full',
    'battery-percent': 'Experimental: battery icon with the charge in percent in photo live view (red at 20% and below); always on; not in full',
    'nef-menu': 'Experimental: choose NEF compression (lossless/compressed/uncompressed) in the extension menu, saved to WL-NEF.CFG; included in full, all three choices confirmed on the camera',
    'date-stamp': 'Film-style date stamp on JPEGs, five formats, four colors and an optional glow, turned for portrait, off by default; included in full',
    'gyro-evr': 'Gyro e-VR: in-body gyro stabilization in movie mode when electronic VR is on; yaw, pitch and roll, cooperates with lens VR; Extensions menu Standard/Enhanced/Native crop/OFF; included in full',
    '3d-tracking': '3D tracking: tracking started with OK or a half-press hands over to a detected person or animal (the fine subject box) as touch tracking does, also during a half-press; photo shooting menu Disable/Enable, OFF by default; included in full',
    'af-frame-colour': 'AF frame colours in the style of Nikon EXPEED 7 cameras (Z9, Z8, Z6III and others): the tracking and person / animal boxes are white, green when a half-press focuses and red when it does not, in AF-S and AF-C alike; the movie-mode person / animal box is white; plain auto-area AF-C frames turn green in focus; other frames unchanged; photo shooting menu Enable / Disable (Enable by default); included in full',
}

# Short titles for the feature cards; PRESET_LABELS / PRESET_LABELS_EN hold the details.
PRESET_TITLES = {
    'zh': {
        'full': '全部功能', 'liveview-dual': '直方图＋水平仪同屏', 'shutter-sound': '自定义快门声音',
        'm900': 'M 档 900 秒曝光', 'mtp-fast': 'MTP 加速', 'video-rot': '视频自动方向',
        'bitrate-slowmo-uncap': '高码率与慢动作扩展', 'aspect-profiles': '多画幅与遮幅',
        'photo-zebra': '照片斑马纹', 'wifi-5ghz': 'Wi-Fi 5 GHz', 'date-stamp': '日期水印',
        'portrait-ui': '竖拍显示', 'gyro-evr': '陀螺仪电子防抖', '3d-tracking': '3D 跟踪',
        'af-frame-colour': '对焦框颜色', 'focal-length': '焦距显示', 'nef-menu': 'NEF 压缩菜单',
        'nef-uncompressed': 'NEF 固定未压缩', 'nef-compressed': 'NEF 固定压缩',
        'battery-percent': '电量百分比',
    },
    'en': {
        'full': 'Everything', 'liveview-dual': 'Histogram + level', 'shutter-sound': 'Custom shutter sound',
        'm900': '900 s in M mode', 'mtp-fast': 'Faster MTP', 'video-rot': 'Video orientation',
        'bitrate-slowmo-uncap': 'Bitrate & slow motion', 'aspect-profiles': 'Aspect profiles',
        'photo-zebra': 'Photo zebra', 'wifi-5ghz': 'Wi-Fi 5 GHz', 'date-stamp': 'Date stamp',
        'portrait-ui': 'Portrait layout', 'gyro-evr': 'Gyro e-VR', '3d-tracking': '3D tracking',
        'af-frame-colour': 'AF frame colours', 'focal-length': 'Focal length', 'nef-menu': 'NEF compression menu',
        'nef-uncompressed': 'NEF fixed uncompressed', 'nef-compressed': 'NEF fixed compressed',
        'battery-percent': 'Battery percent',
    },
}


def check_source(path):
    """Return 'ok', 'missing' or 'bad' without reading a wrong-sized file."""
    path = Path(path)
    if not path.is_file():
        return 'missing'
    if path.stat().st_size != CONTAINER_SIZE:
        return 'bad'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return 'ok' if digest == CONTAINER_SHA256 else 'bad'


def next_free_kit(folder):
    """Pick the first kit folder that does not exist yet."""
    folder = Path(folder)
    index = 1
    while True:
        candidate = folder / (KIT_STEM if index == 1 else f'{KIT_STEM}-{index}')
        if not candidate.exists():
            return candidate
        index += 1


def latest_kit(folder):
    """The newest folder under FOLDER that holds a resident loader EG, or None."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    kits = [p for p in folder.iterdir()
            if (p / card.RESIDENT_NAME).is_file() and (p / card.REPORT_NAME).is_file()]
    return max(kits, key=lambda p: (p / card.RESIDENT_NAME).stat().st_mtime, default=None)


def selected_presets(states):
    """Map preset name to checkbox state; full stands for every preset it contains."""
    if states.get('full'):
        return ['full'] + [name for name in OPT_IN_PRESETS if states.get(name)]
    return [name for name in PRESETS if states.get(name)]


def gui_states(states):
    """The page offers only full and what it contains; opt-in presets stay on the command line."""
    return {name: on for name, on in states.items() if name not in OPT_IN_PRESETS}


def app_folder():
    """Folder beside the exe when frozen, otherwise the working directory."""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()


def ui_language():
    name = locale.getlocale()[0] or ''
    if not name and sys.platform == 'win32':
        import ctypes
        return 'zh' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else 'en'
    return 'zh' if name.lower().startswith(('zh', 'chinese')) else 'en'


def output_problem(mode, output):
    """None when the folder OUTPUT can take this build, else (message key, detail)."""
    existing = [str(p) for p in card.kit_outputs(output, mode == 'patch', mode == 'loader') if p.exists()]
    if existing:
        return 'kit_exists', ', '.join(existing)
    if mode == 'patch' and not all((output / n).is_file() for n in (card.RESIDENT_NAME, card.REPORT_NAME)):
        return 'need_resident', ''
    return None


def run_build(mode, source, presets, output):
    """Build and describe the result for the page; raises ValueError/OSError on failure."""
    kit = card.write_kit(source, presets, output, patch_only=mode == 'patch', loader_only=mode == 'loader')
    return dict(kind=mode, folder=str(output), files=[str(p) for p in kit['written']],
                sha256=kit['resident_sha256'], resident_id=f'{kit["resident"].identity:#010x}',
                patch=str(output / card.PATCH_PATH), patch_bytes=kit['patch_bytes'])


class Api:
    """Methods the page calls through window.pywebview.api; each runs on its own thread."""

    def __init__(self):
        self._base = app_folder()
        self._window = None
        self._busy = threading.Lock()

    def initial(self):
        source = self._base / SOURCE_NAME
        return dict(
            language=ui_language(), presets=[n for n in PRESETS if n not in OPT_IN_PRESETS],
            full=sorted(FULL_FEATURES),
            titles=PRESET_TITLES,
            labels={'zh': PRESET_LABELS, 'en': PRESET_LABELS_EN},
            source=str(source) if source.is_file() else '',
            outputs={'loader': str(next_free_kit(self._base / 'output')),
                     'patch': str(latest_kit(self._base / 'output') or self._base / 'output' / KIT_STEM)})

    def check_source(self, path):
        try:
            return check_source(path.strip()) if path.strip() else 'empty'
        except OSError:
            return 'missing'

    def resolve(self, states):
        chosen = selected_presets(gui_states(states))
        if not chosen:
            return dict(ok=True, presets=[], features=[])
        try:
            return dict(ok=True, presets=chosen, features=resolve_presets(chosen)[1])
        except ValueError as error:
            return dict(ok=False, error=str(error))

    def choose_source(self):
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=str(self._base),
                                                file_types=('Firmware (*.bin;*.BIN)', 'All files (*.*)'))
        return picked[0] if picked else None

    def choose_folder(self, current):
        import webview
        current = Path(current) if current else self._base / 'output'
        start = current if current.is_dir() else current.parent
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                                directory=str(start if start.is_dir() else self._base))
        return picked[0] if picked else None

    def next_kit(self, folder):
        return str(next_free_kit(Path(folder).parent))

    def build(self, mode, source, states, output):
        """MODE 'loader' writes the loader EG to OUTPUT; 'patch' writes OUTPUT/Z30/WL.PATCH for it."""
        if mode not in MODES or not output.strip():
            return dict(ok=False, key='need_folder')
        if check_source(source) != 'ok':
            return dict(ok=False, key='need_source')
        presets = selected_presets(gui_states(states))
        if not presets and mode == 'patch':
            return dict(ok=False, key='need_preset')
        output = Path(output.strip())
        problem = output_problem(mode, output)
        if problem:
            return dict(ok=False, key=problem[0], detail=problem[1])
        if not self._busy.acquire(blocking=False):
            return dict(ok=False, key='busy')
        try:
            return dict(ok=True, **run_build(mode, Path(source), presets, output))
        except (ValueError, OSError) as error:
            return dict(ok=False, key='failed', detail=str(error))
        finally:
            self._busy.release()

    def open_folder(self, folder):
        if sys.platform == 'win32':
            os.startfile(folder)
        else:
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', folder], check=False)


def selftest(source, folder):
    """Maintainer check of a packaged program: loader EG then a full card file, no window."""
    import json
    folder = Path(folder)
    try:
        result = [run_build(mode, Path(source), ['full'], folder) for mode in MODES]
    except Exception as error:  # noqa: BLE001 - reported to the file, the exe has no console
        result = {'error': repr(error)}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'selftest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if isinstance(result, list) else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == '--selftest':
        return selftest(argv[1], argv[2])
    import webview
    api = Api()
    page = files('z30_extensions').joinpath(PAGE).read_text(encoding='utf-8')
    title = 'Z30 固件构建' if ui_language() == 'zh' else 'Z30 Firmware Builder'
    api._window = webview.create_window(title, html=page, js_api=api,
                                       width=1080, height=820, min_size=(860, 640))
    webview.start()
    return 0
