"""Build the GUI program for this platform into dist/; firmware is never bundled.

usage: python tools/build_gui_exe.py [--version TEXT]

Windows, Linux and macOS each build their own program (PyInstaller cannot
cross-build); the release workflow runs this once per platform:

  Windows  dist/WonderLanternZ30-windows-x64.zip     WonderLanternZ30/WonderLanternZ30.exe
  Linux    dist/WonderLanternZ30-linux-x64.tar.gz    WonderLanternZ30/WonderLanternZ30
  macOS    dist/WonderLanternZ30-macos-arm64.zip     WonderLanternZ30/WonderLanternZ30.app

The window is pywebview: WebView2 (WinForms, pythonnet) on Windows, WebKit
(Cocoa, PyObjC) on macOS and Qt WebEngine (PySide6, LGPL) on Linux. The macOS
program is Apple silicon native (arm64), using a libkeystone.dylib compiled from
Keystone 0.9.2 source. dist/build.json names the program and the archive for the
workflow.

--version is shown with the date on the status page of every card file the
program builds (the release workflow passes the tag, e.g. v0.5.0). Without it
the program gets the checkout's short commit ('+' when tracked files changed).
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import keystone
import PyInstaller
import webview

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'src' / 'z30_extensions'
NAME = 'WonderLanternZ30'
DIST = ROOT / 'dist'
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.card import VERSION_FILE, source_version  # noqa: E402

SYSTEM = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
ARCH = {'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine().lower(),
                                                                                    platform.machine().lower())


def dist_info_file(module, pattern, name):
    """A licence file from the installed distribution that provides module."""
    found = glob.glob(str(Path(module.__file__).parent.parent / pattern / '**' / name), recursive=True)
    found = [f for f in found if Path(f).is_file()]
    if not found:
        raise SystemExit(f'{name} not found for {module.__name__}')
    return Path(found[0])


def python_licence():
    base = Path(sys.base_prefix)
    found = [base / 'LICENSE.txt', *sorted(base.glob('lib/python3*/LICENSE.txt')),
             *sorted(base.glob('Resources/LICENSE.txt'))]
    for path in found:
        if path.is_file():
            return path
    raise SystemExit(f'Python LICENSE.txt not found under {base}')


def backend():
    """PyInstaller switches and bundled licences for this platform's pywebview backend."""
    if SYSTEM == 'windows':
        import pythonnet
        return (['--hidden-import', 'clr'],
                [(dist_info_file(pythonnet, 'pythonnet-*.dist-info', 'LICENSE*'), 'pythonnet-LICENSE.txt')])
    if SYSTEM == 'macos':
        return (['--hidden-import', 'objc', '--hidden-import', 'Foundation', '--hidden-import', 'AppKit',
                 '--hidden-import', 'WebKit', '--hidden-import', 'Quartz', '--hidden-import', 'UniformTypeIdentifiers',
                 '--osx-bundle-identifier', 'io.github.wonderlantern.z30'],
                # PyObjC wheels carry no licence file; licenses/PyObjC-MIT.txt is pyobjc-core/License.txt.
                [(ROOT / 'licenses' / 'PyObjC-MIT.txt', 'PyObjC-LICENSE.txt')])
    import qtpy
    return (['--hidden-import', 'qtpy', '--hidden-import', 'PySide6.QtWebEngineWidgets',
             '--hidden-import', 'PySide6.QtWebEngineCore', '--hidden-import', 'PySide6.QtWebChannel',
             '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6'],
            # PySide6 wheels carry no licence file: Qt and PySide6 are used under the LGPL-3.0.
            [(ROOT / 'licenses' / 'LGPL-3.0.txt', 'PySide6-Qt-LGPL-3.0.txt'),
             (ROOT / 'licenses' / 'GPL-3.0.txt', 'GPL-3.0.txt'),
             (dist_info_file(qtpy, 'QtPy-*.dist-info', 'LICENSE*'), 'QtPy-LICENSE.txt')])


def package(folder, licences):
    """Add licence texts for everything bundled, then archive the program folder."""
    notices = folder / 'licenses'
    notices.mkdir(exist_ok=True)
    for source, name in ((ROOT / 'LICENSE', 'LICENSE-MIT.txt'),
                         (ROOT / 'licenses' / 'Wonder-Lantern.txt', 'LICENSE-Wonder-Lantern.txt'),
                         (ROOT / 'licenses' / 'OFL-Noto-CJK.txt', 'OFL-Noto-CJK.txt'),
                         (ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
                         (python_licence(), 'Python-LICENSE.txt'),
                         (dist_info_file(PyInstaller, 'pyinstaller-*.dist-info', 'COPYING.txt'),
                          'PyInstaller-COPYING.txt'),
                         (dist_info_file(keystone, 'keystone_engine-*.dist-info', 'LICENSE.TXT'),
                          'Keystone-LICENSE.txt'),
                         (dist_info_file(webview, 'pywebview-*.dist-info', 'LICENSE*'), 'pywebview-LICENSE.txt'),
                         *licences):
        shutil.copyfile(source, notices / name)
    stem = DIST / f'{NAME}-{SYSTEM}-{ARCH}'
    if SYSTEM == 'windows':
        return Path(shutil.make_archive(str(stem), 'zip', folder.parent, folder.name))
    if SYSTEM == 'macos':
        # ditto keeps the symlinks and permissions inside the .app; a plain zip breaks the bundle.
        archive = stem.with_suffix('.zip')
        archive.unlink(missing_ok=True)
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(folder), str(archive)],
                       check=True)
        return archive
    archive = stem.with_name(stem.name + '.tar.gz')
    subprocess.run(['tar', '-czf', str(archive), '-C', str(folder.parent), folder.name], check=True)
    return archive


