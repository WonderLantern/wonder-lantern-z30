# Wonder Lantern Z30

[简体中文](README.md) · **English**

Wonder Lantern Z30 is an experimental low-level host and firmware-extension research project by **WonderLantern**, specifically for **Nikon Z30 C 1.20**. It explores camera-side extensions similar to Magic Lantern. The current implementation is a Z30-specific prototype, not a general framework for other cameras. Nikon does not endorse or support this project.

The project applies patches with expected-byte checks to user-supplied stock firmware and produces a standalone **EG module** and a JSON build report. The maintainer has reported installation on a Z30 C 1.20. The builder does not recreate a complete Nikon update package, connect to a camera, or write to an SD card. Feature-specific coverage is listed under Verification status below.
On 2026-10-17, the user confirmed that all eight current features have passed physical-camera testing on Nikon Z30 C 1.20, including aspect profiles, the full-information page, sound persistence, and runtime bitrate profiles. Earlier pending-camera statements below describe previous validation stages and are superseded by this confirmation. Offline report flags still describe only the tools' own evidence; this confirmation does not claim exhaustive coverage of every mode, combination, or fault condition.


## Features and presets

`full` includes all eight features below. Select features individually or combine them; `module-host` automatically includes `shutter-sound`.

| Preset | Feature |
| --- | --- |
| `liveview-dual` | Keep existing DISP pages and insert a full-information page (top information, histogram and level) after the horizon page. [Details](docs/liveview-full-info.md) |
| `shutter-sound` | Read a custom electronic-shutter WAV from SD, with enable/disable, selection, preview, and reload. |
| `m900` | Enable the existing stock M-mode extended-exposure path, up to 900 seconds, retaining the M-mode check. |
| `mtp-fast` | Accelerate MTP property-list queries while preserving response semantics. |
| `module-host` | **Experimental** native SD module host that loads and runs ZMOD modules using the sound-menu framework. |
| `video-rot` | Write orientation metadata for new recordings and lock the orientation captured at recording start. |
| `bitrate-slowmo-uncap` | Select and remember Stock, Balanced, or High quality bitrates; extend slow-motion recording to 25 minutes with FAT32 splitting or large exFAT files. |
| `aspect-profiles` | Six photo ratios in one native menu, with a stock-grid reference frame. [Details](docs/aspect-profiles.md) |

Sound and module menus support Simplified Chinese, Traditional Chinese, and English fallback. Current builds do not add a Camera Status page.

## Quick start

First-time users should read the [build guide](PATCH_GUIDE.md) (Chinese). Run these commands in PowerShell from the project root.

### 1. Prepare the source and stock firmware

You need Python **3.10+**, the complete project source including `src/`, and an unmodified **`Z_30_0120.bin`**. Supply the firmware yourself; the repository does not include stock firmware or complete modified update packages.

The builder accepts this exact stock input:

