import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from z30_extensions import gui
from z30_extensions.cli import build_to_files


class GuiHelperTests(unittest.TestCase):
    def test_check_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fw.bin'
            self.assertEqual(gui.check_source(path), 'missing')
            path.write_bytes(b'abc')
            self.assertEqual(gui.check_source(path), 'bad')
            with patch.object(gui, 'CONTAINER_SIZE', 3):
                self.assertEqual(gui.check_source(path), 'bad')
                with patch.object(gui, 'CONTAINER_SHA256', hashlib.sha256(b'abc').hexdigest()):
                    self.assertEqual(gui.check_source(path), 'ok')

    def test_next_free_output_skips_bin_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            self.assertEqual(gui.next_free_output(folder).name, 'EG209020.BIN')
            (folder / 'EG209020.BIN').write_bytes(b'')
            (folder / 'EG209020-2.json').write_text('')
            self.assertEqual(gui.next_free_output(folder).name, 'EG209020-3.BIN')

    def test_selected_presets(self):
        self.assertEqual(gui.selected_presets({'full': True, 'm900': True}), ['full'])
        self.assertEqual(gui.selected_presets({'full': True, 'date-stamp': True}), ['full'])
        self.assertEqual(gui.OPT_IN_PRESETS, ())
        self.assertEqual(gui.selected_presets({'full': True, 'portrait-ui': True}), ['full'])
        self.assertEqual(gui.selected_presets({'wifi-5ghz': True, 'm900': True}), ['m900', 'wifi-5ghz'])
        self.assertEqual(gui.selected_presets({}), [])

    def test_every_preset_has_english_label(self):
        self.assertEqual(set(gui.PRESET_LABELS_EN), set(gui.PRESETS))

    def test_build_to_files_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'eg.bin'
            source.write_bytes(b'eg')
            output = Path(folder) / 'out.BIN'
            output.write_bytes(b'old')
            with patch('z30_extensions.cli.build', return_value=(b'new', {'sha256': 'x'})), \
                    self.assertRaises(FileExistsError):
                build_to_files(source, ['m900'], output, extracted_eg=True)
            self.assertEqual(output.read_bytes(), b'old')
            self.assertFalse(output.with_suffix('.json').exists())


if __name__ == '__main__':
    unittest.main()
