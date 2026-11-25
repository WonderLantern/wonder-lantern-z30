"""Execute menu actions, persistence, runtime geometry and reference-frame code.

Property/UI boundaries and viewport snapshots are explicit fixtures. Native
geometry and original grid generation execute; no sensor or real display model.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from z30_extensions.container import extract_eg
from z30_extensions.features.aspect.geometry import PROFILES, build_trial
from z30_extensions.features.aspect.ui import MAGIC, PREVIEW_CALLERS
from z30_extensions.platform.z30_120 import BASE, sha256
from verify_aspect_trials import measure


def verify(stock,image,report):
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_INVALID
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_PC,UC_ARM_REG_LR
    feature=report['features']['aspect_profiles']
    functions={k:int(v,16) for k,v in feature['functions'].items()}
    offset=feature['state_offset'];files={};s=Fixture(stock,image,report,files)
    properties={0x189e:0,0x1400:3,0x1300:0,0x501:4};busy=[0];events=[];property_writes=[]
    from unicorn import UC_PROT_READ,UC_PROT_EXEC
    s.u.mem_protect(BASE,(len(image)+4095)&~4095,UC_PROT_READ|UC_PROT_EXEC)

    def api(u,address,size,data):
        r0,r1=u.reg_read(UC_ARM_REG_R0),u.reg_read(UC_ARM_REG_R1)
        if address==0x10465984:value=properties.get(r0,0)
        elif address==0x10465990:properties[r0]=r1;property_writes.append((r0,r1));value=1
        elif address==0x101993de:value=busy[0] if r0==0x188c else properties.get(r0,0)
        else:events.append((r0,r1));value=0
        u.reg_write(UC_ARM_REG_R0,value);u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))

    for address in (0x10465984,0x10465990,0x101993de,0x1045f732):
        s.u.hook_add(UC_HOOK_CODE,api,begin=address,end=address)
    observations=[]
    page=0x50080000

    def select(profile):
        s.u.mem_write(page,bytes(44));s.u.mem_write(page,struct.pack('<I',(961<<16)|2))
        s.u.mem_write(page+22,struct.pack('<H',profile))
        s.call(functions['aspect_action'],(page,))

    baseline=measure(stock,stock)
    expected_cache={}
    for profile in (1,2,3,4,5,6,0):
        select(profile)
        assert struct.unpack('<I',s.u.mem_read(s.state+offset,4))[0]==profile
        assert properties[0x501]==(2 if profile else 4)
        assert properties[0x189e]==0  # Derived state must not be written directly.
        record=struct.pack('<4I',MAGIC,4,profile,~(MAGIC^4^profile)&0xffffffff)
        assert files['A:\\WL-ASPECT.CFG']==record and not s.opened
        restored_properties={0x501:0}
        fresh=Fixture(stock,image,report,dict(files),properties=restored_properties)
        assert struct.unpack('<I',fresh.u.mem_read(fresh.state+offset,4))[0]==profile
        assert struct.unpack('<I',fresh.u.mem_read(fresh.state+offset+8,4))[0]==4
        if feature.get('native_area_sync_version'):
            assert restored_properties[0x501]==(2 if profile else 0)
        expected_image=stock
        if profile:
            name=list(PROFILES)[profile-1]
            expected_image,_=build_trial(stock,name)
        expected=measure(stock,expected_image) if profile else baseline
        expected_cache[profile]=expected
        actual=measure(stock,image,s)
        for want,got in zip(expected,actual):
            for field in ('buffer_hex','config_hex','descriptor_hex','internal_selector_wh'):
                if (field=='config_hex' and profile in (1,2) and got['area']==2
                        and feature.get('narrow_source_window_version')):
                    # New independent contract: the source window is cropped
                    # before resizing, not a full-width source plus post trim.
                    x,y,width,height=feature['profiles'][profile-1]['large_xywh']
                    assert got['config_parent_wh']==(width,height)
                    assert got['config_origin_xy']==(16+x,8+y)
                    assert got['config_trim_xy']==(0,0)
                    assert got['config_trimmed_wh']==(width,height)
                    raw=bytearray.fromhex(got[field]);old=bytes.fromhex(want[field])
                    assert struct.unpack_from('<2H',raw,0x1a)==((width+15)&~15,)*2
                    parent_bytes=((width+15)&~15)*struct.unpack_from('<H',raw,0x20)[0]
                    if struct.unpack_from('<I',raw,0x48)[0]:
                        parent_bytes=max(parent_bytes,struct.unpack_from('<H',raw,0x4e)[0]*
                                         struct.unpack_from('<H',raw,0x54)[0])
                    wanted_end=(struct.unpack_from('<I',raw,0x2c)[0]+parent_bytes+15)&~15
                    assert struct.unpack_from('<I',raw,0x30)[0]==wanted_end
                    assert wanted_end<=struct.unpack_from('<I',old,0x30)[0]
                    raw[0x30:0x34]=old[0x30:0x34]
                    # Everything outside the six source-window fields must
                    # retain the independently built v4 contract byte for byte.
                    for delta in (0x1a,0x1c,0x1e,0x22,0x26,0x38):
                        raw[delta:delta+2]=old[delta:delta+2]
                    assert bytes(raw)==old,[(hex(i),a,b) for i,(a,b) in enumerate(zip(raw,old)) if a!=b]
                    continue
                assert want[field]==got[field],(profile,got['area'],got['size'],field)
        observations.append({'profile':profile,'native_geometry_cases':len(actual),
                             'area':properties[0x501],'fresh_state_restored':True})
    area_sync_cases=[]
    if feature.get('native_area_sync_version'):
        for profile in (1,2,6):
            select(profile)
            for native_area in (0,4):
                # Simulate changing the stock menu while an extension choice
                # remains remembered. No custom table may survive that mismatch.
                properties[0x501]=native_area
                inactive=measure(stock,image,s)
                for want,got in zip(baseline,inactive):
                    for field in ('buffer_hex','config_hex','descriptor_hex','internal_selector_wh'):
                        assert want[field]==got[field],('native_area_mismatch',profile,native_area,field)
                s.call(functions['aspect_sync'])
                assert properties[0x501]==2
                area_sync_cases.append({'profile':profile,'starting_native_area':native_area,
                                        'mismatch_uses_stock':True,'menu_sync_area':2})
        properties[0x501]=0;busy[0]=1;s.call(functions['aspect_sync'])
        assert properties[0x501]==0
        busy[0]=0;s.call(functions['aspect_sync']);assert properties[0x501]==2
        select(0)
    source_window_guards=[]
    if feature.get('narrow_source_window_version'):
        from unicorn.arm_const import UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R5
        # Exercise every decline condition directly at the real continuation.
        # Complete normal functions are already covered by the 63 cases above.
        obj,info=0x50080000,0x50081000
        for profile in range(8):
            for fault in ('none','user_area','capture_area','processing98','processing104',
                          'parent_width','parent_height','trim_width','trim_height','trim_x','trim_y'):
                x,width=(464,4640) if profile==2 else (308,4952)
                data=bytearray(0x70)
                for pos,value in ((0x1a,5568),(0x1c,5568),(0x1e,5568),(0x20,3712),
                                  (0x22,5568),(0x24,3712),(0x26,16),(0x28,8),
                                  (0x38,x),(0x3c,width),(0x3e,3712)):
                    struct.pack_into('<H',data,pos,value)
                capture=bytearray(0x300);capture[0x208]=2
                properties[0x501]=0 if fault=='user_area' else 2
                if fault=='capture_area':capture[0x208]=0
                if fault=='processing98':capture[0x98]=1
                if fault=='processing104':capture[0x104]=1
                bad={'parent_width':(0x22,5552),'parent_height':(0x24,3704),
                     'trim_width':(0x3c,width-8),'trim_height':(0x3e,3704),
                     'trim_x':(0x38,x+2),'trim_y':(0x3a,2)}
                if fault in bad:struct.pack_into('<H',data,*bad[fault])
                expected=bytearray(data)
                if profile in (1,2) and fault=='none':
                    for pos,value in ((0x1a,(width+15)&~15),(0x1c,(width+15)&~15),
                                      (0x1e,width),(0x22,width),(0x26,16+x),(0x38,0)):
                        struct.pack_into('<H',expected,pos,value)
                struct.pack_into('<H',expected,0x4e,2496)
                struct.pack_into('<H',expected,0x50,2496)
                s.u.mem_write(obj,bytes(data));s.u.mem_write(info,bytes(capture))
                s.u.mem_write(s.state+offset,struct.pack('<I',profile))
                for reg,value in ((UC_ARM_REG_R4,obj),(UC_ARM_REG_R5,info),(UC_ARM_REG_R3,2496),
                                  (UC_ARM_REG_R1,0x12345678),(UC_ARM_REG_R2,0xabcdef)):
                    s.u.reg_write(reg,value)
                # This continuation was translated during complete-function
                # checks above; Unicorn can run past an until address inside a
                # cached block. Stop explicitly before the next instruction.
                stop_hook=s.u.hook_add(UC_HOOK_CODE,lambda u,a,n,d:u.emu_stop(),
                                       begin=0x10142a2c,end=0x10142a2c)
                try:s.block(0x10142a22,0x10142a2c)
                finally:s.u.hook_del(stop_hook)
                assert bytes(s.u.mem_read(obj,0x70))==expected,(profile,fault)
                assert s.u.reg_read(UC_ARM_REG_R1)==0x12345678
                assert s.u.reg_read(UC_ARM_REG_R2)==0xabcdef
                assert s.u.reg_read(UC_ARM_REG_R3)==2496
                source_window_guards.append({'profile':profile,'fault':fault})
        # The following invalid-row checks expect the last menu choice OFF.
        s.u.mem_write(s.state+offset,bytes(4));properties[0x501]=4
    for invalid in (7,65535):
        before=bytes(s.u.mem_read(s.state+offset,16));select(invalid)
        assert bytes(s.u.mem_read(s.state+offset,16))==before
    busy[0]=1;select(6);assert struct.unpack('<I',s.u.mem_read(s.state+offset,4))[0]==0
    busy[0]=0
    properties[0x501]=4;select(0);assert properties[0x501]==4
    for data in (b'',bytes(16),struct.pack('<4I',MAGIC,0,7,~(MAGIC^7)&0xffffffff),
                 files['A:\\WL-ASPECT.CFG']+b'X',
                 struct.pack('<4I',0x31414C57,0,6,~(0x31414C57^6)&0xffffffff)):
        fresh=Fixture(stock,image,report,{'A:\\WL-ASPECT.CFG':data})
        assert struct.unpack('<I',fresh.u.mem_read(fresh.state+offset,4))[0]==0
    observations.append({'invalid_rows_rejected':2,'busy_rejected':True,'corrupt_or_legacy_files_rejected':5})
    # Reproduce the exact missing link in the old fixture: it seeded area 2
    # into capture inputs, even though the real user setting remained DX.
    def native_size_from_user_area(size):
        s.u.reg_write(UC_ARM_REG_R6,size)
        s.block(0x105A7946,0x105A7974)
        return [s.u.reg_read(UC_ARM_REG_R7),s.u.reg_read(UC_ARM_REG_R0)]
    properties[0x501]=0;properties[0x189e]=2
    s.u.mem_write(s.state+offset,struct.pack('<I',6))
    assert native_size_from_user_area(0)==[5568,3712]
    regressions=[{'scenario':'old derived-property write only','user_area':0,'derived_area':2,
                  'native_wh':native_size_from_user_area(0)}]
    select(6)
    assert properties[0x501]==2
    for size,wanted in enumerate(([5568,2056],[4176,1544],[2784,1024])):
        actual=native_size_from_user_area(size);assert actual==wanted
        regressions.append({'scenario':'fixed user-property write','size':size,'native_wh':actual})
    assert all(key==0x501 for key,value in property_writes)
    # Execute the actual stock BL instructions at all capture call sites, plus
    # excluded file/layout consumers. This checks the return-address guard,
    # property guard, original pointers and ARM register preservation together.
    from unicorn.arm_const import (UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R8,
        UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11)
    preserved=(UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,
               UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11)
    preview_cases=[]
    for kind,base,excluded,column in (('small',0x115a4cfc,0x10340ae5,0),
                                      ('large',0x115a4d10,0x1018ae6f,1)):
        for caller in (*PREVIEW_CALLERS[kind],excluded):
            for profile in (*range(7),7):
                for area in (0,2,4):
                    for index in range(5):
                        properties[0x501]=area
                        s.u.mem_write(s.state+offset,struct.pack('<I',profile))
                        s.u.reg_write(UC_ARM_REG_R0,index)
                        saved=[0x11220000+i for i in range(len(preserved))]
                        for reg,value in zip(preserved,saved):s.u.reg_write(reg,value)
                        s.block(caller-5,caller&~1)
                        assert [s.u.reg_read(reg) for reg in preserved]==saved
                        pointer=s.u.reg_read(UC_ARM_REG_R0)
                        actual=list(struct.unpack('<2H',s.u.mem_read(pointer,4)))
                        custom=(caller!=excluded and 1<=profile<=6 and area==2 and index==1)
                        wanted=(feature['profiles'][profile-1]['preview_wh'][column] if custom else
                                list(struct.unpack('<2H',stock[base-BASE+index*4:base-BASE+index*4+4])))
                        assert actual==list(wanted),(kind,hex(caller),profile,area,index,actual,wanted)
                        if not custom:assert pointer==base+index*4
                        preview_cases.append({'kind':kind,'caller':hex(caller),'profile':profile,
                            'area':area,'index':index,'wh':actual,'custom':custom})
    preview_buffers=[]
    obj,info=0x50080000,0x50081000
    for profile in range(7):
        properties[0x501]=2
        s.u.mem_write(s.state+offset,struct.pack('<I',profile))
        s.u.mem_write(obj,bytes(0x100));s.u.mem_write(info,bytes(0x300))
        s.u.mem_write(info+0x226,b'\x01')
        s.u.mem_write(obj+0x42,struct.pack('<2H',5568,3712))
        for reg,value in ((UC_ARM_REG_R4,obj),(UC_ARM_REG_R5,info),
                          (UC_ARM_REG_R6,0x50090000),(UC_ARM_REG_R10,0)):
            s.u.reg_write(reg,value)
        s.block(0x101434f4,0x1014351e)
        small=list(struct.unpack('<2H',s.u.mem_read(obj+0x7e,4)))
        # Stock 0x10143538 advances r4 by 0x60 before this block; 0x10143548
        # restores it. Seed that live-in, rather than skipping its contract.
        s.u.reg_write(UC_ARM_REG_R4,obj+0x60)
        s.block(0x1014353c,0x10143598)
        large=list(struct.unpack('<2H',s.u.mem_read(obj+0x92,4)))
        wanted=(feature['profiles'][profile-1]['preview_wh'] if profile else [[640,360],[1920,1080]])
        assert [small,large]==[list(x) for x in wanted],(profile,small,large,wanted)
        for wh,box in zip((small,large),((640,360),(1920,1080))):
            assert all(0<v<=maximum and v%4==0 for v,maximum in zip(wh,box))
        preview_buffers.append({'profile':profile,'small':small,'large':large})
    thumb=s.call(0x1020c056)
    assert tuple(struct.unpack('<2H',s.u.mem_read(thumb,4)))==(160,120)
    # Actual original grid math, followed by the built rectangle override.
    viewport=[0,0,720,480]
    s.map(0xc201a7f4,0x1000)

    def snapshot(u,address,size,data):
        pointer=u.reg_read(UC_ARM_REG_R0)
        u.mem_write(pointer,bytes(0x468));u.mem_write(pointer,struct.pack('<I',1))
        u.mem_write(pointer+0x28,b'\x01')  # Explicit native control flag, avoids unrelated setting event.
        for delta in (8,0x14):u.mem_write(pointer+delta,struct.pack('<4h',*viewport))
        u.reg_write(UC_ARM_REG_R0,0);u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))

    s.u.hook_add(UC_HOOK_CODE,snapshot,begin=0x104b9348,end=0x104b9348)
    _,bss_start,bss_length,_=struct.unpack_from('<4I',stock,0x140B0)
    def bss(u,access,address,size,value,data):
        if bss_start<=address and address+size<=bss_start+bss_length:
            s.map(address,size);return True
        return False
    s.u.hook_add(UC_HOOK_MEM_INVALID,bss)
    publications=[]
    def frontier(u,address,size,data):
        u.reg_write(UC_ARM_REG_R0,1);u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))
    # Readiness and asynchronous publication are explicit boundaries. Native
    # updater, original grid, normalizer, override and bank-copy all execute.
    from unicorn.arm_const import UC_ARM_REG_R2
    def publish(u,address,size,data):
        publications.append([u.reg_read(UC_ARM_REG_R1),u.reg_read(UC_ARM_REG_R2)])
        u.reg_write(UC_ARM_REG_R0,0);u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))
    s.u.hook_add(UC_HOOK_CODE,frontier,begin=0x10306328,end=0x10306328)
    s.u.hook_add(UC_HOOK_CODE,publish,begin=0x10453fac,end=0x10453fac)
    grid=[]
    properties[0x501]=2
    if feature.get('reference_frame_version', 1) >= 2:
        # Execute the real native source-window update, rather than inventing
        # an aspect ratio for the view. The viewport itself remains synthetic.
        s.map(0xC237C380, 256)
        s.u.mem_write(s.state+offset,bytes(4))
        s.u.mem_write(0x5008F000,bytes([0,0,2,0]))
        s.call(0x10197D78,(0x5008F000,))
    for dims in ((0,0,720,480),(0,0,1280,720),(20,12,500,652)):
        viewport[:]=dims
        for mode in (0,1,2):
            for style in (0,1):
                properties[0x1300]=style
                s.u.mem_write(s.state+offset,bytes(4));s.call(0x104be422,(mode,))
                original=bytes(s.u.mem_read(0xc201a7f4,32))
                for profile in range(1,7):
                    s.u.mem_write(s.state+offset,struct.pack('<I',profile))
                    s.call(0x104be422,(mode,))
                    raw=bytes(s.u.mem_read(0xc201a7f4,32))
                    if mode:
                        assert raw==original
                    else:
                        hx,hy,w,spacing_y,count_h,vx,vy,h,spacing_x,count_v,*_=struct.unpack('<hhIHHhhIHHHHBBH',raw)
                        if feature.get('reference_frame_version', 1) >= 2:
                            divisions=3+style
                            assert count_v==(divisions+1 if feature.get('reference_frame_borders',True) else divisions-1)
                            assert count_h==(divisions-1 if profile in (1,2) or not feature.get('reference_frame_borders',True) else divisions+1)
                            assert raw[24:]==original[24:]
                            assert not vx%2 and not vy%2
                            assert dims[0]<=vx and dims[1]<=hy
                            assert vx+(count_v-1)*spacing_x+2<=dims[2]
                            assert hy+(count_h-1)*spacing_y+2<=dims[3]
                            if profile in (1,2):
                                crop=feature['profiles'][profile-1]['large_xywh']
                                ideal_left=dims[0]+crop[0]*(dims[2]-dims[0])/5568
                                assert abs((vx-spacing_x if not feature.get('reference_frame_borders',True) else vx)-ideal_left)<=6
                        else:
                            assert count_h==count_v==2 and hx==vx and hy==vy
                        assert not hx%2 and not hy%2 and w>=8 and h>=8
                        assert dims[0]<=hx and dims[1]<=vy and hx+w<=dims[2] and vy+h<=dims[3]
                        if feature.get('reference_frame_version', 1) < 2:
                            assert spacing_x==w-2 and spacing_y==h-2
                        bank=s.call(0x104b5e30,(4,1,0))
                        assert bytes(s.u.mem_read(bank+0x24,32))==raw
                        assert publications[-1]==[16,1]
                        grid.append({'profile':profile,'viewport':dims,'style':style,'rectangle':[hx,hy,w,h],
                                     'parameters':raw.hex()})
                s.u.mem_write(s.state+offset,bytes(4));s.call(0x104be422,(mode,))
                assert bytes(s.u.mem_read(0xc201a7f4,32))==original
    properties[0x1300]=0;viewport[:]=(0,0,720,480)
    for area in (0,4):
        properties[0x501]=area;s.u.mem_write(s.state+offset,bytes(4));s.call(0x104be422,(0,))
        original=bytes(s.u.mem_read(0xc201a7f4,32))
        s.u.mem_write(s.state+offset,struct.pack('<I',6));s.call(0x104be422,(0,))
        assert bytes(s.u.mem_read(0xc201a7f4,32))==original
    return {'source_sha256':sha256(stock),'image_sha256':sha256(image),
            'rom_read_only_during_verification':True,
            'geometry_cases':sum(v.get('native_geometry_cases',0) for v in observations),
            'source_window_guard_cases':source_window_guards,
            'native_area_sync_cases':area_sync_cases,
            'user_area_regression':regressions,'property_writes':property_writes,
            'preview_selector_cases':preview_cases,'preview_native_buffers':preview_buffers,
            'fixed_thumbnail_preserved':True,
            'menu_persistence_cases':observations,'reference_frame_cases':grid,
            'nonzero_modes_kept_stock':True,'off_restores_original_grid':True,
            'models':['SD file services, property getters/setters and navigation are explicit fixtures.',
                      'Viewport, readiness and control flag are synthetic; graphics publication is observed, not consumed.',
                      'Native updater, original grid, normalizer, override and bank copy execute ARM code.'],
            'actual_pixels_verified':False,'physical_verified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','image','report','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--deps',type=Path)
    a=p.parse_args()
    if a.deps:sys.path.insert(0,str(a.deps.resolve()))
    result=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),json.loads(a.report.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {result["geometry_cases"]} runtime geometry and {len(result["reference_frame_cases"])} reference-frame cases')
