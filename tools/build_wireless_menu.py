"""Build US-region research firmware with a persistent native buffer menu."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.features.sound.shutter_sound import add
from build_wireless_region_trial import ENTRY, BEFORE


def build(stock):
    plan=Plan(stock)
    add(plan,wireless=True)
    plan.edit(ENTRY,struct.pack('<4siI',b'US',-1,7),'Wireless profile 7: US/-1',BEFORE)
    image,report=plan.finish()
    report.update(preset='wireless-buffer-menu-research',enabled_features=['shutter-sound-menu-framework','US-region','wireless-buffer-menu'],
                  research=True,includes_full_features=False)
    return image,report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    metadata=a.output.with_suffix('.json')
    if a.output.exists() or metadata.exists():p.error('Choose new output paths')
    image,report=build(extract_eg(a.source.read_bytes()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('xb') as f:f.write(image)
    with metadata.open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
        f.write('\n')
    print('Built wireless menu research image; validation pending')


if __name__=='__main__':main()
