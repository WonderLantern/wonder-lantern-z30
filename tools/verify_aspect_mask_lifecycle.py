"""Replay camera scalar observations and test retirement before GPU filtering.

The recorded scalar values are real camera observations. GPU descriptors,
surface contents and dispatch are explicit ARM fixtures, not actual playback.
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
    for key in ('source','image','report','previous','previous-report','trace','deps','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.features.aspect.mask_trace import FIELDS, decode
    from z30_extensions.features.aspect.mask import UI_SERVICE_PHASE, IMAGE_SLOT, SUBMIT_SITE, SUBMIT_CONTINUE, HISTORY_OFFSET, STATE_LOCK, SNAPSHOT, SOURCE_WINDOW, VIEW_KIND, PHOTO_BACKEND, OVERLAY_BUSY
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE,UC_PROT_READ,UC_PROT_EXEC
    from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,
                                   UC_ARM_REG_SP,UC_ARM_REG_PC)
    stock=extract_eg(a.source.read_bytes());raw=a.trace.read_bytes();observed=decode(raw)

    def fixture(image,report):
        c=Fixture(stock,image,report,{},properties={0x188c:0,0x501:2})
        for _,address,width in FIELDS:c.map(address,width)
        c.u.mem_protect(0x10020000,(len(image)+4095)&~4095,UC_PROT_READ|UC_PROT_EXEC)
        c.aspect=report['features']['aspect_profiles'];c.base=c.state+c.aspect['state_offset']
        c.descriptor,c.target,c.surface=0xc4001000,0xc4002000,0xd0100000
        c.map(c.descriptor,0x100);c.map(c.target,0x100);c.map(c.surface,720*480*4)
        c.arguments=(c.descriptor,0x5678);c.native_calls=0
        def frontier(u,address,size,data):
            if address==0x1058217e:
                assert u.reg_read(UC_ARM_REG_R0)==c.arguments[0]
                c.ret(0);return
            assert u.reg_read(UC_ARM_REG_R4)==c.arguments[1]
            assert u.reg_read(UC_ARM_REG_R5)==c.arguments[0]
            sp=u.reg_read(UC_ARM_REG_SP);r4,r5,r6,pc=struct.unpack('<4I',u.mem_read(sp,16))
            for reg,value in ((UC_ARM_REG_R4,r4),(UC_ARM_REG_R5,r5),(UC_ARM_REG_R6,r6),
                              (UC_ARM_REG_SP,sp+16),(UC_ARM_REG_PC,pc)):u.reg_write(reg,value)
            c.native_calls+=1
        for address in (0x1058217e,SUBMIT_CONTINUE):
            c.u.hook_add(UC_HOOK_CODE,frontier,begin=address,end=address)
        return c

    image=a.image.read_bytes();report=json.loads(a.report.read_text())
    previous=a.previous.read_bytes();oldreport=json.loads(a.previous_report.read_text())
    current,old=fixture(image,report),fixture(previous,oldreport)
    empty=bytes(720*480*4)
    def descriptor(c,command=0xa03,width=720):
        c.u.mem_write(c.descriptor,struct.pack('<I',command))
        c.u.mem_write(c.descriptor+0x14,struct.pack('<I',c.target))
        c.u.mem_write(c.target+0x88,struct.pack('<2I',width,480))
        c.u.mem_write(c.target+0xb0,struct.pack('<I',0xecca7000))
        c.arguments=(c.descriptor,0x5678)
    def dispatch(c):c.call(SUBMIT_SITE,c.arguments)
    def seed(c,row):
        for name,address,width in FIELDS:
            if name=='image_surface':continue
            c.u.mem_write(address,int(row['fields'][name]).to_bytes(width,'little'))
        c.u.mem_write(IMAGE_SLOT,struct.pack('<I',c.surface))
        c.u.mem_write(c.base,struct.pack('<I',row['profile']))
    states=[];old_leaks=[]
    for c in (current,old):
        c.u.mem_write(c.surface,empty);descriptor(c)
    for row in observed['records']:
        for c in (current,old):seed(c,row);dispatch(c)
        stage=row['fields']['ui_service_phase']
        expected=bool(row['mask_predicate'] and stage in (1,2) and row['profile'])
        has_mask=bytes(current.u.mem_read(current.surface,len(empty)))!=empty
        assert has_mask==expected,(row['sequence'],stage,expected)
        before=bytes(old.u.mem_read(old.surface,len(empty)))!=empty
        if stage not in (1,2) and before:old_leaks.append(row['sequence'])
        states.append(dict(sequence=row['sequence'],profile=row['profile'],ui_stage=stage,
                           observed_old_predicate=row['mask_predicate'],new_mask_present=has_mask,
                           old_mask_present=before))
    assert old_leaks,'Supplied camera observations must reproduce the previous eligibility gap'
    def active(c,profile):
        descriptor(c);c.u.mem_write(UI_SERVICE_PHASE,b'\x02')
        c.u.mem_write(VIEW_KIND,bytes(1));c.u.mem_write(PHOTO_BACKEND,struct.pack('<I',1))
        c.u.mem_write(SNAPSHOT,struct.pack('<I',1))
        for address in OVERLAY_BUSY:c.u.mem_write(address,bytes(4))
        c.u.mem_write(SNAPSHOT+8,struct.pack('<4h',0,0,720,480))
        c.u.mem_write(SOURCE_WINDOW,struct.pack('<4H',0,0,5568,3712))
        c.u.mem_write(IMAGE_SLOT,struct.pack('<I',c.surface))
        c.u.mem_write(c.base,struct.pack('<I',profile));c.u.mem_write(c.surface,empty);dispatch(c)
        assert bytes(c.u.mem_read(c.surface,len(empty)))!=empty
    declines=[]
    for profile in (1,2,6):
        for reason in ('other_command','other_size','other_bank','invalid_descriptor'):
            for c in (current,old):
                active(c,profile);c.u.mem_write(UI_SERVICE_PHASE,b'\x04')
                if reason=='other_command':descriptor(c,0x800)
                elif reason=='other_size':descriptor(c,width=1024)
                elif reason=='other_bank':c.u.mem_write(c.target+0xb0,struct.pack('<I',0xd045f200))
                else:c.arguments=(0,0x5678)
                dispatch(c)
            assert bytes(current.u.mem_read(current.surface,len(empty)))==empty
            assert bytes(old.u.mem_read(old.surface,len(empty)))!=empty
            declines.append(dict(profile=profile,descriptor=reason,old_residual_reproduced=True,new_cleared=True))
    # Busy retirement skips rather than blocking. The next submit can retire.
    c=current;active(c,1);before=bytes(c.u.mem_read(c.surface,len(empty)))
    lock=c.base+HISTORY_OFFSET+STATE_LOCK
    c.u.mem_write(lock,struct.pack('<I',1));c.u.mem_write(UI_SERVICE_PHASE,b'\x04')
    descriptor(c,0x800);dispatch(c);assert bytes(c.u.mem_read(c.surface,len(empty)))==before
    c.u.mem_write(lock,bytes(4));dispatch(c);assert bytes(c.u.mem_read(c.surface,len(empty)))==empty
    # Preserve native replacements and never revisit an abandoned surface.
    active(c,1);native=b'\x10\x20\x30\xff'*(720*480);c.u.mem_write(c.surface,native)
    c.u.mem_write(UI_SERVICE_PHASE,b'\x04');descriptor(c,0x800);dispatch(c)
    assert bytes(c.u.mem_read(c.surface,len(empty)))==native
    active(c,1);before=bytes(c.u.mem_read(c.surface,len(empty)))
    replacement=0xd0300000;c.map(replacement,len(empty));c.u.mem_write(replacement,native)
    c.u.mem_write(IMAGE_SLOT,struct.pack('<I',replacement));c.u.mem_write(UI_SERVICE_PHASE,b'\x04')
    descriptor(c,0x800);dispatch(c)
    assert bytes(c.u.mem_read(replacement,len(empty)))==native
    assert bytes(c.u.mem_read(c.surface,len(empty)))==before
    a.output.parent.mkdir(parents=True,exist_ok=True)
    result=dict(image_sha256=hashlib.sha256(image).hexdigest(),previous_sha256=hashlib.sha256(previous).hexdigest(),
        camera_trace_sha256=hashlib.sha256(raw).hexdigest(),camera_scalar_states=states,
        previous_stage_leaks=old_leaks,descriptor_decline_cases=declines,
        busy_retirement_nonblocking=True,native_replacements_preserved=True,replaced_surface_untouched=True,
        actual_gpu_descriptors_recorded=False,physical_playback_fixed=False,
        scope='Replay observed camera scalar fields; explicit GPU descriptors, pixel surfaces and native continuation frontiers.')
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {len(states)} observed scalar states and {len(declines)} pre-filter retirement cases; old behavior reproduced')


if __name__=='__main__':main()
