# Wonder Lantern Z30

[简体中文](README.md) · **English**

Wonder Lantern Z30 is the WonderLantern organization's firmware extension project for the **Nikon Z30 C 1.20**, exploring Magic Lantern-style on-camera extensions. It is specific to the Z30, not a cross-model framework, and it is not supported or endorsed by Nikon.

**New features load from a patch file on the memory card.** You flash the **loader firmware** into the camera once; after that, adding, removing or updating features only means replacing `Z30/WL.PATCH` on the card, with no further flashing. The loader itself needs flashing again only when it gets a low-level update, and the packaging tool tells you when.

> [!TIP]
> ### 📖 [GUI packaging tutorial (with screenshots) →](docs/gui-tutorial.en.md)
> New here? Follow it step by step: download the packaging program → check the stock firmware → build and flash the loader → build the feature card file and put it on the card.

> [!WARNING]
> Modified firmware carries risk. Before flashing the loader:
> - keep the unmodified `Z_30_0120.bin`;
> - know how to update again from **Setup menu → Firmware version**.
>
> As long as the update page opens, you can return to stock firmware; every loader firmware the project hands out is checked for this first. If a card file misbehaves, remove the card or replace the file and restart; no reflash is needed. The packaging tool itself never connects to the camera or writes to an SD card.

## How it works

| Part | Where | Contents |
| --- | --- | --- |
| Loader firmware `EG209020.BIN` | Flashed into the camera, **once** | Stock firmware, Chinese fonts with the common character sets (GB2312 level 1, Big5 level 1) and a loader. Without a card it behaves as stock |
| Feature card file `Z30/WL.PATCH` | Memory card | Code and patches of the chosen features. The loader reads it into memory about 3 s after power-on and the features appear |
| Load log `Z30/WL.LOG` | Memory card (written by the loader) | One line of result; `S=01` means loaded |

- **Changing features only changes the card file:** pick the features again in the packaging tool, copy the new `WL.PATCH` to the card and restart.
- **When the loader needs reflashing:** card files are built for the loader on the camera. Only when a new card file needs something that loader lacks (such as a new card file format), or characters its fonts do not have, does the packaging tool refuse and ask for a loader update. Adding features or updating versions normally does not.
- **Safety checks:** before writing anything the loader checks the card file's CRC, the loader identity and every original byte; if anything differs it leaves the camera's memory untouched and the camera behaves as stock.
- **Load feedback:** once loaded, the photo live view shows "Wonder Lantern ready · 16 features" for about 2 s, and the Status page of the extension menu shows whether the card file loaded, its version and the feature count.

Design, log codes and camera records: [SD card loading](docs/card-loader.md) (Chinese).

## Features

`full` enables all sixteen features below; each one can also be selected alone or in any combination. Aspect, zebra, bitrate, date stamp, portrait UI, the NEF compression menu, gyro e-VR, 3D tracking and AF frame colour automatically include the extension menu framework; the framework no longer depends on the shutter sound, so without `shutter-sound` no sound feature is built.

| Preset | What it does | Notes |
| --- | --- | --- |
| `liveview-dual` | Adds a full-information DISP page (top info + histogram + level) after the level page | [Doc](docs/liveview-full-info.md) |
| `shutter-sound` | Custom electronic-shutter sound from the SD card, with on/off, file choice, preview and reload | [Doc](docs/shutter-sound.md) |
| `m900` | Enables the stock M-mode long-exposure path, up to 900 s | — |
| `mtp-fast` | Faster MTP property-list queries with identical replies | [Verification](docs/verification.md) |
| `video-rot` | Writes orientation metadata for new videos, locked when recording starts | [Doc](docs/video-rotation.md) |
| `bitrate-slowmo-uncap` | Three in-camera bitrate profiles with memory, higher 4K/1080p high-quality bitrates, 25-minute slow-motion limit | [Doc](docs/bitrate-slowmo-uncap.md) |
| `aspect-profiles` | Six photo aspect ratios with a translucent live-view mask; the stock grid stays | [Doc](docs/aspect-profiles.md) |
| `photo-zebra` | Extends the stock zebra pattern to photo live view, with its own switch | [Doc](docs/photo-zebra.md) |
| `wifi-5ghz` | Requests the US/-1 country code on every valid wireless profile to unlock 5 GHz connections | See note¹ |
| `date-stamp` | Film-style date stamp in the lower right of JPEGs: five formats, four colors, optional glow, follows portrait orientation | [Doc](docs/date-stamp.md) |
| `portrait-ui` | Photo live-view info and the i menu turn to a portrait layout when the camera is held upright and fill in on their own after a turn; off by default; touch follows the portrait layout and i-menu setting pages show upright at 2/3; the shutter, aperture and ISO slider pages turn upright too; the histogram pages show an upright histogram | [Doc](docs/portrait-ui.md) |
| `nef-menu` | Choose the NEF compression type (lossless / compressed / uncompressed) in the extension menu, saved to `WL-NEF.CFG` | [Doc](docs/nef-compression.md), all three choices confirmed on a camera in 12-bit and 14-bit |
| `gyro-evr` | Gyro e-VR: in-body gyro stabilization while recording with electronic VR on; yaw, pitch and roll, cooperates with lens VR; Extensions menu: Standard / Enhanced / Native crop / OFF | [Doc](docs/gyro-evr.md) |
| `3d-tracking` | 3D tracking: tracking started with OK or a half-press hands over to a detected person or animal (the fine subject box) as touch tracking does, also during a half-press; photo shooting menu Disable / Enable, OFF by default; **needs the AF-area mode Auto-area AF (people) or (animals)** | [Doc](docs/3d-tracking.md); research module confirmed on a camera, firmware build to be confirmed |
| `af-frame-colour` | AF frame colours in the style of Nikon EXPEED 7 cameras (Z9, Z8, Z6III and others): the tracking and person / animal boxes are white, green when a half-press focuses and red when it does not, in AF-S and AF-C alike; the movie-mode person / animal box is white; plain auto-area AF-C frames turn green in focus; other frames unchanged; photo shooting menu Enable / Disable (Enable by default) | [Doc](docs/af-frame-colour.md); to be confirmed on a camera |
| `focal-length` | Shows the focal length for every CPU lens in photo live view (stock: power-zoom lenses only); always on | [Doc](docs/focal-length.md), confirmed on a camera |

