"""Keep one file record alive while MTP builds an object's property list.

Stock GetObjectPropList reads every property through its own accessor, and each
accessor acquires the object's file record, reads one field and releases it.
The release frees the record, so one object costs a directory query per file
property and a header parse per image property. This wrapper holds one extra
reference for the duration of the per-object builder; the stock accessors then
find the live record. The builder's count-only pass reads no property values,
so the wrapper leaves it alone. The release path is unchanged.
"""
from ...assembly import assemble, constant, invoke, jump

BUILDER = 0x1059E1B8
EXPECTED_PREFIX = bytes.fromhex('2de9f05f 9246 8346')
HANDLE_TO_FILE = 0x10336526
ACQUIRE_RECORD = 0x10461E40
RELEASE_RECORD = 0x10461B20
COUNT_ONLY = 0xC29BECE0 + 0x7C
FOLDER_KIND = 2
VIRTUAL_KIND = 7
CAPACITY = 128


def wrapper(address):
    displaced = ','.join(str(value) for value in EXPECTED_PREFIX)
    return assemble(f'''
        push {{r3-r7,lr}}; mov r4,r0; mov r5,r1; mov r6,r2; movs r7,#0;
        lsrs r0,r4,#27; cmp r0,#{FOLDER_KIND}; beq run; cmp r0,#{VIRTUAL_KIND}; beq run;
        {constant('r0', COUNT_ONLY)} ldr r0,[r0]; cmp r0,#0; bne run;
        mov r0,r4; {invoke(HANDLE_TO_FILE)} {invoke(ACQUIRE_RECORD)} mov r7,r0;
        run: mov r0,r4; mov r1,r5; mov r2,r6; bl body; mov r4,r0;
        cmp r7,#0; beq done; mov r0,r7; {invoke(RELEASE_RECORD)}
        done: mov r0,r4; pop {{r3-r7,pc}};
        body: .byte {displaced}; {constant('r12', (BUILDER + len(EXPECTED_PREFIX)) | 1)} bx r12;
    ''', address, CAPACITY)


def add(plan):
    address = plan.alloc(wrapper(plan.address))
    plan.edit(BUILDER, jump(BUILDER, address, len(EXPECTED_PREFIX)),
              'Hold file record across MTP object property list', EXPECTED_PREFIX)
    plan.features['mtp_fast'] = {
        'wrapper': hex(address | 1), 'builder': hex(BUILDER),
        'scope': 'GetObjectPropList per-object builder; GetObjectInfo and GetThumb are stock',
        'skipped_handle_kinds': [FOLDER_KIND, VIRTUAL_KIND],
        'skipped_when_count_only': hex(COUNT_ONLY),
        'camera_verified': False,
    }
