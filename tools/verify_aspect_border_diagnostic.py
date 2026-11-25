"""Verify border failure counters and the backward-compatible menu trace tail.

Includes the channel-disabled start omitted from v18's producer fixtures.
Captures publication attempts; it does not model physical drawing or delivery.
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
    for key in ('source','image','report','deps','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.features.aspect.border import DIAGNOSTIC_NAMES, DIAGNOSTIC_MAGIC
    from verify_sound_settings import Fixture
    from read_aspect_trace import decode
    from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_INVALID
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2
    stock=extract_eg(a.source.read_bytes());image=a.image.read_bytes()
    report=json.loads(a.report.read_text());feature=report['features']['aspect_profiles']
    f={k:int(v,16) for k,v in feature['functions'].items()}
    props={0x501:2,0x189e:2,0x1129:0}
    s=Fixture(stock,image,report,{},properties=props)
    state=s.state+feature['state_offset'];s.u.mem_write(state,struct.pack('<I',1))
    cache=state+feature['independent_border']['cache_state_word_offset']
    s.map(0xc237c380,256);s.u.mem_write(0xc237c3d2,struct.pack('<4H',0,292,5568,3420))
    _,bss,length,_=struct.unpack_from('<4I',stock,0x140b0)
    def invalid(u,access,address,size,value,data):
        if bss<=address and address+size<=bss+length:s.map(address,size);return True
        return False
    s.u.hook_add(UC_HOOK_MEM_INVALID,invalid)
    bank=s.call(0x104b5e30,(3,1,0));s.map(bank,0x400);s.u.mem_write(bank,bytes(0x400))
    requests=[]
    def frontier(u,address,length,data):
        if address==0x104b9348:
            pointer=u.reg_read(UC_ARM_REG_R0);u.mem_write(pointer,bytes(0x468))
            u.mem_write(pointer,struct.pack('<I',1));u.mem_write(pointer+8,struct.pack('<4h',0,36,720,442));s.ret(0)
        elif address==0x10453fac:
            requests.append([u.reg_read(UC_ARM_REG_R1),u.reg_read(UC_ARM_REG_R2)]);s.ret(0)
        else:s.ret(1 if address==0x104be4a0 else 0)
    for address in (0x104b9348,0x10453fac,0x1044fcf6,0x1044dd08,0x104be4a0):
        s.u.hook_add(UC_HOOK_CODE,frontier,begin=address,end=address)
    def counters():
        return dict(zip(DIAGNOSTIC_NAMES,struct.unpack('<16I',s.u.mem_read(cache+12,64))))
    # No forced component-enable fixture: the v18 publisher exits before
    # requesting a rectangle. Show this separately from manual producer tests.
    s.call(f['aspect_border_publish'],(0,));assert requests==[]
    assert counters()['rectangle_channel_disabled']==1
    assert counters()['rectangle_producer']==0 and counters()['request_attempted']==0
    disabled=dict(counters())
    s.u.mem_write(bank+0x14,struct.pack('<I',1))
    s.call(f['aspect_border_publish'],(0,));assert requests==[[8,1]]
    assert counters()['request_attempted']==1 and counters()['rectangle_producer']==0
    s.call(f['aspect_border_publish'],(0,));assert requests==[[8,1]]
    assert counters()['unchanged_request']==1
    # Producer is explicitly invoked, not claimed to have consumed the request.
    payload=0x50080000;s.u.mem_write(payload,bytes(0x4b4));s.call(0x103c0568,(1,3,payload))
    assert counters()['rectangle_producer']==1 and counters()['apply_succeeded']==1
    assert counters()['rectangle_channel_disabled']==1  # Earlier blocker survives.
    s.call(f['aspect_trace_view_menu'])
    blob=bytes(s.u.mem_read(state+16,feature['diagnostic_trace']['bytes']))
    parsed=decode(blob);item=next(x for x in parsed['records'] if x['event']==5)
    assert item['border']['rectangle_channel_disabled']==1
    assert item['border']['request_attempted']==1
    assert item['border']['apply_succeeded']==1
    assert item['border']['stock_grid_enabled']==0
    assert item['border']['rectangle_channel_enabled']==1
    assert parsed['version']==4 and len(blob)==15384
    result=dict(image_sha256=hashlib.sha256(image).hexdigest(),
                disabled_channel_start=disabled,decoded_border=item['border'],
                format4_length_preserved=len(blob),original_capture_trace_unchanged=True,
                request_delivery_verified=False,physical_output_verified=False,
                models=['Native bank getter and built publisher execute with disabled then enabled bank.',
                        'Publication is intercepted; producer invocation is explicit and separate.',
                        'Readiness and viewport are fixtures; no camera cause inferred.'])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print('PASS disabled-channel start, request/producer separation, persistent blocker counters and format-4 export')


if __name__=='__main__':main()
