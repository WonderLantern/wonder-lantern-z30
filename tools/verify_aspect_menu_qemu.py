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
    p.add_argument('--profiles',type=int,nargs='+',choices=range(7),default=[1,2,3,4,5,6,0])
    p.add_argument('--trace-export-only',action='store_true',
                   help='Seed diagnostic state and verify normal submenu export without property mutation')
    p.add_argument('--verify-area-sync',action='store_true',
                   help='Exercise native DX change followed by custom-submenu backend synchronization')
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    from display_session import Session,png_from_ppm
    report=json.loads(a.report.read_text());image=a.image.read_bytes()
    fixture=json.loads(a.fixture.read_text())
    sound=report['features']['shutter_sound'];aspect=report['features']['aspect_profiles']
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
        if aspect.get('diagnostic_trace'):
            assert 'trace_address' in fixture,'Diagnostic image requires a separate trace-file SD fixture'
            write(fixture['trace_address'],bytes(aspect['diagnostic_trace']['bytes']))
        s.audit.append({'diagnostic_sd_services':True,'physical_card':False})
        lang,font={'en':(3,1),'zh-Hans':(26,8),'zh-Hant':(27,9)}[a.language]
        s.input(0x10465990,[0x1400,lang]);s.input(0x10452cc0,[font])
        s.input(0x1045f732,[2,397]);s.run_for(.5)
        s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
        state=word(int(sound['state_anchor'],16));assert state
        root=state+sound['page_offsets']['955'];page=state+aspect['page_offset']
        root_ids=sound['control_identifiers']['955'];ids=sound['control_identifiers']['961']
        category=next((c for c in sound.get('menu_categories',[]) if c['key']=='aspect'),None)
        aspect_row=category['row'] if category else len(root_ids)-1
        aspect_control=root_ids[aspect_row]
        assert current()==root
        frame('01-root')
        if a.trace_export_only:
            assert aspect.get('diagnostic_trace') and 'trace_address' in fixture
            from read_aspect_trace import decode
            # Explicit property/state fixture: avoid the background flash
            # persistence path, whose NOR command is not modeled by this guest.
            write(0xc2525bd4,struct.pack('<I',2))
            write(state+aspect['state_offset'],struct.pack('<I',1))
            s.input(0x1020bfda,[2,2])
            activate(root,aspect_control,aspect_row);assert current()==page
            frame('02-diagnostic-submenu')
            length=aspect['diagnostic_trace']['bytes'];start=fixture['trace_address']
            blob=b''.join(s.remote.memory(start+offset,min(1024,length-offset))
                          for offset in range(0,length,1024))
            parsed=decode(blob)
            assert any(r['event']==1 and r['profile']==1 and r['wh']==[2472,1856]
                       for r in parsed['records'])
            (s.out/'trace.json').write_text(json.dumps(parsed,indent=2),encoding='utf-8')
            touch(675,28);assert current()==root
            s.audit.append({'diagnostic_submenu_export_verified':True,'bytes':length,
                            'property_and_selection_seeded':True,'physical_card':False,
                            'normal_photo_capture':False})
            s.write_audit()
            print('PASS native submenu deferred trace export; explicitly seeded property/profile',flush=True)
            return
        # Native property read/write, without substituting the property API.
        s.input(0x10465990,[0x501,4])
        assert int(s.input(0x10465984,[0x501])['registers']['r0'],16)==4
        for profile in a.profiles:
            activate(root,aspect_control,aspect_row);assert current()==page
            frame(f'02-choices-before-{profile}')
            activate(page,ids[profile],profile);s.run_for(.5)
            assert current()==root,(profile,hex(current()),hex(root))
            assert word(state+aspect['state_offset'])==profile
            area=int(s.input(0x10465984,[0x501])['registers']['r0'],16)
            assert area==(2 if profile else 4),(profile,area)
            config=s.remote.memory(fixture['config'],16)
            assert struct.unpack('<4I',config)[:3]==(0x32414c57,4,profile)
            frame(f'03-selected-{profile}')
            if a.verify_area_sync and profile in (1,2):
                assert aspect.get('native_area_sync_version')
                s.input(0x10465990,[0x501,0])
                assert int(s.input(0x10465984,[0x501])['registers']['r0'],16)==0
                pointer=int(s.input(0x1020bfda,[2,2])['registers']['r0'],16)
                assert struct.unpack('<2H',s.remote.memory(pointer,4))==(2784,1560)
            if aspect.get('diagnostic_trace'):
                # Execute the real hooked selector, then export through normal
                # submenu entry. This is a diagnostic call, not a camera shot.
                s.input(0x1020bfda,[area,2])
            activate(root,aspect_control,aspect_row);assert current()==page
            if a.verify_area_sync and profile in (1,2):
                assert int(s.input(0x10465984,[0x501])['registers']['r0'],16)==2
                pointer=int(s.input(0x1020bfda,[2,2])['registers']['r0'],16)
                got=list(struct.unpack('<2H',s.remote.memory(pointer,4)))
                assert got==aspect['profiles'][profile-1]['jpeg_wh'][2]
                s.audit.append({'native_dx_change_guard_verified':True,
                                'custom_submenu_sync_to_16_9_verified':True,'profile':profile})
            if aspect.get('diagnostic_trace'):
                from read_aspect_trace import decode
                start=fixture['trace_address'];length=aspect['diagnostic_trace']['bytes']
                blob=b''.join(s.remote.memory(start+offset,min(1024,length-offset))
                              for offset in range(0,length,1024))
                parsed=decode(blob)
                assert any(r['event']==1 and r['profile']==profile for r in parsed['records'])
                if aspect.get('independent_border',{}).get('diagnostic'):
                    border=next(r['border'] for r in parsed['records'] if r['event']==5 and r['profile']==profile)
                    assert 'rectangle_channel_disabled' in border and 'grid_updater' in border
                    s.audit.append({'border_counter_dump_verified':True,'profile':profile,
                                    'camera_display_verified':False})
                (s.out/f'trace-{profile}.json').write_text(json.dumps(parsed,indent=2),encoding='utf-8')
                s.audit.append({'deferred_trace_dump_verified':True,'profile':profile,'bytes':length,
                                'actual_capture':False})
            frame(f'04-marker-{profile}')
            marks=[]
            for slot in range(sound['text_slots']['961']['value'],sound['text_slots']['961']['value']+7):
                length,ptr,style=struct.unpack('<3I',s.remote.memory(0xc2013368+slot*12,12))
                data=s.remote.memory(ptr,length*(2 if lang in (26,27) else 1)) if length else b''
                marks.append(data.decode('utf-16-le' if lang in (26,27) else 'ascii'))
            assert marks==['*' if i==profile else '' for i in range(7)]
            touch(675,28);assert current()==root
            observations.append({'profile':profile,'area':area,'selection_marks':marks,'config':config.hex()})
        frame('root-before-exit')
        for _ in range(3):
            touch(675,28)
            if current()!=root:break
        assert current() in (0xc1832d50,0xc1837160)
        frame('05-returned-stock')
        s.audit.append({'aspect_menu_verified':observations,'language':a.language,
                        'scope':'Assisted QEMU native menus and properties; diagnostic SD services; no camera capture'})
        s.write_audit()
        print(f'PASS {len(observations)} native aspect menu switches ({a.language})',flush=True)
    finally:s.close()


if __name__=='__main__':main()
