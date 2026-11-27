"""Generate real annotated screenshots and workflow diagram for docs/shutter-sound-tutorial.md.

Uses the real src/z30_extensions/gui_page.html rendered in headless Microsoft Edge
with a lightweight stand-in pywebview bridge and a real synthesized example WAV.
"""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions import gui, sound_wav  # noqa: E402

OUT = ROOT / 'docs' / 'images' / 'sound-tutorial'
PAGE = ROOT / 'src' / 'z30_extensions' / gui.PAGE
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
WIDTH = 1080


def _example_b64(slot=2):
    return base64.b64encode(sound_wav.example_wav(slot)).decode('ascii')


def _build_wrapped_html(scenario_js):
    html = PAGE.read_text(encoding='utf-8')
    ex2 = _example_b64(2)
    inject = f"""<script>
const __EX2_B64 = {json.dumps(ex2)};
const __BASE = 'D:\\\\WonderLanternZ30';
window.pywebview = {{
  api: {{
    initial: async () => ({{
      language: 'zh',
      presets: {json.dumps([n for n in gui.PRESETS if n not in gui.OPT_IN_PRESETS])},
      full: {json.dumps(sorted(gui.FULL_FEATURES))},
      titles: {json.dumps(gui.PRESET_TITLES)},
      labels: {{zh: {{}}, en: {{}}}},
      source: __BASE + '\\\\Z_30_0120.bin',
      outputs: {{
        loader: __BASE + '\\\\output\\\\card',
        patch: __BASE + '\\\\output\\\\card',
        sound: __BASE + '\\\\output\\\\sound',
      }},
    }}),
    check_source: async () => 'ok',
    resolve: async () => ({{ok: true, features: []}}),
    folder_state: async () => 'none',
    next_kit: async () => __BASE + '\\\\output\\\\card-2',
    open_folder: async () => {{}},
    choose_source: async () => null,
    choose_folder: async () => null,
    example_sound: async slot => ({{ok: true, name: 'leica-m11-shutter.wav', data_b64: __EX2_B64}}),
    save_sound: async (folder, slot, pcmB64) => {{
      const rel = slot === 'root' ? 'SHUTTER.WAV' : `SHUTTER/${{slot}}.WAV`;
      const pcmLen = Math.floor(pcmB64.length * 3 / 4);
      return {{
        ok: true, folder, file: folder + '\\\\' + rel.replace('/', '\\\\'), rel,
        bytes: pcmLen + 44, samples: Math.floor(pcmLen / 2), duration_ms: Math.round(pcmLen / 16),
      }};
    }},
  }}
}};

function __annotate(marks) {{
  document.querySelectorAll('.shot-mark').forEach(e => e.remove());
  for (const [selector, n, pad] of marks) {{
    const el = document.querySelector(selector);
    if (!el) continue;
    const r = el.getBoundingClientRect(), p = pad ?? 6;
    const box = document.createElement('div');
    box.className = 'shot-mark';
    Object.assign(box.style, {{
      position: 'absolute', left: (r.left + scrollX - p) + 'px', top: (r.top + scrollY - p) + 'px',
      width: (r.width + 2 * p) + 'px', height: (r.height + 2 * p) + 'px', border: '3px solid #f04a4a',
      borderRadius: '12px', boxSizing: 'border-box', pointerEvents: 'none', zIndex: 50,
    }});
    const badge = document.createElement('div');
    badge.className = 'shot-mark';
    badge.textContent = n;
    Object.assign(badge.style, {{
      position: 'absolute', left: (r.left + scrollX - p - 12) + 'px',
      top: (r.top + scrollY - p - 12) + 'px', width: '24px', height: '24px', borderRadius: '50%',
      background: '#f04a4a', color: '#fff', font: '700 13px/24px "Segoe UI", sans-serif', textAlign: 'center',
      boxShadow: '0 0 0 2px #fff', zIndex: 51,
    }});
    document.body.append(box, badge);
  }}
}}

function __makeDemoWaveform() {{
  // Synthesize a realistic 0.55s mono AudioBuffer with leading silence + double-click shutter transient
  // so the waveform clearly demonstrates auto-trimming and yellow selection handles.
  const sr = 44100, dur = 0.55, len = Math.floor(sr * dur);
  const ctx = new OfflineAudioContext(1, len, sr);
  const buf = ctx.createBuffer(1, len, sr);
  const ch = buf.getChannelData(0);
  let seed = 1337;
  const rnd = () => {{ seed = (seed * 16807) % 2147483647; return (seed / 1073741823.5) - 1; }};
  for (let i = 0; i < len; i++) {{
    const t = i / sr;
    let v = 0;
    if (t >= 0.14 && t < 0.24) {{
      const u = t - 0.14;
      v += (0.6 * rnd() + 0.3 * Math.sin(2 * Math.PI * 720 * u) + 0.15 * Math.sin(2 * Math.PI * 1850 * u))
         * Math.exp(-u / 0.013);
    }}
    if (t >= 0.225 && t < 0.36) {{
      const u = t - 0.225;
      v += (0.48 * rnd() + 0.35 * Math.sin(2 * Math.PI * 940 * u)) * Math.exp(-u / 0.017);
    }}
    ch[i] = Math.max(-1, Math.min(1, v * 0.92));
  }}
  return buf;
}}

window.addEventListener('load', async () => {{
  theme = 'dark'; applyTheme();
  window.dispatchEvent(new Event('pywebviewready'));
  await new Promise(r => setTimeout(r, 200));
  switchTab('sound');
  sndBuf = __makeDemoWaveform();
  sndMono = mixToMono(sndBuf);
  sndName = 'shutter-sample.mp3';
  autoTrimSelection();
  await new Promise(r => setTimeout(r, 150));
  {scenario_js}
}});
</script>
"""
    return html.replace('</body>', inject + '</body>')


