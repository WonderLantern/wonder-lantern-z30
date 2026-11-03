"""Build the GUI program folder into dist/; firmware is never bundled."""
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'src' / 'z30_extensions'

def package():
    """Add licence texts for everything bundled, then zip the program folder."""
    folder = ROOT / 'dist' / 'WonderLanternZ30'
    notices = folder / 'licenses'
    notices.mkdir(exist_ok=True)
    python = Path(sys.base_prefix)
    pyinstaller = next(Path(p).parent / 'COPYING.txt' for p in glob.glob(
        str(Path(PyInstaller.__file__).parent.parent / 'pyinstaller-*.dist-info' / '**' / 'COPYING.txt'), recursive=True))
    for source, name in ((ROOT / 'LICENSE', 'LICENSE-MIT.txt'),
                         (ROOT / 'licenses' / 'Wonder-Lantern.txt', 'LICENSE-Wonder-Lantern.txt'),
                         (ROOT / 'licenses' / 'OFL-Noto-CJK.txt', 'OFL-Noto-CJK.txt'),
                         (ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
                         (python / 'LICENSE.txt', 'Python-LICENSE.txt'),
                         (next(python.glob('tcl/tk*/demos/license.terms')), 'Tcl-Tk-license.terms'),
                         (pyinstaller, 'PyInstaller-COPYING.txt')):
        shutil.copyfile(source, notices / name)
    return shutil.make_archive(str(ROOT / 'dist' / 'WonderLanternZ30-windows-x64'), 'zip', folder.parent, folder.name)


if __name__ == '__main__':
    if not (PACKAGE / 'asm_cache.gz').is_file():
        raise SystemExit('Run tools/build_asm_cache.py first')
    status = subprocess.call([
        # One-folder mode: a one-file exe must unpack to a temp directory at
        # startup and refuses to run when it cannot create one.
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', 'WonderLanternZ30', '--paths', str(ROOT / 'src'),
        # Keystone is GPLv2 and must not ship; asm_cache.gz replaces it at runtime.
        '--exclude-module', 'keystone',
        # build.py imports features lazily. Data paths are explicit because
        # --collect-data may read an older pip-installed copy instead of src/.
        '--collect-submodules', 'z30_extensions',
        '--add-data', f'{PACKAGE / "asm_cache.gz"}{os.pathsep}z30_extensions',
        '--add-data', f'{PACKAGE / "platform" / "z30_120_zero_samples.json"}{os.pathsep}z30_extensions/platform',
        '--add-data', f'{PACKAGE / "platform" / "z30_120_fonts.json"}{os.pathsep}z30_extensions/platform',
        '--add-data', f'{PACKAGE / "platform" / "z30_120_extra_glyphs.json"}{os.pathsep}z30_extensions/platform',
        '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
        '--specpath', str(ROOT / 'build' / 'pyinstaller'),
        str(ROOT / 'patch_gui.py'),
    ], cwd=ROOT)
    if status:
        raise SystemExit(status)
    print(package())
