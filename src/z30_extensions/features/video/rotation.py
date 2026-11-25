"""Latch native orientation when a new movie mux context is configured.

The native video tkhd matrix slot owns the snapshot for that context. Header
finalization copies from it instead of sampling the camera at stop time.
The shared stock matrix, mvhd and audio tracks stay unchanged.
https://github.com/WonderLantern/wonder-lantern-z30/issues/3
"""
from ...assembly import assemble, constant, invoke, jump

MATRIX_ADDRESS = 0x102EDE28
EXPECTED_MATRIX = bytes.fromhex(
    '00010000 00000000 00000000'
    '00000000 00010000 00000000'
    '00000000 00000000 40000000')
ROT90_MATRIX = bytes.fromhex(
    '00000000 ffff0000 00000000'
    '00010000 00000000 00000000'
    '00000000 00000000 40000000')


ROT180_MATRIX = bytes.fromhex(
    'ffff0000 00000000 00000000'
    '00000000 ffff0000 00000000'
    '00000000 00000000 40000000')
ROT270_MATRIX = bytes.fromhex(
    '00000000 00010000 00000000'
    'ffff0000 00000000 00000000'
    '00000000 00000000 40000000')

# Native pose IDs group 0/3 as landscape and 1/2 as portrait. Physical side
# mapping must be checked on the camera; do not infer it from simulated RAM.
POSE_DEGREES = (0, 90, 270, 180)
POSE_MATRICES = (EXPECTED_MATRIX, ROT90_MATRIX, ROT270_MATRIX, ROT180_MATRIX)
GET_ORIENTATION = 0x1021B5F6
ORIENTATION_PREFIX = bytes.fromhex('ec48c16804200129')
ORIENTATION_ROOT = 0xC1807DDC
ORIENTATION_STATE = 0xC1D41318
CONFIGURE = 0x102EF402
CONFIGURE_PREFIX = bytes.fromhex('2de9f041f8b1f1b10a68')
CONFIGURE_RESUME = CONFIGURE + len(CONFIGURE_PREFIX)
CONFIGURE_ERROR = 0x102EF448
VIDEO_MATRIX_COPY = 0x102EE81E
VIDEO_COPY_PREFIX = bytes.fromhex('aff6f8110ba8abf6dae9')
VIDEO_COPY_RESUME = VIDEO_MATRIX_COPY + len(VIDEO_COPY_PREFIX)
VIDEO_MATRIX_OFFSET = 0x109
MEMCPY = 0x10199CF0  # Alignment-aware ARM copy; tkhd slot is byte-aligned.
CONFIGURE_CAPACITY = 192
COPY_CAPACITY = 64


def configure_wrapper(address, matrices):
    return assemble(f'''
        push {{r3-r7,lr}}; mov r4,r0; bl body; mov r6,r0;
        cmp r6,#0; bne done;
        {invoke(GET_ORIENTATION)}
        cmp r0,#3; bls known; movs r0,#0;
        known: movs r1,#36; mul r0,r0,r1;
        {constant('r1', matrices)} add r1,r0;
        addw r0,r4,#{VIDEO_MATRIX_OFFSET}; movs r2,#36;
        {invoke(MEMCPY, thumb=False)}
        done: mov r0,r6; pop {{r3-r7,pc}};
        body: push {{r4-r8,lr}};
        cbz r0,invalid; cbz r1,invalid; ldr r2,[r1];
        {constant('r12', CONFIGURE_RESUME | 1)} bx r12;
        invalid: {constant('r12', CONFIGURE_ERROR | 1)} bx r12;
    ''', address, CONFIGURE_CAPACITY)


def copy_wrapper(address):
    # Native 160-byte frame, r4=context and r2=36 already exist at entry.
    return assemble(f'''
        cbz r4,stock; addw r1,r4,#{VIDEO_MATRIX_OFFSET}; b copy;
        stock: {constant('r1', MATRIX_ADDRESS)}
        copy: add r0,sp,#0x2c;
        {invoke(MEMCPY, thumb=False)}
        {constant('r12', VIDEO_COPY_RESUME | 1)} bx r12;
    ''', address, COPY_CAPACITY)


def add(plan):
    if plan.image.read(MATRIX_ADDRESS, 36) != EXPECTED_MATRIX:
        raise ValueError('Unexpected stock video matrix')
    if plan.image.read(GET_ORIENTATION, len(ORIENTATION_PREFIX)) != ORIENTATION_PREFIX:
        raise ValueError('Unexpected native orientation getter')
    matrices = plan.alloc(b''.join(POSE_MATRICES))
    configure = plan.alloc(configure_wrapper(plan.address, matrices))
    copier = plan.alloc(copy_wrapper(plan.address))
    plan.edit(CONFIGURE, jump(CONFIGURE, configure, len(CONFIGURE_PREFIX)),
              'Latch movie orientation after native video configuration', CONFIGURE_PREFIX)
    plan.edit(VIDEO_MATRIX_COPY, jump(VIDEO_MATRIX_COPY, copier, len(VIDEO_COPY_PREFIX)),
              'Copy latched orientation into video tkhd', VIDEO_COPY_PREFIX)
    plan.features['video_rot'] = {
        'mode': 'native orientation latched per new movie mux context',
        'automatic_orientation': True, 'metadata_only': True,
        'pose_degrees': list(POSE_DEGREES), 'unknown_pose_degrees': 0,
        'orientation_getter': hex(GET_ORIENTATION),
        'configure_hook': hex(configure | 1), 'copy_hook': hex(copier | 1),
        'matrix_table': hex(matrices), 'matrix_slot_offset': hex(VIDEO_MATRIX_OFFSET),
        'state_owner': 'existing video tkhd matrix bytes inside each mux context',
        'shared_matrix_unchanged': True, 'mvhd_and_audio_unchanged': True,
        'camera_verified': False, 'pose_direction_device_verified': False,
    }
