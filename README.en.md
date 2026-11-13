# Wonder Lantern Z30

[简体中文](README.md) · **English**

Wonder Lantern Z30 is the WonderLantern organization's experimental firmware extension project for the **Nikon Z30 C 1.20**, exploring Magic Lantern-style on-camera extensions. The builder applies byte-verified patches to stock firmware that you supply, and writes a modified **EG module** plus a JSON build report. It is a Z30-specific prototype, not a cross-model framework, and it is not supported or endorsed by Nikon.

> [!WARNING]
> Modified firmware carries risk. Before flashing:
> - keep the unmodified `Z_30_0120.bin`;
> - know how to update again from **Setup menu → Firmware version**.
>
> As long as the update page opens, you can return to stock firmware. Every test build the project hands out is checked for this first. The builder itself never connects to the camera or writes to an SD card.

## Features

`full` enables all eleven features below; each one can also be selected alone or in any combination. Aspect, zebra, bitrate, date stamp and portrait UI automatically include the `shutter-sound` menu framework.

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
| `portrait-ui` | Photo live-view info turns to a portrait layout when the camera is held upright and fills in on its own after a turn; off by default; the i menu, touch-opened setting pages and the histogram stay landscape | [Doc](docs/portrait-ui.md) |

¹ `wifi-5ghz` only changes the wireless country request; it does not write the camera's sales region or NVRAM. Cameras that could use 2.4 GHz channels 12 and 13 may no longer connect on them (inferred, not tested). It does not promise a 5 GHz access point from the camera itself or faster transfers, and the channels and power in use must suit local rules.

## Quick start

### Stock firmware

Bring your own firmware: the repository ships neither stock firmware nor a modified update package. The builder accepts exactly this file:

