"""Extend the local QEMU SD fixture with an independent large diagnostic file."""
import argparse
import json
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--deps',type=Path)
    a=p.parse_args()
    if a.deps:sys.path.insert(0,str(a.deps.resolve()))
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from z30_extensions.assembly import assemble, constant as c, invoke as n
    fixture=json.loads(a.base.read_text())
    fixture['trace_address']=0xc6008000
    for service in fixture['services']:
        if service['site']==0x103382a2:
            source=f'''ldr r3,[r0];ldrb r2,[r3,#13];cmp r2,#68;bne normal;
                movs r3,#9;b done;normal:ldrb r3,[r3,#3];cmp r3,#87;
                ite eq;moveq r3,#7;movne r3,#8;
                done:str r3,[r0,#8];{c('r0',0x04370080)}bx lr;'''
        elif service['site']==0x102c8a6a:
            source=f'''push {{r4-r6,lr}};mov r4,r2;mov r5,r3;
                cmp r0,#9;bne config;{c('r0',fixture['trace_address'])}b copy;
                config:{c('r0',fixture['config'])}
                copy:{n(0x10199bdc,thumb=False)}str r4,[r5];movs r0,#0;pop {{r4-r6,pc}};'''
        else:continue
        service['code']=assemble(source,service['address'],256).hex()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(fixture,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
