"""The built-in gyro e-VR feature (gyro-evr, part of full)."""
import hashlib
import runpy
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'
try:
    import unicorn  # noqa: F401
except ImportError:  # pragma: no cover
    unicorn = None


class GyroEvrPayloadTest(unittest.TestCase):
    def test_payload_integrity_and_presets(self):
        from z30_extensions.features.gyro.payload import CODE, SHA256, SIZE
        from z30_extensions.build import PRESETS, resolve_presets
        from z30_extensions.features.gyro.builtin import BUILTIN_MAX
        self.assertEqual(len(CODE), SIZE)
        self.assertEqual(hashlib.sha256(CODE).hexdigest(), SHA256)
        self.assertTrue(0 < SIZE <= BUILTIN_MAX and SIZE % 4 == 0)
        self.assertIn('gyro-evr', PRESETS)
        self.assertIn('gyro-evr', resolve_presets('full')[1])
        # the Gyro e-VR menu page lives in the extension menu framework
        self.assertEqual(resolve_presets(['gyro-evr'])[1], ['gyro-evr'])

    def test_payload_matches_source(self):
        # The committed payload is only reproducible with the pinned compiler
        # (tools/build_gyro_evr.py GCC_VERSION); other versions emit other code.
        tool = runpy.run_path(str(ROOT / 'tools' / 'build_gyro_evr.py'), run_name='build_gyro_evr')
        if not (shutil.which('arm-none-eabi-gcc') and shutil.which('sh')):
            self.skipTest('needs arm-none-eabi-gcc and sh')
        found = tool['compiler_version']()
        if found != tool['GCC_VERSION']:
            self.skipTest(f"needs arm-none-eabi-gcc {tool['GCC_VERSION']} (found {found})")
        result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'build_gyro_evr.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(FIRMWARE.exists() and unicorn, 'needs Z_30_0120.bin and Unicorn')
class GyroEvrFirmwareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'gyro-evr')

    def test_dormant_cells_and_report(self):
        from z30_extensions.features.modules import evr_hook, imu_hook
        from z30_extensions.features.gyro import builtin as evr_builtin
        from z30_extensions.platform.z30_120 import BASE
        self.assertIn('gyro-evr', self.report['enabled_features'])
        self.assertNotIn('rec_hook', self.report['features'])
        from z30_extensions.features.gyro import ui as gyro_ui
        for name in ('gyro_evr', 'evr_builtin', 'evr_hook', 'imu_hook', 'gyro_evr_control'):
            self.assertIn(name, self.report['features'])
        control = self.report['features']['gyro_evr_control']
        self.assertEqual(control['rows'], list(gyro_ui.LABELS))
        self.assertEqual(control['default'], 'Standard')
        self.assertEqual(control['settings_file'], 'WL-GYRO.CFG')
        self.assertIsNotNone(self.report['features']['evr_builtin']['property_shim'])
        for site in (evr_hook.SITE, evr_hook.SITE2, imu_hook.SITE):
            self.assertNotEqual(self.image[site - BASE:site - BASE + 8], self.stock[site - BASE:site - BASE + 8])
        # the cells live in RAM: nothing in the image may pre-arm them
        for cell in (evr_hook.HOOK_CELL, imu_hook.IMU_CELL, evr_builtin.AUTO_CELL):
            self.assertLess(cell, BASE) if cell < BASE else self.assertGreaterEqual(cell, 0xC0000000)

    def test_research_hook_flags_conflict(self):
        from z30_extensions.build import build
        with self.assertRaises(ValueError):
            build(self.stock, ['gyro-evr', 'shutter-sound'], module_host=True, evr_hook=True)
        with self.assertRaises(ValueError):
            build(self.stock, ['gyro-evr'], module_host=True, imu_hook=True)

    def test_builtin_autostart_in_emulator(self):
        script = runpy.run_path(str(ROOT / 'research' / 'evr' / 'emulate_builtin.py'))
        path = ROOT / 'analysis' / 'eg_gyro_evr_test.bin'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.image)
        try:
            script['main'](str(path), int(self.report['features']['gyro_evr_control']['choice'], 16))
        finally:
            path.unlink()
