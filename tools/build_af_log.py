"""Build the AF-log research kit: an EG with the SD module host and the IMU-task hook,
plus Z30/CORE.ZMOD from modules/af_log, modules/af_scan (--module scan) or
modules/af_handover (--module handover) or modules/af_retarget (--module retarget);
the ZMOD needs arm-none-eabi-gcc. log and scan share one EG; handover and
retarget each need their own.

For log / log9 / scan the EG is `full` without gyro-evr (gyro-evr owns the IMU hook)
plus module-host and imu-hook; for handover it is `full` without nef-menu (gyro-evr
included; the extension root holds 8 rows) plus module-host and the detection-result hook (face_hook); for
retarget, scan-box, snap, snap-shown, snap-focus, log5..log8 and touchflag (one EG) it is `full` without gyro-evr plus module-host,
imu-hook and face_hook. Read only: the module copies the record at 0xC237F578 on every
stock IMU tick; see docs/af-tracking-research.md.

Usage: build_af_log.py --source Z_30_0120.bin --output OUTDIR
Writes OUTDIR/EG209020.BIN, OUTDIR/EG209020.json and OUTDIR/Z30/CORE.ZMOD.
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--no-zmod', action='store_true', help='EG only (no C compiler needed)')
    p.add_argument('--module', choices=('log', 'log9', 'log10', 'log11', 'log12', 'log5', 'log6', 'log7', 'log8', 'touchflag', 'snap-shown', 'snap-focus', 'scan', 'scan-box', 'snap', 'handover', 'retarget'), default='log',
                   help='log: sample the AF / detection records; scan: find the words that change only while tracking; '
                        'handover: press OK when a subject appears while tracking; '
                        'retarget: move the tracker onto the subject (method B, no key press); '
                        'scan-box: find the words that change only while the tracking box is shown and moved (screen 1); '
                        'snap: snapshot RAM 300 ms after each tracking start (touch vs button start); '
                        'log5: af_log with the touch / half-press windows (retarget EG); '
                        'log6: af_log with the AF mode chooser windows (retarget EG); '
                        'touchflag: mark OK / half-press tracking as touch-started (retarget EG); '
                        'log7: af_log for the subject-lost timeout and the AF in-focus result (retarget EG); '
                        'snap-shown: snapshots on entering / leaving the tracking mode (screen 1, retarget EG); '
                        'log8: af_log for the OK key code and the UI AF frame lists (retarget EG)')
    a = p.parse_args()
    from z30_extensions.container import extract_eg, CONTAINER_SHA256
    from z30_extensions.build import build, resolve_presets
    _, features = resolve_presets('full')
    if a.module == 'handover':
        # the extension root fits 8 rows: full has 8 pages, the module host would be the 9th
        research = [f for f in features if f not in ('nef-menu', '3d-tracking')]
        image, report = build(extract_eg(a.source.read_bytes()), research, module_host=True, face_hook=True)
    elif a.module in ('retarget', 'scan-box', 'snap', 'log5', 'log6', 'touchflag', 'log7', 'snap-shown', 'log8'):
        research = [f for f in features if f not in ('gyro-evr', '3d-tracking')]
        image, report = build(extract_eg(a.source.read_bytes()), research, module_host=True, imu_hook=True, face_hook=True)
    else:
        research = [f for f in features if f != 'gyro-evr']
        image, report = build(extract_eg(a.source.read_bytes()), research, module_host=True, imu_hook=True)
    report['container_sha256'] = CONTAINER_SHA256
    report['research'] = 'af-log: read-only AF record sampling (modules/af_log)'
    a.output.mkdir(parents=True, exist_ok=True)
    eg, meta = a.output / 'EG209020.BIN', a.output / 'EG209020.json'
    if eg.exists() or meta.exists():
        sys.exit(f'{eg} or {meta} exists; refusing to overwrite')
    meta.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    eg.write_bytes(image)
    print(f'Built {eg}: {report["sha256"]}')
    if a.no_zmod:
        return
    with tempfile.TemporaryDirectory() as tmp:
        name = {'log': 'af_log', 'scan': 'af_scan', 'scan-box': 'af_scan', 'snap': 'af_snap', 'snap-shown': 'af_snap', 'snap-focus': 'af_snap', 'log5': 'af_log', 'log6': 'af_log', 'log7': 'af_log', 'log8': 'af_log', 'log9': 'af_log', 'log10': 'af_log', 'log11': 'af_log', 'log12': 'af_log', 'touchflag': 'af_touchflag', 'handover': 'af_handover', 'retarget': 'af_retarget'}[a.module]
        raw = Path(tmp) / f'{name}.bin'
        extra = {'scan-box': ['-DSCAN_BOX'], 'log5': ['-DLOG5'], 'log6': ['-DLOG6'], 'log7': ['-DLOG7'], 'snap-shown': ['-DSNAP_SHOWN'], 'snap-focus': ['-DSNAP_FOCUS'], 'log8': ['-DLOG8'], 'log9': ['-DLOG9'], 'log10': ['-DLOG10'], 'log12': ['-DLOG12']}.get(a.module, [])
        if a.module == 'log11':
            extra = ['-DLOG11', '-DTRK3D_GET=' + report['features']['tracking3d_control']['functions']['trk3d_get'] + 'u']
        subprocess.run(['sh', str(ROOT / 'modules' / name / 'build.sh'), str(raw), *extra], check=True)
        (a.output / 'Z30').mkdir(exist_ok=True)
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'prepare_module.py'), '--code', str(raw),
                        '--output', str(a.output / 'Z30' / 'CORE.ZMOD')], check=True)


if __name__ == '__main__':
    main()
