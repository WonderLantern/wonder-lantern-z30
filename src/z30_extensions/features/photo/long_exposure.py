"""Enable native extended M-mode times through a narrow flag-read hook."""
import struct
from ...thumb import Thumb

GETTER = 0x100F303C
KEY = 0x11A3
EXPECTED_PREFIX = bytes.fromhex('38b50c4600212160')


def read_hook(address):
    a = Thumb(address)
    a.load(3, 'extended_m_key', KEY)
    a.emit('9842')
    a.branch(1, 'original')
    a.emit('0120 0860 7047')
    a.label('original')
    a.emit(EXPECTED_PREFIX.hex())
    a.load(3, 'continuation', (GETTER + 8) | 1)
    a.emit('1847')
    return a.finish()


def add(plan):
    address = plan.alloc(read_hook(plan.address))
    jump = bytes.fromhex('004b1847') + struct.pack('<I', address | 1)
    plan.edit(GETTER, jump, 'Read extended M flag as enabled', EXPECTED_PREFIX)
    plan.features['m900'] = {
        'read_hook': hex(address | 1), 'property_key': hex(KEY),
        'maximum_seconds': 900, 'stored_setting_unchanged': True,
        'enabled': 'always-on read override; native M-mode gating remains',
        'physical_900_second_capture_verified': False,
    }
