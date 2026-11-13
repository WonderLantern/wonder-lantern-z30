import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from z30_extensions.build import PRESETS, resolve_presets
from z30_extensions.cli import choose_presets, main


class PresetTests(unittest.TestCase):
    def test_wifi_5ghz_is_in_full_and_composes_without_duplicates(self):
        self.assertIn('wifi-5ghz', PRESETS)
        self.assertEqual(resolve_presets('wifi-5ghz')[1], ['wifi-5ghz'])
        self.assertIn('wifi-5ghz', resolve_presets('full')[1])
        self.assertEqual(resolve_presets(['wifi-5ghz', 'full', 'wifi-5ghz'])[1],
                         resolve_presets('full')[1])
        with patch('builtins.input', return_value='10'), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['wifi-5ghz'])

    def test_date_stamp_is_in_full_and_brings_the_menu_framework(self):
        self.assertIn('date-stamp', PRESETS)
        self.assertIn('date-stamp', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('date-stamp')[1], ['shutter-sound', 'date-stamp'])
        self.assertEqual(resolve_presets(['full', 'date-stamp'])[1], resolve_presets('full')[1])
        with patch('builtins.input', return_value='11'), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['date-stamp'])

    def test_photo_zebra_is_in_full_and_composes_without_duplicates(self):
        self.assertIn('photo-zebra', PRESETS)
        self.assertIn('photo-zebra', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('photo-zebra')[1], ['shutter-sound', 'photo-zebra'])
        self.assertEqual(resolve_presets(['full', 'photo-zebra', 'photo-zebra'])[1],
                         resolve_presets('full')[1])

    def test_aspect_profiles_is_in_full_and_composes_without_duplicates(self):
        self.assertIn('aspect-profiles', PRESETS)
        self.assertIn('aspect-profiles', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('aspect-profiles')[1], ['shutter-sound', 'aspect-profiles'])
        self.assertEqual(resolve_presets(['aspect-profiles','full'])[1],
                         resolve_presets('full')[1])
    def test_module_host_is_not_a_patcher_preset(self):
        self.assertNotIn('module-host', PRESETS)
        self.assertNotIn('modular', PRESETS)
        for selection in ('module-host', ['full', 'module-host']):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                resolve_presets(selection)
        self.assertNotIn('module-host', resolve_presets('full')[1])

    def test_aliases_and_duplicates_have_stable_order(self):
        selected, features = resolve_presets(['shutter-sound', 'full', 'm900', 'full'])
        self.assertEqual(selected, ['shutter-sound', 'full', 'm900'])
        self.assertEqual(features, ['liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'video-rot',
                          'bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'wifi-5ghz', 'date-stamp', 'portrait-ui'])
        self.assertEqual(resolve_presets(['modular', 'full'])[1], features)

    def test_full_enables_all_features_and_modular_keeps_its_scope(self):
        self.assertEqual(resolve_presets('full')[1],
                         ['liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'video-rot',
                          'bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'wifi-5ghz', 'date-stamp', 'portrait-ui'])
        self.assertEqual(resolve_presets('modular')[1],
                         ['liveview-dual', 'm900', 'shutter-sound'])
        self.assertEqual(resolve_presets(['full', 'mtp-fast', 'modular'])[1],
                         resolve_presets('full')[1])

    def test_independent_combination(self):
        self.assertEqual(resolve_presets(['m900', 'liveview-dual'])[1],
                         ['liveview-dual', 'm900'])
        self.assertEqual(resolve_presets(['mtp-fast', 'full'])[1],
                         ['liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'video-rot',
                          'bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'wifi-5ghz', 'date-stamp', 'portrait-ui'])

    def test_portrait_ui_is_in_full_and_brings_the_menu_framework(self):
        self.assertIn('portrait-ui', PRESETS)
        self.assertEqual(resolve_presets('full')[1][-1], 'portrait-ui')
        self.assertEqual(resolve_presets('portrait-ui')[1], ['shutter-sound', 'portrait-ui'])
        self.assertEqual(resolve_presets(['portrait-ui', 'full'])[1][-1], 'portrait-ui')

    def test_invalid_and_empty_selection(self):
        for selection in ([], 'unknown', ['full', 'unknown']):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                resolve_presets(selection)

    def test_video_rot_is_in_full_and_composes_in_stable_order(self):
        self.assertIn('video-rot', resolve_presets()[1])
        self.assertNotIn('video-rot', resolve_presets('modular')[1])
        self.assertEqual(resolve_presets('video-rot')[1], ['video-rot'])
        self.assertEqual(resolve_presets(['video-rot', 'full', 'video-rot'])[1],
                         resolve_presets('full')[1])
        self.assertEqual(resolve_presets(['full', 'video-rot'])[1],
                         resolve_presets(['video-rot', 'full'])[1])
        with patch('builtins.input', return_value='6'), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['video-rot'])

    def test_bitrate_slowmo_uncap_is_in_full_and_composes_in_stable_order(self):
        self.assertIn('bitrate-slowmo-uncap', resolve_presets()[1])
        self.assertNotIn('bitrate-slowmo-uncap', resolve_presets('modular')[1])
        self.assertEqual(resolve_presets('bitrate-slowmo-uncap')[1], ['shutter-sound', 'bitrate-slowmo-uncap'])
        self.assertEqual(resolve_presets(['bitrate-slowmo-uncap', 'video-rot'])[1],
                         ['shutter-sound', 'video-rot', 'bitrate-slowmo-uncap'])
        with patch('builtins.input', return_value='7'), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['bitrate-slowmo-uncap'])

    def test_interactive_numbers_names_and_retry(self):
        with patch('builtins.input', side_effect=['0', '2, m900 2']), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['liveview-dual', 'm900'])
        with patch('builtins.input', return_value=''), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(choose_presets(), ['full'])

    def test_cli_combination_and_noninteractive_default(self):
        for selection in ([], ['liveview-dual', 'm900'], ['date-stamp'], ['video-rot'], ['full', 'video-rot']):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'stock.bin'
                output = Path(folder) / 'EG209020.BIN'
                source.write_bytes(b'stock')
                with patch('sys.stdin.isatty', return_value=False), \
                     patch('z30_extensions.cli.extract_eg', return_value=b'eg'), \
                     patch('z30_extensions.cli.build', return_value=(b'patched', {'sha256': 'test'})) as builder, \
                     patch('sys.stdout', new=io.StringIO()):
                    self.assertEqual(main(selection + ['--source', str(source), '--output', str(output)]), 0)
                builder.assert_called_once_with(b'eg', selection or ['full'])
                self.assertEqual(output.read_bytes(), b'patched')
                self.assertEqual(json.loads(output.with_suffix('.json').read_text())['input_kind'], 'stock_container')

    def test_cli_interactive_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'stock.bin'
            source.write_bytes(b'eg')
            with patch('sys.stdin.isatty', return_value=True), \
                 patch('builtins.input', return_value='2 4'), \
                 patch('z30_extensions.cli.build', return_value=(b'patched', {'sha256': 'test'})) as builder, \
                 patch('sys.stdout', new=io.StringIO()):
                main(['--extracted-eg', '--source', str(source), '--output', str(Path(folder) / 'out.bin')])
            builder.assert_called_once_with(b'eg', ['liveview-dual', 'm900'])


if __name__ == '__main__':
    unittest.main()