def version_file(version):
    """The stamped version, in the build folder; plain ASCII so the status page can draw it."""
    if not re.fullmatch(r'[0-9A-Za-z.+_-]{1,16}', version):
        raise SystemExit(f'--version must be 1-16 characters of 0-9 A-Z a-z . + _ -: {version!r}')
    path = ROOT / 'build' / 'pyinstaller' / VERSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version + '\n', encoding='utf-8')
    return path


# NSS loads its crypto modules (libsoftokn3, libfreebl3) at run time from its own
# folder; a bundled libnss3 without them breaks Qt WebEngine, so the system's NSS
# is used instead, as browsers on Linux do.
SYSTEM_NSS = ('libnss', 'libnspr4.', 'libplc4.', 'libplds4.', 'libsmime3.', 'libssl3.',
              'libsoftokn3.', 'libfreebl')


def drop_system_nss(folder):
    for path in folder.rglob('lib*'):
        if path.name.startswith(SYSTEM_NSS) and path.is_file():
            print(f'Using the system copy of {path.name}')
            path.unlink()


def keystone_library():
    """The library keystone.py loads first: keystone.dll, libkeystone.so or libkeystone.dylib.

    The macOS wheel also has libkeystone.0.dylib, which keystone.py never loads."""
    folder = Path(keystone.__file__).parent
    for name in ('keystone.dll', 'libkeystone.so', 'libkeystone.dylib'):
        if (folder / name).is_file():
            return folder / name
    raise SystemExit(f'No Keystone library next to {keystone.__file__}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--version', help='Version shown on the card status page (default: the git commit)')
    args = parser.parse_args()
    version = args.version or source_version() or 'app'
    print(f'Program version: {version} ({SYSTEM}-{ARCH})')
    # Every data file in platform/ that build.py reads at run time.
    platform_data = [arg for path in sorted((PACKAGE / 'platform').glob('*'))
                     if path.suffix in ('.json', '.png')
                     for arg in ('--add-data', f'{path}{os.pathsep}z30_extensions/platform')]
    switches, licences = backend()
    # PyInstaller writes the onedir program to dist/NAME (and dist/NAME.app on macOS);
    # the archive holds one folder, NAME, with the program and its licences.
    for old in (DIST / NAME, DIST / f'{NAME}.app', DIST / 'macos'):
        shutil.rmtree(old, ignore_errors=True)
    work = ROOT / 'build' / 'pyinstaller'
    status = subprocess.call([
        # One-folder mode: a one-file program must unpack to a temp directory at
        # startup and refuses to run when it cannot create one.
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', NAME, '--paths', str(ROOT / 'src'),
        # Keystone ships inside the program and assembles at build time, so
        # asm_cache.gz is not bundled. keystone.py loads its library from its
        # own folder, so the library goes next to the module.
        '--hidden-import', 'keystone',
        '--add-binary', f'{keystone_library()}{os.pathsep}keystone',
        # build.py imports features lazily. Data paths are explicit because
        # --collect-data may read an older pip-installed copy instead of src/.
        '--collect-submodules', 'z30_extensions',
        '--add-data', f'{PACKAGE / "gui_page.html"}{os.pathsep}z30_extensions',
        '--add-data', f'{version_file(version)}{os.pathsep}z30_extensions',
        # pywebview picks its backend at run time.
        '--collect-all', 'webview',
        '--exclude-module', 'tkinter',
        *switches,
        *platform_data,
        '--distpath', str(DIST if SYSTEM != 'macos' else DIST / 'macos'), '--workpath', str(work),
        '--specpath', str(work),
        str(ROOT / 'patch_gui.py'),
    ], cwd=ROOT)
    if status:
        raise SystemExit(status)
    folder = DIST / NAME
    if SYSTEM == 'macos':
        folder.mkdir()
        shutil.move(str(DIST / 'macos' / f'{NAME}.app'), str(folder / f'{NAME}.app'))
        shutil.rmtree(DIST / 'macos')
        program = folder / f'{NAME}.app' / 'Contents' / 'MacOS' / NAME
    else:
        program = folder / (f'{NAME}.exe' if SYSTEM == 'windows' else NAME)
    if SYSTEM == 'linux':
        drop_system_nss(folder)
    archive = package(folder, licences)
    (DIST / 'build.json').write_text(json.dumps(dict(
        system=SYSTEM, arch=ARCH, version=version, program=str(program.relative_to(ROOT)),
        archive=str(archive.relative_to(ROOT))), indent=2) + '\n', encoding='utf-8')
    print(archive)


if __name__ == '__main__':
    main()
