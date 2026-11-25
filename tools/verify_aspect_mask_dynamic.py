"""Execute dynamic mask code with explicit view/source, real profile state.

The original submit prologue executes; its device helper/continuation are
bounded frontiers. No sensor, cache-coherency or physical screen proof.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools')]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','deps','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.features.aspect.mask import SNAPSHOT, SOURCE_WINDOW, IMAGE_SLOT, SUBMIT_SITE, SUBMIT_CONTINUE, VIEW_KIND, OVERLAY_BUSY, PHOTO_BACKEND, HISTORY_OFFSET, STATE_LOCK, UI_SERVICE_PHASE, display_regions
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_SP,UC_ARM_REG_PC
    stock=extract_eg(a.source.read_bytes());image=a.image.read_bytes()
    report=json.loads(a.report.read_text());aspect=report['features']['aspect_profiles']
    alpha=aspect['soft_mask']['alpha'];assert 0<alpha<255
    mask_word=struct.pack('<I',alpha<<24)
    f={k:int(v,16) for k,v in aspect['functions'].items()}
    c=Fixture(stock,image,report,{})
    c.map(UI_SERVICE_PHASE,1);c.u.mem_write(UI_SERVICE_PHASE,b'\x01')
    if (aspect.get('diagnostic_trace') or {}).get('format')=='WMD1':
        from z30_extensions.features.aspect.mask_trace import FIELDS
        for _,address,width in FIELDS:
            c.map(address,width)
    page_bytes={name:bytes(c.u.mem_read(c.state+offset,44))
                for name,offset in report['features']['menu_framework']['page_offsets'].items()}
    from unicorn import UC_PROT_READ, UC_PROT_EXEC
    c.u.mem_protect(0x10020000,(len(image)+4095)&~4095,UC_PROT_READ|UC_PROT_EXEC)
    # Explicit native photo-overlay state; does not follow the grid setting.
    c.map(VIEW_KIND,4)
    for address in OVERLAY_BUSY:c.map(address,4);c.u.mem_write(address,bytes(4))
    c.u.mem_write(VIEW_KIND,bytes(1));c.u.mem_write(PHOTO_BACKEND,struct.pack('<I',1))
    state=c.state+aspect['state_offset'];surface=0xd0100000
    c.map(surface,720*480*4);c.map(SNAPSHOT,0x468);c.map(SOURCE_WINDOW,8);c.map(IMAGE_SLOT,4)
    c.u.mem_write(SNAPSHOT,struct.pack('<I',1))
    c.u.mem_write(IMAGE_SLOT,struct.pack('<I',surface))
    # Remain zero throughout: neither the display bank nor its visibility
    # needs to exist for the new geometry path.
    c.map(0xc201a7f4,32);c.u.mem_write(0xc201a7f4,bytes(32))
    descriptor,target=0xc4001000,0xc4002000
    c.map(descriptor,0x100);c.map(target,0x100)
    c.u.mem_write(descriptor,struct.pack('<I',0xa03))
    c.u.mem_write(descriptor+0x14,struct.pack('<I',target))
    c.u.mem_write(target+0x88,struct.pack('<2I',720,480))
    c.u.mem_write(target+0xb0,struct.pack('<I',0xecca7000))
    calls=[];original_args=(descriptor,0x5678)
    def frontier(u,address,size,data):
        if address==0x1058217e:
            assert u.reg_read(UC_ARM_REG_R0)==original_args[0]
            c.ret(0)
        else:
            assert u.reg_read(UC_ARM_REG_R4)==original_args[1]
            assert u.reg_read(UC_ARM_REG_R5)==original_args[0]
            sp=u.reg_read(UC_ARM_REG_SP)
            r4,r5,r6,pc=struct.unpack('<4I',u.mem_read(sp,16))
            for reg,value in ((UC_ARM_REG_R4,r4),(UC_ARM_REG_R5,r5),(UC_ARM_REG_R6,r6),
                              (UC_ARM_REG_SP,sp+16),(UC_ARM_REG_PC,pc)):
                u.reg_write(reg,value)
            calls.append('original-prologue-and-continuation')
    for address in (0x1058217e,SUBMIT_CONTINUE):
        c.u.hook_add(UC_HOOK_CODE,frontier,begin=address,end=address)
    plain=b'\x12\x34\x56\x00'*(720*480)
    view,out=0x50080000,0x50080100
    goldens=[]
    for profile,source,viewport,want in [
        (1,(0,0,5568,3712),(0,0,720,480),(40,0,680,480)),
        (1,(0,292,5568,3420),(0,36,720,442),(40,36,680,442)),
        (2,(0,0,5568,3712),(0,0,720,480),(60,0,660,480)),
        (3,(0,0,5568,3712),(0,0,720,480),(0,60,720,420))]:
        c.u.mem_write(SOURCE_WINDOW,struct.pack('<4H',*source))
        c.u.mem_write(view,struct.pack('<4h',*viewport))
        assert c.call(f['aspect_mask_project'],(out,view,int(aspect['geometry_table'],16)+profile*16,profile))==1
        got=struct.unpack('<4i',c.u.mem_read(out,16));assert got==want,(got,want)
        goldens.append(dict(profile=profile,source=source,viewport=viewport,photo=got))
    cases=[]
    for source in ((0,0,5568,3712),(0,292,5568,3420)):
        for viewport in ((0,0,720,480),(0,36,720,442)):
            for profile in range(1,7):
                c.u.mem_write(state,struct.pack('<I',profile))
                c.u.mem_write(SOURCE_WINDOW,struct.pack('<4H',*source))
                c.u.mem_write(SNAPSHOT+8,struct.pack('<4h',*viewport))
                c.u.mem_write(view,struct.pack('<4h',*viewport))
                assert c.call(f['aspect_mask_project'],(out,view,int(aspect['geometry_table'],16)+profile*16,profile))==1
                photo=struct.unpack('<4i',c.u.mem_read(out,16));mask=display_regions(photo,viewport);assert mask
                c.u.mem_write(surface,plain)
                c.call(SUBMIT_SITE,original_args)
                got=bytes(c.u.mem_read(surface,len(plain)));want=bytearray(plain)
                for l,t,r,b in mask:
                    for y in range(t,b):
                        row=(479-y)*720*4
                        want[row+l*4:row+r*4]=mask_word*(r-l)
                assert got==want,(profile,source,viewport)
                c.call(SUBMIT_SITE,original_args)
                assert bytes(c.u.mem_read(surface,len(plain)))==got
                l,t,r,b=mask[0];mid=(l+r)//2;my=(t+b)//2
                # An interior rewrite must be repaired even when both corners
                # already contain the desired word (old shortcut was unsound).
                ptr=surface+((479-my)*720+mid)*4
                c.u.mem_write(ptr,b'ABC\0');c.call(SUBMIT_SITE,original_args)
                assert bytes(c.u.mem_read(ptr,4))==mask_word
                for graphic in (b'ABC\x44',b'\0\0\0\xff',struct.pack('<I',(alpha-1)<<24)):
                    c.u.mem_write(ptr,graphic);c.call(SUBMIT_SITE,original_args)
                    assert bytes(c.u.mem_read(ptr,4))==mask_word
                assert bytes(c.u.mem_read(0xc201a7f4,32))==bytes(32)
                cases.append(dict(profile=profile,source=source,viewport=viewport,photo=photo,mask=mask))
    guards=[]
    for cause in ('off','bad_profile','empty_view','oversize_view','invalid_source','changed_profile','changed_view','changed_source','bad_surface',
                  'other_command','other_target','other_size','bad_target','bad_descriptor',
                  'nonphoto_kind','not_ready','movie_backend','overlay_busy','changed_context',
                  'ui_phase_0','ui_phase_3','ui_phase_4','changed_ui_phase'):
        c.u.mem_write(UI_SERVICE_PHASE,bytes([int(cause[-1]) if cause.startswith('ui_phase_') else 1]))
        c.u.mem_write(VIEW_KIND,bytes([2 if cause=='nonphoto_kind' else 0]))
        c.u.mem_write(SNAPSHOT,struct.pack('<I',0 if cause=='not_ready' else 1))
        c.u.mem_write(PHOTO_BACKEND,struct.pack('<I',4 if cause=='movie_backend' else 1))
        c.u.mem_write(OVERLAY_BUSY[0],struct.pack('<I',1 if cause=='overlay_busy' else 0))
        c.u.mem_write(descriptor,struct.pack('<I',0x800 if cause=='other_command' else 0xa03))
        c.u.mem_write(descriptor+0x14,struct.pack('<I',0 if cause=='bad_target' else target))
        c.u.mem_write(target+0x88,struct.pack('<2I',1024 if cause=='other_size' else 720,480))
        c.u.mem_write(target+0xb0,struct.pack('<I',0xd045f200 if cause=='other_target' else 0xecca7000))
        c.u.mem_write(state,struct.pack('<I',0 if cause=='off' else 7 if cause=='bad_profile' else 1))
        viewport=(0,0,0,0) if cause=='empty_view' else (0,0,722,480) if cause=='oversize_view' else (0,0,720,480)
        source=(0,0,0,0) if cause=='invalid_source' else (0,0,5568,3712)
        c.u.mem_write(SNAPSHOT+8,struct.pack('<4h',*viewport));c.u.mem_write(SOURCE_WINDOW,struct.pack('<4H',*source))
        c.u.mem_write(IMAGE_SLOT,struct.pack('<I',0 if cause=='bad_surface' else surface));c.u.mem_write(surface,plain)
        hook=None
        if cause.startswith('changed_'):
            def mutate(u,address,size,data):
                if cause=='changed_ui_phase':
                    u.mem_write(UI_SERVICE_PHASE,b'\x04');return
                target=(SNAPSHOT if cause=='changed_context' else state if cause=='changed_profile'
                        else SNAPSHOT+8 if cause=='changed_view' else SOURCE_WINDOW)
                u.mem_write(target,struct.pack('<I',0 if cause=='changed_context' else 2))
            point=f['aspect_mask_project']&~1
            hook=c.u.hook_add(UC_HOOK_CODE,mutate,begin=point,end=point)
        if cause=='bad_descriptor':
            original_args=(0,0x5678)
        c.call(SUBMIT_SITE,original_args)
        original_args=(descriptor,0x5678)
        if hook is not None:c.u.hook_del(hook)
        assert bytes(c.u.mem_read(surface,len(plain)))==plain,cause
        assert struct.unpack('<I',c.u.mem_read(state+HISTORY_OFFSET+STATE_LOCK,4))[0]==0,cause
        guards.append(cause)
    history=state+HISTORY_OFFSET
    lifecycle=[]
    def active(profile=6):
        c.u.mem_write(UI_SERVICE_PHASE,b'\x02')
        c.u.mem_write(VIEW_KIND,bytes(1));c.u.mem_write(SNAPSHOT,struct.pack('<I',1))
        c.u.mem_write(PHOTO_BACKEND,struct.pack('<I',1))
        for address in OVERLAY_BUSY:c.u.mem_write(address,bytes(4))
        c.u.mem_write(SNAPSHOT+8,struct.pack('<4h',0,0,720,480))
        c.u.mem_write(SOURCE_WINDOW,struct.pack('<4H',0,0,5568,3712))
        c.u.mem_write(state,struct.pack('<I',profile))
        c.u.mem_write(IMAGE_SLOT,struct.pack('<I',surface))
    empty=bytes(len(plain))
    for profile in range(1,7):
        active(profile);c.u.mem_write(surface,empty);c.call(SUBMIT_SITE,original_args)
        assert struct.unpack('<I',c.u.mem_read(history,4))[0]==1
        assert struct.unpack('<I',c.u.mem_read(history+STATE_LOCK,4))[0]==0
        c.u.mem_write(VIEW_KIND,b'\x02');c.call(SUBMIT_SITE,original_args)
        assert bytes(c.u.mem_read(surface,len(empty)))==empty,profile
        assert struct.unpack('<I',c.u.mem_read(history,4))[0]==0
        active(profile);c.call(SUBMIT_SITE,original_args)
        assert bytes(c.u.mem_read(surface,len(empty)))!=empty
        c.u.mem_write(state,bytes(4));c.call(SUBMIT_SITE,original_args)
        assert bytes(c.u.mem_read(surface,len(empty)))==empty
        lifecycle.append(dict(profile=profile,nonphoto_cleans=True,reentry_restores=True,off_cleans=True))
    # Retire old rectangles before changing ratio, including pixels which
    # move from a side bar into the next profile's visible photograph.
    transitions=[]
    active(1);c.u.mem_write(surface,empty);c.call(SUBMIT_SITE,original_args)
    for profile in (6,2,3,1,5,4):
        active(profile);c.call(SUBMIT_SITE,original_args)
        c.u.mem_write(view,struct.pack('<4h',0,0,720,480))
        assert c.call(f['aspect_mask_project'],(out,view,int(aspect['geometry_table'],16)+profile*16,profile))==1
        photo=struct.unpack('<4i',c.u.mem_read(out,16));wanted=bytearray(empty)
        for l,t,r,b in display_regions(photo,(0,0,720,480)):
            for y in range(t,b):wanted[((479-y)*720+l)*4:((479-y)*720+r)*4]=mask_word*(r-l)
        assert bytes(c.u.mem_read(surface,len(empty)))==wanted,profile
        transitions.append(profile)
    # A native opaque replacement (including black) must survive cleanup.
    for native_pixel in (b'\x12\x34\x56\xff',b'\0\0\0\xff',b'\x12\x34\x56\x44'):
        active();c.u.mem_write(surface,empty);c.call(SUBMIT_SITE,original_args)
        native_image=native_pixel*(720*480);c.u.mem_write(surface,native_image)
        c.u.mem_write(VIEW_KIND,b'\x02');c.call(SUBMIT_SITE,original_args)
        assert bytes(c.u.mem_read(surface,len(empty)))==native_image
    active();c.u.mem_write(surface,empty);c.call(SUBMIT_SITE,original_args)
    before=bytes(c.u.mem_read(surface,len(empty)))
    c.u.mem_write(history+STATE_LOCK,struct.pack('<I',1));c.u.mem_write(VIEW_KIND,b'\x02')
    c.call(SUBMIT_SITE,original_args)
    assert bytes(c.u.mem_read(surface,len(empty)))==before
    c.u.mem_write(history+STATE_LOCK,bytes(4));c.call(SUBMIT_SITE,original_args)
    assert bytes(c.u.mem_read(surface,len(empty)))==empty
    # Never clear a replaced surface or touch the old one on retirement.
    active();c.u.mem_write(surface,empty);c.call(SUBMIT_SITE,original_args)
    old_surface=bytes(c.u.mem_read(surface,len(empty)))
    replacement=0xd0300000;c.map(replacement,len(empty));native_image=b'\x12\x34\x56\xff'*(720*480)
    c.u.mem_write(replacement,native_image);c.u.mem_write(IMAGE_SLOT,struct.pack('<I',replacement))
    c.u.mem_write(VIEW_KIND,b'\x02');c.call(SUBMIT_SITE,original_args)
    assert bytes(c.u.mem_read(replacement,len(empty)))==native_image
    assert bytes(c.u.mem_read(surface,len(empty)))==old_surface
    assert struct.unpack('<I',c.u.mem_read(history,4))[0]==0
    # Compare with the complete native predicate, including its original
    # readiness logic. Only the synchronized snapshot COPY is a frontier;
    # UI property reads, view-kind dispatch and readiness branches execute.
    def snapshot_copy(u,address,size,data):
        destination=u.reg_read(UC_ARM_REG_R0)
        u.mem_write(destination,bytes(0x468))
        u.mem_write(destination,bytes(u.mem_read(SNAPSHOT,4)))
        c.ret(0)
    snapshot_hook=c.u.hook_add(UC_HOOK_CODE,snapshot_copy,begin=0x104b9348,end=0x104b9348)
    contexts=[]
    c.u.mem_write(UI_SERVICE_PHASE,b'\x02')
    try:
        for kind in range(31):
            for ready in range(4):
                for backend in (0,1,4):
                    for busy in ((0,0),(1,0),(0,1)):
                        c.u.mem_write(VIEW_KIND,bytes([kind]))
                        c.u.mem_write(SNAPSHOT,struct.pack('<I',ready))
                        c.u.mem_write(PHOTO_BACKEND,struct.pack('<I',backend))
                        for address,value in zip(OVERLAY_BUSY,busy):c.u.mem_write(address,struct.pack('<I',value))
                        allowed=c.call(f['aspect_mask_photo_context'])
                        native=c.call(0x104be4a0)
                        expected=int(kind in (0,6) and not any(busy) and backend==1 and bool(ready&1))
                        expected_native=int(kind in (0,6) and not any(busy) and
                                            (backend==1 and bool(ready&1) or backend==4 and bool(ready&2)))
                        assert allowed==expected and native==expected_native,(kind,ready,backend,busy,allowed,native)
                        assert not allowed or native==1
                        contexts.append(dict(kind=kind,ready=ready,backend=backend,busy=busy,
                                             mask_allowed=allowed,native_overlay_allowed=native))
    finally:c.u.hook_del(snapshot_hook)
    for name,offset in report['features']['menu_framework']['page_offsets'].items():
        assert bytes(c.u.mem_read(c.state+offset,44))==page_bytes[name],('page overwritten by mask history',name)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(image_sha256=hashlib.sha256(image).hexdigest(),
        alpha=alpha,golden_projections=goldens,cases=cases,guards=guards,photo_context_cases=contexts,
        lifecycle_cases=lifecycle,ratio_transitions=transitions,
        native_replacements_preserved=True,try_lock_nonblocking_verified=True,replaced_surface_untouched=True,
        native_snapshot_copy_is_a_frontier=True,grid_record_zero_throughout=True,
        rom_read_only_during_execution=True,
        all_native_menu_page_structures_preserved=True,
        repeated_fill_idempotent=True,interior_rewrite_repaired=True,opaque_residual_holes_repaired=True,
        upper_ui_pixels_verified=False,
        original_submit_prologue_verified=len(calls),physical_cache_and_pixels_verified=False),indent=2)+'\n')
    print('PASS',len(cases),'dynamic mask cases;',len(guards),'guards; zero grid, preserved ABI and idempotency')


if __name__=='__main__':main()
