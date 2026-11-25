"""Replay native file geometry with a fixed file and changing shooting profiles.

Synthetic parsed file records are explicit fixtures. Native preview scaling,
metadata queries, buffer preparation and exact-dimension fitting execute ARM.
This is not a JPEG decoder, physical playback or application-lifecycle model.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def verify(stock, image, report, previous, previous_report):
    from native_runtime import Context
    from verify_sound_settings import Fixture
    from z30_extensions.features.aspect.ui import FILE_GEOMETRY_CALLERS
    from unicorn import UC_PROT_READ, UC_PROT_EXEC, UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0

    def fixture(binary, metadata):
        c = (Fixture(stock, binary, metadata, {}, properties={0x501: 2})
             if metadata else Context(stock, binary))
        c.u.mem_protect(0x10020000, (len(binary)+4095)&~4095, UC_PROT_READ | UC_PROT_EXEC)
        return c

    native, current, old = (fixture(stock, None), fixture(image, report),
                            fixture(previous, previous_report))
    obj, context, info, pair = (0x50080000, 0x50081000, 0x50090000, 0x50098000)

    def run(c, metadata, profile, area, preview_index, processing):
        if metadata:
            c.u.mem_write(c.state+metadata['features']['aspect_profiles']['state_offset'],
                          struct.pack('<I', profile))
        c.u.mem_write(obj, bytes(0x100))
        c.u.mem_write(context, bytes(0x1000))
        c.u.mem_write(info, bytes(0x6000))
        c.u.mem_write(obj, struct.pack('<I', context))
        c.u.mem_write(obj+5, b'\x01')
        c.u.mem_write(context+0x3a4, struct.pack('<I', info))
        c.u.mem_write(info+0x208, bytes([area]))
        c.u.mem_write(info+0x226, bytes([preview_index]))
        c.u.mem_write(info+0x228, bytes([processing]))
        c.u.mem_write(info+0x28d4, struct.pack('<I', 1200))
        c.u.mem_write(info+0x28dc, struct.pack('<I', 800))
        c.u.mem_write(pair, bytes([0, 0]))
        c.call(0x1038fa8c, (obj, 1, pair))
        scaled = list(struct.unpack('<2I', c.u.mem_read(obj+16, 8)))
        queries = []
        for kind in range(4):
            pointer = c.call(0x10221ae2, (info, kind, 0))
            queries.append(list(struct.unpack('<2H', c.u.mem_read(pointer, 4))))
        c.u.mem_write(obj, bytes(0x100))
        c.call(0x10342612, (obj, info))
        buffer = bytes(c.u.mem_read(obj, 0x70)).hex()
        return dict(scaled=scaled, queries=queries, buffer=buffer)

    cases, reproduced = [], []
    for area in (0, 2, 4):
        for preview_index in range(5):
            for processing in (0, 3):
                expected = run(native, None, 0, area, preview_index, processing)
                for profile in range(8):
                    actual = run(current, report, profile, area, preview_index, processing)
                    before = run(old, previous_report, profile, area, preview_index, processing)
                    assert actual == expected, (area, preview_index, processing, profile, actual, expected)
                    row = dict(area=area, preview_index=preview_index, processing=processing,
                               current_profile=profile, scaled=actual['scaled'], queries=actual['queries'])
                    cases.append(row)
                    if before != expected:
                        reproduced.append(dict(**row, previous_scaled=before['scaled'],
                                               previous_queries=before['queries'],
                                               buffer_changed=before['buffer'] != expected['buffer']))
    assert reproduced, 'Previous artifact must reproduce cross-profile contamination'
    # Exact per-file width/height fit, including custom photographs whose
    # aspect is not representable by a stock area enum. Keep this native path.
    fits = []
    shapes = [(5568,3712), (5568,3128), (4952,3712), (4640,3712),
              (5568,2784), (5568,2368), (5568,2328), (5568,2056)]
    for width, height in shapes:
        expected = None
        for profile in range(7):
            current.u.mem_write(current.state+report['features']['aspect_profiles']['state_offset'],
                                 struct.pack('<I', profile))
            current.u.mem_write(obj, bytes(0x100))
            current.u.mem_write(obj+12, struct.pack('<I', context))
            current.u.mem_write(context+0x398, struct.pack('<2H', width, height))
            current.call(0x10340a0c, (obj, 0, 0))
            wh = list(struct.unpack('<2H', current.u.mem_read(context+0x39c, 4)))
            if expected is None: expected = wh
            assert wh == expected
            assert abs(wh[0]/wh[1] - width/height) < .015
            fits.append(dict(file_wh=[width,height], current_profile=profile, fitted_wh=wh))
    # Every guarded return address must be a real native BL to a selector,
    # and must return the stock pointer, even if profile and user-area match.
    guarded = []
    targets = {0x1020bfda,0x1020bfc8,0x1020c05c,0x1020c072,
               0x1020c07c,0x1020c0d2,0x1020c128}
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    for caller in FILE_GEOMETRY_CALLERS:
        address = caller-5
        inst = next(decoder.disasm(stock[address-0x10020000:address-0x10020000+4], address))
        assert inst.mnemonic == 'bl' and int(inst.op_str.removeprefix('#'),16) in targets
        target = int(inst.op_str.removeprefix('#'),16)
        args = (2,0) if target == 0x1020bfda else (0,2,0,0)
        wanted = native.call(target, args)
        for profile in range(1,7):
            current.u.mem_write(current.state+report['features']['aspect_profiles']['state_offset'],
                                 struct.pack('<I', profile))
            from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
            for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),args):
                current.u.reg_write(reg,value)
            # The complete functions above already cached these continuations;
            # stop at the instruction boundary, including cached TBs.
            point=caller & ~1
            hook=current.u.hook_add(UC_HOOK_CODE,lambda u,a,n,d:u.emu_stop(),begin=point,end=point)
            try: current.block(address, point)
            finally: current.u.hook_del(hook)
            assert current.u.reg_read(UC_ARM_REG_R0) == wanted, (hex(caller),profile)
        guarded.append(hex(caller))
    return dict(image_sha256=hashlib.sha256(image).hexdigest(),
                previous_sha256=hashlib.sha256(previous).hexdigest(),
                file_geometry_cases=cases, previous_regressions=reproduced,
                exact_file_fit_cases=fits, native_guarded_callers=guarded,
                parsed_file_records_are_fixtures=True, physical_playback_verified=False,
                jpeg_decoding_verified=False, liveview_mask_context_verified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','image','report','previous-image','previous-report','output','deps'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    result=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),
                  json.loads(a.report.read_text()),a.previous_image.read_bytes(),
                  json.loads(a.previous_report.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('PASS',len(result['file_geometry_cases']),'file geometry cases;',
          len(result['previous_regressions']),'previous failures reproduced;',
          len(result['exact_file_fit_cases']),'exact file fits')


if __name__=='__main__':main()
