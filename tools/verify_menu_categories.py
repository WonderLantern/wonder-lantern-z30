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
    sound=report['features']['menu_framework'];functions={k:int(v,16) for k,v in sound['functions'].items()}
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
    from z30_extensions.features.menu_framework import root_layout
    paths=sound['menu_paths']
    status=sound.get('status_page')
    rows,_=root_layout(set(paths)-{'status'}) if status else ([],[])
    rows=(['status'] if status else [])+rows
    assert [item['key'] for item in sound['menu_categories']]==rows,(sound['menu_categories'],rows)
    for cat in sound['category_pages'].values():
        assert word(pages[cat['index']]+4)==pages[PAGE_INDEX],cat
    for key,path in paths.items():
        # Walk root -> (category ->) page; each step opens the next page, which answers to the step's page.
        for step in path:
            if step['page']!=PAGE_INDEX:
                # A native shooting-menu row may have pointed the page at the tab container.
                fixture.u.mem_write(pages[step['opens']]+4,struct.pack('<I',int(sound['root_parent'],16)))
            click(step['page'],step['row'])
            assert nav[:1]==[step['opens']],(key,step,nav)
            assert word(pages[step['opens']]+4)==pages[step['page']],(key,step)
            if step['page']!=PAGE_INDEX:
                assert focus and focus[-1][0]==pages[step['opens']],(key,step,focus)
        cases.append(dict(page=key,path=[step['opens'] for step in path],parent=path[-1]['page'],real_action_executed=True))
    if status:
        # Status rows: system rows do nothing; a value row opens its page, which answers to
        # the status page and to its own holder again when opened from there.
        for row,key in enumerate(status['rows']):
            click(status['index'],row)
            if key not in paths:
                assert nav==[],(key,nav);continue
            target=paths[key][-1]['opens']
            assert nav==[target] and word(pages[target]+4)==pages[status['index']],(key,nav)
            holder=paths[key][-1]['page']
            for step in paths[key]:click(step['page'],step['row'])
            assert word(pages[target]+4)==pages[holder],(key,'parent back to its holder')
            cases.append(dict(status_row=key,opens=target,parent_restored=holder))
        # A failed save marks the status page.
        fixture.set(52,8);assert word(fixture.state+status['flag_offset'])==0
        fixture.call(functions['show_error'],(pages[PAGE_INDEX],))
        assert word(fixture.state+status['flag_offset'])==1
        fixture.set(52,0);fixture.u.mem_write(fixture.state+status['flag_offset'],bytes(4))
        cases.append(dict(save_failure_marks_status=True))
    for cat in list(sound['category_pages'].values())+([status] if status else []):
        fixture.u.mem_write(pages[cat['index']]+22,struct.pack('<2H',1,1))   # a stale cursor from an earlier visit
        click(PAGE_INDEX,cat['root_row'])
        top=struct.unpack('<2H',bytes(fixture.u.mem_read(pages[cat['index']]+22,4)))
        assert nav==[cat['index']] and top==(0,0) and focus==[[pages[cat['index']],sound['control_identifiers'][str(cat['index'])][0]]],(cat,nav,focus)
        cases.append(dict(category=cat['index'],opens_at_first_row=True,focus_first_row=True))
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
