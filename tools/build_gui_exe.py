"""Build the GUI program folder into dist/; firmware is never bundled.

usage: python tools/build_gui_exe.py [--version TEXT]

--version is shown with the date on the status page of every card file the
program builds (the release workflow passes the tag, e.g. v0.5.0). Without it
the program gets the checkout's short commit ('+' when tracked files changed).
"""
import argparse
import glob
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pythonnet
import keystone
import PyInstaller
import webview

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'src' / 'z30_extensions'
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.card import VERSION_FILE, source_version  # noqa: E402


def dist_info_file(module, pattern, name):
    """A licence file from the installed distribution that provides module."""
    found = glob.glob(str(Path(module.__file__).parent.parent / pattern / '**' / name), recursive=True)
    found = [f for f in found if Path(f).is_file()]
    if not found:
        raise SystemExit(f'{name} not found for {module.__name__}')
    return Path(found[0])


def package():
    """Add licence texts for everything bundled, then zip the program folder."""
    folder = ROOT / 'dist' / 'WonderLanternZ30'
    notices = folder / 'licenses'
    notices.mkdir(exist_ok=True)
    python = Path(sys.base_prefix)
    for source, name in ((ROOT / 'LICENSE', 'LICENSE-MIT.txt'),
                         (ROOT / 'licenses' / 'Wonder-Lantern.txt', 'LICENSE-Wonder-Lantern.txt'),
                         (ROOT / 'licenses' / 'OFL-Noto-CJK.txt', 'OFL-Noto-CJK.txt'),
                         (ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
                         (python / 'LICENSE.txt', 'Python-LICENSE.txt'),
                         (dist_info_file(PyInstaller, 'pyinstaller-*.dist-info', 'COPYING.txt'),
                          'PyInstaller-COPYING.txt'),
                         (dist_info_file(keystone, 'keystone_engine-*.dist-info', 'LICENSE.TXT'),
                          'Keystone-LICENSE.txt'),
                         (dist_info_file(webview, 'pywebview-*.dist-info', 'LICENSE*'), 'pywebview-LICENSE.txt'),
                         (dist_info_file(pythonnet, 'pythonnet-*.dist-info', 'LICENSE*'), 'pythonnet-LICENSE.txt')):
        shutil.copyfile(source, notices / name)
    return shutil.make_archive(str(ROOT / 'dist' / 'WonderLanternZ30-windows-x64'), 'zip', folder.parent, folder.name)


def version_file(version):
    """The stamped version, in the build folder; plain ASCII so the status page can draw it."""
    if not re.fullmatch(r'[0-9A-Za-z.+_-]{1,16}', version):
        raise SystemExit(f'--version must be 1-16 characters of 0-9 A-Z a-z . + _ -: {version!r}')
    path = ROOT / 'build' / 'pyinstaller' / VERSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version + '\n', encoding='utf-8')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--version', help='Version shown on the card status page (default: the git commit)')
    args = parser.parse_args()
    version = args.version or source_version() or 'app'
    print(f'Program version: {version}')
    # Every data file in platform/ that build.py reads at run time.
    platform_data = [arg for path in sorted((PACKAGE / 'platform').glob('*'))
                     if path.suffix in ('.json', '.png')
                     for arg in ('--add-data', f'{path}{os.pathsep}z30_extensions/platform')]
    keystone_dll = next(Path(keystone.__file__).parent.glob('*keystone*.dll'))
    shutil.rmtree(ROOT / 'dist' / 'WonderLanternZ30', ignore_errors=True)
    status = subprocess.call([
        # One-folder mode: a one-file exe must unpack to a temp directory at
        # startup and refuses to run when it cannot create one.
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', 'WonderLanternZ30', '--paths', str(ROOT / 'src'),
        # Keystone ships inside the program and assembles at build time, so
        # asm_cache.gz is not bundled. keystone.py loads keystone.dll from its
        # own folder, so the DLL goes next to the module.
        '--hidden-import', 'keystone',
        '--add-binary', f'{keystone_dll}{os.pathsep}keystone',
        # build.py imports features lazily. Data paths are explicit because
        # --collect-data may read an older pip-installed copy instead of src/.
        '--collect-submodules', 'z30_extensions',
        '--add-data', f'{PACKAGE / "gui_page.html"}{os.pathsep}z30_extensions',
        '--add-data', f'{version_file(version)}{os.pathsep}z30_extensions',
        # pywebview picks its Windows backend (WinForms + WebView2 via pythonnet) at run time.
        '--collect-all', 'webview', '--hidden-import', 'clr',
        '--exclude-module', 'tkinter',
        *platform_data,
        '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
        '--specpath', str(ROOT / 'build' / 'pyinstaller'),
        str(ROOT / 'patch_gui.py'),
    ], cwd=ROOT)
    if status:
        raise SystemExit(status)
    print(package())