def _workflow_html():
    return """<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #0f1115; color: #e8eaef; width: 1080px;
    font: 14px/1.55 "Segoe UI Variable Text", "Microsoft YaHei UI", sans-serif; padding: 28px 34px 32px; }
  h1 { margin: 0 0 18px; font-size: 19px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  h1 span { background: #f2c94c; color: #1b1607; font-size: 12px; padding: 2px 9px; border-radius: 99px; }
  .steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
  .step { background: #171a21; border: 1px solid #2a2f3a; border-radius: 12px; padding: 16px 16px 15px; position: relative; }
  .step .num { color: #f2c94c; font-weight: 800; font-size: 12px; letter-spacing: 1px; margin-bottom: 6px; }
  .step h2 { margin: 0 0 8px; font-size: 15px; font-weight: 650; }
  .step p { margin: 0; color: #9aa3b4; font-size: 12.8px; }
  .step code { background: #0f1115; border: 1px solid #2a2f3a; border-radius: 5px; padding: 1px 5px;
    font: 12px "Cascadia Mono", Consolas, monospace; color: #e8eaef; }
  .bottom { margin-top: 18px; display: grid; grid-template-columns: 1.15fr 1fr; gap: 14px; }
  .box { background: #171a21; border: 1px solid #2a2f3a; border-radius: 12px; padding: 15px 18px; }
  .box h3 { margin: 0 0 8px; font-size: 14px; color: #f2c94c; }
  pre { margin: 0; font: 12.5px/1.6 "Cascadia Mono", Consolas, monospace; color: #d6dbe6; }
  .muted { color: #7e8798; }
  .ok { color: #4fcf8a; }
  ul { margin: 0; padding-left: 18px; color: #c5ccd9; font-size: 13px; }
  li { margin: 4px 0; }
</style></head><body>
  <h1>自定义快门声音：4 步上手流程 <span>全图形界面 · 无需 FFmpeg</span></h1>
  <div class="steps">
    <div class="step">
      <div class="num">STEP 01</div>
      <h2>准备任意音频</h2>
      <p>手机录音或网上下载的 <code>MP3</code> / <code>WAV</code> / <code>M4A</code> / <code>FLAC</code> / <code>OGG</code>，或直接使用内置 4 款示例音。</p>
    </div>
    <div class="step">
      <div class="num">STEP 02</div>
      <h2>裁剪并试听</h2>
      <p>在程序「快门音频」页自动去开头静音、拖动黄线选 <code>≤ 1.08 s</code> 片段，实时试听 8 kHz 实际效果。</p>
    </div>
    <div class="step">
      <div class="num">STEP 03</div>
      <h2>一键导出标准 WAV</h2>
      <p>自动转码为相机严格要求的 <code>8 kHz / 单声道 / 16-bit / 44 字节头</code>，直接按槽位命名输出。</p>
    </div>
    <div class="step">
      <div class="num">STEP 04</div>
      <h2>拷卡并在机身开启</h2>
      <p>把文件拷到 SD 卡根目录，在相机 <b>扩展菜单 → 快门声音</b> 选文件、开启功能，并打开<b>静音拍摄</b>。</p>
    </div>
  </div>
  <div class="bottom">
    <div class="box">
      <h3>SD 卡根目录放置结构</h3>
      <pre>SD 卡根目录/
├─ Z30/
│  └─ WL.PATCH          <span class="muted">← 功能卡文件（需勾选 shutter-sound 或 full）</span>
├─ <span class="ok">SHUTTER.WAV</span>          <span class="muted">← 主快门声槽位（可选）</span>
└─ <span class="ok">SHUTTER/</span>             <span class="muted">← 多快门声槽位文件夹（可选）</span>
   ├─ <span class="ok">1.WAV</span>
   ├─ <span class="ok">2.WAV</span>
   ├─ <span class="ok">3.WAV</span>
   └─ <span class="ok">4.WAV</span></pre>
    </div>
    <div class="box">
      <h3>相机触发条件（缺一不可）</h3>
      <ul>
        <li>卡文件包含 <b>自定义快门声音</b>（默认 <code>full</code> 已包含）。</li>
        <li>在 <b>扩展菜单 → 快门声音 → 声音文件</b> 选中你拷贝的槽位。</li>
        <li>将 <b>启用</b> 设为 <b>开启</b>（可先点 <b>预听声音</b> 测试）。</li>
        <li>开启相机原厂 <b>静音拍摄</b>（仅静音拍照时触发，录像不触发）。</li>
      </ul>
    </div>
  </div>
</body></html>"""


