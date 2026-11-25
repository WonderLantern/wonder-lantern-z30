"""Execute owned switch/actions and SD restore; hardware services are fixtures."""
import argparse
import json
import struct
from pathlib import Path
from verify_sound_settings import Fixture
from z30_extensions.container import extract_eg
from z30_extensions.features.photo.zebra_ui import MAGIC
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R1


def verify(stock, image, report):
    control = report['features']['photo_zebra_control']
    off = control['state_offset']
    functions = {k: int(v,16) for k,v in control['functions'].items()}
    cases = []
    for value in (0,1):
        files = {}
        a = Fixture(stock,image,report,files,properties={})
        a.set(off,value)
        assert a.call(functions['zebra_save']) == 1
        b = Fixture(stock,image,report,files,properties={})
        assert b.word(off) == value and not b.opened
        cases.append(dict(fresh_state_restore=value))
    valid = struct.pack('<4I',MAGIC,0,1,~(MAGIC^1)&0xffffffff)
    for payload in (b'',valid[:8],valid+b'X',valid[:-1]+b'\0',
                    struct.pack('<4I',MAGIC,0,2,~(MAGIC^2)&0xffffffff)):
        a = Fixture(stock,image,report,{'A:\\WL-ZEBRA.CFG':payload},properties={})
        assert a.word(off) == 0 and not a.opened
    cases.append(dict(invalid_records_fail_closed=5))
    for pattern in (0,1,2):
        properties = {0x1182:pattern,0x1183:7}
        a = Fixture(stock,image,report,{},properties=properties)
        navigation = []
        def boundary(u,address,size,data):
            if address == 0x1045f732:
                navigation.append(u.reg_read(UC_ARM_REG_R1))
            a.ret(0)
        for address in (a.f['labels']&~1,0x1045f732,0x1045d54c,0x10453fac):
            a.u.hook_add(UC_HOOK_CODE,boundary,begin=address,end=address)
        page = a.state+control['page_offset']
        for value in (1,0,1):
            a.u.mem_write(page+22,struct.pack('<2H',value,0))
            a.call(functions['zebra_action'],(page,))
            assert a.word(off)==value and navigation[-1]==955, (a.word(off),value,navigation,a.word(52))
            assert properties=={0x1182:pattern,0x1183:7}
            a.call(functions['zebra_restore'])
            assert a.word(off)==value
        a.short_write = True
        assert a.call(functions['zebra_save'])==0 and a.word(52)==8
        assert a.word(off)==1
        cases.append(dict(native_pattern_preserved=pattern,threshold_preserved=7,
                          menu_changes=3,save_failure_retains_active_selection=True))
    return cases


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','image','report','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    report=json.loads(a.report.read_text())
    cases=verify(extract_eg(a.source.read_bytes()),a.image.read_bytes(),report)
    a.output.write_text(json.dumps(dict(cases=cases,scope=__doc__,physical_verified=False),indent=2)+'\n')
    print('PASS photo switch SD/action/restore cases:',len(cases))
