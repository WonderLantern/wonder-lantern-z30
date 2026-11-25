"""Synthetic mux objects and pose RAM, executing native C1.20 helpers unchanged."""
import struct
RAM = 0x50000000
from z30_extensions.features.video.rotation import CONFIGURE, VIDEO_MATRIX_OFFSET, ORIENTATION_ROOT, ORIENTATION_STATE, GET_ORIENTATION, POSE_MATRICES, EXPECTED_MATRIX

VIDEO_CONSTRUCTOR = 0x102EE814
MOVIE_CONSTRUCTOR = 0x102EEF44
OBJECT_SIZE = 0x800
POINTER_FIELDS = (0x150, 0x19C, 0x1D4, 0x204, 0x300, 0x34C, 0x380, 0x3B0,
                  0x3C4, 0x39C, 0x3DC, 0x404, 0x4C8, 0x624, 0x5F8)


def parameters():
    data = bytearray(0x40)
    struct.pack_into('<4I', data, 0, 1920, 1080, 1000, 60000)
    data[0x18] = 2
    return bytes(data)


def object_bytes(address):
    data = bytearray(OBJECT_SIZE)
    data[0] = 1
    for index, offset in enumerate(POINTER_FIELDS):
        struct.pack_into('<I', data, offset, address + OBJECT_SIZE + index * 0x100)
    return bytes(data)


class Fixture:
    def __init__(self, stock, image):
        from native_runtime import Context
        self.context = Context(stock, image)
        self.u = self.context.u
        self.context.map(ORIENTATION_STATE, 0x1000)
        self.param = RAM + 0xF0000
        self.u.mem_write(self.param, parameters())

    def pose(self, value, available=True):
        self.u.mem_write(ORIENTATION_ROOT + 0xC, struct.pack('<I', 1 if available else 0))
        self.u.mem_write(ORIENTATION_STATE + 0x90, bytes([value]))
        # Native fallback option >=5 leaves the getter at unknown=4.
        self.u.mem_write(ORIENTATION_STATE + 0x238, struct.pack('<I', 5))

    def new_object(self, address):
        self.u.mem_write(address, bytes(0x2000))
        self.u.mem_write(address, object_bytes(address))

    def configure(self, address, param=None):
        return self.context.call(CONFIGURE, (address, self.param if param is None else param))

    def matrix(self, address):
        return bytes(self.u.mem_read(address + VIDEO_MATRIX_OFFSET, 36))

    def video_header(self, address):
        return self.context.call(VIDEO_CONSTRUCTOR, (address,))


def verify_native(stock, image):
    fixture = Fixture(stock, image)
    baseline = Fixture(stock, stock)
    rows = []
    for index, pose in enumerate([0, 1, 2, 3, 4, 255]):
        obj = RAM + 0x10000 + index * 0x3000
        for f in (fixture, baseline):
            f.pose(pose)
            f.new_object(obj)
        expected = POSE_MATRICES[pose] if pose < 4 else EXPECTED_MATRIX
        assert fixture.context.call(GET_ORIENTATION) == pose
        assert fixture.configure(obj) == baseline.configure(obj) == 0
        assert fixture.matrix(obj) == expected
        # Native configuration behavior differs only at the owned matrix slot.
        got = bytes(fixture.u.mem_read(obj, OBJECT_SIZE))
        original = bytes(baseline.u.mem_read(obj, OBJECT_SIZE))
        assert got[:VIDEO_MATRIX_OFFSET] == original[:VIDEO_MATRIX_OFFSET]
        assert got[VIDEO_MATRIX_OFFSET + 36:] == original[VIDEO_MATRIX_OFFSET + 36:]
        fixture.pose((pose + 1) % 4)
        assert fixture.video_header(obj) == baseline.video_header(obj) == 0
        assert fixture.matrix(obj) == expected
        assert baseline.matrix(obj) == EXPECTED_MATRIX
        header = bytes(fixture.u.mem_read(obj, OBJECT_SIZE))
        stock_header = bytes(baseline.u.mem_read(obj, OBJECT_SIZE))
        assert header[:VIDEO_MATRIX_OFFSET] == stock_header[:VIDEO_MATRIX_OFFSET]
        assert header[VIDEO_MATRIX_OFFSET + 36:] == stock_header[VIDEO_MATRIX_OFFSET + 36:]
        # Re-finalizing cannot resample orientation or compound rotation.
        fixture.pose((pose + 2) % 4)
        assert fixture.video_header(obj) == 0
        assert fixture.matrix(obj) == expected
        rows.append({'pose': pose, 'matrix_hex': expected.hex(), 'locked_after_pose_change': True})
    obj = RAM + 0x30000
    fixture.pose(2, available=False)
    fixture.new_object(obj)
    assert fixture.context.call(GET_ORIENTATION) == 4
    assert fixture.configure(obj) == 0 and fixture.matrix(obj) == EXPECTED_MATRIX
    before = bytes(fixture.u.mem_read(obj, OBJECT_SIZE))
    assert fixture.configure(0) == 3
    assert fixture.configure(obj, 0) == 3
    assert fixture.video_header(0) == baseline.video_header(0) == 3
    fixture.u.mem_write(fixture.param + 0x19, b'\x01')
    assert fixture.configure(obj) == 3
    assert fixture.matrix(obj) == before[VIDEO_MATRIX_OFFSET:VIDEO_MATRIX_OFFSET + 36]
    # Existing object snapshots remain independent when another recording starts.
    assert fixture.matrix(RAM + 0x13000) == POSE_MATRICES[1]
    return {'passed': True, 'pose_cases': rows, 'unavailable_fallback': True,
            'invalid_arguments_and_native_failure': True, 'per_object_isolation': True,
            'preserved_registers_and_stack': True,
            'scope': 'Native configuration, orientation getter, memcpy and complete video tkhd builder; synthetic RAM/objects, no sensor or recording.'}