def _render_edge(html_path, png_path, height, profile_dir):
    cmd = [
        str(EDGE),
        '--headless=new',
        '--disable-gpu',
        '--no-first-run',
        '--no-default-browser-check',
        f'--user-data-dir={profile_dir}',
        '--hide-scrollbars',
        '--force-device-scale-factor=1',
        f'--window-size={WIDTH},{height}',
        '--virtual-time-budget=3500',
        f'--screenshot={png_path}',
        html_path.as_uri(),
    ]
    subprocess.run(cmd, check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _crop(src_png, dst_png, top, bottom):
    with Image.open(src_png) as img:
        y0 = max(0, top)
        y1 = min(img.height, bottom)
        img.crop((0, y0, WIDTH, y1)).save(dst_png)


def main():
    if not EDGE.is_file():
        raise SystemExit(f'Microsoft Edge not found at {EDGE}')
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        prof = tmp / 'edge-profile'

        # 00-workflow.png
        wf_html = tmp / 'workflow.html'
        wf_html.write_text(_workflow_html(), encoding='utf-8')
        wf_png = OUT / '00-workflow.png'
        _render_edge(wf_html, wf_png, 500, prof)
        print('Created', wf_png.relative_to(ROOT))

        # 01-tab-and-load.png
        s1_html = tmp / 's1.html'
        s1_raw = tmp / 's1.png'
        s1_html.write_text(_build_wrapped_html("""
            __annotate([
              ['#tabBtnSound', 1, 5],
              ['#dropzone', 2, 5],
              ['.examples', 3, 6],
              ['#soundSourceState', 4, 6],
            ]);
        """), encoding='utf-8')
        _render_edge(s1_html, s1_raw, 1200, prof)
        _crop(s1_raw, OUT / '01-tab-and-load.png', 0, 322)
        print('Created', (OUT / '01-tab-and-load.png').relative_to(ROOT))

        # 02-waveform-edit.png
        s2_html = tmp / 's2.html'
        s2_raw = tmp / 's2.png'
        s2_html.write_text(_build_wrapped_html("""
            __annotate([
              ['.wave-wrap', 1, 5],
              ['#btnAutoTrim', 2, 5],
              ['#sndGain', 3, 8],
              ['#btnPlay8k', 4, 5],
            ]);
        """), encoding='utf-8')
        _render_edge(s2_html, s2_raw, 1200, prof)
        _crop(s2_raw, OUT / '02-waveform-edit.png', 318, 682)
        print('Created', (OUT / '02-waveform-edit.png').relative_to(ROOT))

        # 03-export-done.png
        s3_html = tmp / 's3.html'
        s3_raw = tmp / 's3.png'
        s3_html.write_text(_build_wrapped_html("""
            document.querySelector('.slot-btn[data-slot="1"]').click();
            showSoundResult({
              ok: true,
              folder: __BASE + '\\\\output\\\\sound',
              file: __BASE + '\\\\output\\\\sound\\\\SHUTTER\\\\1.WAV',
              rel: 'SHUTTER/1.WAV',
              bytes: 2868,
              samples: 1412,
              duration_ms: 177,
            });
            __annotate([
              ['#soundOut', 1, 5],
              ['#soundSlots', 2, 6],
              ['#goSound', 3, 5],
              ['#resultSound', 4, 5],
            ]);
        """), encoding='utf-8')
        _render_edge(s3_html, s3_raw, 1300, prof)
        _crop(s3_raw, OUT / '03-export-done.png', 678, 1150)
        print('Created', (OUT / '03-export-done.png').relative_to(ROOT))


if __name__ == '__main__':
    main()