| Item | Value |
| --- | --- |
| Camera / firmware | Nikon Z30 / C 1.20 |
| File | `Z_30_0120.bin`, 34,496,798 bytes |
| SHA-256 | `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |

### Graphical app (Windows, recommended)

1. Download `WonderLanternZ30-windows-x64.zip` from [Releases](https://github.com/WonderLantern/wonder-lantern-z30/releases/latest) and **extract all files** before running.
2. Put `Z_30_0120.bin` in the extracted folder, or choose it in the app.
3. Open `WonderLanternZ30.exe` and wait for the firmware check to pass. The interface switches between Chinese and English.
4. Tick the features (default `full`) and click Build. Output goes to the `output` folder next to the app; existing files are never overwritten.

The app is not code-signed, so Windows SmartScreen may warn; compare the SHA-256 listed on the Release page first. On other systems, run `python patch_gui.py` or use the command line.

### Command line

Needs Python **3.10+** and the full source tree. From the project root (PowerShell):

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

- `.[sound]` installs the Keystone assembler. Every feature except `m900` or `wifi-5ghz` built on their own needs it.
- Without presets, a multi-select menu lists `full` and the eleven features as entries 1–12; press Enter for `full`.
- Or name the presets directly, separated by spaces; repeated features apply once:

```powershell
python patch.py liveview-dual m900 mtp-fast --source "Z_30_0120.bin" --output "output/EG209020-custom.BIN"
```

- **macOS:** if installation is followed by `fail to load the dynamic library`, run this in the Python environment you build with:

```bash
brew install keystone
ln -s "$(brew --prefix keystone)/lib/libkeystone.dylib" "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/keystone/libkeystone.dylib"
```

Step-by-step instructions and common errors are in the [offline build guide](PATCH_GUIDE.md) (Chinese).

### Output

| File | Purpose |
| --- | --- |
| `EG209020.BIN` | The modified stand-alone EG module, not a repackaged `Z_30_0120.bin` |
| `.json` with the same name | Input/output digests, selected features, and every edit's offset, original bytes and replacement bytes |

- The builder first checks the stock package's size and SHA-256, decodes it internally and checks every CRC, then verifies original bytes, bounds and non-overlap for each edit before rebuilding the CRC.
- It never modifies its input and refuses to overwrite existing output.
- `Built` means the local build finished. It does not mean every camera behavior is verified.

Implementation details: [build notes](docs/build.md) (Chinese).

## Using the features

### Menus and language

- **Menu entry:** new features live in **Setup menu → Extensions** (Simplified Chinese 自定义菜单, Traditional 自訂選單). A sound-only build names the entry `Shutter sound`.
- **Direct rows:** Photo aspect, Photo zebra and Date stamp are also listed at the end of the photo shooting menu, and Video bitrate at the end of the video shooting menu; press up once from the first row to reach them. Direct rows show `--` on the right. These direct rows are verified in QEMU only.
- **Language:** menus follow the stock **Language** setting and show Simplified Chinese, Traditional Chinese or English; other languages show English.
- **Added glyphs:** the stock Chinese fonts only carry menu characters. Builds add every printable ASCII character and the characters the Chinese labels need, converted from Noto Sans CJK (SIL OFL 1.1). This does not provide arbitrary Chinese text input.

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
| Build and unit tests | 145 unit tests pass; builds are byte-for-byte reproducible with zero CRC residue |
| EG installation | Maintainer confirmed installation on Z30 C 1.20 |
| Menus, language and added glyphs | QEMU native pages and Unicorn instruction checks; the added Chinese glyphs, the Language page (every region's list) and the firmware update page have QEMU checks and user camera confirmation |
| Dual live view, shutter sound, M900, aspect profiles | Confirmed by users on camera |
| Date stamp | Five formats, four colors, glow and both portrait orientations confirmed on camera |
| Video orientation | Camera feedback for landscape, both portrait sides and in-recording lock; no separate record for upside-down landscape |
| Portrait UI | Layout, automatic fill-in after a turn (about 0.3–0.5 s by USB timing) and switching back and forth confirmed on camera; the release build without the diagnostic readout is confirmed separately |
| Bitrate and slow motion | Profiles, Balanced and memory confirmed on camera; the 4K 200/168 row has no camera record yet |
| Photo zebra | Live view, stock thresholds and file integrity verified on camera; the separate switch is checked in QEMU only |
| MTP acceleration | Matches stock on 590 property-list replies, with faster queries |
| Wi-Fi 5 GHz | A user confirmed 5 GHz connections; throughput and long-term stability untested |

`camera_verified: false` in a build report means **the build script itself ran no camera test**; camera results are recorded in the feature documents.

## Documentation

| Document | Contents |
| --- | --- |
| [Offline build guide](PATCH_GUIDE.md) | GUI and command-line steps, common errors (Chinese) |
| [Contributor guide](CONTRIBUTING.en.md) | Code layout, writing patches, tests and QEMU checks, commits and releases |
| [Build notes](docs/build.md) | Input checks, decoding, preset composition, patch flow |
| [Verification](docs/verification.md) | Unit tests, stock instruction execution, camera evidence |
| [UI verification log](docs/ui-qemu.md) | Menus, Chinese text, fonts and added glyphs, Language and firmware update pages |
| Feature documents | Linked from the feature table |
| [Licenses and third-party notices](THIRD_PARTY_NOTICES.md) | Per-file licenses and asset sources |

Topic documents are mostly in Chinese; this English README covers the same content as the Chinese one.

## License

- **Project identity:** this project belongs to the **WonderLantern** organization and is an independent experiment, not supported or endorsed by Nikon.
- **Code:** original code and documents are under the [MIT License](LICENSE). The sound, menu and aspect implementation files listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) are under the [Wonder Lantern Community License](licenses/Wonder-Lantern.txt), which allows use, modification and free redistribution but forbids paid distribution.
- **Added glyphs:** the glyphs converted from Noto fonts are under the [SIL OFL 1.1](licenses/OFL-Noto-CJK.txt).
- **Per-file terms:** each file's SPDX tag and the list above decide.
- **Nikon:** nothing here grants rights to Nikon firmware, Nikon resources or trademarks.
