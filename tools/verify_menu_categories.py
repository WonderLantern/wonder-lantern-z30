"""Execute grouped-menu actions and parent contracts with explicit UI frontiers.

The candidate getter/action, SD-WAV loading and preference save code execute.
Text, focus, navigation and redraw consumers are bounded fixtures; actual native
menu rendering is checked separately by verify_mask_menu_qemu.
"""
import argparse
import hashlib
import io
import json
import struct
import sys
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools')]


def verify(stock,image,report):
    from verify_sound_settings import Fixture
    from z30_extensions.features.sound_layout import PAGE_INDEX,FILES_INDEX,SWITCH_INDEX,POPUP_INDEX
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1
    sound=report['features']['shutter_sound'];functions={k:int(v,16) for k,v in sound['functions'].items()}
    stream=io.BytesIO()
    with wave.open(stream,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000);audio.writeframes(b'\x01\0'*80)
    files={'A:\\SHUTTER.WAV':stream.getvalue()}
    fixture=Fixture(stock,image,report,files,properties={})
    pages={int(k):fixture.state+v for k,v in sound['page_offsets'].items()}
    nav=[];focus=[]
    def boundary(u,address,size,data):
        if address==0x1045f732:
            assert u.reg_read(UC_ARM_REG_R0)==2
            nav.append(u.reg_read(UC_ARM_REG_R1))
        if address==0x1045d54c:
            focus.append([u.reg_read(UC_ARM_REG_R0),u.reg_read(UC_ARM_REG_R1)])
        fixture.ret(0)
    for address in (functions['labels']&~1,0x1045f732,0x1045d54c,0x10453fac):
        fixture.u.hook_add(UC_HOOK_CODE,boundary,begin=address,end=address)
    def word(address):return int.from_bytes(fixture.u.mem_read(address,4),'little')
    def click(page,row):
        nav.clear();focus.clear()
        fixture.u.mem_write(pages[page]+22,struct.pack('<2H',row,0))
        fixture.call(functions['action'],(pages[page],))
    cases=[]
    identifiers=[ident for ids in sound['control_identifiers'].values() for ident in ids]
    assert len(set(identifiers))==len(identifiers)
    for index,pointer in pages.items():
        assert fixture.call(functions['getter'],(2,index))==pointer
        assert int.from_bytes(fixture.u.mem_read(pointer+2,2),'little')==index
    categories=sound['menu_categories']
    expected=[key for key,feature in (('aspect','aspect_profiles'),('zebra','photo_zebra_control'),('stamp','date_stamp_control'),('bitrate','bitrate_profiles'),
                                     ('sound','shutter_sound'),('modules','module_host'))
              if feature in report['features']]
    if len(expected)==1:expected=[]
    assert [item['key'] for item in categories]==expected
    for item in categories:
        target=item['page_index']
        assert word(pages[target]+4)==pages[PAGE_INDEX]
        click(PAGE_INDEX,item['row'])
        assert nav==[target],(item,nav)
        cases.append(dict(category=item['key'],target_page=target,real_action_executed=True))
    sound_page=sound['sound_settings_page']
    for index in (FILES_INDEX,SWITCH_INDEX):
        assert word(pages[index]+4)==pages[sound_page]
    for row,target in ((0,SWITCH_INDEX),(1,FILES_INDEX)):
        click(sound_page,row);assert nav==[target] and focus
        cases.append(dict(sound_row=row,opens=target,parent_is_sound=True))
    click(FILES_INDEX,0)
    assert nav==[sound_page] and fixture.word(44)==0 and fixture.word(40)>0
    assert 'A:\\WL-SOUND.CFG' in files
    cases.append(dict(file_selection_loads_and_saves=True,returns_to=sound_page))
    click(SWITCH_INDEX,0)
    assert nav==[sound_page] and fixture.word(36)==0
    cases.append(dict(sound_off_saves=True,returns_to=sound_page))
    fixture.set(40,0)
    click(sound_page,2)
    assert fixture.word(52)==1 and nav==[POPUP_INDEX]
    assert word(pages[POPUP_INDEX]+4)==pages[sound_page]
    click(POPUP_INDEX,0)
    assert nav==[sound_page] and fixture.word(52)==0
    cases.append(dict(preview_error_returns_to_sound=True))
    fixture.short_write=True
    click(SWITCH_INDEX,0)
    assert fixture.word(52)==8 and nav==[sound_page,POPUP_INDEX]
    assert word(pages[POPUP_INDEX]+4)==pages[sound_page]
    click(POPUP_INDEX,0)
    assert nav==[sound_page] and fixture.word(52)==0
    cases.append(dict(save_error_preserves_sound_parent=True))
    return cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','deps','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();sys.path.insert(0,str(args.deps.resolve()))
    from z30_extensions.container import extract_eg
    image=args.image.read_bytes();report=json.loads(args.report.read_text())
    assert hashlib.sha256(image).hexdigest()==report['sha256']
    cases=verify(extract_eg(args.source.read_bytes()),image,report)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(image_sha256=report['sha256'],cases=cases,
                                         scope=__doc__,physical_verified=False),indent=2)+'\n')
    print(f'PASS {len(cases)} native menu category/action/parent cases')


if __name__=='__main__':main()
