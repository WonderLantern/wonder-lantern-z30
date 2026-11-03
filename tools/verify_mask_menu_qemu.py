"""Native QEMU menu/touch checks plus dynamic soft-mask live-view capture.

Replays the built image into the stock-gated display machine (same technique as
verify_aspect_menu_qemu), switches the native Photo aspect property, returns to
live view and captures the displayed frame. Expected regions are independently
computed from the profile's native crop and the current viewport/source window;
active profiles must have nonempty regions and visibly dim bright pixels.
"""
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
    p.add_argument('--geometry-fixture',action='store_true',help='Explicit viewport/source data absent from the assisted sensorless boot')
    p.add_argument('--observe-ui',action='store_true',help='Save repeated LCD frames and a temporary stock-submit comparison (diagnostic only)')
    p.add_argument('--stock-submit-control',action='store_true',help='Diagnostic control: restore stock submit before boot; no mask proof')
    p.add_argument('--verify-menu-categories',action='store_true',help='Exercise grouped root and sound submenu through native navigation')
    p.add_argument('--menus-only',action='store_true',help='Verify guest GPU menu frames without the assisted live-view LCD scanout model')
    p.add_argument('--photo-context-fixture',action='store_true',help='Explicit native photo-overlay eligibility fields absent from assisted sensorless boot')
    p.add_argument('--verify-photo-context',action='store_true',help='Exercise native nonphoto view kinds and readiness changes with fixed selected aspect; not an album/file decoder')
    a=p.parse_args()
    sys.path.insert(0,str(a.qemu_root.resolve()/'tools'))
    from display_session import Session,png_from_ppm
    from liveview_session import start, DEVICE_ENV
    report=json.loads(a.report.read_text());image=a.image.read_bytes()
    assert hashlib.sha256(image).hexdigest()==report['sha256'],'Build/report hash mismatch'
    fixture=json.loads(a.fixture.read_text())
    sound=report['features']['shutter_sound'];aspect=report['features']['aspect_profiles']
    s=Session(str(a.output_dir.resolve()),device_env={} if a.menus_only else DEVICE_ENV);observations=[];observations_mask=[]
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
    def back():
        # Follow the actual owned/native parent, including the sound category.
        parent=word(current()+4)
        ident=int.from_bytes(s.remote.memory(parent+2,2),'little')
        s.input(0x1045f732,[2,ident]);s.run_for(.25)
    def activate(page,ident,row):
        s.input(0x1045d54c,[page,ident]);s.run_for(.25)
        s.input(0x103ce96e,[0x27e]);s.run_for(.5)
    def frame(name):
        active=current();index=int.from_bytes(s.remote.memory(active+2,2),'little')
        expected=sound['pages'].get(str(index),{}).get('title_index')
        if expected is not None:
            for attempt in range(32):
                s.run_for(.25)
                group=word(active+12);registry=word(active+36)
                if not group or not registry:continue
                widget=int(s.input(0x10454210,[0xa18,group])['registers']['r0'],16)
                if widget and word(widget+8)==expected:break
            else:raise AssertionError(('Native page title never bound',index,expected))
        samples=[]
        for _ in range(8):
            s.run_for(.25);raw=(s.graphics/'gui-latest.ppm').read_bytes()
            pixels=raw.split(b'\n',3)[3]
            white=sum(all(v>=190 for v in pixels[j:j+3]) for j in range(0,len(pixels),3))
            samples.append((white,len(samples),raw))
        # The old brightest-frame heuristic selected a stale parent page when
        # its labels had more white pixels than the child. Keep the latest.
        raw=samples[-1][2]
        (s.out/(name+'.png')).write_bytes(png_from_ppm(raw))
        assert max(x[0] for x in samples)>500
        s.audit.append({'frame':name,'white_pixels':samples[-1][0],
                        'native_page':index,'native_title_slot':expected})
    soft=aspect.get('soft_mask') or {}
    assert soft.get('dynamic') and not soft.get('trial'),'expected a dynamic soft-mask build'
    SNAPSHOT=int(soft['viewport_snapshot'],16)
    SOURCE=int(soft['source_window'],16)
    POINTS=[(20,80),(700,80),(360,12),(360,468),(360,42),(360,432),
            (100,80),(650,80),(430,150),(430,330)]
    def raw_frame():
        """Return the most recent displayed frame (whole PPM bytes)."""
        last=None
        for _ in range(4):
            s.run_for(.25)
            last=(s.graphics/'gui-latest.ppm').read_bytes()
        return last
    def sample(raw):
        assert raw.split(b'\n',3)[1]==b'720 480', 'Must sample actual 720x480 live view'
        pixels=raw.split(b'\n',3)[3]
        return {f'{x},{y}':list(pixels[(y*720+x)*3:(y*720+x)*3+3]) for x,y in POINTS}
    def present():
        s.input(0x1032427e,[]);s.run_for(.4)
    def geometry():
        return dict(viewport=list(struct.unpack('<4h',s.remote.memory(SNAPSHOT+8,8))),
                    source=list(struct.unpack('<4H',s.remote.memory(SOURCE,8))))
    def apply_geometry_fixture():
        if not a.geometry_fixture:return
        original=geometry()
        write(SNAPSHOT+8,struct.pack('<4h',0,36,720,442))
        write(SOURCE,struct.pack('<4H',0,292,5568,3420))
        s.audit.append({'explicit_geometry_fixture':geometry(),'original_geometry':original,
                        'sensor_pipeline_verified':False})
    def photo_context():
        fields=soft['photo_context_fields']
        context=dict(kind=s.remote.memory(int(fields['view_kind'],16),1)[0],
                    busy=[word(int(v,16)) for v in fields['busy']],
                    backend=word(int(fields['backend'],16)),ready=word(int(fields['ready_flags'],16)))
        if fields.get('ui_service_phase'):
            context['ui_service_phase']=s.remote.memory(int(fields['ui_service_phase'],16),1)[0]
        return context
    def apply_photo_context_fixture():
        if not soft.get('native_photo_context_guard'):return
        original=photo_context()
        s.audit.append({'observed_photo_context':original})
        if not a.photo_context_fixture:return
        fields=soft['photo_context_fields']
        s.input(0x10310c52,[0])
        for address in fields['busy']:write(int(address,16),bytes(4))
        write(int(fields['backend'],16),struct.pack('<I',1))
        write(int(fields['ready_flags'],16),struct.pack('<I',original['ready']|1))
        s.audit.append({'explicit_photo_context_fixture':photo_context(),'original':original,
                        'physical_application_mode_verified':False})
    def expected_regions(profile, observed):
        if not profile:return []
        l,t,r,b=observed['viewport'];sx,sy,ex,ey=observed['source']
        assert 0<=l<r<=720 and 0<=t<b<=480,('invalid native viewport',observed)
        x,y,w,h=aspect['profiles'][profile-1]['large_xywh'];vw,vh=r-l,b-t
        if profile<=2:
            assert (sx,sy,ex,ey) in ((0,0,5568,3712),(0,292,5568,3420)),observed
            div=lambda a,b:(abs(a)//b)*(-1 if a<0 else 1)
            x0=l+div((x-sx)*vw,ex-sx);x1=l+div((x+w-sx)*vw,ex-sx)
            y0=t+div((y-sy)*vh,ey-sy);y1=t+div((y+h-sy)*vh,ey-sy)
        else:
            ow,oh=(vh*w//h,vh) if vw*h>=vh*w else (vw,vw*h//w)
            ow,oh=ow&~3,oh&~3
            x0=(l+(vw-ow)//2)&~1;y0=(t+(vh-oh)//2)&~1;x1=x0+ow;y1=y0+oh
        x0=max(l,(x0+1)&~1);y0=max(t,(y0+1)&~1)
        x1=min(r,x1&~1);y1=min(b,y1&~1)
        assert x0<x1 and y0<y1,observed
        if soft.get('coverage'):
            x0=0 if x0==l else x0;y0=0 if y0==t else y0
            x1=720 if x1==r else x1;y1=480 if y1==b else y1
            l,t,r,b=0,0,720,480
        result=[q for q in ((l,t,r,y0),(l,y1,r,b),(l,y0,x0,y1),(x1,y0,r,y1))
                if q[0]<q[2] and q[1]<q[3]]
        assert result,('active profile must have nonempty mask regions',profile,observed)
        return result
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
        if a.stock_submit_control:
            write(0x1055d112,bytes.fromhex('70b50c46054625f031f8'))
            s.audit.append({'stock_submit_control':True,'mask_pixels_verified':False})
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
        if not a.menus_only:
            start(s);apply_geometry_fixture();apply_photo_context_fixture();present();baseline_raw=raw_frame()
            grid_enabled=int(s.input(0x10465984,[0x1129])['registers']['r0'],16)
            assert grid_enabled==0,('This run must prove grid-OFF independence',grid_enabled)
            s.audit.append({'native_grid_enabled':grid_enabled})
            (s.out/'00-live-baseline.png').write_bytes(png_from_ppm(baseline_raw))
            baseline=sample(baseline_raw)
            s.audit.append({'live_baseline':baseline,'geometry':geometry()})
        s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
        state=word(int(sound['state_anchor'],16));assert state
        root=state+sound['page_offsets']['955'];page=state+aspect['page_offset']
        root_ids=sound['control_identifiers']['955'];ids=sound['control_identifiers']['961']
        categories={item['key']:item for item in sound.get('menu_categories',[])}
        aspect_row=categories['aspect']['row'] if categories else len(root_ids)-1
        aspect_control=root_ids[aspect_row]
        assert current()==root
        frame('01-root')
        if a.verify_menu_categories:
            assert {'aspect','sound'}<=set(categories),categories
            for key,item in categories.items():
                activate(root,item['control_id'],item['row'])
                target=state+sound['page_offsets'][str(item['page_index'])]
                assert current()==target,(key,hex(current()),hex(target))
                assert word(target+4)==root
                frame('category-'+key)
                if key=='sound':
                    sound_ids=sound['control_identifiers'][str(item['page_index'])]
                    activate(target,sound_ids[1],1)
                    files=state+sound['page_offsets']['956'];assert current()==files
                    assert word(files+4)==target
                    frame('sound-files')
                    back();assert current()==target
                    activate(target,sound_ids[0],0)
                    switches=state+sound['page_offsets']['957'];assert current()==switches
                    assert word(switches+4)==target
                    switch_ids=sound['control_identifiers']['957']
                    activate(switches,switch_ids[0],0);assert current()==target
                    assert word(state+36)==0
                    frame('sound-off-returned')
                back();assert current()==root
            s.audit.append({'native_menu_categories_verified':list(categories),
                            'sound_choices_parent_and_return_verified':True})
        if a.menus_only:
            assert a.verify_menu_categories,'Menu-only mode requires the category checks'
            back();assert current() in (0xc1832d50,0xc1837160)
            frame('returned-stock')
            s.audit.append({'menu_gpu_frames_only':True,'language':a.language,
                            'live_view_pixels_verified':False})
            s.write_audit()
            print('PASS native grouped menu hierarchy and GPU menu frames; no live-view pixel proof',flush=True)
            return
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
            length=aspect['diagnostic_trace']['bytes'];trace_base=fixture['trace_address']
            blob=b''.join(s.remote.memory(trace_base+offset,min(1024,length-offset))
                          for offset in range(0,length,1024))
            parsed=decode(blob)
            assert any(r['event']==1 and r['profile']==1 and r['wh']==[2472,1856]
                       for r in parsed['records'])
            (s.out/'trace.json').write_text(json.dumps(parsed,indent=2),encoding='utf-8')
            back();assert current()==root
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
            # Return to live view and capture the composited soft-mask result.
            back()
            for _ in range(3):
                if current()!=root:break
                back()
            assert current() in (0xc1832d50,0xc1837160),(profile,hex(current()))
            start(s);apply_geometry_fixture();apply_photo_context_fixture();present();raw=raw_frame();observed=geometry()
            expected=expected_regions(profile,observed)
            if a.stock_submit_control:expected=[]
            (s.out/f'04-live-{profile}.png').write_bytes(png_from_ppm(raw))
            if a.observe_ui:
                from z30_extensions.features.aspect_mask import SUBMIT_SITE,SUBMIT_EXPECTED
                patched=s.remote.memory(SUBMIT_SITE,10)
                for mode in ('active','stock-submit','restored'):
                    write(SUBMIT_SITE,bytes.fromhex(SUBMIT_EXPECTED) if mode=='stock-submit' else patched)
                    present()
                    for number in range(8):
                        s.run_for(.25)
                        snapshot=(s.graphics/'gui-latest.ppm').read_bytes()
                        (s.out/f'ui-{profile}-{mode}-{number}.png').write_bytes(png_from_ppm(snapshot))
                s.audit.append({'ui_observation':profile,'temporary_stock_submit_comparison':True})
            live=sample(raw)
            record={'live_profile':profile,'pixels':live,'baseline':baseline,
                    'geometry':observed,'expected_regions':expected}
            factor=(1-soft['alpha']/255)
            def dimmed(v,b):return all(abs(v[i]-round(b[i]*factor))<=12 for i in range(3))
            def unchanged(v,b):return all(abs(v[i]-b[i])<=6 for i in range(3))
            in_mask=[k for k in POINTS
                     if any(r[0]<=k[0]<r[2] and r[1]<=k[1]<r[3] for r in expected)]
            in_photo=[k for k in POINTS if k not in in_mask]
            dim_points=[k for k in in_mask
                        if dimmed(live[f'{k[0]},{k[1]}'],baseline[f'{k[0]},{k[1]}'])]
            clean=[k for k in in_photo
                   if unchanged(live[f'{k[0]},{k[1]}'],baseline[f'{k[0]},{k[1]}'])]
            record.update({'dimmed_points':[list(k) for k in dim_points],
                           'clean_points':[list(k) for k in clean],'checks_passed':False})
            observations_mask.append(record);s.audit.append(record)
            assert len(clean)==len(in_photo),(profile,'photo/background changed',clean,
                                             in_photo,live,baseline)
            bright=[k for k in in_mask if max(baseline[f'{k[0]},{k[1]}'])>=40]
            if profile and not a.stock_submit_control:
                assert expected and bright,('vacuous mask observation',profile,expected,in_mask)
                assert dim_points,(profile,'mask regions not dimmed',live,baseline,expected)
                for k in bright:
                    assert k in dim_points,(profile,'bright mask point not dimmed',k,
                                            live[f'{k[0]},{k[1]}'],
                                            baseline[f'{k[0]},{k[1]}'])
            if not profile or a.stock_submit_control:
                changed=[k for k in POINTS if not unchanged(live[f'{k[0]},{k[1]}'],
                                                            baseline[f'{k[0]},{k[1]}'])]
                record['changed_points']=[list(k) for k in changed]
                assert not changed,(profile,'OFF must restore baseline',changed,live,baseline)
            record['checks_passed']=True
            if a.verify_photo_context and profile and profile==a.profiles[0]:
                helper=int(aspect['functions']['aspect_mask_photo_context'],16)&~1
                context_checks=[]
                assert int(s.input(helper,[])['registers']['r0'],16)==1
                # Exact native photo-overlay predicate, invoked from InitTask.
                assert int(s.input(0x104be4a0,[])['registers']['r0'],16)==1
                original_context=photo_context();fields=soft['photo_context_fields']
                for index,(kind,ready) in enumerate(((2,original_context['ready']),
                        (0,original_context['ready']),
                        (0,original_context['ready']&~1),(0,original_context['ready']))):
                    s.input(0x10310c52,[kind]);write(int(fields['ready_flags'],16),struct.pack('<I',ready))
                    allowed=int(s.input(helper,[])['registers']['r0'],16)
                    native=int(s.input(0x104be4a0,[])['registers']['r0'],16)
                    assert allowed==native==int(kind in (0,6) and bool(ready&1))
                    present();snapshot=raw_frame();samples=sample(snapshot)
                    (s.out/f'photo-context-{index}.png').write_bytes(png_from_ppm(snapshot))
                    errors=[]
                    for k in bright:
                        key=f'{k[0]},{k[1]}'
                        valid=(dimmed(samples[key],baseline[key]) if allowed
                               else unchanged(samples[key],baseline[key]))
                        if not valid:errors.append(dict(point=list(k),actual=samples[key],baseline=baseline[key]))
                    item=dict(context=photo_context(),native_predicate=native,mask_predicate=allowed,
                              selected_profile=profile,errors=errors,passed=not errors,
                              view_kind_and_readiness_are_fixtures=True,actual_album_verified=False)
                    context_checks.append(item)
                    (s.out/'photo-context-checks.json').write_text(json.dumps(dict(
                        image_sha256=hashlib.sha256(image).hexdigest(),checks=context_checks),indent=2))
                    assert not errors,('photo context/stale mask',item)
                s.audit.append({'photo_context_checks':context_checks,'actual_album':False})
                if soft.get('retire_before_descriptor_filter'):
                    phase_checks=[];original_phase=photo_context()['ui_service_phase']
                    for index,phase in enumerate((4,2)):
                        # Original scalar setter; explicit lifecycle stimulus,
                        # not a claim that the real album app was entered.
                        s.input(0x10457dac,[phase])
                        allowed=int(s.input(helper,[])['registers']['r0'],16)
                        native=int(s.input(0x104be4a0,[])['registers']['r0'],16)
                        assert native==1 and allowed==int(phase==2)
                        present();snapshot=raw_frame();samples=sample(snapshot)
                        (s.out/f'lifecycle-stage-{phase}.png').write_bytes(png_from_ppm(snapshot))
                        errors=[]
                        for k in bright:
                            key=f'{k[0]},{k[1]}'
                            valid=(dimmed(samples[key],baseline[key]) if allowed else unchanged(samples[key],baseline[key]))
                            if not valid:errors.append(dict(point=list(k),actual=samples[key],baseline=baseline[key]))
                        item=dict(ui_stage=phase,native_photo_predicate=native,mask_predicate=allowed,
                                  errors=errors,passed=not errors,actual_album_verified=False)
                        phase_checks.append(item)
                        (s.out/'lifecycle-stage-checks.json').write_text(json.dumps(dict(
                            image_sha256=hashlib.sha256(image).hexdigest(),checks=phase_checks),indent=2))
                        assert not errors,('UI stage retirement/reentry',item)
                    s.input(0x10457dac,[original_phase]);present()
                    s.audit.append({'lifecycle_stage_checks':phase_checks,'explicit_stage_stimulus':True})
            # Re-enter the menu so the remaining native checks see the root page.
            s.input(int(sound['functions']['open'],16)&~1,[]);s.run_for(1)
            for _ in range(3):
                if current()==root:break
                back()
            assert current()==root,(profile,hex(current()))
            frame(f'04-marker-{profile}')
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
                trace_base=fixture['trace_address'];length=aspect['diagnostic_trace']['bytes']
                blob=b''.join(s.remote.memory(trace_base+offset,min(1024,length-offset))
                              for offset in range(0,length,1024))
                parsed=decode(blob)
                if parsed.get('format')=='WMD1':
                    assert any(r['profile']==profile for r in parsed['records'])
                    if a.verify_photo_context and profile==a.profiles[0]:
                        assert {r['mask_predicate'] for r in parsed['records']}=={0,1}
                    s.audit.append({'mask_scalar_trace_export_verified':True,
                                    'retained_records':len(parsed['records']),
                                    'actual_album_verified':False})
                else:
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
            back();assert current()==root
            observations.append({'profile':profile,'area':area,'selection_marks':marks,'config':config.hex()})
        frame('root-before-exit')
        for _ in range(3):
            back()
            if current()!=root:break
        assert current() in (0xc1832d50,0xc1837160)
        frame('05-returned-stock')
        (s.out/'mask-observations.json').write_text(
            json.dumps(observations_mask,indent=2),encoding='utf-8')
        s.audit.append({'aspect_menu_verified':observations,'language':a.language,
                        'soft_mask_verified':[] if a.stock_submit_control else observations_mask,
                        'stock_submit_control':bool(a.stock_submit_control),
                        'scope':'Assisted QEMU native menus/properties, ROM replay of the built image, '
                                'dynamic soft-mask live-view capture; diagnostic SD services; no camera capture'})
        s.write_audit()
        label='stock-submit control checks (NO mask proof)' if a.stock_submit_control else 'live-view mask checks'
        print(f'PASS {len(observations)} native aspect menu switches ({a.language}); '
              f'{len(observations_mask)} {label}',flush=True)
    finally:
        (s.out/'mask-observations.json').write_text(
            json.dumps(observations_mask,indent=2),encoding='utf-8')
        s.close()


if __name__=='__main__':main()
