"""Execute projected frame/grid generation, clipping, alignment and guards."""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools')]
FORMAT = struct.Struct('<hhIHHhhIHHHHBBH')


def expected(profile, crop, viewport, source, divisions, include_borders=True):
    from z30_extensions.features.aspect.frame import axis
    x0,y0,x1,y1 = viewport
    vw,vh = x1-x0,y1-y0
    x,y,w,h = crop
    if profile in (1,2):
        sx,sy,ex,ey=source
        div=lambda a,b: (abs(a)//b)*(-1 if a<0 else 1)
        left=x0+div((x-sx)*vw,ex-sx)
        right=x0+div((x+w-sx)*vw,ex-sx)
        top=y0+div((y-sy)*vh,ey-sy)
        bottom=y0+div((y+h-sy)*vh,ey-sy)
    else:
        if vw*h>=vh*w:
            width,height=vh*w//h,vh
        else:
            width,height=vw,vw*h//w
        width,height=width&~3,height&~3
        left=(x0+(vw-width)//2)&~1
        top=(y0+(vh-height)//2)&~1
        right,bottom=left+width,top+height
    ax,ay=axis(left,right,x0,x1,divisions,include_borders=include_borders),axis(top,bottom,y0,y1,divisions,include_borders=include_borders)
    if ax is None or ay is None:
        return None
    a,b,first_x,step_x,nx=ax
    c,d,first_y,step_y,ny=ay
    return [a,first_y,b-a,step_y,ny,first_x,c,d-c,step_x,nx]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','image','report','output','deps'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    from z30_extensions.features.aspect.frame import axis
    from verify_sound_settings import Fixture
    stock=extract_eg(a.source.read_bytes());image=a.image.read_bytes();report=json.loads(a.report.read_text('utf-8'))
    feature=report['features']['aspect_profiles'];f={k:int(v,16) for k,v in feature['functions'].items()}
    c=Fixture(stock,image,report,{},properties={0x501:2});c.map(0xC237C380,256)
    grid,view,scratch=0x50080000,0x50081000,0x50082000
    axis_cases=[]
    for start,end,lo,hi in ((0,720,0,720),(-44,524,0,480),(40,680,0,720),(-40,20,0,480),
                             (500,800,0,480),(0,7,0,720),(0,70000,0,720),(0,480,20,500),
                             (0,16,0,16)):
        for divisions in (0,3,4,5):
            c.u.mem_write(scratch,struct.pack('<5i',start,end,lo,hi,divisions)+bytes(20))
            value=c.call(f['aspect_frame_axis'],(scratch,));want=axis(start,end,lo,hi,divisions,include_borders=feature.get('reference_frame_borders',True))
            assert bool(value)==(want is not None)
            if want is not None:
                assert struct.unpack('<5i',c.u.mem_read(scratch+20,20))==want
            axis_cases.append(dict(start=start,end=end,lower=lo,upper=hi,divisions=divisions,accepted=bool(value)))
    cases=[]
    for source in ((0,0,5568,3712),(0,292,5568,3420)):
        c.u.mem_write(0xC237C3D2,struct.pack('<4H',*source))
        for viewport in ((0,0,720,480),(0,36,720,442),(20,12,500,652),(0,0,1920,1080),(0,0,480,720)):
            c.u.mem_write(view,struct.pack('<4h',*viewport))
            for profile in range(1,7):
                for style in (0,1):
                    original=FORMAT.pack(0,160,720,160,2+style,240,0,480,240,2+style,9,1,0,0,0)
                    c.u.mem_write(grid,original)
                    value=c.call(f['aspect_frame_prepare'],(grid,view,int(feature['geometry_table'],16)+16*profile,profile))
                    raw=bytes(c.u.mem_read(grid,32));got=FORMAT.unpack(raw)
                    crop=feature['profiles'][profile-1]['large_xywh']
                    want=expected(profile,crop,viewport,source,style+3,feature.get('reference_frame_borders',True))
                    assert want is not None and value==1 and list(got[:10])==want
                    assert raw[24:]==original[24:]
                    hx,hy,w,dy,nh,vx,vy,h,dx,nv=got[:10]
                    assert not any(v%2 for v in (hx,hy,w,dy,vx,vy,h,dx))
                    assert nh>=1 and nv>=1
                    assert hy+(nh-1)*dy+2<=viewport[3] and vx+(nv-1)*dx+2<=viewport[2]
                    if profile in (1,2) and source[1]==292:
                        assert nh==style+2
                        assert nv==style+(4 if feature.get('reference_frame_borders',True) else 2)
                        ideal=viewport[0]+crop[0]*(viewport[2]-viewport[0])/5568
                        assert abs((vx-dx if not feature.get('reference_frame_borders',True) else vx)-ideal)<=6
                    cases.append(dict(profile=profile,viewport=viewport,source=source,style=style,
                                      fields=list(got),parameters=raw.hex()))
    guards=[]
    for source,viewport,count in (((0,0,0,0),(0,0,720,480),2),
                                  ((0,828,5568,2884),(0,0,720,480),2),
                                  ((0,292,5568,3420),(0,0,0,480),2),
                                  ((0,292,5568,3420),(-2,0,720,480),2),
                                  ((0,292,5568,3420),(0,0,9000,480),2),
                                  ((0,292,5568,3420),(0,0,720,480),4)):
        c.u.mem_write(0xC237C3D2,struct.pack('<4H',*source));c.u.mem_write(view,struct.pack('<4h',*viewport))
        original=FORMAT.pack(0,160,720,160,count,240,0,480,240,count,9,1,0,0,0);c.u.mem_write(grid,original)
        assert c.call(f['aspect_frame_prepare'],(grid,view,int(feature['geometry_table'],16)+16,1))==0
        assert bytes(c.u.mem_read(grid,32))==original
        guards.append(dict(source=source,viewport=viewport,count=count,grid_kept_stock=True))
    result=dict(image_sha256=sha256(image),axis_cases=axis_cases,frame_cases=cases,guards=guards,
                camera_verified=False,limits=['Viewport and source window are explicit inputs.',
                    'Borders outside native visible source are clipped; full DX photo height is not newly displayed.',
                    'Selected grid style is regenerated inside the photo region, not retained at old full-view coordinates.',
                    'Native grid remains required; this is one combined grid, not a GPU overlay.'])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {len(axis_cases)} axis cases, {len(cases)} projected frame/grid cases, {len(guards)} unchanged-grid guards')


if __name__=='__main__':main()
