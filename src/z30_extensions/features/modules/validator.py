"""Target module validation with bounded reads and CRC32."""
from ...thumb import Thumb
from .files import MAX_CODE, MODEL, FIRMWARE, ABI
from ...platform.z30_120 import STOCK_SHA256 as EXPECTED_SHA


def validator_code(address):
    """int validate(bytes *r0, uint32 length r1, Result *r2).

    Success writes [payload pointer, payload size, Thumb entry pointer]. Failure
    returns zero and leaves Result untouched. Buffers must be aligned to four;
    Result must not overlap file bytes. Reads only after length/range checks.
    CRC32 is IEEE reflected, matching the reference container implementation.
    """
    a = Thumb(address)
    a.emit('f0b5 83b0 0446 0d46 1646')
    serial = [0]
    def reject(condition):
        # A short inverted branch around a long branch avoids conditional
        # range overflow as the validation function grows.
        label = 'accept_'+str(serial[0]); serial[0] += 1
        a.branch(condition ^ 1, label); a.branch(14, 'invalid'); a.label(label)
    a.emit('002c'); reject(0)
    a.emit('002e'); reject(0)
    a.emit('402d'); reject(3)  # unsigned length < 64
    a.emit('0320 0442'); reject(1)
    a.emit('0642'); reject(1)
    # Reject pointer addition carry and overlapping output range.
    a.emit('6019'); reject(2)  # end = file + length, carry set
    a.emit('3146 0c31'); reject(2)
    a.emit('8642'); a.branch(2, 'separate')  # out >= end
    a.emit('a142'); reject(8)  # out+12 > file
    a.label('separate')
    constants = [(0, int.from_bytes(b'ZMOD', 'little')), (4, 1),
                 (8, ABI), (12, MODEL), (16, FIRMWARE)]
    constants += [(32+4*i, int.from_bytes(bytes.fromhex(EXPECTED_SHA)[i*4:i*4+4], 'little'))
                  for i in range(8)]
    for offset, value in constants:
        a.emit((0x6800 | ((offset//4) << 6) | (4 << 3)).to_bytes(2, 'little').hex())
        a.load(1, 'constant_'+str(offset), value)
        a.emit('8842'); reject(1)
    a.emit('6769 002f'); reject(0)  # code size at +20
    a.load(0, 'maximum', MAX_CODE); a.emit('8742'); reject(8)
    a.emit('0320 0742'); reject(1)
    a.emit('3846 4030 a842'); reject(1)  # exact length
    a.emit('a069 0121 0842'); reject(1)  # even entry
    a.emit('b842'); reject(2)           # entry < code size
    a.emit('0090 e069 0190 0297')       # entry, expected CRC, size
    a.emit('2046 4030 3946')
    a.load(3, 'initial_crc', 0xffffffff)
    a.load(7, 'polynomial', 0xedb88320)
    a.label('byte'); a.emit('0278 0130 5340 0822')
    a.label('bit'); a.emit('5b08'); a.branch(3, 'no_xor')
    a.emit('7b40')
    a.label('no_xor'); a.emit('013a'); a.branch(1, 'bit')
    a.emit('0139'); a.branch(1, 'byte')
    a.emit('db43 0198 8342'); reject(1)
    a.emit('2046 4030 3060 029f 7760 0099 4018 0121 0843 b060')
    a.emit('0120 03b0 f0bd')
    a.label('invalid'); a.emit('0020 03b0 f0bd')
    return a.finish()
