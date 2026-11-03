# Contributor guide

[简体中文](CONTRIBUTING.md) · **English**

This guide is for developers who want to change or add features. For usage, see the [README](README.en.md).

## Ground rules

1. **The update path must keep working.** Every build handed to someone for camera tests must still open **Setup menu → Firmware version** and update normally. That is the only way back to stock firmware. Any feature that touches shared stock data (fonts, styles, menu tables) needs the [pre-delivery checks](#pre-delivery-checks) first.
2. **Patch against stock bytes.** Every edit carries the original bytes, and the build refuses to run if they differ. Never modify stock data in place: copy the table into the tail and redirect its references.
3. **Do not distribute stock content.** The repository and release packages never contain stock firmware, the decoded module, generated EGs or stock glyph bitmaps. Read stock data from the user's firmware at build time.
4. **Record evidence separately.** Keep offline checks, QEMU checks and camera feedback apart. Documents state only what the evidence shows, and say "unverified" otherwise.

## Development environment

```powershell
python -m pip install -e '.[sound,verify]'
```

- **Python 3.10+.** `sound` installs Keystone (assembling patch code); `verify` installs Unicorn (tests that execute stock and patch instructions).
- **Stock firmware.** Put an unmodified `Z_30_0120.bin` in the repository root (ignored by `.gitignore`). Firmware-dependent tests and tools pick it up automatically and are skipped without it.
- **QEMU (optional).** Place z30-qemu (the Z30 machine based on WonderLantern/z7-qemu) next to this repository as `../z30-qemu`, build it as its README describes, and put the decoded stock module at `../z30-qemu/.local/firmware/eg2090_0120a0.bi`. QEMU tools run under Linux or WSL with `python3` and take `--qemu-root ../z30-qemu`.
- **Glyph fonts (only when adding Chinese labels).** Run `python -m pip install fonttools`, then download `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`, `Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf` and `Sans/Mono/NotoSansMonoCJKsc-Regular.otf` from [noto-cjk](https://github.com/notofonts/noto-cjk) into `build/fonts/noto-cjk/`.

## Repository layout

| Path | Contents |
| --- | --- |
| `patch.py`, `patch_gui.py` | Run the command line or GUI straight from the source tree |
| `src/z30_extensions/cli.py`, `gui.py` | Command line and GUI; both take the preset list from `build.py` |
| `src/z30_extensions/build.py` | `PRESETS`, `FEATURE_ORDER`, preset expansion and feature composition |
| `src/z30_extensions/container.py` | Verifies and decodes `Z_30_0120.bin`, extracts the EG module |
| `src/z30_extensions/plan.py`, `patch.py` | Patch plan (tail allocation, original-byte checks), patch application, CRC rebuild, report |
| `src/z30_extensions/assembly.py` | Keystone assembly, pre-assembled cache, common instruction snippets |
| `src/z30_extensions/platform/z30_120.py` | Stock module identity and address mapping (`StockImage`) |
| `src/z30_extensions/platform/fonts.py` | Stock font format: character maps, glyph index, outline codec, font rebuild |
| `src/z30_extensions/features/` | Feature implementations; `ui_text.py` handles menu text, `cjk_fonts.py` the added glyphs |
| `tests/` | Unit tests (`unittest`) |
| `tools/` | Research and verification tools: `inspect_*` and `probe_*` analyze, `verify_*` check, `build_*` generate data and releases |
| `docs/` | Design, evidence and verification records per feature (mostly Chinese) |
| `examples/modules/` | SD module interface header (research) |

`build/`, `output/`, `analysis/` and `dist/` hold local products and are all ignored by Git.

## Writing a patch

Each feature is a module under `features/` exposing `add(plan, ...)`. `Plan` wraps the stock image and the "tail" appended to the end of the module:

```python
from ..assembly import assemble, constant, invoke

def add(plan):
    code = plan.alloc(bytes(64))                     # reserve tail space; returns its runtime address
    plan.put(code, assemble(f'''
        push {{r4,lr}}; {constant('r0', 0x1400)} {invoke(0x10465984)}
        pop {{r4,pc}};
    ''', code, 64))                                  # Thumb assembly, must fit the reserved size
    plan.edit(SITE, new_bytes, 'my-feature-hook',
              expected=STOCK_BYTES)                  # same-length edit; the original bytes must match
    plan.features['my_feature'] = dict(hook=hex(code))  # goes into the JSON report
```

- **Addresses:** all addresses are runtime addresses. ROM starts at `0x10020000`; initialized data is mapped from `0xC1800000`, and `StockImage` converts to file offsets. Read stock content with `plan.image.read(address, size)` and `plan.image.words(address, count)`.
- **Hooks:** to hook stock code, build long jumps with `assembly.jump(site, target, length)`. The patch must re-execute the displaced instructions and then jump back.
- **Stock tables:** never edit them in place. Copy the table into the tail and point the literals that reference it at the copy. Find every reference first. `features/cjk_fonts.py` is the example: the font pointer table and the size table are two tables, and copying only one of them hung the stock Language page.
- **Assembly cache:** `assemble()` checks `asm_cache.gz` first and only calls Keystone on a miss. After you change assembly source, regenerate the cache for the Windows release (see [Releases](#releases)).

## Adding a feature or preset

1. Implement it under `src/z30_extensions/features/` and call it in order from `build()` in `build.py`.
2. Public preset: add it to both `PRESETS` and `FEATURE_ORDER`. `FULL_FEATURES` currently equals `FEATURE_ORDER`, so a new feature joins `full` automatically. To make an opt-in preset outside `full`, turn `FULL_FEATURES` into an explicit list; the GUI shows presets outside `full` as extra options. If the feature uses the menu framework, also add `shutter-sound` in `resolve_presets()`.
3. The command-line menu and GUI read `PRESETS` directly, but update the numbering and combination expectations in `tests/test_presets.py`.
4. Write unit tests for new code. Where patch code can run under Unicorn, execute it (see `tests/test_ui_text.py`).
5. Write a topic document in `docs/` covering hooks, evidence and what is unverified, and update the feature tables in both READMEs.
6. If a new file uses the Wonder Lantern Community License, add the SPDX header and list it in `THIRD_PARTY_NOTICES.md`; other new files default to MIT.

## Menu text and Chinese fonts

- **Labels and translations:** menu labels are allocated with `ui_text.TextResources.alloc('English text')`. Chinese translations live in `ui_text.TRANSLATIONS`, at most 24 UTF-16 code units each.
- **Stock fonts:** the stock Chinese fonts (Simplified font 2, Traditional font 0) only contain menu characters. At build time, `cjk_fonts` adds the glyphs from `platform/z30_120_extra_glyphs.json`: all printable ASCII plus the characters the labels need.
- **Missing-glyph check:** `check_glyphs()` checks every label during the build and fails if a label uses a character the font lacks. When a new label needs missing glyphs, run:

  ```powershell
  python tools/build_cjk_glyphs.py --firmware Z_30_0120.bin --write
  ```

  It converts the missing characters from the Noto fonts and updates the JSON. Commit the JSON together with the code.
- **Query and preview:** `tools/font_coverage.py <text>` lists which characters the stock fonts lack; `tools/render_stock_glyphs.py --fill-from <Noto OTF> <text> -o out.png` previews stock and added glyphs side by side.
- **Format details:** the font format and its evidence are in the [UI verification log](docs/ui-qemu.md) (Chinese).

## Testing

### Unit tests

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests
```

- Firmware-dependent tests are skipped without the stock firmware in the root.
- Instruction-executing tests are skipped without Unicorn.
- All tests must pass before you commit.

### QEMU checks

Run them in WSL with `--image`/`--report` pointing at a build. The common ones:

| Tool | Checks |
| --- | --- |
| `verify_firmware_update_qemu.py --expect-page 0xc1839680` | The stock Firmware version page opens |
| `probe_language_page_qemu.py --region N --scroll 35` | The Language page's full list opens and scrolls for a given region code |
| `verify_ui_qemu.py` | Extensions menu, Chinese text, back navigation and first entry |
| `verify_<feature>_qemu.py` | Each feature's menu flow (date stamp, bitrate, aspect, zebra, …) |

QEMU limits to keep in mind:
- **Region code:** the emulated region byte defaults to the invalid `0xFF`, so the Language page shows English only. Pass `--region` 0–12 to cover real language lists (region 9 lists Simplified and Traditional Chinese; regions 5, 6, 7 and 12 are long lists including Chinese).
- **SD card:** the machine does not mount a virtual SD card, so the update itself can only be verified on the camera.
- **Boot:** QEMU uses assisted boot and does not replace camera tests of real start-up, recording, SD or USB.

### Pre-delivery checks

Before handing a build to someone for camera tests:

1. All unit tests pass.
2. Build into a new `output/<name>/` directory (the builder neither creates directories nor overwrites files) and record its SHA-256 so the tester can check it.
3. `verify_firmware_update_qemu.py` opens the stock Firmware version page in Simplified Chinese, Traditional Chinese and English.
4. If the change touches shared stock data (fonts, styles, menus), check the Language page under several region codes and run the affected menu verifications.
5. The first item of the camera checklist is always: the Firmware version page opens and a normal update works.

## Camera records

- Write camera results into the matching topic document, with the date, the build (directory name or SHA-256) and what was observed.
- For a reported problem, first try to reproduce it in QEMU, then fix it, then run the pre-delivery checks again.
- Rename a broken build's directory (for example `-BAD-...`) so it is not tested again by mistake.

## Documents, licenses and commits

- **Documents:** topic documents are mostly Chinese; keep the two READMEs in step.
- **Commit messages:** use an English imperative subject such as `Add missing CJK and ASCII glyphs to the stock UI fonts`, and explain the reason and evidence in the body. Keep each commit to one change.
- **Never commit:** stock firmware, generated BIN/EG files, `asm_cache.gz`, Noto font files or private test media; `.gitignore` covers these.
- **Third-party material:** when adding third-party code, fonts or data, record its source and license in `THIRD_PARTY_NOTICES.md` and put the full license text in `licenses/`.

## Releases

The Windows release excludes Keystone (GPLv2) and ships pre-assembled machine code instead:

```powershell
python -m pip install '.[gui-exe]'
python tools/build_asm_cache.py
python tools/build_gui_exe.py
```

- **Cache:** `build_asm_cache.py` assembles every feature combination, then rebuilds with Keystone blocked and compares hashes.
- **Package:** `build_gui_exe.py` produces `dist/WonderLanternZ30-windows-x64.zip` with every license text: MIT, the Wonder Lantern Community License, OFL, Python, Tcl/Tk and PyInstaller.