| Property | Value |
| --- | --- |
| Camera / firmware | Nikon Z30 / C 1.20 |
| File size | 34,496,798 bytes |
| SHA-256 | `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |

### 2. Install dependencies and build

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

`.[sound]` installs the Keystone assembler required by `full`. The additional `liveview-dual` DISP page also requires Keystone. Standalone `m900` needs only the Python standard library. Sound, the module host, bitrate profiles, and MTP acceleration require Keystone. The bitrate preset includes the sound-menu framework, with sound disabled by default. For standalone video orientation, `.[video]` provides the same assembler dependency as `.[sound]`.

On macOS, if installation still leaves `fail to load the dynamic library`, activate the Python environment used for building, then install and link the native library:

```bash
brew install keystone
ln -s "$(brew --prefix keystone)/lib/libkeystone.dylib" "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/keystone/libkeystone.dylib"
python -c "from keystone import Ks; print('Keystone OK')"
```

### 3. Select presets

Omitting presets in an interactive terminal displays a selection menu. Options **1–9** are `full`, followed by the eight presets in the table above. Enter numbers or names separated by spaces or commas; an empty response selects `full`.

- `2 4 5` or `liveview-dual,m900,mtp-fast`: combined display, M900, and MTP acceleration.
- `6` or `module-host`: the experimental module host and its required sound framework.
- `1` or `full`: all eight features, including orientation and bitrate/slow-motion changes.

Alternatively, specify presets directly on the command line, separated by spaces:

```powershell
python patch.py liveview-dual m900 mtp-fast --source "Z_30_0120.bin" --output "output/EG209020-custom.BIN"
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020-full.BIN"
```

Each feature is applied once, in a fixed build order. `full video-rot` and `full bitrate-slowmo-uncap` enable the same feature set as `full` alone. With redirected input or a noninteractive script, omission defaults to `full`; `--interactive` forces the menu.

The legacy alias `modular` is still accepted on the command line or when typed into the menu, but has no numbered entry. It includes only `liveview-dual`, `m900`, `shutter-sound`, and `module-host`. It excludes MTP acceleration, video orientation, and bitrate/slow-motion changes. Use `full` for the current complete bundle.

### 4. Inspect the output

The builder verifies the stock package's size and SHA-256, decodes it internally, validates container/module CRCs and EG identity, and checks expected bytes, boundaries, and overlaps for each patch. It then applies the patches and rebuilds the CRC. Offsets refer to the **extracted EG module**.

| File | Purpose |
| --- | --- |
| `EG209020.BIN`, or your chosen name | Standalone EG module; not a repackaged `Z_30_0120.bin`. |
| Matching `.json` | Input/output digests, selected presets, expanded features, edit locations, expected/replacement bytes, appended code, and feature information. |

The input is never modified. Existing output or report files are refused: choose a fresh path or move the previous BIN and JSON first. `Built` confirms a local build; a valid CRC does not establish correctness of all camera behavior. See [build details](docs/build.md) for implementation notes and the developer-only `--extracted-eg` mode.

## Menus and language

Menus follow the stock **Language** setting. Simplified and Traditional Chinese use the stock Chinese fonts; all other languages receive English labels. Other translations are not implemented.

| Build content | English entry | Simplified Chinese entry | Traditional Chinese entry |
| --- | --- | --- | --- |
| Sound framework only | `Shutter sound` | 快门声音 | 快門聲音 |
| Module host included, such as `full` | `Extensions` | 自定义菜单 | 自訂選單 |
| Module page | `SD Modules` | SD 卡功能 | SD 卡功能 |

Fixes cover missing titles, clipping by empty value columns, disappearing sidebars, back-button residue after returning, and corrupted entry labels in non-Chinese languages or on the first menu entry after reboot. The user reported the preceding drawing and language fixes working on hardware; the new message page and preferences still need separate hardware validation. Native QEMU and constructor comparisons are in the [UI verification record](docs/ui-qemu.md).

WAV filenames, paths, and module output remain ASCII. Stock Chinese fonts contain a limited character set, and menu wording uses checked characters. Arbitrary Chinese text input and a complete new font set are not provided.

## Custom shutter sound

The entry menu provides enable/disable, file selection, preview, and reload. Selection pages mark the current choice with `*`; press OK once to apply. Selecting a file does not automatically enable sound or play it.

Supported SD paths:

```text
SD card root/
├─ SHUTTER.WAV
└─ SHUTTER/
   ├─ 1.WAV
   ├─ 2.WAV
   ├─ 3.WAV
   └─ 4.WAV
