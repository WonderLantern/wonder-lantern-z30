"""Native QEMU buffer-menu navigation with explicit SD service models."""
import argparse
import json
import struct
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-root','image','report','fixture','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--language',choices=('zh-Hans','en'),default='zh-Hans')
    p.add_argument('--isolate-menu',action='store_true',help='Diagnostic only: omit network loader hook')
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from display_session import Session,png_from_ppm
    from z30_extensions.features.wireless_ui import MAGIC
    report=json.loads(a.report.read_text());image=a.image.read_bytes();fixture=json.loads(a.fixture.read_text())
    feature=report['features']['wireless_buffer'];sound=report['features']['menu_framework']
    s=Session(str(a.output_dir.resolve()),fresh=True)
    checks=[]

    def write(address,data):
        for off in range(0,len(data),1024):
            part=data[off:off+1024]
            assert s.remote.command(f'M{address+off:x},{len(part):x}:'+part.hex())=='OK'
        assert s.remote.memory(address,len(data))==data

    def config(index):return struct.pack('<4I',MAGIC,0,index,~(MAGIC^index)&0xFFFFFFFF)
    def current():return int(s.input(0x1046109A,[])['registers']['r0'],16)
    def frame(name):
        samples=[]
        for i in range(6):
            s.run_for(.25);raw=(s.graphics/'gui-latest.ppm').read_bytes();px=raw.split(b'\n',3)[3]
            score=sum(px[j:j+3]!=bytes(3) for j in range(0,len(px),3))
            white=sum(all(v>=190 for v in px[j:j+3]) for j in range(0,len(px),3))
            samples.append((score,white,i,raw))
        raw=max(samples,key=lambda x:x[:3])[3]
        (s.out/(name+'.png')).write_bytes(png_from_ppm(raw))
        return name+'.png'

    try:
        scatter=[]
        for edit in report['edits']:
            address=0x10020000+int(edit['offset'],16)
            if a.isolate_menu and address==0x10396880:
                s.audit.append({'diagnostic_omitted_loader_hook':hex(address)})
                continue
            old,new=bytes.fromhex(edit['expected_hex']),bytes.fromhex(edit['replacement_hex'])
            assert s.remote.memory(address,len(old))==old
            write(address,new)
            if 0x11F81C94<=address and address+len(old)<=0x12079EEC:
                scatter.append((0xC1800000+address-0x11F81C94,new))
        stocksize=(a.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000+stocksize-2,image[stocksize-2:])
        s.input(0x102355EE,[0xC182E688,1]);s.run_for(2)
        for address,data in scatter:write(address,data)
        # Install file models only after native initialization, as in the
        # established settings QEMU fixture; never replace early boot I/O.
        for e in fixture['services']:
            write(e['address'],bytes.fromhex(e['code']));write(e['site'],bytes.fromhex(e['jump']))
        write(fixture['config'],config(1));write(fixture['wav_address'],bytes.fromhex(fixture['wav']))
        code,font=(26,8) if a.language=='zh-Hans' else (3,1)
        s.input(0x10465990,[0x1400,code]);s.input(0x10452CC0,[font])
        s.input(0x1045F732,[2,397]);s.run_for(1)
        s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
        checks.append(dict(root_frame=frame('01-root')))
        f={k:int(v,16)&~1 for k,v in feature['functions'].items()}
        for index in range(4):
            s.input(f['bitrate_open'],[]);s.run_for(1)
            page=current()
            assert int.from_bytes(s.remote.memory(page+2,2),'little')==feature['page_index']
            s.input(0x1045D54C,[page,feature['controls'][index]])
            s.run_for(.5)
            before=frame(f'02-choice-{index}')
            s.input(0x103CE96E,[0x27E]);s.run_for(1)
            active=current()
            assert int.from_bytes(s.remote.memory(active+2,2),'little')==955
            saved=s.remote.memory(fixture['config'],16)
            assert saved==config(index)
            state=int(s.input(f['bitrate_get'],[])['registers']['r0'],16)
            assert int.from_bytes(s.remote.memory(state,4),'little')==index
            checks.append(dict(index=index,requested_kib=64<<index,saved_config=saved.hex(),
                               selected_frame=before,root_frame=frame(f'03-saved-{index}')))
        s.input(0x1045F732,[2,397]);s.run_for(1)
        checks.append(dict(return_frame=frame('04-return-setup')))
        print('Native buffer-menu choices/save/return passed',flush=True)
    finally:
        (s.out/'wireless-checks.json').write_text(json.dumps(dict(checks=checks,language=a.language,
            scope='Native widgets/GPU/actions; explicitly modeled SD; no radio or camera'),indent=2)+'\n')
        s.close()


if __name__=='__main__':main()
