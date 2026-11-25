"""Execute sound save/restore with explicit SD fixtures, including a fresh RAM boot.
No physical card/audio model; built ARM routines execute under Unicorn.
"""
import argparse,json,struct,sys,wave,io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from native_runtime import Context,RAM
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_PC,UC_ARM_REG_LR
from z30_extensions.container import extract_eg
from z30_extensions.build import build
from z30_extensions.features.menu.layout import *

class Fixture(Context):
 def __init__(self,stock,image,report,files,short_write=False,close_error=False,initialize=True,properties=None):
  super().__init__(stock,image)
  self.f={k:int(v,16) for k,v in report['features']['shutter_sound']['functions'].items()}
  self.files=files;self.opened={};self.fd=3;self.alloc=RAM+0x10000;self.short_write=short_write;self.close_error=close_error
  self.calls=[]
  for a in (0x10211f4c,0x103382a2,0x10338414,0x102c8a6a,0x1033832e,0x102c89ee):self.u.hook_add(UC_HOOK_CODE,self.service,begin=a,end=a)
  if properties is not None:
   def property_api(u,a,n,d):
    key=u.reg_read(UC_ARM_REG_R0)
    if a==0x10465990:properties[key]=u.reg_read(UC_ARM_REG_R1);value=1
    else:value=properties.get(key,0)
    self.ret(value)
   for a in (0x10465984,0x10465990,0x101993de):self.u.hook_add(UC_HOOK_CODE,property_api,begin=a,end=a)
  self.state=self.call(self.f['ensure']) if initialize else 0
 def word(self,off):return int.from_bytes(self.u.mem_read(self.state+off,4),'little')
 def set(self,off,value):self.u.mem_write(self.state+off,struct.pack('<I',value))
 def ret(self,value):self.u.reg_write(UC_ARM_REG_R0,value);self.u.reg_write(UC_ARM_REG_PC,self.u.reg_read(UC_ARM_REG_LR))
 def service(self,u,a,n,d):
  r=[u.reg_read(x) for x in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)]
  self.calls.append(hex(a))
  if a==0x10211f4c:return self.ret(self.alloc)
  if a==0x103382a2:
   ptr=int.from_bytes(u.mem_read(r[0],4),'little');path=bytes(u.mem_read(ptr,64)).split(b'\0')[0].decode()
   if r[1]==0x301:self.files[path]=b''
   elif path not in self.files:return self.ret(SUCCESS+1)
   self.fd+=1;self.opened[self.fd]=(path,0);u.mem_write(r[0]+8,struct.pack('<I',self.fd));return self.ret(SUCCESS)
  if a==0x10338414:
   fd=int.from_bytes(u.mem_read(r[0]+8,4),'little');path,pos=self.opened[fd];data=self.files[path][pos:pos+r[2]]
   if data:u.mem_write(r[1],data)
   u.mem_write(r[3],struct.pack('<I',len(data)));self.opened[fd]=(path,pos+len(data));return self.ret(SUCCESS)
  if a==0x102c8a6a:
   path,pos=self.opened[r[0]];count=r[2]-(1 if self.short_write else 0);self.files[path]=bytes(u.mem_read(r[1],count));u.mem_write(r[3],struct.pack('<I',count));return self.ret(0)
  fd=r[0] if a==0x102c89ee else int.from_bytes(u.mem_read(r[0]+8,4),'little')
  if self.close_error:return self.ret(1 if a==0x102c89ee else SUCCESS+1)
  self.opened.pop(fd,None)
  if a==0x1033832e:u.mem_write(r[0]+8,struct.pack('<i',-1))
  return self.ret(0 if a==0x102c89ee else SUCCESS)

def verify(stock,image,report):
 rows=[];w=io.BytesIO()
 with wave.open(w,'wb') as a:a.setnchannels(1);a.setsampwidth(2);a.setframerate(8000);a.writeframes(b'\x01\x00'*80)
 wav=w.getvalue()
 for enabled in (0,1):
  for selected in range(5):
   paths=['A:\\SHUTTER.WAV']+[f'A:\\SHUTTER\\{i}.WAV' for i in range(1,5)]
   files={p:wav for p in paths};first=Fixture(stock,image,report,files);first.set(ENABLED,enabled);first.set(SELECTED,selected)
   assert first.call(first.f['save_settings'])==1 and not first.opened
   second=Fixture(stock,image,report,files)
   assert second.word(ENABLED)==enabled and second.word(SELECTED)==selected and not second.opened
   assert bool(second.word(LENGTH))==bool(enabled)
   rows.append({'fresh_ram_boot':True,'enabled':enabled,'selected':selected,'loaded':bool(second.word(LENGTH))})
 valid=struct.pack('<4I',CONFIG_MAGIC,1,2,~(CONFIG_MAGIC^1^2)&0xffffffff)
 for payload in (b'',valid[:8],valid+b'X',valid[:-1]+b'\x00',struct.pack('<4I',CONFIG_MAGIC,2,0,0)):
  s=Fixture(stock,image,report,{'A:\\WL-SOUND.CFG':payload});assert s.word(ENABLED)==0 and s.word(LENGTH)==0
 rows.append({'corrupt_settings_rejected':5})
 for mode in ('short','close','missing_wav','invalid_wav'):
  files={'A:\\WL-SOUND.CFG':valid,'A:\\SHUTTER\\2.WAV':wav if mode!='invalid_wav' else b'bad'}
  if mode=='missing_wav':files.pop('A:\\SHUTTER\\2.WAV')
  s=Fixture(stock,image,report,files,short_write=mode=='short',close_error=mode=='close')
  if mode in ('short','close'):assert s.call(s.f['save_settings'])==0 and s.word(RESULT)==8
  else:assert s.word(ENABLED)==0 and s.word(LENGTH)==0
  rows.append({'failure':mode,'enabled':s.word(ENABLED),'result':s.word(RESULT)})
 return rows
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 stock=extract_eg(a.source.read_bytes());rows=verify(stock,a.image.read_bytes(),json.loads(a.report.read_text()))
 a.output.write_text(json.dumps({'scope':'Built ARM code; explicit SD services, no physical camera validation','cases':rows},indent=2)+'\n');print(f'{len(rows)} sound persistence cases passed')
