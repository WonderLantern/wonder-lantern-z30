"""The read-only AF log research kit (modules/af_log, tools/build_af_log.py)."""
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'
GCC = subprocess.run(['sh', '-c', 'command -v arm-none-eabi-gcc'], capture_output=True).returncode == 0
try:
    import unicorn  # noqa: F401
except ImportError:  # pragma: no cover
    unicorn = None


class AfLogModuleTest(unittest.TestCase):
    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_module_logs_changes_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog')
            self.assertEqual(script['main'](str(raw)), (30, 40, 20, 30, 40))

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log5_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log5.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG5'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog5')
            self.assertEqual(script['main'](str(raw), True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log6_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log6.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG6'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog6')
            self.assertEqual(script['main'](str(raw), False, True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log7_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log7.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG7'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog7')
            self.assertEqual(script['main'](str(raw), False, False, True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log8_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log8.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG8'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog8')
            self.assertEqual(script['main'](str(raw), False, False, False, True), 10)


    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log12_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log12.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG12'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog12')
            self.assertEqual(script['main'](str(raw), *([False] * 7), True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log11_variant_reads_the_3d_tracking_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log11.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG11', '-DTRK3D_GET=0x12100101u'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog11')
            self.assertEqual(script['main'](str(raw), False, False, False, False, False, False, True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log10_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log10.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG10'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog10')
            self.assertEqual(script['main'](str(raw), False, False, False, False, False, True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_log9_variant_writes_its_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_log9.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_log' / 'build.sh'), str(raw), '-DLOG9'], check=True)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aflog.py'), run_name='aflog9')
            self.assertEqual(script['main'](str(raw), False, False, False, False, True), 10)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_scan_module_finds_tracking_only_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_scan.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_scan' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            out = Path(tmp) / 'SC.BIN'
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afscan.py'), run_name='afscan')
            self.assertTrue(script['main'](str(raw), str(out)))
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afscan.py'), run_name='parse')
            found = [c[0] for c in parse['candidates'](parse['load'](str(out)))]
            self.assertIn(0xC2345670, found)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_box_scan_counts_the_shown_not_tracking_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_scan_box.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_scan' / 'build.sh'), str(raw), '-DSCAN_BOX'], check=True)
            out = Path(tmp) / 'SCB.BIN'
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afscan.py'), run_name='afscanbox')
            self.assertTrue(script['main'](str(raw), str(out), True))
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afscan.py'), run_name='parsebox')
            s = parse['load'](str(out))
            self.assertTrue(s['box'])
            found = [c[0] for c in parse['candidates'](s)]
            self.assertIn(0xC2345670, found)
            self.assertNotIn(0xC2311110, found)


    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_handover_module_presses_ok_once_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_handover.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_handover' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            script = runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afhandover.py'), run_name='afhand')
            self.assertTrue(script['main'](str(raw)))
            out = Path(tmp) / 'HO.BIN'
            script['main'](str(raw), str(out))
            replay = runpy.run_path(str(ROOT / 'research' / 'af' / 'replay_handover.py'), run_name='replay')
            self.assertEqual(len(replay['replay'](str(out))), 3)     # the C rule (v7) and the replay agree

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_retarget_module_moves_tracker_without_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_retarget.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_retarget' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            out = Path(tmp) / 'RT.BIN'
            runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afretarget.py'), run_name='afrt')['main'](str(raw), str(out))
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afretarget.py'), run_name='prt')
            hdr, recs = parse['load'](str(out))
            self.assertEqual(hdr['fires'], 2)                    # one from detection updates, one from IMU ticks
            self.assertEqual(hdr['hooks'], 3)
            self.assertEqual(sum(1 for r in recs if r[4] & 2), 12)

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_snapshot_module_finds_the_start_method_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_snap.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_snap' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            out = Path(tmp) / 'SN.BIN'
            runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afsnap.py'), run_name='afsnap')['main'](str(raw), str(out))
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afsnap.py'), run_name='psnap')
            found = parse['candidates'](parse['load'](str(out)), [2, 2])
            self.assertEqual([a for a, _ in found], [0xC23812A0])

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_snapshot_shown_variant_finds_the_mode_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_snap_shown.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_snap' / 'build.sh'), str(raw), '-DSNAP_SHOWN'], check=True)
            out = Path(tmp) / 'SNS.BIN'
            runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afsnap.py'), run_name='afsnaps')['main'](str(raw), str(out), True)
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afsnap.py'), run_name='psnaps')
            found = parse['candidates'](parse['load'](str(out)), 'shown')
            self.assertEqual(found, [(0xC237F684, [1, 0]), (0xC18C723C, [1, 0])])   # the trigger itself, then the mode word

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_snapshot_focus_variant_finds_the_style_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_snap_focus.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_snap' / 'build.sh'), str(raw), '-DSNAP_FOCUS'], check=True)
            out = Path(tmp) / 'SNF.BIN'
            runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_afsnap.py'), run_name='afsnapf')['main'](str(raw), str(out), False, True)
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_afsnap.py'), run_name='psnapf')
            found = parse['candidates'](parse['load'](str(out)), 'focus')
            # the style word in the display info, then the trigger itself; the 3-tick blip is dropped
            self.assertEqual(found, [(0xC2016E40, [0x12, 0x11, 0x10]), (0xC25A1E70, [2, 1, 0])])

    @unittest.skipUnless(GCC and unicorn, 'needs arm-none-eabi-gcc and unicorn')
    def test_touchflag_module_sets_only_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'af_touchflag.bin'
            subprocess.run(['sh', str(ROOT / 'modules' / 'af_touchflag' / 'build.sh'), str(raw)], check=True)
            self.assertLess(raw.stat().st_size, 16384)
            out = Path(tmp) / 'TF.BIN'
            self.assertEqual(runpy.run_path(str(ROOT / 'research' / 'af' / 'emulate_aftouchflag.py'), run_name='aftf')['main'](str(raw), str(out)), 2)
            parse = runpy.run_path(str(ROOT / 'research' / 'af' / 'parse_aftouchflag.py'), run_name='ptf')
            h, recs = parse['load'](str(out))
            self.assertEqual((h['sets'], len(recs)), (2, 30))


@unittest.skipUnless(FIRMWARE.exists(), 'needs Z_30_0120.bin')
class AfLogFirmwareTest(unittest.TestCase):
    def test_research_eg_has_host_and_imu_hook_without_gyro_evr(self):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build, resolve_presets
        _, features = resolve_presets('full')
        _, report = build(extract_eg(FIRMWARE.read_bytes()), [f for f in features if f != 'gyro-evr'],
                          module_host=True, imu_hook=True)
        self.assertIn('module-host', report['enabled_features'])
        self.assertIn('imu-hook', report['enabled_features'])
        self.assertNotIn('gyro-evr', report['enabled_features'])
