"""Local firmware build GUI; never connects to a camera or writes an SD card."""
import hashlib
import locale
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from .build import FULL_FEATURES, PRESETS, resolve_presets
from .cli import PRESET_LABELS, build_to_files
from .container import CONTAINER_SHA256, CONTAINER_SIZE

SOURCE_NAME = 'Z_30_0120.bin'
OUTPUT_STEM = 'EG209020'
# Selectable next to full instead of being implied by it.
OPT_IN_PRESETS = tuple(name for name in PRESETS if name != 'full' and name not in FULL_FEATURES)

PRESET_LABELS_EN = {
    'full': 'All eleven features, including aspect profiles, the film-style date stamp and the NEF compression menu',
    'liveview-dual': 'Histogram and level together on a new full-info DISP page',
    'shutter-sound': 'Custom electronic shutter sound',
    'm900': '900-second exposure in M mode',
    'mtp-fast': 'Faster MTP property list queries',
    'video-rot': 'Automatic video orientation, locked when recording starts',
    'bitrate-slowmo-uncap': 'In-camera bitrate profiles; longer slow-motion recording',
    'aspect-profiles': 'Custom photo aspect ratios with a translucent mask, keeps stock grid',
    'photo-zebra': 'Zebra pattern in photo live view, with its own switch',
    'wifi-5ghz': 'Wi-Fi 5 GHz region unlock; all valid profiles request US/-1, included in full',
    'nef-uncompressed': 'Experimental: NEF fixed to uncompressed (confirmed on the camera as Packed 14-bit); not in full, replaces the nef-menu inside full',
    'nef-compressed': 'Experimental: NEF fixed to compressed (lossy), confirmed on the camera as Lossy type 2; not in full, replaces the nef-menu inside full',
    'nef-menu': 'Experimental: choose NEF compression (lossless/compressed/uncompressed) in the extension menu, saved to WL-NEF.CFG; included in full, the effect of the menu choice is not verified on the camera yet',
    'date-stamp': 'Film-style date stamp on JPEGs, five formats, four colors and an optional glow, turned for portrait, off by default; included in full',
}

