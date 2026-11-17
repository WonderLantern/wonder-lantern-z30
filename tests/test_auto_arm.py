import shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'
GCC = shutil.which('arm-none-eabi-gcc')
try:
    import unicorn
except ImportError:
    unicorn = None


@unittest.skipUnless(FIRMWARE.exists() and GCC and unicorn, 'needs Z_30_0120.bin, arm-none-eabi-gcc and Unicorn')
class AutoArmBuiltinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build, resolve_presets
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'auto_arm.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'auto_arm' / 'build.sh'), str(raw),
                            '-DREMAP', '-DDIRECT', '-DBUILTIN'], check=True)
            cls.code = raw.read_bytes()
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        _, full = resolve_presets('full')
        cls.features = [f for f in full if f != 'gyro-evr']
        cls.image, cls.report = build(cls.stock, cls.features, module_host=True, auto_arm=cls.code)

    def test_report_and_menu_row(self):
        from z30_extensions.features import auto_arm, auto_arm_ui
        f = self.report['features']
        self.assertIn('auto-arm', self.report['enabled_features'])
        self.assertEqual(f['auto_arm']['property_key'], hex(auto_arm.KEY))
        self.assertEqual(f['auto_arm_builtin']['code_bytes'], len(self.code))
        self.assertEqual(f['imu_hook']['autostart'], 'boot')
        control = f['auto_arm_control']
        self.assertEqual((control['default'], control['settings_file'], control['page_index']),
                         ('enable', 'WL-AARM.CFG', auto_arm_ui.PAGE_INDEX))

    def test_conflicts(self):
        from z30_extensions.build import build
        with self.assertRaises(ValueError):
            build(self.stock, self.features + ['gyro-evr'], module_host=True, auto_arm=self.code)
        with self.assertRaises(ValueError):
            build(self.stock, self.features, module_host=True, imu_hook=True, auto_arm=self.code)

    def test_builtin_in_emulator(self):
        import runpy
        script = runpy.run_path(str(ROOT / 'research' / 'auto_arm' / 'emulate_builtin.py'))
        path = ROOT / 'analysis' / 'eg_auto_arm_test.bin'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.image)
        try:
            script['main'](str(path), int(self.report['features']['auto_arm_control']['choice'], 16))
        finally:
            path.unlink()


if __name__ == '__main__':
    unittest.main()
