"""Execute menu persistence, split download and socket setup with explicit fixtures."""
import argparse
import json
import struct
from pathlib import Path
from verify_sound_settings import Fixture
from probe_wireless_send import probe_options, probe_host_dispatch
from z30_extensions.container import extract_eg
from z30_extensions.features.research.wireless_ui import MAGIC, SOURCE, DESTINATION, LENGTH, BUFFER_SITE
from z30_extensions.platform.z30_120 import BASE

PATH='A:\\WL-WIFI.CFG'


def config(index):
    return struct.pack('<4I',MAGIC,0,index,~(MAGIC^index)&0xFFFFFFFF)


def verify(stock,image,report):
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2
    feature=report['features']['wireless_buffer']
    f={k:int(v,16) for k,v in feature['functions'].items()}
    observations=[]
    for index in range(4):
        files={}
        first=Fixture(stock,image,report,files)
        page=0x50090000
        first.u.mem_write(page+22,struct.pack('<2H',index,0))
        navigation=[]

        def nav(u,a,n,d):
            navigation.append(u.reg_read(UC_ARM_REG_R1))
            first.ret(0)

        first.u.hook_add(UC_HOOK_CODE,nav,begin=0x1045F732,end=0x1045F732)
        first.call(f['bitrate_action'],(page,))
        assert files[PATH]==config(index) and navigation==[955]
        fresh=Fixture(stock,image,report,files,initialize=False)
        calls=[]
        def ipc(u,a,n,d):
            r0,r1,r2=[u.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)]
            assert r0==6
            fd,second,third,fourth=struct.unpack('<4I',u.mem_read(r2,16))
            assert fourth==0
            if r1==13:
                if fd in (3,7,9):
                    u.mem_write(second,bytes([16,2])+struct.pack('>H',15740 if fd!=9 else 80)+bytes(12))
                    u.mem_write(third,struct.pack('<I',16))
                    u.mem_write(r2,bytes(4));fresh.ret(0)
                else:
                    u.mem_write(r2,struct.pack('<I',0xffffffff));fresh.ret(0xffffffff)
            elif r1==11:
                option,value,length=struct.unpack('<3I',u.mem_read(third,12))
                assert second==0xffff and option==0x1001
                assert int.from_bytes(u.mem_read(length,4),'little')==4
                calls.append((fd,int.from_bytes(u.mem_read(value,4),'little')));fresh.ret(0)
            else:raise AssertionError(r1)
        fresh.u.hook_add(UC_HOOK_CODE,ipc,begin=0x10476c7c,end=0x10476c7c)
        def cache(u,a,n,d):fresh.ret(0)
        for address in (0x104836a8,0x10483738,0x10204b0c):
            fresh.u.hook_add(UC_HOOK_CODE,cache,begin=address,end=address)
        fresh.call(f['wireless_apply'],(),limit=300000)
        assert calls==[(3,65536<<index),(7,65536<<index)],calls
        assert not fresh.opened
        observations.append(dict(profile=index,requested_size=65536<<index,
                                  action_saved=True,fresh_state_restored=True,native_ipc=calls))
    from unicorn.arm_const import UC_ARM_REG_R5,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_PC,UC_ARM_REG_CPSR
    gating=[]
    for kind,length,channel in ((2,8,0),(4,8,1),(10,8,0),(2,7,0)):
        ctx=Fixture(stock,image,report,{})
        packet=0x50090000;epilogue=packet+64
        ctx.u.mem_write(packet,struct.pack('<2I',length,kind))
        ctx.u.mem_write(epilogue,bytes.fromhex('bde8f081'))
        invoked=[]
        def gate(u,a,n,d):
            if a==(f['wireless_apply']&~1):invoked.append(True);ctx.ret(0);return
            assert u.reg_read(UC_ARM_REG_R5)==channel
            assert u.reg_read(UC_ARM_REG_R7)==packet and u.reg_read(UC_ARM_REG_R8)==length
            assert bool(u.reg_read(UC_ARM_REG_CPSR)&(1<<30))==(channel==0)
            u.reg_write(UC_ARM_REG_PC,epilogue|1)
        for address in (f['wireless_apply']&~1,0x1031eabc):
            ctx.u.hook_add(UC_HOOK_CODE,gate,begin=address,end=address)
        ctx.call(0x1031eab0,(packet,length,channel))
        assert bool(invoked)==(kind in (2,4) and length>=8)
        gating.append(dict(kind=kind,length=length,channel=channel,applied=bool(invoked)))
    observations.append(dict(packet_gating=gating))
    for payload in (b'',config(0)[:8],config(0)+b'X',config(4),config(0)[:-1]+b'\0'):
        bad=Fixture(stock,image,report,{PATH:payload})
        assert int.from_bytes(bad.u.mem_read(bad.state+feature['state_offset'],4),'little')==1
        assert not bad.opened
    for short,close in ((True,False),(False,True)):
        bad=Fixture(stock,image,report,{},short_write=short,close_error=close)
        assert bad.call(f['bitrate_save'])==0 and bad.word(52)==8
    observations.append(dict(invalid_configs_rejected=5,save_failures=2,default=1))
    observations.append(dict(original_data_dispatch=probe_host_dispatch(image,stock)))
    return observations


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    rows=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),json.loads(a.report.read_text()))
    with a.output.open('x',encoding='utf-8') as f:
        json.dump(dict(scope=__doc__,cases=rows,hardware_verified=False),f,indent=2)
        f.write('\n')
    print('Passed four menu-save/fresh-state/native-IPC cases, five invalid records and two save failures')


if __name__=='__main__':main()
