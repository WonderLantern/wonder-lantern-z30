"""Execute native file-preview preparation with explicit parsed-file fixtures.

The file service is a fixture. This does not execute JPEG hardware or prove
physical playback. Optional NEF input supplies actual embedded JPEG sizes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

from native_runtime import Context, STOP, STACK
from unicorn import UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                              UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_PC,
                              UC_ARM_REG_LR, UC_ARM_REG_SP)
from z30_extensions.features.aspect_raw_playback import DIMENSION_SITES, PACK_CALLERS


OBJ, INFO, META, DESC, OUT = 0x50080000, 0x50081000, 0x50082000, 0x50083000, 0x50084000
REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)


class Fixture(Context):
    def __init__(self, stock, image):
        super().__init__(stock, image)
        self.u.mem_protect(0x10020000, (len(image)+4095)&~4095, UC_PROT_READ|UC_PROT_EXEC)
        self.preview = {4: (640, 236), 5: (1920, 708)}
        self.failure = False
        self.queries = []
        for address in (0x103913C0, 0x103918A8, 0x103918CA, 0x10340C24,
                        0x103408CC, 0x10348212, 0x103A1FA8, 0x10391934,
                        0x10391D9C, 0x10391E94, 0x103A23F0):
            self.u.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)

    def service(self, u, address, size, data):
        r = [u.reg_read(reg) for reg in REGS]
        result = 0
        if address == 0x103A23F0:
            result = 1
        elif address == 0x103408CC:
            u.mem_write(r[1], bytes(4))
            u.mem_write(r[2], struct.pack('<I', META))
            result = 1
        elif address == 0x103A1FA8:
            self.queries.append(r[:3])
            assert r[0] in (4, 5) and r[1] in (1, 2) and r[2] == 0x1234
            if self.failure:
                result = 0x80000001
            else:
                u.mem_write(r[3], struct.pack('<2H', *self.preview[r[0]]))
        u.reg_write(UC_ARM_REG_R0, result)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def prepare(self, wh=(5568,2056), file_type=1, flag=0):
        self.queries.clear()
        for pointer, length in ((OBJ,0x100),(INFO,0x400),(META,0x100),(DESC,0x100),(OUT,0x20)):
            self.u.mem_write(pointer, bytes(length))
        self.u.mem_write(OBJ, struct.pack('<IB', 0x1234, file_type))
        self.u.mem_write(OBJ+12, struct.pack('<I', INFO))
        self.u.mem_write(OBJ+28, struct.pack('<I', DESC))
        self.u.mem_write(INFO+0x398, struct.pack('<2H', *wh))
        # Native area code 10 maps to preview enum 1 (16:9).
        self.u.mem_write(META, struct.pack('<I', 10 << 4))
        self.u.mem_write(META+4, struct.pack('<I', flag))
        self.u.mem_write(META+12, struct.pack('<4H', *wh, 160, 120))
        self.u.mem_write(META+0x16, b'\x01')

    def at_caller(self, target, caller, args, stack_args=()):
        u = self.u
        for reg, value in zip(REGS, args): u.reg_write(reg, value)
        u.reg_write(UC_ARM_REG_R4, OBJ)
        u.reg_write(UC_ARM_REG_SP, STACK+0xF0000)
        u.reg_write(UC_ARM_REG_LR, caller)
        if stack_args: u.mem_write(STACK+0xF0000, struct.pack('<'+'I'*len(stack_args), *stack_args))
        stop = caller & ~1
        hook = u.hook_add(UC_HOOK_CODE, lambda u,a,n,d:u.emu_stop(), begin=stop, end=stop)
        try: u.emu_start(target|1, stop, count=100000)
        finally: u.hook_del(hook)
        assert u.reg_read(UC_ARM_REG_PC) == stop
        assert u.reg_read(UC_ARM_REG_SP) == STACK+0xF0000
        assert u.reg_read(UC_ARM_REG_R4) == OBJ


def verify(stock, image, report, sample=None):
    current, original = Fixture(stock,image), Fixture(stock,stock)
    if sample:
        from inspect_aspect_jpeg import inspect
        streams = inspect(sample.read_bytes())
        assert [(s['width'],s['height']) for s in streams] == [(640,236),(1920,708),(5568,2056)]
    shapes = report['features']['aspect_profiles']['raw_preview_playback']['file_shapes']
    cases = []
    # Full native descriptor producer: original gets stock geometry, patched
    # gets each explicit stream geometry, including legacy custom photographs.
    for wh in shapes + [[5568,3712],[5568,3128],[1234,567]]:
        custom = wh in shapes
        for file_type in (1,2,4):
            for flags in (0,1<<4,1<<11):
                for legacy in (False,True):
                    previews = {4:(640,360),5:(1920,1080)} if legacy else {4:(640,236),5:(1920,708)}
                    outputs=[]
                    for fixture in (original,current):
                        fixture.prepare(wh,file_type,flags)
                        fixture.preview=previews
                        fixture.call(0x103472DE,(OBJ,))
                        outputs.append(bytes(fixture.u.mem_read(DESC,0x90)))
                    if not custom or file_type not in (1,2):
                        assert outputs[0]==outputs[1], (wh,file_type,flags)
                        assert not current.queries
                    else:
                        for index in range(6):
                            kind,enabled,width,height = struct.unpack_from('<B3x3I',outputs[1],index*24)
                            if enabled==1 and kind in (4,5):
                                assert (width,height)==previews[kind], (wh,file_type,flags,index,width,height)
                    cases.append(dict(file_wh=wh,file_type=file_type,flags=flags,legacy=legacy,
                                      changed=outputs[0]!=outputs[1],queries=len(current.queries)))
    # Issue 38: new custom NEFs share the stock 16:9 / DX RAW planes. A stock
    # NEF of the same plane (stock main JPEG and previews) must keep the exact
    # native descriptor; a new custom NEF must receive its own previews.
    stock_planes=[]
    for wh,main,previews,custom in (
            ((5600,3144),(5568,3128),{4:(640,360),5:(1920,1080)},False),
            ((5600,3728),(5568,3712),{4:(640,424),5:(1620,1080)},False),
            ((5600,3144),(5568,2056),{4:(640,236),5:(1920,708)},True),
            ((5600,3728),(4952,3712),{4:(640,480),5:(1440,1080)},True)):
        assert list(wh) in shapes, wh
        for file_type in (1,2):
            for flags in (0,1<<4,1<<11):
                outputs=[]
                for fixture in (original,current):
                    fixture.prepare(list(wh),file_type,flags)
                    fixture.u.mem_write(META+12,struct.pack('<2H',*main))
                    fixture.preview=previews
                    fixture.call(0x103472DE,(OBJ,))
                    outputs.append(bytes(fixture.u.mem_read(DESC,0x90)))
                if not custom:
                    assert outputs[0]==outputs[1], ('stock plane NEF changed',wh,file_type,flags)
                else:
                    for index in range(6):
                        kind,enabled,width,height=struct.unpack_from('<B3x3I',outputs[1],index*24)
                        if enabled==1 and kind in (4,5):
                            assert (width,height)==previews[kind], (wh,main,file_type,flags,kind)
                stock_planes.append(dict(file_wh=list(wh),main_wh=list(main),custom=custom,
                                         file_type=file_type,flags=flags))
    full_metadata=[]
    for file_type in (1,2):
        for flags in (0,1<<4,1<<11):
            for stream_index,kind in ((7,4),(2,5)):
                current.prepare(file_type=file_type,flag=flags)
                current.preview={4:(640,236),5:(1920,708)}
                current.u.mem_write(OBJ+6,struct.pack('<H',stream_index))
                current.call(0x10340C84,(OBJ,))
                actual=struct.unpack('<2H',current.u.mem_read(INFO+0x39c,4))
                assert actual==current.preview[kind], (file_type,flags,kind,actual)
                full_metadata.append(dict(file_type=file_type,flags=flags,kind=kind,wh=actual))
    # Both metadata getters: only exact native callers may use the file object
    # in r4. All others preserve native behavior without dereferencing r4.
    metadata_cases=[]
    for name,(target,callers) in DIMENSION_SITES.items():
        for caller in callers+(STOP|1,):
            for small in (0,1):
                for failed in (False,True):
                    for fixture in (original,current):
                        fixture.prepare();fixture.preview={4:(640,236),5:(1920,708)};fixture.failure=failed
                        fixture.at_caller(target,caller,(OUT,1,small))
                    actual=bytes(current.u.mem_read(OUT,4));native=bytes(original.u.mem_read(OUT,4))
                    expected=struct.pack('<2H', *((640,236) if small else (1920,708)))
                    assert actual==(expected if caller in callers and not failed else native)
                    metadata_cases.append(dict(getter=name,caller=hex(caller),small=small,failed=failed))
    # Reject malformed or oversized query results and preserve the descriptor.
    rejected=[]
    for wh in ((0,236),(640,0),(641,236),(640,481),(65535,65535)):
        for failed in (False,True):
            outputs=[]
            for fixture in (original,current):
                fixture.prepare();fixture.preview={4:wh,5:(1920,708)};fixture.failure=failed
                fixture.at_caller(0x103488B2,0x1034750D,(OUT,4,1,640),(360,7,8))
                outputs.append(bytes(fixture.u.mem_read(OUT,24)))
            assert outputs[0]==outputs[1]
            rejected.append(dict(wh=wh,failed=failed))
    assert any(row['changed'] for row in cases), 'Must reproduce stock-size mismatch'
    return dict(image_sha256=hashlib.sha256(image).hexdigest(),native_producer_cases=cases,
                metadata_cases=metadata_cases,full_metadata_cases=full_metadata,rejected_queries=rejected,
                stock_plane_nef_cases=stock_planes,
                sample_stream_dimensions_verified=bool(sample),file_service_is_fixture=True,
                jpeg_decoder_executed=False,physical_playback_verified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('source','image','report','output'):p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--sample',type=Path)
    a=p.parse_args()
    from z30_extensions.container import extract_eg
    result=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),json.loads(a.report.read_text()),a.sample)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('PASS',len(result['native_producer_cases']),'native producer cases;',len(result['metadata_cases']),'metadata cases')


if __name__=='__main__':main()