¹ `wifi-5ghz` only changes the wireless country request; it does not write the camera's sales region or NVRAM. Cameras that could use 2.4 GHz channels 12 and 13 may no longer connect on them (inferred, not tested). It does not promise a 5 GHz access point from the camera itself or faster transfers, and the channels and power in use must suit local rules.

### Optional presets (not in `full`)

The two presets below fix NEF to one compression type at build time, without a menu. They are mutually exclusive and are not part of `full`. Selected together with `full`, they replace the `nef-menu` inside it, for example `full nef-uncompressed`.

| Preset | What it does | Notes |
| --- | --- | --- |
| `nef-uncompressed` | Fixes NEF to uncompressed at build time | [Doc](docs/nef-compression.md), confirmed on a camera |
| `nef-compressed` | Fixes NEF to compressed (lossy) at build time | [Doc](docs/nef-compression.md), confirmed on a camera |

## Quick start

### Stock firmware

You supply the firmware yourself; the repository contains neither stock firmware nor modified update packages. The packaging tool accepts only this stock file:

| Item | Value |
| --- | --- |
| Camera / firmware | Nikon Z30 / C 1.20 |
| File | `Z_30_0120.bin`, 34,496,798 bytes |
| SHA-256 | `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |

### Graphical app (Windows, recommended)

Screenshots of every step and common problems are in the **[GUI packaging tutorial](docs/gui-tutorial.en.md)**.

1. Download `WonderLanternZ30-windows-x64.zip` from [Releases](https://github.com/WonderLantern/wonder-lantern-z30/releases/latest) and **extract the whole archive** before running it.
2. Put `Z_30_0120.bin` into the extracted folder, or choose it in the program.
3. Open `WonderLanternZ30.exe` and wait for the firmware check to pass. The interface switches between Chinese and English, dark and light.
4. **First time: build the loader firmware.** Click "Build loader EG" and flash `EG209020.BIN` from `output/card` into the camera (Setup menu → Firmware version). Without a card file, check that the camera behaves as stock and the firmware version page works.
5. **Build the feature card file.** The loader folder field already points at that folder; pick the features, click "Build card file", copy the resulting `Z30` folder to the root of the memory card and restart the camera.
6. **Later feature changes and updates:** build a new card file with the new program and the same loader folder (move the old `Z30/WL.PATCH` away first) and replace the file on the card; no reflash. Repeat step 4 only when the program asks for a loader update.

The program is not code-signed, so Windows SmartScreen may warn about it; check the SHA-256 listed on the Release page first. It needs the Microsoft Edge WebView2 runtime that comes with Windows 10/11. On other systems run `python patch_gui.py`, or use the command line.

### Command line

You need Python **3.10+** and the complete source tree. Run these commands in the project root (PowerShell):

```powershell
python -m pip install '.[sound]'
python patch.py full --card --source "Z_30_0120.bin" --output "output/card"
```

- This writes `output/card/EG209020.BIN` (the loader firmware, flashed once) and `output/card/Z30/WL.PATCH` (copied to the card).
- To change features later, add `--patch-only` in the same folder: only a new card file is built, for the loader already flashed (move the old `Z30/WL.PATCH` away first):

```powershell
python patch.py liveview-dual m900 mtp-fast --card --patch-only --source "Z_30_0120.bin" --output "output/card"
```

- `.[sound]` installs the Keystone assembler.
- Without presets the builder shows a multi-select menu: entries 1–19 are `full`, the sixteen features above and the two optional presets; press Enter for `full`. Separate several presets with spaces; repeated features are applied once.
- Card files from the GUI packaging program always have the eighth tab. Even when no selected feature has a menu page, the tab holds the Status page.
- `tools/build_card.py` does the same with a few extra options, such as `--menu-tab` (the extension menu as an eighth tab on the left of the menu; the GUI always turns it on) and `--no-banner` (no live-view ready banner).
- **macOS:** if you see `fail to load the dynamic library` after installation, run this in the Python environment used for building:

```bash
brew install keystone
ln -s "$(brew --prefix keystone)/lib/libkeystone.dylib" "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/keystone/libkeystone.dylib"
```

For step-by-step instructions and common errors, see the [offline build guide](PATCH_GUIDE.md) (Chinese).

### Output

| File | Purpose |
| --- | --- |
| `EG209020.BIN` | The loader firmware, a standalone EG module (not a repacked `Z_30_0120.bin`), flashed into the camera once |
| `EG209020.json` | The loader's build report: digests, loader identity and capabilities; `--patch-only` card builds read it, so keep it next to the BIN |
| `Z30/WL.PATCH` | The feature card file, copied to the `Z30` folder at the root of the memory card |

- The packaging tool checks the stock package size and SHA-256, decodes it internally and checks every CRC level, then verifies original bytes, bounds and non-overlap for each change.
- It never modifies its input and refuses to overwrite existing output.
- A finished build only means the local build succeeded; it does not mean every camera behavior has been verified.

Implementation details: [build notes](docs/build.md) (Chinese).

### Without a card: flashing everything (developers)

Without `--card` the command line writes the features straight into one complete EG (`python patch.py full --source "Z_30_0120.bin" --output "output/EG209020.BIN"`). Every feature change then needs a reflash and removing the card cannot recover from a problem, so this is kept for development and comparison tests only; the graphical app does not offer it.

## Using the features

### Menus and language

- **Menu entry:** new features live in **Setup menu → Extensions** (Simplified Chinese 自定义菜单, Traditional 自訂選單). A sound-only build names the entry `Shutter sound`. Built with the GUI, or with `--menu-tab` on the command line, the menu is an eighth tab (lantern icon) on the left of the menu instead.
- **Root page:** the first row is Status (whether the card file loaded, version, feature count, whether settings were saved); below it the features are grouped by purpose, each feature page inside its category page ([notes](docs/menu-framework.md#根页分类2026-11-25), Chinese).
- **Direct rows:** Photo aspect, Photo zebra and Date stamp are also listed at the end of the photo shooting menu, and Video bitrate at the end of the video shooting menu; press up once from the first row to reach them. Direct rows show `--` on the right. These direct rows are verified in QEMU only.
- **Language:** menus follow the stock **Language** setting and show Simplified Chinese, Traditional Chinese or English; other languages show English.
- **Added glyphs:** the stock Chinese fonts only carry menu characters. The loader firmware adds every printable ASCII character, the characters the Chinese labels need, GB2312 level 1 and Big5 level 1 to the stock Chinese fonts. The glyphs are converted from Noto Sans CJK (SIL OFL 1.1). This does not provide arbitrary Chinese text input.
- **Firmware version page:** the loader firmware shows the project name and copyright as two lines on Setup → Firmware version, with or without a card.

### Custom shutter sound

Put the sounds on the SD card:

```text
SD card root/
├─ SHUTTER.WAV
└─ SHUTTER/
   ├─ 1.WAV  …  4.WAV