```

Preparing `SHUTTER.WAV` in the root is recommended. The numbered files and `SHUTTER` directory are optional. Enabling sound without a loaded sample reads the current selection, initially `SHUTTER.WAV`. You can instead load an existing numbered file before enabling sound, so the root file is not mandatory for every usage path.

Audio must be **8000 Hz, mono, signed 16-bit little-endian PCM, nonempty, and at most 1.08 seconds**, with a standard 44-byte WAV header and no extra chunks. Normalize a short integer-PCM WAV with:

```powershell
python tools/prepare_wav.py input.wav SHUTTER.WAV
```

The converter uses linear interpolation; consider an anti-aliasing resampler first for high-rate audio. Firmware updates do not require replacing your existing WAV files. No third-party audio is included; `example(slot)` in the script generates original synthetic samples.

Sound defaults to OFF without a valid saved record. It plays for silent still-photo captures only when both this feature and stock silent photography are enabled; video and nonsilent photos do not trigger it, and preview works while OFF. Successful file selections and switch changes are saved to `WL-SOUND.CFG` in the SD-card root. After reboot, the previous selection and enabled state are restored on the first menu entry or photo trigger, without opening the menu first. The sound file must remain at its original path; the first photo may incur an extra SD read. Corrupt preferences or a failed startup sound load leave sound OFF. Save failures report that the current setting was not saved. Failed loads preserve the previous valid sample and switch state. Errors appear in a separate message page body while menu titles remain unchanged. The new message page and preferences have instruction-level and assisted QEMU coverage; the user has also confirmed physical-camera testing. See [sound details](docs/shutter-sound.md).

## SD module host (experimental)

`module-host` and `full` load one native module from **`Z30/CORE.ZMOD`**. Modules do not start automatically: load first, then run. A successful module and its RAM state remain until shutdown. The maintainer has reported hardware loading and execution.

Generate the counter example:

```powershell
python tools/prepare_module.py --example counter --output "output/CORE.ZMOD"
```

Copy it to `Z30/CORE.ZMOD` on the SD card. Open **Setup menu → Extensions / 自定义菜单 → SD Modules / SD 卡功能**, select `Load CORE.ZMOD`, then `Run module`. See [module documentation](docs/modules.md) for other examples, replacement procedures, and the historical pagination adapter for the original host.

The interface is **ABI 2**, with a **16 KiB** payload limit and **64-byte** private context. The output contract accepts up to **40 printable ASCII characters**; Chinese menus currently display the first **24** at most. Modules are trusted native ARM Thumb code. CRC32 detects damage, not authenticity; there is no signature verification or sandbox. Hot unloading and automatic startup are not implemented; replacing a successfully loaded module requires a reboot. Missing cross-core cache-service responses can cause an indefinite wait. A Lua interpreter is not integrated.

## Automatic video orientation

After stock configuration succeeds for a new recording's container object, `video-rot` reads the stock orientation and locks that object to **0°, 90°, 270°, or 180°**. Unknown or unavailable orientation falls back to 0°. Rotating the camera during recording does not change the current object's orientation. This implements the behavior discussed in [issue #3](https://github.com/WonderLantern/wonder-lantern-z30/issues/3).

Only video-track orientation metadata for new recordings changes. Pixels and existing files are not rotated; the shared matrix, movie header, and audio track retain stock behavior. Players must honor orientation metadata. The user confirmed landscape, both portrait directions, and locking during recording. Upside-down landscape has no separately recorded result, and original MP4 matrices have not been checked byte for byte in this repository. See [video-orientation details](docs/video-rotation.md).

## Video bitrates and slow motion

Choose a profile under **Setup → Extensions → Video bitrate**. `*` marks the current profile. High quality is the default. The selection is stored in `WL-VIDEO.CFG` at the SD-card root and restored on the first menu entry or movie-parameter read after restart. Missing or invalid settings use High quality. A save failure opens a separate message page; the current selection remains available for subsequent recordings.

Rates are in **Mbps, maximum / average**:

| High-quality mode | Stock | Balanced | High quality (default) |
| --- | --- | --- | --- |
| 1080p 60p, 50p | 56 / 48 | 84 / 70 | 108 / 90 |
| 1080p 30p, 25p, 24p | 28 / 24 | 48 / 40 | 72 / 60 |

The slow-motion **recording-time** limit rises from 3 to **25 minutes**. FAT32 allows up to 8 files; exFAT uses one large file. Bitrates for 4K, 120p/100p, slow motion, and standard quality remain stock. Resolution, frame rate, GOP, and the 125-minute limit for other modes remain stock. The 25-minute limit accounts for fixed-capacity index tables; recording is not unlimited.

Profiles affect subsequent native movie configurations and do not write to an active encoder. Stock restores bitrates only; the 25-minute slow-motion extension remains enabled. Earlier hardware tests covered fixed High quality rates and slow-motion file integrity. Runtime switching, Balanced, and persisted profile selection still need camera validation. See [bitrate and slow-motion details](docs/bitrate-slowmo-uncap.md) for parameters, QEMU boundaries, and earlier hardware records.

## Verification status

Offline results and user hardware reports are separate. Evidence for one preset does not establish coverage of every combination, mode, or failure condition.

| Scope | Recorded evidence |
| --- | --- |
| Builds and regressions | 34 tests passed for the current UI fixes; CRC and byte-for-byte reproducibility passed for `shutter-sound`, `module-host`, and `full`. |
| Menus and language | Native QEMU pages/constructors and Unicorn instruction checks; the user reported the preceding drawing/language version working on hardware. New message-page and preferences behavior still needs hardware validation. |
| EG installation and SD host | Maintainer confirmation of Z30 C 1.20 installation and ZMOD loading/execution. |
| Video orientation | Hardware feedback for the standalone preset's orientation and recording-time lock; see the dedicated coverage notes. |
| MTP acceleration | 590 property-list responses matched stock, with reduced query times; conditions and numbers are in the verification document. |
| Bitrates and slow motion | Earlier fixed High quality builds have hardware file checks; runtime profiles, Balanced, and persistence still need camera validation. |

Hardware results for combined histogram/level display, shutter-sound playback, and M-mode extended exposure are not individually recorded in the verification document. `camera_verified: false` means **the build script itself did not perform a camera test**; hardware feedback is recorded separately. Reported EG installation does not establish that every generated image has been tested. This tool does not build complete Nikon update packages.

Run tests without stock firmware:

```powershell
python -m pip install '.[sound,verify]'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

