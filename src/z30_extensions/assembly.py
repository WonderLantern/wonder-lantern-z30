"""Checked assembly helpers for optional Keystone-backed target features."""
import struct


def constant(register, value):
    return f'movw {register}, #{value & 65535}; movt {register}, #{value >> 16};'


def invoke(address, thumb=True):
    return constant('r12', address | 1 if thumb else address & ~1) + ' blx r12;'


def jump(site, target, length=8):
    if site % 4 == 0:
        code = bytes.fromhex('dff800f0') + struct.pack('<I', target | 1)
    else:
        code = bytes.fromhex('dff804f0 00bf') + struct.pack('<I', target | 1)
    if length < len(code) or (length - len(code)) % 2:
        raise ValueError('Jump does not fit the displaced instruction window')
    return code + bytes.fromhex('00bf') * ((length - len(code)) // 2)


def assemble(source, address, capacity):
    try:
        from keystone import KS_ARCH_ARM, KS_MODE_THUMB, KS_MODE_V8, Ks
    except ImportError as error:
        if isinstance(error, ModuleNotFoundError) and error.name == 'keystone':
            message = 'Keystone is not installed; run python -m pip install ".[sound]".'
        else:
            message = ('Keystone is installed but failed to load; check its native library '
                       f'and Python compatibility (see README). Original error: {error}')
        raise ValueError(message) from error
    raw, _ = Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_V8).asm(source, addr=address, as_bytes=True)
    if raw is None or len(raw) > capacity:
        raise ValueError(f'Assembled function exceeds its {capacity}-byte slot')
    return bytes(raw)
