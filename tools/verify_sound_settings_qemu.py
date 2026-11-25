"""QEMU sound preferences checks with explicitly substituted SD service boundaries.

Candidate ARM save, restore, WAV validation/conversion and allocation execute.
The SD fixture is not a physical card or an unassisted camera boot.
"""
import argparse,json,struct,sys,io,wave
from pathlib import Path

MODEL=0x12100000
CONFIG=0xc6001000
WAV=0x12101000

def prepare(target):
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
 from z30_extensions.assembly import constant as c, invoke as n, assemble, jump
 from z30_extensions.features.menu.layout import CONFIG_MAGIC, SUCCESS
 w=io.BytesIO()
 with wave.open(w,'wb') as a:a.setnchannels(1);a.setsampwidth(2);a.setframerate(8000);a.writeframes(b'\x01\x00'*80)
 wav=w.getvalue()
 sources={
  0x103382a2:f'''ldr r3,[r0];ldrb r3,[r3,#3];cmp r3,#87;ite eq;moveq r3,#7;movne r3,#8;
     str r3,[r0,#8];{c('r0',SUCCESS)}bx lr;''',
  0x10338414:f'''push {{r4-r8,lr}};mov r4,r3;mov r5,r1;ldr r0,[r0,#8];cmp r0,#7;bne wav;
     {c('r6',CONFIG)}movs r7,#16;b copy;wav:{c('r6',WAV)}movs r7,#{len(wav)};
     copy:cmp r2,r7;it lo;movlo r7,r2;mov r0,r5;mov r1,r6;mov r2,r7;{n(0x10199bdc,thumb=False)}
     str r7,[r4];{c('r0',SUCCESS)}pop {{r4-r8,pc}};''',
  0x1033832e:f'''mvn r1,#0;str r1,[r0,#8];{c('r0',SUCCESS)}bx lr;''',
  0x102c89ee:'movs r0,#0;bx lr;',
  0x102c8a6a:f'''push {{r4-r6,lr}};mov r4,r2;mov r5,r3;{c('r0',CONFIG)}
     {n(0x10199bdc,thumb=False)}str r4,[r5];movs r0,#0;pop {{r4-r6,pc}};''',
 }
 edits=[]
 for idx,(site,source) in enumerate(sources.items()):
  address=MODEL+idx*256;raw=assemble(source,address,256)
  edits.append(dict(site=site,address=address,code=raw.hex(),jump=jump(site,address,12).hex()))
 target.write_text(json.dumps(dict(config=CONFIG,wav_address=WAV,wav=wav.hex(),magic=CONFIG_MAGIC,services=edits),indent=2)+'\n')

def verify(a):
 sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
 from display_session import Session
 fixture=json.loads(a.fixture.read_text());report=json.loads(a.report.read_text());image=a.image.read_bytes();base=0x10020000
 s=Session(str(a.output_dir.resolve()));observations=[]
 def write(address,data):
  for off in range(0,len(data),1024):
   part=data[off:off+1024];assert s.remote.command(f'M{address+off:x},{len(part):x}:'+part.hex())=='OK'
  assert s.remote.memory(address,len(data))==data
 def cfg(enabled,selected):
  m=fixture['magic'];return struct.pack('<4I',m,enabled,selected,~(m^enabled^selected)&0xffffffff)
 try:
  scatter=[]
  for e in report['edits']:
   address=base+int(e['offset'],16);old=bytes.fromhex(e['expected_hex']);new=bytes.fromhex(e['replacement_hex']);assert s.remote.memory(address,len(old))==old;write(address,new)
   if 0x11f81c94<=address and address+len(old)<=0x12079eec:scatter.append((0xc1800000+address-0x11f81c94,old,new))
  size=(a.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size;write(base+size-2,image[size-2:])
  s.input(0x102355ee,[0xc182e688,1]);s.run_for(2)
  for address,old,new in scatter:
   assert s.remote.memory(address,len(old)) in (old,new);write(address,new)
  # Explicit diagnostic ROM overlays replace only named file services. Never
  # modify source files or claim that these are native SD hardware results.
  for e in fixture['services']:
   write(e['address'],bytes.fromhex(e['code']));write(e['site'],bytes.fromhex(e['jump']))
   s.audit.append({'diagnostic_sd_service_overlay':hex(e['site']),'fixture_code':hex(e['address'])})
  write(WAV,bytes.fromhex(fixture['wav']));write(CONFIG,cfg(1,3))
  sound=report['features']['shutter_sound'];f={k:int(v,16)&~1 for k,v in sound['functions'].items()}
  anchor=int(sound['state_anchor'],16);assert int.from_bytes(s.remote.memory(anchor,4),'little')in (0,int(sound['anchor_table'],16))
  # No menu entry: the photo trigger allocates state and restores preferences.
  # Keep silent-photo gating OFF so this check never starts an audio job.
  s.input(0x10465990,[0x57b,0]);s.input(f['trigger'],[])
  state=int.from_bytes(s.remote.memory(anchor,4),'little')
  def word(off):return int.from_bytes(s.remote.memory(state+off,4),'little')
  assert word(36)==1 and word(44)==3 and word(40)>0
  observations.append({'first_photo_without_menu':True,'enabled':word(36),'selected':word(44),'pcm_bytes':word(40)})
  for enabled in (0,1):
   for selected in range(5):
    write(state+36,struct.pack('<I',enabled));write(state+44,struct.pack('<I',selected))
    result=s.input(f['save_settings'],[]);assert int(result['registers']['r0'],16)==1
    assert s.remote.memory(CONFIG,16)==cfg(enabled,selected)
    write(state+36,bytes(12));s.input(f['restore_settings'],[])
    assert word(36)==enabled and word(44)==selected and bool(word(40))==bool(enabled)
    observations.append({'saved_and_reloaded':True,'enabled':enabled,'selected':selected})
  write(CONFIG,bytes(16));write(state+36,bytes(12));s.input(f['restore_settings'],[]);assert word(36)==0 and word(40)==0
  observations.append({'corrupt_record_keeps_off':True})
  s.audit.append({'sound_preferences_verified':observations,'scope':'Assisted QEMU; SD services substituted explicitly; no physical SD or audio validation'});s.write_audit()
  print(f'{len(observations)} QEMU sound preferences cases passed',flush=True)
 finally:s.close()

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prepare-fixture',type=Path);p.add_argument('--fixture',type=Path);p.add_argument('--qemu-root',type=Path);p.add_argument('--image',type=Path);p.add_argument('--report',type=Path);p.add_argument('--output-dir',type=Path);a=p.parse_args()
 if a.prepare_fixture:prepare(a.prepare_fixture)
 else:
  if not all((a.fixture,a.qemu_root,a.image,a.report,a.output_dir)):p.error('Verification needs fixture, qemu-root, image, report and output-dir')
  verify(a)
