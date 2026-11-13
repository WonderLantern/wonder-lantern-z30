import unittest
from unittest.mock import patch

from z30_extensions import assembly


class AssemblyCacheTests(unittest.TestCase):
    def test_hit_needs_no_keystone_and_miss_reports_it(self):
        key = assembly.cache_key('nop;', 0x1000)
        with patch.object(assembly, '_cache', {key: b'\x00\xbf'}), patch.dict('sys.modules', {'keystone': None}):
            self.assertEqual(assembly.assemble('nop;', 0x1000, 2), b'\x00\xbf')
            with self.assertRaisesRegex(ValueError, 'Keystone is not installed'):
                assembly.assemble('nop;', 0x1002, 2)

    def test_hit_still_enforces_capacity(self):
        key = assembly.cache_key('nop;', 0x1000)
        with patch.object(assembly, '_cache', {key: b'\x00\xbf'}):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                assembly.assemble('nop;', 0x1000, 1)

    def test_out_of_range_cbz_is_refused(self):
        try:
            import keystone  # noqa: F401
        except Exception:
            self.skipTest('Keystone not available')
        with patch.object(assembly, '_cache', {}):
            far = 'cbz r1,far;' + 'movw r0,#1;' * 40 + 'far:bx lr;'
            with self.assertRaisesRegex(ValueError, 'cannot reach far'):
                assembly.assemble(far, 0x12000000, 512)
            near = 'cbz r1,near;movw r0,#1;near:cbnz r2,end;end:bx lr;'
            self.assertTrue(assembly.assemble(near, 0x12000000, 64))


if __name__ == '__main__':
    unittest.main()
