import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from z30_extensions import gui, sound_wav
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

    def test_selected_presets(self):
        self.assertEqual(gui.selected_presets({'full': True, 'm900': True}), ['full'])
        self.assertEqual(gui.selected_presets({'full': True, 'date-stamp': True}), ['full'])
        self.assertEqual(gui.OPT_IN_PRESETS, ('nef-uncompressed', 'nef-compressed', 'battery-percent'))
        self.assertEqual(gui.selected_presets({'full': True, 'portrait-ui': True}), ['full'])
        self.assertEqual(gui.selected_presets({'full': True, 'nef-menu': True}), ['full'])
        self.assertEqual(gui.selected_presets({'full': True, 'nef-compressed': True}), ['full', 'nef-compressed'])
        self.assertEqual(gui.selected_presets({'nef-menu': True, 'm900': True}), ['m900', 'nef-menu'])
        self.assertEqual(gui.selected_presets({'wifi-5ghz': True, 'm900': True}), ['m900', 'wifi-5ghz'])
        self.assertEqual(gui.selected_presets({}), [])

    def test_next_free_kit(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            self.assertEqual(gui.next_free_kit(folder).name, 'card')
            (folder / 'card').mkdir()
            self.assertEqual(gui.next_free_kit(folder).name, 'card-2')

    def test_output_problem(self):
        with tempfile.TemporaryDirectory() as folder:
            kit = Path(folder) / 'card'
            self.assertIsNone(gui.output_problem('loader', kit))
            # A card file needs no loader folder: it is built for this program's loader EG.
            self.assertIsNone(gui.output_problem('patch', kit))
            self.assertEqual(gui.has_resident(kit), 'none')
            (kit / 'Z30').mkdir(parents=True)
            (kit / 'Z30' / 'WL.PATCH').write_bytes(b'')
            self.assertIsNone(gui.output_problem('loader', kit))
            (kit / 'EG209020.BIN').write_bytes(b'')
            self.assertEqual(gui.output_problem('loader', kit)[0], 'kit_exists')
            (kit / 'Z30' / 'WL.PATCH').rename(kit / 'old.PATCH')
            self.assertEqual(gui.output_problem('patch', kit)[0], 'half_resident')
            (kit / 'old.PATCH').rename(kit / 'Z30' / 'WL.PATCH')
            (kit / 'EG209020.json').write_text('')
            self.assertEqual(gui.has_resident(kit), 'both')
            self.assertEqual(gui.output_problem('patch', kit)[0], 'kit_exists')
            (kit / 'Z30' / 'WL.PATCH').unlink()
            self.assertIsNone(gui.output_problem('patch', kit))
            self.assertEqual(gui.latest_kit(Path(folder)), kit)

    def test_every_preset_has_titles(self):
        for language in ('zh', 'en'):
            self.assertEqual(set(gui.PRESET_TITLES[language]), set(gui.PRESETS))

    def test_every_preset_has_english_label(self):
        self.assertEqual(set(gui.PRESET_LABELS_EN), set(gui.PRESETS))

    def test_run_build_always_uses_the_menu_tab(self):
        kit = dict(written=[], resident_sha256='x', resident=SimpleNamespace(identity=1), patch_bytes=1,
                   resident_from='built')
        for mode in gui.MODES:
            with patch('z30_extensions.card.write_kit', return_value=kit) as write_kit:
                gui.run_build(mode, Path('eg.bin'), ['focal-length'], Path('out'))
            self.assertIs(write_kit.call_args.kwargs['menu_tab'], True)
            self.assertIs(write_kit.call_args.kwargs['rebuild_resident'], mode == 'patch')

    def test_patch_folder_skips_used_kits(self):
        with tempfile.TemporaryDirectory() as folder:
            api = gui.Api()
            api._base = Path(folder)
            self.assertEqual(api.patch_folder().name, 'card')
            kit = Path(folder) / 'output' / 'card'
            kit.mkdir(parents=True)
            (kit / 'EG209020.BIN').write_bytes(b'')
            (kit / 'EG209020.json').write_text('')
            self.assertEqual(api.patch_folder(), kit)
            (kit / 'Z30').mkdir()
            (kit / 'Z30' / 'WL.PATCH').write_bytes(b'')
            self.assertEqual(api.patch_folder().name, 'card-2')

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

    def test_sound_wav_encode_validate_and_examples(self):
        pcm = b'\x00\x00\xff\x7f\x00\x80'
        wav = sound_wav.encode_canonical_wav(pcm)
        self.assertEqual(len(wav), 44 + len(pcm))
        self.assertEqual(sound_wav.validate_canonical_wav(wav), len(pcm))
        with self.assertRaises(ValueError):
            sound_wav.encode_canonical_wav(b'')
        with self.assertRaises(ValueError):
            sound_wav.encode_canonical_wav(b'\x00')
        with self.assertRaises(ValueError):
            sound_wav.encode_canonical_wav(b'\x00' * (sound_wav.MAX_DATA_BYTES + 2))
        with self.assertRaises(ValueError):
            sound_wav.validate_canonical_wav(wav[:24] + b'\x44\xac\x00\x00' + wav[28:])
        for slot in (1, 2, 3, 4):
            ex = sound_wav.example_wav(slot)
            self.assertGreater(sound_wav.validate_canonical_wav(ex), 0)

    def test_api_sound_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            api = gui.Api()
            api._base = Path(folder)
            init = api.initial()
            self.assertIn('sound', init['outputs'])

            ex = api.example_sound(2)
            self.assertTrue(ex['ok'])
            raw_wav = base64.b64decode(ex['data_b64'])
            self.assertGreater(sound_wav.validate_canonical_wav(raw_wav), 0)
            self.assertFalse(api.example_sound(9)['ok'])

            pcm = raw_wav[44:]
            pcm_b64 = base64.b64encode(pcm).decode('ascii')
            out_dir = Path(folder) / 'sound_out'

            # Save to root slot SHUTTER.WAV
            r1 = api.save_sound(str(out_dir), 'root', pcm_b64, overwrite=False)
            self.assertTrue(r1['ok'])
            self.assertEqual(r1['rel'], 'SHUTTER.WAV')
            self.assertEqual((out_dir / 'SHUTTER.WAV').read_bytes(), raw_wav)

            # Refuse overwrite by default
            r2 = api.save_sound(str(out_dir), 'root', pcm_b64, overwrite=False)
            self.assertFalse(r2['ok'])
            self.assertEqual(r2['key'], 'sound_exists')

            # Allow overwrite when requested
            r3 = api.save_sound(str(out_dir), 'root', pcm_b64, overwrite=True)
            self.assertTrue(r3['ok'])

            # Save to numbered slot SHUTTER/3.WAV
            r4 = api.save_sound(str(out_dir), '3', pcm_b64, overwrite=False)
            self.assertTrue(r4['ok'])
            self.assertEqual(r4['rel'], 'SHUTTER/3.WAV')
            self.assertEqual((out_dir / 'SHUTTER' / '3.WAV').read_bytes(), raw_wav)

            # Error cases
            self.assertEqual(api.save_sound('', 'root', pcm_b64)['key'], 'need_folder')
            self.assertEqual(api.save_sound(str(out_dir), '9', pcm_b64)['key'], 'sound_failed')
            self.assertEqual(api.save_sound(str(out_dir), '1', '!!!not-b64!!!')['key'], 'sound_failed')


if __name__ == '__main__':
    unittest.main()
