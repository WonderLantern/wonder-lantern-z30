"""QEMU native bitrate menu, record getter and preferences verification.

Uses explicit diagnostic SD-service overlays, never a physical card or encoder.
Native menu widgets and GPU frames execute without host-side drawing.
"""
import argparse
import json
import struct
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-root','image','report','fixture','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--language',choices=('en','zh-Hans','zh-Hant'),default='zh-Hans')
    p.add_argument('--representative-records',action='store_true',help='UI-language pass: check 1080 30p HIGH rather than all 30 records per profile')
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from display_session import Session,png_from_ppm
    from z30_extensions.features.bitrate_ui import MAGIC
    from verify_bitrate_profiles import config,INPUTS
    from z30_extensions.features.bitrate_slowmo_uncap import MODE_TABLE,RECORD,RATES,RECORDS,BITRATES_MBPS,rates
    from z30_extensions.features.bitrate_ui import BALANCED_MBPS
    report=json.loads(a.report.read_text());image=a.image.read_bytes();fixture=json.loads(a.fixture.read_text())
    sound=report['features']['shutter_sound'];video=report['features']['bitrate_profiles']
    s=Session(str(a.output_dir.resolve()));frames={};observations=[]
    def write(address,data):
        for off in range(0,len(data),1024):
            part=data[off:off+1024]
            assert s.remote.command(f'M{address+off:x},{len(part):x}:'+part.hex())=='OK'
        assert s.remote.memory(address,len(data))==data
    def word(address):return int.from_bytes(s.remote.memory(address,4),'little')
    def frame(name):
        samples=[]
        for i in range(8):
            s.run_for(.25);raw=(s.graphics/'gui-latest.ppm').read_bytes();pixels=raw.split(b'\n',3)[3]
            score=sum(pixels[j:j+3]!=bytes(3) for j in range(0,len(pixels),3))
            white=sum(all(v>=190 for v in pixels[j:j+3]) for j in range(0,len(pixels),3))
            samples.append((score,white,i,raw))
        raw=max(samples,key=lambda x:x[:3])[3];frames[name]=raw
        (s.out/(name+'.png')).write_bytes(png_from_ppm(raw))
        s.audit.append(dict(frame=name,native_white_samples=[v[1] for v in samples]))
        return raw
    def pixels(raw,box,white=False):
        data=raw.split(b'\n',3)[3];x0,y0,x1,y1=box
        return sum((all(v>=190 for v in data[(y*720+x)*3:(y*720+x)*3+3]) if white else
                    data[(y*720+x)*3:(y*720+x)*3+3]!=bytes(3))
                   for y in range(y0,y1) for x in range(x0,x1))
    def select(page,identifier):s.input(0x1045d54c,[page,identifier])
    def activate(page):
        row=int.from_bytes(s.remote.memory(page+22,2),'little')+int.from_bytes(s.remote.memory(page+24,2),'little')
        s.audit.append(dict(touch_activate_page=hex(page),selected_row=row));s.write_audit()
        # Diagnostic cursor selection and native redraw are asynchronous.
        # Require an actual page change, with bounded normal touch retries.
        for _ in range(3):
            touch(250,86+row*52)
            if current()!=page:break
    def current():return int(s.input(0x1046109a,[])['registers']['r0'],16)
    def touch(x,y):
        for contact in (1,0):
            packet=bytearray(60);struct.pack_into('<I',packet,0,1)
            packet[8],packet[9]=contact,1;struct.pack_into('<2H',packet,0x12,x,y)
            s.input(0x103ce7e6,[('scratch',0)],payload=packet);s.run_for(.5)
    try:
        scatter=[];base=0x10020000
        for edit in report['edits']:
            addr=base+int(edit['offset'],16);old=bytes.fromhex(edit['expected_hex']);new=bytes.fromhex(edit['replacement_hex'])
            assert s.remote.memory(addr,len(old))==old;write(addr,new)
            if 0x11f81c94<=addr and addr+len(old)<=0x12079eec:scatter.append((0xc1800000+addr-0x11f81c94,new))
        size=(a.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(base+size-2,image[size-2:]);s.input(0x102355ee,[0xc182e688,1]);s.run_for(2)
        for addr,new in scatter:write(addr,new)
        stock_records=[s.remote.memory(MODE_TABLE+i*RECORD,28) for i in range(30)]
        # Recover stock rate words from the stock file, since the candidate
        # still contains its guarded historical high-rate data edits.
        stock=(a.qemu_root/'.local/firmware/eg2090_0120a0.bi').read_bytes()
        for i in range(30):stock_records[i]=stock[MODE_TABLE-base+i*RECORD:MODE_TABLE-base+(i+1)*RECORD]
        for service in fixture['services']:
            write(service['address'],bytes.fromhex(service['code']));write(service['site'],bytes.fromhex(service['jump']))
        write(fixture['wav_address'],bytes.fromhex(fixture['wav']));write(fixture['config'],config(0))
        s.audit.append(dict(diagnostic_sd_services=[hex(v['site']) for v in fixture['services']],physical_card=False))
        # No menu: first native record read restores the saved stock profile.
        result=s.input(0x10355754,[('scratch',0),('scratch',16)],payload=bytes((1,2,0))+bytes(61))
        assert bytes.fromhex(result['scratch']['result'])[36:44]==rates((28,24))
        state=word(int(sound['state_anchor'],16));assert state
        assert word(state+video['state_offset'])==0
        observations.append(dict(first_record_without_menu_restored=True,profile=0))
        lang,font={'en':(3,1),'zh-Hans':(26,8),'zh-Hant':(27,9)}[a.language]
        s.input(0x10465990,[0x1400,lang]);s.input(0x10452cc0,[font])
        s.input(0x1045f732,[2,397]);s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
        root=state+sound['page_offsets']['955'];page=state+video['page_offset']
        root_ids=sound['control_identifiers']['955'];ids=sound['control_identifiers'][str(video['page_index'])]
        category=next((c for c in sound.get('menu_categories',[]) if c['key']=='bitrate'),None)
        bitrate_row=category['row'] if category else len(root_ids)-1
        bitrate_control=root_ids[bitrate_row]
        for profile in range(3):
            select(root,bitrate_control);activate(root);s.run_for(1)
            active=current();assert active==page,(hex(active),hex(page),s.remote.memory(root+20,8).hex())
            assert word(page+36)!=0
            raw=frame('profile-menu-'+str(profile));assert pixels(raw,(14,16,50,470))>500
            select(page,ids[profile]);activate(page);s.run_for(1)
            assert current()==root
            assert word(state+video['state_offset'])==profile
            assert s.remote.memory(fixture['config'],16)==config(profile)
            raw=frame('profile-selected-'+str(profile));assert pixels(raw,(14,16,50,470))>500
            record_indices=(10,) if a.representative_records else range(30)
            for index in record_indices:
                mode=INPUTS[index]
                result=s.input(0x10355754,[('scratch',0),('scratch',16)],payload=bytes(mode)+bytes(61))
                record=bytes.fromhex(result['scratch']['result'])[16:44]
                expected=bytearray(stock_records[index])
                for name,pair in ({},BALANCED_MBPS,BITRATES_MBPS)[profile].items():
                    if RECORDS[name]==index:expected[RATES:RATES+8]=rates(pair)
                assert record==expected,(profile,index)
            # Reopen and require exactly one selection marker in guest text slots.
            select(root,bitrate_control);activate(root);s.run_for(1);frame('profile-mark-'+str(profile))
            marks=[]
            for slot in range(sound['text_slots']['960']['value'],sound['text_slots']['960']['value']+3):
                length,ptr,style=struct.unpack('<3I',s.remote.memory(0xc2013368+slot*12,12))
                text=s.remote.memory(ptr,length*(2 if lang in (26,27) else 1)) if length else b''
                marks.append(text.decode('utf-16-le' if lang in (26,27) else 'ascii'))
            assert marks==['*' if i==profile else '' for i in range(3)]
            # Native right-upper back event, not the apply callback.
            touch(675,28);assert current()==root
            observations.append(dict(profile=profile,native_records_checked=len(record_indices),card_record_saved=True,selection_marks=marks))
        # A deterministic raw write failure exercises the actual error page.
        write(0x102c8a6a,bytes.fromhex('01207047'))
        s.audit.append(dict(diagnostic_write_failure='0x102c8a6a',candidate_file_unchanged=True))
        select(root,bitrate_control);activate(root)
        assert current()==page;frame('save-error-source')
        for close_name,coords in (('back',(675,28)),('ok',(350,310))):
            select(page,ids[1]);activate(page);s.run_for(1);assert word(state+52)==8
            assert word(state+video['state_offset'])==1
            popup=state+sound['page_offsets']['959'];assert word(popup+4)==page and current()==popup
            raw=frame('save-error-'+close_name);assert pixels(raw,(150,150,620,260),white=True)>100
            touch(*coords);frame('save-error-'+close_name+'-closed');assert word(state+52)==0 and current()==page
        touch(675,28);assert current()==root;frame('returned-root')
        touch(675,28)
        active=current();assert active in (0xc1832d50,0xc1837160),hex(active)
        raw=frame('returned-stock');assert pixels(raw,(14,16,50,470))>500
        assert pixels(raw,(636,4,716,52))==0
        observations.append(dict(save_error_body=True,native_back_clears_error=True,stock_back_corner_erased=True))
        s.audit.append(dict(bitrate_profiles_verified=observations,language=a.language,scope=__doc__));s.write_audit()
        print(f'{len(observations)} QEMU bitrate cases passed ({a.language})',flush=True)
    finally:s.close()


if __name__=='__main__':main()