See [verification instructions](docs/verification.md) and the [UI record](docs/ui-qemu.md) for identity/CRC checks, boundaries, stock-instruction execution, MTP comparisons, and the external QEMU environment. Assisted QEMU boot and diagnostic inputs do not replace complete hardware boot, recording, SD/USB, or failure-scenario testing.

## Documentation index

Detailed documents are currently mainly in Chinese. The [Chinese README](README.md) covers the same usage and scope as this English version.

| Document | Contents |
| --- | --- |
| [Build guide](PATCH_GUIDE.md) | Inputs, menu selection, outputs, and common errors. |
| [Build details](docs/build.md) | Identity, decoding, preset combinations, and patch workflow. |
| [Sound details](docs/shutter-sound.md) | Menus, trigger conditions, format, and state. |
| [Module documentation](docs/modules.md) | Host operation, ABI 2, examples, and experimental limits. |
| [Video orientation](docs/video-rotation.md) | Locking, metadata, and hardware records. |
| [Bitrates and slow motion](docs/bitrate-slowmo-uncap.md) | Parameters, index capacity, and hardware file checks. |
| [Verification instructions](docs/verification.md) | Tests, stock-instruction execution, MTP, and hardware evidence. |
| [UI verification record](docs/ui-qemu.md) | Chinese menus, rendering, return behavior, and first-entry fixes. |
| [Licenses and third-party notices](THIRD_PARTY_NOTICES.md) | File-specific licenses and resource provenance. |

## Licensing and project identity

This project belongs to **WonderLantern** and is independent and experimental. Nikon does not endorse or support it.

Original code and documentation use the [MIT License](LICENSE), except for the seven sound/menu implementation files listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): `shutter_sound.py`, `sound_audio.py`, `sound_ui.py`, `sound_layout.py`, `sound_settings.py`, `bitrate_ui.py`, and `ui_text.py`. Those files use the [Wonder Lantern Community License](licenses/Wonder-Lantern.txt).

The community license permits use, modification, and free redistribution, requires publicly distributed modified versions to provide source under the same license, and prohibits charging for software distribution. Paid professional photography or filmmaking using it is permitted. Combined code must comply with each file's terms. The restriction on claiming derivative versions as official Wonder Lantern releases concerns software-release identity; it does not change this repository's ownership by the WonderLantern organization.

These licenses grant no rights to Nikon firmware, stock resources, third-party software, or trademarks. Stock firmware and menu templates are read locally from matching user input. Reference-project and dependency provenance and licensing are in the [third-party notices](THIRD_PARTY_NOTICES.md).
