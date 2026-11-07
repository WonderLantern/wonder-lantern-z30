import struct

MODE_UNCOMPRESSED = 0
MODE_LOSSLESS = 1
MODE_COMPRESSED = 2

PRESET_MODES = {
    'nef-uncompressed': MODE_UNCOMPRESSED,
    'nef-compressed': MODE_COMPRESSED,
}


def load_immediate(register, value):
    if not 0 <= register <= 7 or not 0 <= value <= 255:
        raise ValueError('Unsupported immediate load')
    return struct.pack('<HH', 0xF240, (register << 8) | value)


def small_immediate(register, value):
    if not 0 <= register <= 7 or not 0 <= value <= 255:
        raise ValueError('Unsupported immediate load')
    return struct.pack('<H', 0x2000 | (register << 8) | value)


SITES = (
    (0x101E77B8, bytes.fromhex('c178'), small_immediate, 1,
     'source: image parameter record byte 3 into the live setting'),
    (0x10143468, bytes.fromhex('95f82c02'), load_immediate, 0,
     'capture config builder: mode byte into the pipeline config'),
    (0x101434B6, bytes.fromhex('95f82c72'), load_immediate, 7,
     'capture config builder: mode byte into the file size table index'),
    (0x101536A0, bytes.fromhex('90f85a12'), load_immediate, 1,
     'live setting to file parameters, shot path'),
    (0x10153A06, bytes.fromhex('95f85a02'), load_immediate, 0,
     'live setting to pipeline request packet'),
    (0x1015F956, bytes.fromhex('90f85a12'), load_immediate, 1,
     'live setting to file parameters, alternate path'),
)


def add(plan, preset):
    if preset not in PRESET_MODES:
        raise ValueError(f'Unknown NEF compression preset: {preset}')
    mode = PRESET_MODES[preset]
    for address, expected, encode, register, name in SITES:
        plan.edit(address, encode(register, mode), f'Force NEF compression mode {mode}: {name}', expected)
    plan.features['nef_compression'] = {
        'preset': preset,
        'mode': mode,
        'mode_names': {'0': 'uncompressed', '1': 'lossless compressed', '2': 'compressed'},
        'sites': [hex(address) for address, _, _, _, _ in SITES],
        'revision': 2,
        'menu_entry_added': False,
        'stored_setting_unchanged': True,
        'camera_verified': False,
    }
