"""Native QEMU date stamp menu/touch checks with explicit diagnostic SD services."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root','image','report','fixture','output-dir'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--language',choices=('en','zh-Hans','zh-Hant'),default='zh-Hans')
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    from display_session import Session,png_from_ppm
    report=json.loads(a.report.read_text());image=a.image.read_bytes()
    fixture=json.loads(a.fixture.read_text())
    sound=report['features']['shutter_sound'];stamp=report['features']['date_stamp_control']
    s=Session(str(a.output_dir.resolve()));observations=[]
    s.audit.append({'candidate_sha256':hashlib.sha256(image).hexdigest(),
                    'selected_presets':report['selected_presets']})
    def write(address,data):
        for off in range(0,len(data),1024):
            v=data[off:off+1024]
            assert s.remote.command(f'M{address+off:x},{len(v):x}:'+v.hex())=='OK'
        assert s.remote.memory(address,len(data))==data
    def word(address):return int.from_bytes(s.remote.memory(address,4),'little')
    def current():return int(s.input(0x1046109a,[])['registers']['r0'],16)
    def touch(x,y):
        for contact in (1,0):
            packet=bytearray(60);struct.pack_into('<I',packet,0,1)
            packet[8],packet[9]=contact,1;struct.pack_into('<2H',packet,0x12,x,y)
            s.input(0x103ce7e6,[('scratch',0)],payload=packet);s.run_for(.5)
    def activate(page,ident,row):
        # The eight-row stamp page can still be building after .25 s; a touch then misses.
        s.input(0x1045d54c,[page,ident]);s.run_for(1)
        for _ in range(3):
            touch(250,86+row*52)
            if current()!=page:break
    def frame(name):
        samples=[]
        for _ in range(5):
            s.run_for(.25);raw=(s.graphics/'gui-latest.ppm').read_bytes()
            pixels=raw.split(b'\n',3)[3]
            white=sum(all(v>=190 for v in pixels[j:j+3]) for j in range(0,len(pixels),3))
            samples.append((white,len(samples),raw))
        raw=max(samples,key=lambda x:x[:2])[2]
        (s.out/(name+'.png')).write_bytes(png_from_ppm(raw))
        assert max(x[0] for x in samples)>500
        s.audit.append({'frame':name,'white_pixels':max(x[0] for x in samples)})
    try:
        scatter=[]
        for e in report['edits']:
            address=0x10020000+int(e['offset'],16)
            old,new=bytes.fromhex(e['expected_hex']),bytes.fromhex(e['replacement_hex'])
            assert s.remote.memory(address,len(old))==old
            write(address,new)
            if 0x11f81c94<=address and address+len(old)<=0x12079eec:
                scatter.append((0xc1800000+address-0x11f81c94,new))
        size=(a.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000+size-2,image[size-2:])
        s.input(0x102355ee,[0xc182e688,1]);s.run_for(2)
        for address,new in scatter:write(address,new)
        for item in fixture['services']:
            write(item['address'],bytes.fromhex(item['code']));write(item['site'],bytes.fromhex(item['jump']))
        write(fixture['config'],bytes(16))
        s.audit.append({'diagnostic_sd_services':True,'physical_card':False})
        lang,font={'en':(3,1),'zh-Hans':(26,8),'zh-Hant':(27,9)}[a.language]
        s.input(0x10465990,[0x1400,lang]);s.input(0x10452cc0,[font])
        s.input(0x1045f732,[2,397]);s.run_for(.5)
        s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
        state=word(int(sound['state_anchor'],16));assert state
        root=state+sound['page_offsets']['955'];page=state+stamp['page_offset']
        root_ids=sound['control_identifiers']['955'];ids=sound['control_identifiers']['964']
        category=next((c for c in sound.get('menu_categories',[]) if c['key']=='stamp'),None)
        stamp_row=category['row'] if category else len(root_ids)-1
        stamp_control=root_ids[stamp_row]
        assert current()==root
        frame('01-root')
        assert word(state+stamp['state_offset']) == 0
        color=0
        for value in (5,2,3,4,1,0,2):
            activate(root,stamp_control,stamp_row);assert current()==page
            frame(f'02-choices-{value}')
            activate(page,ids[value],value);s.run_for(.5)
            assert current()==root,(value,hex(current()),hex(root))
            assert word(state+stamp['state_offset'])==value
            config=s.remote.memory(fixture['config'],16)
            magic,reserved,selected,check=struct.unpack('<4I',config)
            assert (magic,reserved,selected,check)==(0x31544457,color,value,~(0x31544457^color^value)&0xffffffff)
            frame(f'03-selected-{value}')
            activate(root,stamp_control,stamp_row);assert current()==page
            marks=[]
            for slot in range(sound['text_slots']['964']['value'],sound['text_slots']['964']['value']+6):
                length,ptr,style=struct.unpack('<3I',s.remote.memory(0xc2013368+slot*12,12))
                data=s.remote.memory(ptr,length*(2 if lang in (26,27) else 1)) if length else b''
                marks.append(data.decode('utf-16-le' if lang in (26,27) else 'ascii'))
            assert marks==['*' if i==value else '' for i in range(6)]
            frame(f'04-marker-{value}')
            for attempt in range(3):
                touch(675,28)
                if current()==root:break
            assert current()==root,(hex(current()),hex(root),hex(word(page+4)))
            observations.append(dict(selected=value,config=config.hex(),marks=marks))
        # The color and glow rows stay on the page, step their value and save it.
        activate(root,stamp_control,stamp_row);assert current()==page
        # The page must finish constructing, or the focus request below is dropped.
        frame('05-color-start')
        color_row,glow_row,glow=stamp['color_row'],stamp['glow_row'],stamp['glow_flag']
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
        from z30_extensions.features.ui_text import TRANSLATIONS
        def shown(slot):
            length,ptr,_=struct.unpack('<3I',s.remote.memory(0xc2013368+slot*12,12))
            data=s.remote.memory(ptr,length*(2 if lang in (26,27) else 1)) if length else b''
            return data.decode('utf-16-le' if lang in (26,27) else 'ascii')
        def local(text):return text if lang==3 else TRANSLATIONS[text][lang==27]
        for row,expected in ((color_row,1),(color_row,2),(color_row,3),(color_row,0),
                             (glow_row,glow),(color_row,glow|1),(glow_row,1)):
            # activate() retries while the page is unchanged, which these rows never leave.
            s.input(0x1045d54c,[page,ids[row]]);s.run_for(.25)
            previous=word(state+stamp['color_offset'])
            for _ in range(3):
                touch(250,86+row*52);s.run_for(.5)
                if word(state+stamp['color_offset'])!=previous:break
            actual=word(state+stamp['color_offset'])
            assert current()==page and actual==expected,(hex(current()),hex(page),row,actual,expected)
            assert struct.unpack('<4I',s.remote.memory(fixture['config'],16))[1]==expected
            mark=sound['text_slots']['964']['value']
            assert shown(mark+color_row)==local(stamp['colors'][expected%glow]),shown(mark+color_row)
            assert shown(mark+glow_row)==local('ON' if expected&glow else 'OFF'),shown(mark+glow_row)
            frame(f'05-{"glow" if row==glow_row else "color"}-{expected}')
            observations.append(dict(row=row,style=expected))
        # Reopening the page for each step must not bury the way back.
        for attempt in range(3):
            touch(675,28)
            if current()==root:break
        assert current()==root,(hex(current()),hex(root))
        s.audit.append(dict(date_stamp_menu=observations,language=a.language,
                            diagnostic_sd_services=True,physical_verified=False))
        s.write_audit()
        print('PASS native date stamp menu:',a.language,flush=True)
    finally:s.close()


if __name__=='__main__':main()
