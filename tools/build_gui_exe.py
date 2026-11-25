"""Build the GUI program folder into dist/; firmware is never bundled."""
import glob
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


if __name__ == '__main__':
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
