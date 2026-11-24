# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Record the selected photo aspect as an Adobe crop in the NEF XMP packet.

Issue 38: Adobe rejects non-stock RAW planes and ignores Nikon CropArea, but
applies crs: crop settings from the XMP packet embedded in IFD0 (checked in
ACR with a header-edited stock Z30 NEF). The stock camera-raw-defaults (crd:)
writer 0x101B862E fills a bounded text buffer {cursor, remaining, overflow}
that the camera embeds in that packet. After its closing </rdf:Description>,
append one rdf:Description with the crs: crop of the selected profile.

Only when the buffer has room: on overflow the stock code discards the whole
crd block, so a full buffer keeps the stock text without a crop.
"""
from ..assembly import constant as c, invoke as n, jump

CLOSE_SITE = 0x101B86B4
CLOSE_EXPECTED = 'd3a1384600f008f8'  # adr r1,closer; mov r0,r7; bl append
CLOSE_TEXT = 0x101B8A04               # '</rdf:Description>'
APPEND = 0x101B86CC                   # printf-style append(buffer, format, ...)
CONTINUE = 0x101B86BC
AREA_KEY = 0x501
CRS = 'http://ns.adobe.com/camera-raw-settings/1.0/'

# Adobe normalizes the crop to its default crop of the stock RAW plane:
# 5568x3712 for DX, 5568x3128 for 16:9 (the camera's own CropArea sizes).
DEFAULT_CROP = {(5600, 3728): (5568, 3712), (5600, 3144): (5568, 3128)}


def crop_box(profile):
    """Normalized (left, top, right, bottom) of the JPEG window in Adobe's frame."""
    width, height = DEFAULT_CROP[tuple(profile['raw_plane_wh'])]
    jpeg_w, jpeg_h = profile['jpeg_wh'][0]
    left = (width - jpeg_w) / 2 / width
    top = (height - jpeg_h) / 2 / height
    return left, top, 1 - left, 1 - top


def crop_xml(profile):
    left, top, right, bottom = crop_box(profile)
    values = (('HasCrop', 'True'), ('CropTop', f'{top:.6f}'), ('CropLeft', f'{left:.6f}'),
              ('CropBottom', f'{bottom:.6f}'), ('CropRight', f'{right:.6f}'),
              ('CropAngle', '0'), ('CropConstrainToWarp', '0'))
    body = ''.join(f'<crs:{k}>{v}</crs:{k}>' for k, v in values)
    text = f'<rdf:Description rdf:about="" xmlns:crs="{CRS}">{body}</rdf:Description>'
    assert '%' not in text  # used directly as the append format string
    return text


def install(plan, functions, codes, sizes, profiles):
    texts = [crop_xml(p) for p in profiles]
    length = len(texts[0])
    assert all(len(t) == length for t in texts)
    pointers = [plan.alloc(t.encode('ascii') + b'\0') for t in texts]
    import struct
    table = plan.alloc(struct.pack('<6I', *pointers))
    sizes['aspect_xmp_crop'] = 192
    functions['aspect_xmp_crop'] = plan.alloc(bytes(192))
    # r7 = stock crd text buffer, r4/r6 live in the stock continuation.
    codes['aspect_xmp_crop'] = f'''mov r0,r7;{c('r1',CLOSE_TEXT)}{n(APPEND)}
        push {{r4-r7}};
        {n(functions['aspect_get'])}cmp r0,#0;beq out;ldr r5,[r0];subs r5,#1;cmp r5,#5;bhi out;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne out;
        ldr r0,[r7,#8];cmp r0,#0;bne out;
        ldr r0,[r7,#4];{c('r1',length+1)}cmp r0,r1;bls out;
        {c('r1',table)}ldr r1,[r1,r5,lsl #2];mov r0,r7;{n(APPEND)}
        out:pop {{r4-r7}};{c('r12',CONTINUE|1)}bx r12;'''
    plan.edit(CLOSE_SITE, jump(CLOSE_SITE, functions['aspect_xmp_crop'], 8),
              'Aspect crop as Adobe crs: settings in the NEF XMP packet', bytes.fromhex(CLOSE_EXPECTED))
    return dict(version=1, site=hex(CLOSE_SITE), writer='0x101b862e', append=hex(APPEND),
                namespace=CRS, text_bytes=length,
                crops={p['name']: [round(v, 6) for v in crop_box(p)] for p in profiles},
                default_crop_assumption={f'{k[0]}x{k[1]}': list(v) for k, v in DEFAULT_CROP.items()},
                acr_header_edit_verified=True, camera_verified=True)
