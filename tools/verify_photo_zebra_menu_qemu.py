"""Native QEMU menu/touch checks with explicit diagnostic SD services."""
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
    sound=report['features']['shutter_sound'];zebra=report['features']['photo_zebra_control']
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
        s.input(0x1045d54c,[page,ident]);s.run_for(.25)
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
        root=state+sound['page_offsets']['955'];page=state+zebra['page_offset']
        root_ids=sound['control_identifiers']['955'];ids=sound['control_identifiers']['963']
        category=next((c for c in sound.get('menu_categories',[]) if c['key']=='zebra'),None)
        zebra_row=category['row'] if category else len(root_ids)-1
        zebra_control=root_ids[zebra_row]
        assert current()==root
        frame('01-root')
        assert word(state+zebra['state_offset']) == 0
        for value in (1,0,1):
            activate(root,zebra_control,zebra_row);assert current()==page
            frame(f'02-choices-{value}')
            activate(page,ids[value],value);s.run_for(.5)
            assert current()==root,(value,hex(current()),hex(root))
            assert word(state+zebra['state_offset'])==value
            config=s.remote.memory(fixture['config'],16)
            magic,reserved,selected,check=struct.unpack('<4I',config)
            assert (magic,reserved,selected,check)==(0x315a4c57,0,value,~(0x315a4c57^value)&0xffffffff)
            frame(f'03-selected-{value}')
            activate(root,zebra_control,zebra_row);assert current()==page
            marks=[]
            for slot in range(sound['text_slots']['963']['value'],sound['text_slots']['963']['value']+2):
                length,ptr,style=struct.unpack('<3I',s.remote.memory(0xc2013368+slot*12,12))
                data=s.remote.memory(ptr,length*(2 if lang in (26,27) else 1)) if length else b''
                marks.append(data.decode('utf-16-le' if lang in (26,27) else 'ascii'))
            assert marks==['*' if i==value else '' for i in range(2)]
            frame(f'04-marker-{value}')
            for attempt in range(3):
                touch(675,28)
                if current()==root:break
            assert current()==root,(hex(current()),hex(root),hex(word(page+4)))
            observations.append(dict(selected=value,config=config.hex(),marks=marks))
        s.audit.append(dict(photo_zebra_switch_menu=observations,language=a.language,
                            diagnostic_sd_services=True,physical_verified=False))
        s.write_audit()
        print('PASS native photo zebra menu:',a.language,flush=True)
    finally:s.close()


if __name__=='__main__':main()
