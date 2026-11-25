"""Execute native mode selection and built profile/SD code with explicit file fixtures.

All 30 records are checked, including unused native 720p rows. This is ARM
instruction verification, not a physical encoder or card-speed measurement.
"""
import argparse
import json
import struct
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from z30_extensions.container import extract_eg
from z30_extensions.features.video.bitrate_ui import MAGIC, BALANCED_MBPS
from z30_extensions.features.video.bitrate import MODE_TABLE, RECORD, RATES, RECORDS, BITRATES_MBPS, rates

PATH='A:\\WL-VIDEO.CFG'
MODE,DEST=0x50090000,0x50090100
INPUTS=([(0,f,q) for f in (2,3,4) for q in (0,1)] +
        [(1,f,q) for f in range(7) for q in (0,1)] +
        [(2,f,q) for f in range(2) for q in (0,1)] +
        [(1,f,q) for f in range(7,10) for q in (0,1)])


def config(profile, reserved=0):
    return struct.pack('<4I', MAGIC,reserved,profile,~(MAGIC^reserved^profile)&0xffffffff)


def verify(stock,image,report):
    from verify_sound_settings import Fixture
    feature=report['features']['bitrate_profiles']
    functions={k:int(v,16) for k,v in feature['functions'].items()}
    offset=feature['state_offset']
    observations=[]
    fixture=Fixture(stock,image,report,{})
    for profile,overrides in enumerate(({},BALANCED_MBPS,BITRATES_MBPS)):
        fixture.u.mem_write(fixture.state+offset,struct.pack('<I',profile))
        for index,mode in enumerate(INPUTS):
            fixture.u.mem_write(MODE,bytes(mode))
            fixture.u.mem_write(DEST-4,b'Z'*36)
            assert fixture.call(0x10355754,[MODE,DEST])==DEST
            result=bytes(fixture.u.mem_read(DEST,28))
            expected=bytearray(stock[MODE_TABLE-0x10020000+index*RECORD:MODE_TABLE-0x10020000+(index+1)*RECORD])
            for name,pair in overrides.items():
                if RECORDS[name]==index:expected[RATES:RATES+8]=rates(pair)
            assert result==expected,(profile,index,result.hex(),expected.hex())
            assert bytes(fixture.u.mem_read(DEST-4,4))==b'ZZZZ' and bytes(fixture.u.mem_read(DEST+28,4))==b'ZZZZ'
            observations.append(dict(profile=profile,record=index,native_mode=list(mode),rates_bps=struct.unpack_from('<2I',result,20)))
    for profile in range(3):
        files={}
        first=Fixture(stock,image,report,files)
        first.u.mem_write(first.state+offset,struct.pack('<I',profile))
        assert first.call(functions['bitrate_save'])==1
        assert files[PATH]==config(profile) and not first.opened
        # A fresh guest RAM instance, with no menu entry. The hooked native
        # record getter allocates state and restores the card selection.
        fresh=Fixture(stock,image,report,files,initialize=False)
        fresh.u.mem_write(MODE,bytes((1,2,0)))
        fresh.call(0x10355754,[MODE,DEST])
        fresh.state=fresh.call(fresh.f['get'])
        assert struct.unpack('<I',fresh.u.mem_read(fresh.state+offset,4))[0]==profile
        pair=({},BALANCED_MBPS,BITRATES_MBPS)[profile].get('1080 30p HIGH',(28,24))
        assert bytes(fresh.u.mem_read(DEST+20,8))==rates(pair) and not fresh.opened
        observations.append(dict(fresh_ram_first_record=True,profile=profile))
    for data in (b'',config(0)[:8],config(0)+b'X',config(0)[:-1]+b'\0',config(3),config(0,1)):
        bad=Fixture(stock,image,report,{PATH:data})
        assert struct.unpack('<I',bad.u.mem_read(bad.state+offset,4))[0]==2 and not bad.opened
    observations.append(dict(corrupt_configs_rejected=6,default_profile=2))
    for short,close in ((True,False),(False,True)):
        bad=Fixture(stock,image,report,{},short_write=short,close_error=close)
        bad.u.mem_write(bad.state+offset,struct.pack('<I',1))
        assert bad.call(functions['bitrate_save'])==0 and bad.word(52)==8
        assert struct.unpack('<I',bad.u.mem_read(bad.state+offset,4))[0]==1
        observations.append(dict(save_failure='short_write' if short else 'close',active_selection_retained=True))
    return observations


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    rows=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),json.loads(a.report.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(scope=__doc__,cases=rows),indent=2)+'\n')
    print(f'{len(rows)} bitrate profile cases passed')