TEXT = {
    'zh': {
        'switch': 'English',
        'title': 'Wonder Lantern Z30 固件构建',
        'source': '原厂固件', 'browse': '浏览…', 'features': '功能',
        'output': '输出文件', 'build': '开始构建', 'building': '正在构建，请稍候…',
        'open_folder': '打开输出文件夹', 'enabled': '将启用：{}', 'none': '（未选择）',
        'notes': '注意：wifi-5ghz（包含在 full 中）可能影响 2.4 GHz 第 12、13 信道的网络，'
                 '可用信道与功率规定因使用地点而异。',
        'source_empty': '请选择原版 Z_30_0120.bin（Z30 C 1.20）。',
        'source_ok': '已校验：原版 Z30 C 1.20 固件。',
        'source_missing': '找不到该文件。',
        'source_bad': '文件不匹配：需要未经修改的原版 Z30 C 1.20 固件。',
        'need_source': '请先选择通过校验的原版固件。',
        'need_preset': '请至少选择一项功能。',
        'need_output': '请指定输出文件，且不能使用 .json 后缀。',
        'exists': '输出文件或同名 JSON 报告已存在，程序不会覆盖。请换一个文件名。',
        'keystone': '缺少 Keystone 汇编组件。请运行：python -m pip install ".[sound]"',
        'failed': '构建失败：{}',
        'built': '构建完成：{}\nSHA-256：{}\n报告：{}',
        'reminder': '该文件是独立 EG 模块，不是完整 Nikon 更新包。构建成功不代表所有机身行为均已验证。',
    },
    'en': {
        'switch': '中文',
        'title': 'Wonder Lantern Z30 Firmware Builder',
        'source': 'Stock firmware', 'browse': 'Browse…', 'features': 'Features',
        'output': 'Output file', 'build': 'Build', 'building': 'Building, please wait…',
        'open_folder': 'Open output folder', 'enabled': 'Enabled: {}', 'none': '(none selected)',
        'notes': 'Note: wifi-5ghz (included in full) may affect 2.4 GHz channel 12/13 '
                 'networks, and channel and power rules vary by location.',
        'source_empty': 'Select the original Z_30_0120.bin (Z30 C 1.20).',
        'source_ok': 'Verified: original Z30 C 1.20 firmware.',
        'source_missing': 'File not found.',
        'source_bad': 'File does not match: an unmodified Z30 C 1.20 firmware is required.',
        'need_source': 'Select a verified original firmware first.',
        'need_preset': 'Select at least one feature.',
        'need_output': 'Choose an output file; it must not have a .json suffix.',
        'exists': 'The output file or its JSON report already exists and will not be overwritten. Choose another name.',
        'keystone': 'The Keystone assembler is missing. Run: python -m pip install ".[sound]"',
        'failed': 'Build failed: {}',
        'built': 'Built: {}\nSHA-256: {}\nReport: {}',
        'reminder': 'This is a standalone EG module, not a full Nikon update package. '
                    'A successful build does not prove camera behaviour.',
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


def next_free_output(folder):
    """Pick the first image name whose BIN and JSON report are both unused."""
    folder = Path(folder)
    index = 1
    while True:
        suffix = '' if index == 1 else f'-{index}'
        candidate = folder / f'{OUTPUT_STEM}{suffix}.BIN'
        if not candidate.exists() and not candidate.with_suffix('.json').exists():
            return candidate
        index += 1


def selected_presets(states):
    """Map preset name to checkbox state; full stands for every preset it contains."""
    if states.get('full'):
        return ['full'] + [name for name in OPT_IN_PRESETS if states.get(name)]
    return [name for name in PRESETS if states.get(name)]


def app_folder():
    """Folder beside the exe when frozen, otherwise the working directory."""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()


def ui_language():
    name = locale.getlocale()[0] or ''
    return 'zh' if name.lower().startswith(('zh', 'chinese')) else 'en'


def friendly_error(error, text):
    message = str(error)
    if 'Keystone' in message and not getattr(sys, 'frozen', False):
        return text['keystone']
    return text['failed'].format(message)


def main(argv=None):
    import tkinter as tk
    from tkinter import filedialog, ttk

    language = ui_language()
    # Handlers read these at call time, so switching language updates them in place.
    text = dict(TEXT[language])
    root = tk.Tk()
    root.columnconfigure(1, weight=1)
    events = queue.Queue()
    base = app_folder()

    source_var = tk.StringVar()
    source_status = tk.StringVar()
    output_var = tk.StringVar(value=str(next_free_output(base / 'output')))
    enabled_var = tk.StringVar()
    state = {'source_ok': False, 'check': 0, 'building': False, 'language': language, 'source_key': 'source_empty'}
    checks = {name: tk.BooleanVar(value=name == 'full') for name in PRESETS}
    boxes = {}

    def set_source_status(key):
        state['source_key'] = key
        source_status.set(text[key])

    def apply_language():
        text.update(TEXT[state['language']])
        labels = PRESET_LABELS if state['language'] == 'zh' else PRESET_LABELS_EN
        root.title(text['title'])
        for widget, key in translated:
            widget.configure(text=text[key])
        for name, box in boxes.items():
            box.configure(text=f'{name} — {labels[name]}')
        source_status.set(text[state['source_key']])
        refresh_features()

    def switch_language():
        state['language'] = 'en' if state['language'] == 'zh' else 'zh'
        apply_language()

    def refresh_build_button():
        build_button.configure(state='disabled' if state['building'] else 'normal')

    def refresh_features(*_):
        full = checks['full'].get()
        for name, box in boxes.items():
            if name != 'full' and name not in OPT_IN_PRESETS:
                box.configure(state='disabled' if full else 'normal')
                if full:
                    checks[name].set(True)
        chosen = selected_presets({name: var.get() for name, var in checks.items()})
        try:
            enabled = resolve_presets(chosen)[1] if chosen else []
        except ValueError as error:
            enabled_var.set(str(error))
            return
        enabled_var.set(text['enabled'].format(', '.join(enabled) or text['none']))

    def start_source_check(*_):
        # Hashing 34 MB is visible on slow disks, and only the newest path counts.
        state['source_ok'] = False
        state['check'] += 1
        ticket, value = state['check'], source_var.get().strip()
        if not value:
            set_source_status('source_empty')
            return

        def work():
            try:
                events.put(('source', ticket, check_source(value)))
            except OSError:
                events.put(('source', ticket, 'missing'))
        threading.Thread(target=work, daemon=True).start()

    def choose_source():
        path = filedialog.askopenfilename(filetypes=[('Firmware', '*.bin *.BIN'), ('*', '*')])
        if path:
            source_var.set(path)

    def choose_output():
        current = Path(output_var.get() or base / f'{OUTPUT_STEM}.BIN')
        path = filedialog.asksaveasfilename(initialdir=current.parent, initialfile=current.name,
                                            defaultextension='.BIN', confirmoverwrite=False)
        if path:
            output_var.set(path)

    def log(message):
        log_box.configure(state='normal')
        log_box.insert('end', message + '\n')
        log_box.see('end')
        log_box.configure(state='disabled')

    def start_build():
        if not state['source_ok']:
            return log(text['need_source'])
        chosen = selected_presets({name: var.get() for name, var in checks.items()})
        if not chosen:
            return log(text['need_preset'])
        value = output_var.get().strip()
        output = Path(value) if value else None
        if output is None or output.suffix.lower() == '.json':
            return log(text['need_output'])
        if output.exists() or output.with_suffix('.json').exists():
            return log(text['exists'])
        source = Path(source_var.get().strip())

        def work():
            try:
                output.parent.mkdir(parents=True, exist_ok=True)
                report, metadata = build_to_files(source, chosen, output)
                events.put(('built', output, report['sha256'], metadata))
            except (ValueError, OSError) as error:
                events.put(('failed', error))

        state['building'] = True
        refresh_build_button()
        progress.start(12)
        log(text['building'])
        threading.Thread(target=work, daemon=True).start()

    def finish_build():
        state['building'] = False
        refresh_build_button()
        progress.stop()

    def poll():
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'source' and event[1] == state['check']:
                state['source_ok'] = event[2] == 'ok'
                set_source_status('source_' + event[2])
            elif event[0] == 'built':
                finish_build()
                log(text['built'].format(event[1], event[2], event[3]))
                log(text['reminder'])
                state['last_folder'] = event[1].parent
                folder_button.configure(state='normal')
                output_var.set(str(next_free_output(event[1].parent)))
            elif event[0] == 'failed':
                finish_build()
                log(friendly_error(event[1], text))
        root.after(100, poll)

    def open_folder():
        folder = state.get('last_folder')
        if folder is None:
            return
        if sys.platform == 'win32':
            os.startfile(folder)
        else:
            subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)], check=False)

    pad = {'padx': 8, 'pady': 4}
    source_label = ttk.Label(root)
    source_label.grid(row=0, column=0, sticky='w', **pad)
    ttk.Entry(root, textvariable=source_var, width=60).grid(row=0, column=1, sticky='ew', **pad)
    source_button = ttk.Button(root, command=choose_source)
    source_button.grid(row=0, column=2, **pad)
    ttk.Label(root, textvariable=source_status).grid(row=1, column=1, sticky='w', padx=8)
    language_button = ttk.Button(root, command=switch_language)
    language_button.grid(row=1, column=2, **pad)

    frame = ttk.LabelFrame(root)
    frame.grid(row=2, column=0, columnspan=3, sticky='ew', **pad)
    for row, name in enumerate(PRESETS):
        boxes[name] = ttk.Checkbutton(frame, variable=checks[name], command=refresh_features)
        boxes[name].grid(row=row, column=0, sticky='w', padx=6, pady=1)
    ttk.Label(root, textvariable=enabled_var, wraplength=680).grid(row=3, column=0, columnspan=3, sticky='w', **pad)
    notes_label = ttk.Label(root, wraplength=680)
    notes_label.grid(row=4, column=0, columnspan=3, sticky='w', **pad)

    output_label = ttk.Label(root)
    output_label.grid(row=5, column=0, sticky='w', **pad)
    ttk.Entry(root, textvariable=output_var).grid(row=5, column=1, sticky='ew', **pad)
    output_button = ttk.Button(root, command=choose_output)
    output_button.grid(row=5, column=2, **pad)

    build_button = ttk.Button(root, command=start_build)
    build_button.grid(row=6, column=0, **pad)
    progress = ttk.Progressbar(root, mode='indeterminate')
    progress.grid(row=6, column=1, sticky='ew', **pad)
    folder_button = ttk.Button(root, command=open_folder, state='disabled')
    folder_button.grid(row=6, column=2, **pad)

    log_box = tk.Text(root, height=8, state='disabled', wrap='word')
    log_box.grid(row=7, column=0, columnspan=3, sticky='nsew', **pad)
    root.rowconfigure(7, weight=1)

    translated = [(source_label, 'source'), (source_button, 'browse'), (language_button, 'switch'),
                  (frame, 'features'), (notes_label, 'notes'), (output_label, 'output'),
                  (output_button, 'browse'), (build_button, 'build'), (folder_button, 'open_folder')]
    apply_language()
    source_var.trace_add('write', start_source_check)
    if (base / SOURCE_NAME).is_file():
        source_var.set(str(base / SOURCE_NAME))
    poll()
    root.mainloop()
    return 0
