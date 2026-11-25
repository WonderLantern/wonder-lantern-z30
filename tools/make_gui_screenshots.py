"""Render the screenshots and diagrams of docs/gui-tutorial*.md.

usage: python tools/make_gui_screenshots.py [--browser msedge|chromium]

Needs Playwright (`pip install playwright`) and Microsoft Edge (the default) or
Playwright's Chromium. The real gui_page.html is driven through a stand-in for
the pywebview bridge: the feature list, titles and the "will enable" summary
come from z30_extensions.gui, while file dialogs, firmware checks and builds
are canned with example paths (D:\\WonderLanternZ30) and the real results of a
full build (loader id, SHA-256, card file size). No firmware is read and
nothing is written outside docs/images/gui-tutorial.

Chinese images are NN-name.png, English ones NN-name.en.png; 1080 px wide like
the program's default window. Red frames and numbers mark what the tutorial
text refers to as ①, ②, ...
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions import gui  # noqa: E402

OUT = ROOT / 'docs' / 'images' / 'gui-tutorial'
PAGE = (ROOT / 'src' / 'z30_extensions' / gui.PAGE).as_uri()
BASE = 'D:\\WonderLanternZ30'
KITS = BASE + '\\output\\'
# A full build with this program version (tools/build_gui_exe.py selftest).
RESIDENT_ID = '0x67f308e7'
RESIDENT_SHA = 'be618dea203bffa5155fe1759f29284260cf781d76bf3e3f96a988bb15fbc760'
PATCH_BYTES = 416736
WIDTH = 1080


class FakeApi:
    """The answers gui.Api gives, without firmware, dialogs or builds."""

    def __init__(self, language, source='ok', folder='none'):
        self.language, self.source, self.folder = language, source, folder

    def initial(self):
        return dict(language=self.language, presets=[n for n in gui.PRESETS if n not in gui.OPT_IN_PRESETS],
                    full=sorted(gui.FULL_FEATURES), titles=gui.PRESET_TITLES,
                    labels={'zh': gui.PRESET_LABELS, 'en': gui.PRESET_LABELS_EN},
                    source=BASE + '\\Z_30_0120.bin', outputs={'loader': KITS + 'card', 'patch': KITS + 'card'})

    def check_source(self, path):
        return self.source

    def resolve(self, states):
        return gui.Api.resolve(None, states)

    def folder_state(self, folder):
        return self.folder

    def next_kit(self, folder):
        stem = folder.rstrip('\\').rsplit('\\', 1)[-1]
        index = int(stem.split('-')[1]) + 1 if '-' in stem else 2
        return f'{KITS}card-{index}'

    def build(self, mode, source, states, output):
        if mode == 'loader':
            return dict(ok=True, kind=mode, folder=output, resident_id=RESIDENT_ID, sha256=RESIDENT_SHA,
                        files=[output + '\\EG209020.BIN', output + '\\EG209020.json'], resident_from='built')
        return dict(ok=True, kind=mode, folder=output, resident_id=RESIDENT_ID, sha256=RESIDENT_SHA,
                    files=[output + '\\Z30\\WL.PATCH'], patch_bytes=PATCH_BYTES,
                    resident_from='folder' if self.folder == 'both' else 'program')

    def choose_source(self):
        return None

    def choose_folder(self, current):
        return None

    def open_folder(self, folder):
        return None


BRIDGE = """
window.pywebview = {api: new Proxy({}, {get: (_, name) => (...args) => window.pyCall(name, JSON.stringify(args))})};
window.addEventListener('load', () => window.dispatchEvent(new Event('pywebviewready')));
"""

# Frames and numbered badges over page elements, drawn into the page itself.
ANNOTATE = """
marks => {
  document.querySelectorAll('.shot-mark').forEach(e => e.remove());
  for (const [selector, n, pad] of marks) {
    const el = document.querySelector(selector);
    const r = el.getBoundingClientRect(), p = pad ?? 6;
    const box = document.createElement('div');
    box.className = 'shot-mark';
    Object.assign(box.style, {position: 'absolute', left: (r.left + scrollX - p) + 'px', top: (r.top + scrollY - p) + 'px',
      width: (r.width + 2 * p) + 'px', height: (r.height + 2 * p) + 'px', border: '3px solid #f04a4a',
      borderRadius: '12px', boxSizing: 'border-box', pointerEvents: 'none', zIndex: 50});
    const badge = document.createElement('div');
    badge.className = 'shot-mark';
    badge.textContent = n;
    Object.assign(badge.style, {position: 'absolute', left: (r.left + scrollX - p - 12) + 'px',
      top: (r.top + scrollY - p - 12) + 'px', width: '24px', height: '24px', borderRadius: '50%',
      background: '#f04a4a', color: '#fff', font: '700 13px/24px "Segoe UI", sans-serif', textAlign: 'center',
      boxShadow: '0 0 0 2px #fff', zIndex: 51});
    document.body.append(box, badge);
  }
}
"""


def region(page, *selectors, pad=20, top=None, bottom=None):
    """Full-width clip around the elements (page coordinates)."""
    rects = [page.eval_on_selector(s, 'e => { const r = e.getBoundingClientRect(); '
                                      'return [r.top + scrollY, r.bottom + scrollY]; }') for s in selectors]
    y0 = min(r[0] for r in rects) - pad if top is None else top
    y1 = max(r[1] for r in rects) + pad if bottom is None else bottom
    return dict(x=0, y=max(0, y0), width=WIDTH, height=y1 - max(0, y0))


class Shooter:
    def __init__(self, browser, language):
        self.browser, self.language = browser, language
        self.suffix = '' if language == 'zh' else '.en'

    def open(self, scheme='dark', **fake):
        api = FakeApi(self.language, **fake)
        context = self.browser.new_context(viewport=dict(width=WIDTH, height=900), color_scheme=scheme,
                                           device_scale_factor=1)
        context.expose_function('pyCall', lambda name, args: getattr(api, name)(*json.loads(args)))
        context.add_init_script(BRIDGE)
        page = context.new_page()
        page.goto(PAGE)
        page.wait_for_function("document.getElementById('sourceState').className.includes('ok') || "
                               "document.getElementById('sourceState').className.includes('bad')")
        page.wait_for_function("document.getElementById('summary').textContent.length > 0")
        page.wait_for_timeout(300)
        return page

    def save(self, page, name, clip, marks=()):
        page.mouse.move(1, 1)  # no hover tooltip in the picture
        page.wait_for_timeout(100)
        page.evaluate(ANNOTATE, [list(m) for m in marks])
        page.wait_for_timeout(100)
        path = OUT / f'{name}{self.suffix}.png'
        page.screenshot(path=str(path), clip=clip, full_page=True)
        print(path.relative_to(ROOT))
        page.context.close()

    def build(self, page, button):
        page.click(button)
        page.wait_for_function(f"!document.getElementById('{button[1:]}').disabled")
        page.wait_for_timeout(400)

    def all(self):
        page = self.open()
        self.save(page, '01-overview', dict(x=0, y=0, width=WIDTH, height=1050))

        page = self.open()
        self.save(page, '02-source-ok', region(page, 'section.plain', pad=16),
                  [('#pickSource', 1, 4), ('#sourceState', 2, 6)])

        page = self.open(source='bad')
        self.save(page, '03-source-bad', region(page, 'section.plain', pad=16), [('#sourceState', 1, 6)])

        page = self.open()
        self.save(page, '04-loader', region(page, '#goLoader', 'section.card', pad=20),
                  [('#loaderOut', 1, 6), ('[data-t=loader_once]', 2, 6), ('#goLoader', 3, 6)])

        page = self.open()
        self.build(page, '#goLoader')
        loader = 'section.card:nth-of-type(2)'
        self.save(page, '05-loader-done', region(page, loader, pad=20),
                  [('#resultLoader', 1, 6), ('#loaderOut', 2, 6)])

        patch = 'section.card:nth-of-type(3)'
        page = self.open()
        self.save(page, '06-patch-full', region(page, patch, pad=20),
                  [('#patchOut', 1, 6), ('#patchFor', 2, 8), ('#everything', 3, 4), ('#goPatch', 4, 6)])

        page = self.open()
        page.click('#everything')
        for name in ('liveview-dual', 'm900', 'aspect-profiles', 'date-stamp', 'focal-length'):
            page.click(f'.feat[data-p="{name}"]')
        page.wait_for_timeout(400)
        self.save(page, '07-patch-custom', region(page, patch, pad=20),
                  [('.feat[data-p="liveview-dual"]', 1, 4), ('#summary', 2, 6)])

        page = self.open()
        self.build(page, '#goPatch')
        self.save(page, '08-patch-done', region(page, 'section.card:nth-of-type(3) .head', '#resultPatch', pad=20),
                  [('#resultPatch .kv', 1, 8), ('#patchOut', 2, 6)])

        page = self.open(folder='both')
        page.fill('#patchOut', KITS + 'card')
        page.dispatch_event('#patchOut', 'input')
        page.wait_for_timeout(300)
        self.save(page, '09-patch-for-folder', region(page, 'section.card:nth-of-type(3) .head', '#everything', pad=20),
                  [('#patchOut', 1, 6), ('#patchFor', 2, 8)])


def light_english(browser):
    shooter = Shooter(browser, 'en')
    page = shooter.open(scheme='light')
    shooter.suffix = ''
    shooter.save(page, '10-light-en', dict(x=0, y=0, width=WIDTH, height=420), [('#theme', 1, 4), ('#lang', 2, 4)])


def diagrams(browser):
    """The flow (11-workflow) and the platforms (12-platforms), drawn as HTML."""
    template = (ROOT / 'tools' / 'gui_tutorial_diagrams.html').read_text(encoding='utf-8')
    for language, suffix in (('zh', ''), ('en', '.en')):
        context = browser.new_context(viewport=dict(width=WIDTH, height=600), device_scale_factor=1)
        page = context.new_page()
        page.set_content(template)
        for figure in ('workflow', 'platforms'):
            page.evaluate('([f, l]) => show(f, l)', [figure, language])
            page.wait_for_timeout(100)
            name = {'workflow': '11-workflow', 'platforms': '12-platforms'}[figure]
            path = OUT / f'{name}{suffix}.png'
            page.locator('#figure').screenshot(path=str(path))
            print(path.relative_to(ROOT))
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--browser', default='msedge', choices=('msedge', 'chromium'))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge' if args.browser == 'msedge' else None)
        for language in ('zh', 'en'):
            Shooter(browser, language).all()
        light_english(browser)
        diagrams(browser)
        browser.close()


if __name__ == '__main__':
    main()
