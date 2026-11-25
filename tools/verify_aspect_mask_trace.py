"""Execute bounded mask tracing, wrap, freeze and deferred export in ARM.

Scalar source values and file services are explicit fixtures. This validates
recording and export, not the semantics of the camera's actual playback state.
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
    from z30_extensions.features.aspect.mask_trace import FIELDS, TRACE_OFFSET, TRACE_BYTES, decode
    from z30_extensions.features.aspect.mask import HISTORY_OFFSET, STATE_LOCK
    from verify_sound_settings import Fixture
    stock=extract_eg(a.source.read_bytes());image=a.image.read_bytes()
    report=json.loads(a.report.read_text());feature=report['features']['aspect_profiles']
    sha=hashlib.sha256(image).hexdigest();assert sha==report['sha256']
    assert feature['diagnostic_trace']['format']=='WMD1'
    functions={k:int(v,16) for k,v in feature['functions'].items()}
    c=Fixture(stock,image,report,{},properties={0x188c:0,0x501:2})
    base=c.state+feature['state_offset'];trace=base+TRACE_OFFSET
    page_bytes={name:bytes(c.u.mem_read(c.state+offset,44))
                for name,offset in report['features']['menu_framework']['page_offsets'].items()}
    values={}
    for index,(name,address,width) in enumerate(FIELDS):
        c.map(address,width);value=(index+1)*3
        values[name]=value;c.u.mem_write(address,value.to_bytes(width,'little'))
    c.u.mem_write(base,struct.pack('<I',1))
    c.u.mem_write(base+HISTORY_OFFSET+STATE_LOCK,struct.pack('<I',1))
    sample=lambda allowed:c.call(functions['aspect_mask_trace_sample'],(allowed,))
    parse=lambda:decode(bytes(c.u.mem_read(trace,TRACE_BYTES)))
    before=len(c.calls)
    sample(1);first=parse();assert first['counter']==1 and first['records'][0]['fields']==values
    for _ in range(100):sample(1)
    assert parse()['counter']==1,'Identical repeated frames must not fill the trace'
    for index in range(80):
        c.u.mem_write(FIELDS[0][1],bytes([index]))
        sample(index&1)
    wrapped=parse();assert wrapped['counter']==81
    assert [r['sequence'] for r in wrapped['records']]==list(range(18,82))
    assert len(c.calls)==before,'Graphics-side recording must not invoke any file service'
    saved=bytes(c.u.mem_read(trace,TRACE_BYTES))
    c.u.mem_write(trace+20,struct.pack('<I',1));sample(1)
    assert bytes(c.u.mem_read(trace,20))==saved[:20]
    c.u.mem_write(trace+20,bytes(4))
    # In-flight producer: do not start any menu file operation or wait.
    before=len(c.calls);c.call(functions['aspect_mask_trace_save'])
    assert len(c.calls)==before and int.from_bytes(c.u.mem_read(trace+20,4),'little')==0
    c.u.mem_write(base+HISTORY_OFFSET+STATE_LOCK,bytes(4))
    c.call(functions['aspect_mask_trace_save'])
    exported=c.files['A:\\WL-ASPECT.DBG'];assert decode(exported)['records']==wrapped['records']
    assert not c.opened and int.from_bytes(c.u.mem_read(trace+20,4),'little')==0
    # Previously observed pixels/state are never embedded as a variable blob.
    assert len(exported)==TRACE_BYTES and all(row['profile']==1 for row in parse()['records'])
    for name,offset in report['features']['menu_framework']['page_offsets'].items():
        assert bytes(c.u.mem_read(c.state+offset,44))==page_bytes[name],name
    failures=[]
    for short,close in ((True,False),(False,True)):
        c.short_write=short;c.close_error=close
        c.call(functions['aspect_mask_trace_save'])
        assert int.from_bytes(c.u.mem_read(trace+20,4),'little')==0
        failures.append(dict(short_write=short,close_error=close,freeze_released=True))
    from read_aspect_trace import decode as unified_decode
    assert unified_decode(exported)['format']=='WMD1'
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(image_sha256=sha,distinct_records=81,retained_records=64,
        repeated_frames_suppressed=100,field_roundtrips=len(FIELDS),no_graphics_file_io=True,
        writer_busy_skips_export=True,normal_menu_export_roundtrip=True,menu_structures_preserved=True,
        failure_paths=failures,physical_playback_verified=False),indent=2)+'\n')
    print('PASS mask trace: 20 fields, change suppression, 64-slot wrap, busy/freeze, menu export and errors')


if __name__=='__main__':main()
