"""Execute the opt-in border producer, native packer/converter and guards.

Explicit photo readiness, viewport, display palette and register fixtures.
No natural graphics scheduling, camera pixels or hardware acknowledgement.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools')]
RECORD = struct.Struct('<II7H2xI')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'image', 'report', 'output', 'deps'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.features.aspect.border import rectangles
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2

    stock = extract_eg(a.source.read_bytes())
    image = a.image.read_bytes()
    report = json.loads(a.report.read_text())
    feature = report['features']['aspect_profiles']
    f = {k: int(v, 16) for k,v in feature['functions'].items()}
    props = {0x501:2, 0x1129:1}
    c = Fixture(stock,image,report,{},properties=props)
    c.map(0xc237c380,256)
    _, bss, size, _ = struct.unpack_from('<4I', stock, 0x140b0)
    def missing(u,access,address,length,value,data):
        if bss <= address and address+length <= bss+size:
            c.map(address,length)
            return True
        return False
    c.u.hook_add(UC_HOOK_MEM_INVALID,missing)
    viewport = [0,36,720,442]
    visible = [0,0,1]  # UI busy, hidden flag, photo ready.
    flags = [1]
    def frontier(u,address,length,data):
        if address == 0x104b9348:
            ptr = u.reg_read(UC_ARM_REG_R0)
            u.mem_write(ptr, bytes(0x468))
            u.mem_write(ptr,struct.pack('<I',flags[0]))
            u.mem_write(ptr+8,struct.pack('<4h',*viewport))
            c.ret(0)
        else:
            c.ret(visible[(0x1044fcf6,0x1044dd08,0x104be4a0).index(address)])
    for address in (0x104b9348,0x1044fcf6,0x1044dd08,0x104be4a0):
        c.u.hook_add(UC_HOOK_CODE,frontier,begin=address,end=address)
    payload, output, view = 0x50080000,0x50082000,0x50083000
    state = c.state+feature['state_offset']
    bank = c.call(0x104b5e30,(3,1,0))
    c.map(bank,0x400)
    # Original bank holds 42 native records, with independent active AF boxes.
    c.u.mem_write(bank,bytes(0x400))
    occupied = (0,3,39)
    for i in occupied:
        c.u.mem_write(bank+0x2c+20*i,struct.pack('<II6H',0,1,100,100,180,180,4,9))
    bank_before = bytes(c.u.mem_read(bank,0x400))
    # Source conversion uses a separate explicit hardware viewport fixture.
    c.map(0xc1d57ac0+0x2f00,8)
    c.u.mem_write(0xc1d57ac0+0x2f00,struct.pack('<II',8192,8192))
    c.u.mem_write(0xc1808170,b'\x00')  # Explicit RGB-mode fixture.
    c.map(0x20141000,4096)
    handle = 0x50085000
    c.u.mem_write(handle,struct.pack('<II',0x143,0))
    writes=[]
    def device_write(u,access,address,length,value,data):
        writes.append([hex(address),value])
    c.u.hook_add(UC_HOOK_MEM_WRITE,device_write,begin=0x20141800,end=0x20141bff)
    cases = []
    for source in ((0,0,5568,3712),(0,292,5568,3420)):
        c.u.mem_write(0xc237c3d2,struct.pack('<4H',*source))
        for dims in ((0,36,720,442),(0,0,720,480),(20,12,500,652),(0,0,1920,1080)):
            viewport[:] = dims
            for profile in range(1,7):
                c.u.mem_write(state,struct.pack('<I',profile))
                c.u.mem_write(view,struct.pack('<4h',*dims))
                assert c.call(f['aspect_border_project'],(output,view,int(feature['geometry_table'],16)+profile*16,profile))==1
                bounds = struct.unpack('<4i',c.u.mem_read(output,16))
                want = rectangles(bounds,dims)
                for grid_on in (0,1):
                    props[0x1129]=grid_on
                    c.u.mem_write(payload,bytes(0x4b4))
                    c.call(0x103c0568,(1,3,payload))
                    actual=[]
                    for i in range(42):
                        row = bytes(c.u.mem_read(payload+4+28*i,28))
                        if i in occupied:
                            assert RECORD.unpack(row)[-1] == 1
                            assert RECORD.unpack(row)[3:7] == (100,100,176,176)
                        elif RECORD.unpack(row)[-1] == 1:
                            v=RECORD.unpack(row)
                            w,h=v[5]-v[3],v[6]-v[4]
                            strokes=(8,2) if w<=h else (2,8)
                            assert v[:3] == (0,65535,0) and v[7:9] == strokes
                            actual.append(tuple(v[3:7]))
                            assert c.call(0x10281182,(payload+4+28*i,output))==1
                            assert c.call(0x1029d24c,(output,))==1
                            descriptor = bytes(c.u.mem_read(output,20))
                            assert struct.unpack_from('<2H',descriptor,8)==strokes
                            assert descriptor[12:15] == bytes([255,255,0])
                            start=len(writes)
                            assert c.call(0x102893ce,(handle,i,output,0))==0
                            assert len(writes)-start==4
                            # Check orientation survives native conversion and
                            # submission; this does not simulate LCD coverage.
                            assert writes[start+2][1]==(strokes[0]<<16)|strokes[1]
                    assert sorted(actual)==sorted(want),(profile,dims,actual,want)
                    assert bytes(c.u.mem_read(bank,0x400))==bank_before
                    cases.append(dict(profile=profile,source=source,viewport=dims,
                                      grid_enabled=grid_on,borders=actual,af_preserved=True))
    # Run the actual rectangle consumer, including its enable bitmap update.
    # Locks and device state remain explicit fixtures, not a running camera.
    def lock(u,address,length,data):
        c.ret(1)
    for address in (0x10199bb0,0x10199baa,0x1021ce38,0x1021ce24):
        c.u.hook_add(UC_HOOK_CODE,lock,begin=address,end=address)
    device_state=0x50088000
    c.u.mem_write(device_state,bytes(0x2000))
    c.u.mem_write(0xc1808170+0x1c,struct.pack('<I',handle))
    c.u.mem_write(0xc1808170+0x34,struct.pack('<I',device_state))
    c.u.mem_write(device_state+0x234,struct.pack('<I',1))
    c.u.mem_write(device_state+0x20f,b'\x01')
    c.map(0xc1d611c8,0x500)
    c.u.mem_write(0xc1d611c8+3,b'\x03')
    c.u.mem_write(0xc1d611c8+0x4f9,b'\x03')
    c.u.mem_write(payload-4,b'\x03\0\0\0')
    def enabled_records():
        low,high=struct.unpack('<II',c.u.mem_read(device_state+0x200,8))
        return {i for i in range(42) if ((high if i<32 else low)>>(i if i<32 else i-32))&1}
    assert c.call(0x1036471e,(0,payload-4))==1
    assert enabled_records()==set(occupied)|{1,2,4,5}
    c.u.mem_write(state,bytes(4));c.call(0x103c0568,(1,3,payload))
    assert c.call(0x1036471e,(0,payload-4))==1
    assert enabled_records()==set(occupied)
    # OFF and each visibility/source guard must reproduce the original packer.
    guards=[]
    c.u.mem_write(state,bytes(4));c.call(0x103c0568,(1,3,payload))
    baseline=bytes(c.u.mem_read(payload,0x4b4))
    for reason in ('off','wrong_area','ui_busy','hidden','not_ready','invalid_flags','invalid_source','records_full'):
        c.u.mem_write(state,struct.pack('<I',0 if reason=='off' else 1))
        props[0x501]=0 if reason=='wrong_area' else 2
        visible[:]=[int(reason=='ui_busy'),int(reason=='hidden'),int(reason!='not_ready')]
        flags[0]=0 if reason=='invalid_flags' else 1
        c.u.mem_write(0xc237c3d2,struct.pack('<4H',*((0,0,0,0) if reason=='invalid_source' else (0,292,5568,3420))))
        if reason=='records_full':
            for i in range(42):
                c.u.mem_write(bank+0x2c+20*i,struct.pack('<II6H',0,1,100,100,180,180,4,9))
        c.u.mem_write(payload,bytes(0x4b4));c.call(f['aspect_border_original'],(1,3,payload))
        original=bytes(c.u.mem_read(payload,0x4b4))
        assert c.call(f['aspect_border_apply'],(payload,))==0
        assert bytes(c.u.mem_read(payload,0x4b4))==original
        guards.append(reason)
    # Clean OFF repacks the bank instead of leaving a previously appended row.
    c.u.mem_write(bank,bank_before);props[0x501]=2;visible[:]=[0,0,1];flags[0]=1
    c.u.mem_write(state,struct.pack('<I',1));c.call(0x103c0568,(1,3,payload))
    assert bytes(c.u.mem_read(payload,0x4b4))!=baseline
    c.u.mem_write(state,bytes(4));c.call(0x103c0568,(1,3,payload))
    # Disabled records retain irrelevant coordinates; the original enable
    # bitmap, rather than zeroing the entire structure, defines cleanup.
    assert {i for i in range(42) if int.from_bytes(c.u.mem_read(payload+28+28*i,4),'little')==1}==set(occupied)
    for i in occupied:
        assert bytes(c.u.mem_read(payload+4+28*i,28))==baseline[4+28*i:32+28*i]
    # Other output masks must remain byte-identical to the original producer.
    c.u.mem_write(state,struct.pack('<I',1))
    for mask in (2,4):
        other=c.call(0x104b5e30,(3,mask,0));c.map(other,0x400);c.u.mem_write(other,bytes(0x400))
        c.u.mem_write(payload,bytes(0x4b4));c.call(f['aspect_border_original'],(mask,3,payload))
        original=bytes(c.u.mem_read(payload,0x4b4))
        c.u.mem_write(payload,bytes(0x4b4));c.call(0x103c0568,(mask,3,payload))
        assert bytes(c.u.mem_read(payload,0x4b4))==original
    publications=[]
    def publish(u,address,length,data):
        publications.append([u.reg_read(UC_ARM_REG_R0),u.reg_read(UC_ARM_REG_R1),u.reg_read(UC_ARM_REG_R2)])
        c.ret(0)
    c.u.hook_add(UC_HOOK_CODE,publish,begin=0x10453fac,end=0x10453fac)
    for enabled in (0,1):
        c.u.mem_write(bank+0x14,struct.pack('<I',enabled))
        start=len(publications);c.call(f['aspect_border_publish'],(0,))
        assert len(publications)-start==enabled
    assert publications==[[0x10453ac3,8,1]]
    # Execute the actual patched monitor with grid OFF as well as ON. Other
    # display eligibility functions are explicit stable-photo fixtures.
    def monitor_frontier(u,address,length,data):
        c.ret({0x1045c3ee:1,0x1044f0d6:3}.get(address,0))
    for address in (0x10310c4c,0x10359e96,0x1044f662,0x1045c3ee,0x1044f0d6,0x10455dc4):
        c.u.hook_add(UC_HOOK_CODE,monitor_frontier,begin=address,end=address)
    c.u.mem_write(0xc1829600,struct.pack('<5I',0,1,0,0,0))
    monitor_cases=[]
    for grid_on in (0,1):
        props[0x1129]=grid_on
        c.u.mem_write(state,struct.pack('<I',1))
        c.u.mem_write(state+feature['independent_border']['cache_state_word_offset'],bytes(12))
        before=dict(props);start=len(publications)
        c.call(0x104be73a)
        assert publications[start:]==[[0x10453ac3,8,1]]
        c.call(0x104be73a)
        assert len(publications)==start+1  # Stable view does not flood the queue.
        assert props==before
        monitor_cases.append(dict(grid_enabled=grid_on,independent_border_requested=True))
    start=len(publications)
    viewport[0]+=2;viewport[2]+=2
    c.call(f['aspect_border_publish'],(0,))
    assert len(publications)==start+1
    c.call(f['aspect_border_publish'],(0,))
    assert len(publications)==start+1
    c.call(f['aspect_border_publish'],(1,))
    assert len(publications)==start+2  # A real native change can force refresh.
    c.u.mem_write(state,bytes(4));c.u.mem_write(state+12,struct.pack('<I',1))
    c.call(f['aspect_border_publish'],(0,))
    assert len(publications)==start+3  # OFF requests a final original repack.
    c.u.mem_write(state,bytes(16))
    start=len(publications);c.call(f['aspect_border_publish'],(0,))
    assert len(publications)==start  # Never-used/OFF feature adds no request.
    result=dict(image_sha256=hashlib.sha256(image).hexdigest(),cases=cases,
                guards=guards,off_repack_restores_original=True,other_masks_unchanged=True,
                publication=publications,original_grid_property_unchanged=True,
                native_monitor_cases=monitor_cases,off_without_border_has_no_extra_request=True,
                stable_refresh_suppressed=True,viewport_refresh_verified=True,native_change_refresh_verified=True,
                native_rectangle_register_writes=len(writes),
                native_rectangle_consumer_and_enable_cleanup_verified=True,
                actual_pixels_verified=False,worker_lifecycle_verified=False,
                long_side_stroke_pixels=8,end_cap_stroke_pixels=2,
                models=['Explicit readiness/viewport; original 42-record bank and native producer execute.',
                        'Converter and validator execute, but device scheduling and physical output unverified.',
                        'Native component must already be enabled; no forced visibility.'])
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print('PASS',len(cases),'border/grid cases;',len(guards),'guards; AF records, OFF cleanup and other masks preserved')


if __name__=='__main__':
    main()