```

- **Format:** 8000 Hz, mono, 16-bit signed PCM, non-empty and at most 1.08 s, with a standard 44-byte WAV header. Convert with `python tools/prepare_wav.py input.wav SHUTTER.WAV`.
- **Trigger:** it plays for silent photos only, so the stock **Silent photography** must be on; videos and non-silent photos don't trigger it.
- **Memory:** the selection and switch are saved to `WL-SOUND.CFG` at the card root and restored after a restart.

See the [sound notes](docs/shutter-sound.md).

### Video bitrate and slow motion

Pick a profile in **Extensions → Video bitrate**. The default is High quality, saved in `WL-VIDEO.CFG`. Values in Mbps, maximum / average:

| High-quality mode | Stock | Balanced | High quality (default) |
| --- | --- | --- | --- |
| 4K 30p, 25p, 24p | 144 / 120 | 168 / 140 | 200 / 168 |
| 1080p 60p, 50p | 56 / 48 | 84 / 70 | 108 / 90 |
| 1080p 30p, 25p, 24p | 28 / 24 | 48 / 40 | 72 / 60 |

- The slow-motion recording limit grows from 3 to 25 minutes: up to 8 files on FAT32, one file on exFAT.
- Other modes keep their stock bitrates, frame rates and 125-minute limit.
- The Stock profile restores bitrates only; it does not undo the slow-motion extension.

### Other features

- **Video orientation:** locks 0°, 90°, 180° or 270° when recording starts and writes orientation metadata only, without rotating pixels ([doc](docs/video-rotation.md)).
- **Photo aspect:** masks at about 50% strength; choosing Off restores the stock frame ([doc](docs/aspect-profiles.md)).
- **Date stamp:** off by default, and applies to in-camera JPEGs only ([doc](docs/date-stamp.md)).

## Verification status

Offline checks and camera feedback are recorded separately. This table lists existing evidence only and does not claim every mode or failure condition is covered.

| Area | Evidence |
| --- | --- |
| Build and unit tests | 295 unit tests pass (19 more need optional tools such as arm-none-eabi-gcc and are skipped without them); builds are byte-for-byte reproducible with zero CRC residue |
| Loader installation | Maintainer confirmed installation on Z30 C 1.20; the firmware update page still works afterwards |
| Menus, language and added glyphs | QEMU native pages and Unicorn instruction checks; the added Chinese glyphs, the Language page (every region's list) and the firmware update page have QEMU checks and user camera confirmation |
| Dual live view, shutter sound, M900, aspect profiles | Confirmed by users on camera |
| Date stamp | Five formats, four colors, glow and both portrait orientations confirmed on camera |
| Video orientation | Camera feedback for landscape, both portrait sides and in-recording lock; no separate record for upside-down landscape |
| Portrait UI | Layout, automatic fill-in after a turn (about 0.3–0.5 s by USB timing) and switching back and forth confirmed on camera; the release build without the diagnostic readout is confirmed separately |
| Bitrate and slow motion | Profiles, Balanced and memory confirmed on camera; the 4K 200/168 row has no camera record yet |
| Photo zebra | Live view, stock thresholds and file integrity verified on camera; the separate switch is checked in QEMU only |
| MTP acceleration | Matches stock on 590 property-list replies, with faster queries |
| Wi-Fi 5 GHz | A user confirmed 5 GHz connections; throughput and long-term stability untested |
| SD card loading | The loader matches the reference implementation byte for byte in Unicorn; the resident EG's Language and firmware version pages are checked in QEMU; loading the `full` card file, recovery by removing the card, rejection of a damaged file and firmware updates are confirmed on camera; the second loader version, the category root page, the eighth tab and the ready banner are confirmed on camera, the Status page is checked in QEMU only |

`camera_verified: false` in a build report means **the build script itself ran no camera test**; camera results are recorded in the feature documents.

## Documentation

| Document | Contents |
| --- | --- |
| [GUI packaging tutorial](docs/gui-tutorial.en.md) | Illustrated walk-through: check the firmware, build and flash the loader, build the card file, troubleshooting |
| [Offline build guide](PATCH_GUIDE.md) | GUI and command-line steps, common errors (Chinese) |
| [Contributor guide](CONTRIBUTING.en.md) | Code layout, writing patches, tests and QEMU checks, commits and releases |
| [Build notes](docs/build.md) | Input checks, decoding, preset composition, patch flow |
| [Verification](docs/verification.md) | Unit tests, stock instruction execution, camera evidence |
| [UI verification log](docs/ui-qemu.md) | Menus, Chinese text, fonts and added glyphs, Language and firmware update pages |
| [SD card loading](docs/card-loader.md) | Resident EG, card file format, loading steps, log codes and camera record; the earlier memory probe is in [MMU probe](docs/card-loader-probe.md) |
| [Extension menu framework](docs/menu-framework.md) | Framework split from the shutter sound, category root page, Status page, eighth tab; layout plan in [menu-layout](docs/menu-layout.md) |
| [Ready banner](docs/ready-banner.md) | Live-view banner after the card file loads, image alignment rule |
| Feature documents | Linked from the feature table |
| [Licenses and third-party notices](THIRD_PARTY_NOTICES.md) | Per-file licenses and asset sources |

Topic documents are mostly in Chinese; this English README covers the same content as the Chinese one.

## License

- **Project identity:** this project belongs to the **WonderLantern** organization and is an independent project, not supported or endorsed by Nikon.
- **Code:** original code and documents are under the [MIT License](LICENSE). The files listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) are under the [Wonder Lantern Community License](licenses/Wonder-Lantern.txt), which allows use, modification and free redistribution but forbids paid distribution.
- **Added glyphs:** the glyphs converted from Noto fonts are under the [SIL OFL 1.1](licenses/OFL-Noto-CJK.txt).
- **Per-file terms:** each file's SPDX tag and the list above decide.
- **Nikon:** nothing here grants rights to Nikon firmware, Nikon resources or trademarks.
