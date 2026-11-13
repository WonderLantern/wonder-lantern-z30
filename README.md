# Wonder Lantern Z30

**简体中文** · [English](README.en.md)

Wonder Lantern Z30 是 WonderLantern 组织面向 **Nikon Z30 C 1.20** 的实验性固件扩展项目，探索类似 Magic Lantern 的机身端扩展能力。构建器在你自己提供的原厂固件上应用逐字节校验过的补丁，输出一个修改后的 **EG 模块**和一份 JSON 构建报告。它是 Z30 专用原型，不是通用的跨机型框架，也不代表 Nikon 支持或认可。

> [!WARNING]
> 修改固件有风险。刷入前请确认：
> - 保留未经修改的 `Z_30_0120.bin`；
> - 知道如何通过 **设定菜单 → 固件版本** 重新升级。
>
> 只要能进入升级页面，就能刷回原厂固件。项目交付的每个测试构建都会先检查这一点。构建程序本身不连接相机，也不写 SD 卡。

## 功能一览

`full` 包含下表全部十二项功能，也可以单独选择或任意组合。画幅、斑马纹、码率、日期水印、竖拍显示和 NEF 压缩菜单会自动带上 `shutter-sound` 的菜单框架。

| 预设 | 功能 | 说明 |
| --- | --- | --- |
| `liveview-dual` | 在水平仪页后新增“顶部信息＋直方图＋水平仪”全信息 DISP 页 | [文档](docs/liveview-full-info.md) |
| `shutter-sound` | 从 SD 卡读取自定义电子快门声音，可开关、选择文件、预听、重新加载 | [文档](docs/shutter-sound.md) |
| `m900` | 启用原厂已有的 M 档长曝光路径，最长 900 秒 | — |
| `mtp-fast` | 加速 MTP 属性列表查询，返回数据不变 | [验证](docs/verification.md) |
| `video-rot` | 为新录像写入方向元数据，锁定开始录制时的方向 | [文档](docs/video-rotation.md) |
| `bitrate-slowmo-uncap` | 机内三档码率并记忆，提高 4K/1080p 高画质码率；慢动作录制上限延长到 25 分钟 | [文档](docs/bitrate-slowmo-uncap.md) |
| `aspect-profiles` | 六种照片比例，带半透明取景遮幅，保留原厂网格 | [文档](docs/aspect-profiles.md) |
| `photo-zebra` | 把原厂斑马纹扩展到照片取景，独立开关 | [文档](docs/photo-zebra.md) |
| `wifi-5ghz` | 全部有效无线配置请求 US/-1 国家码，解锁 5 GHz 连接 | 见注¹ |
| `date-stamp` | JPEG 右下角的胶片风格日期水印，五种格式、四种颜色、可选光晕，随竖拍转向 | [文档](docs/date-stamp.md) |
| `portrait-ui` | 竖拿时照片取景信息改为竖版，转动后自动补全，默认关闭；i 菜单、触摸设置页和直方图仍为横版 | [文档](docs/portrait-ui.md) |
| `nef-menu` | 在扩展菜单里选择 NEF 压缩类型（无失真压缩／压缩／未压缩），保存到 `WL-NEF.CFG` | [文档](docs/nef-compression.md)，默认选项下实拍正常，菜单选择的效果尚未实机验证 |

¹ `wifi-5ghz` 只改无线国家请求，不写整机销售地区或 NVRAM。原本可用 2.4 GHz 第 12、13 信道的机身可能无法再连接这些信道（推断，未实测）。它不保证相机自身的 5 GHz 热点，也不保证提速；有效信道与功率需符合使用地点的规定。

### 可选预设（不在 `full` 中）

下面两个预设把 NEF 固定为某种压缩类型，没有菜单，互斥，默认不包含在 `full` 中。和 `full` 一起选时会替换其中的 `nef-menu`，例如 `full nef-uncompressed`。

| 预设 | 功能 | 说明 |
| --- | --- | --- |
| `nef-uncompressed` | 构建时把 NEF 固定为未压缩 | [文档](docs/nef-compression.md)，已有实机确认 |
| `nef-compressed` | 构建时把 NEF 固定为压缩（有损） | [文档](docs/nef-compression.md)，已有实机确认 |

## 快速开始

### 准备原厂固件

固件需要自行准备，仓库不附带原厂固件，也不附带修改后的升级包。构建器只接受下面这个原厂文件：

| 项目 | 值 |
| --- | --- |
| 相机 / 固件 | Nikon Z30 / C 1.20 |
| 文件 | `Z_30_0120.bin`，34,496,798 字节 |
| SHA-256 | `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |

### 图形界面（Windows，推荐）

1. 从 [Releases](https://github.com/WonderLantern/wonder-lantern-z30/releases/latest) 下载 `WonderLanternZ30-windows-x64.zip`，**完整解压**后再运行。
2. 把 `Z_30_0120.bin` 放进解压后的文件夹，或在程序里手动选择。
3. 打开 `WonderLanternZ30.exe`，等待固件校验通过。界面可切换中文或英文。
4. 勾选功能（默认 `full`），点击“开始构建”。产物写入程序旁的 `output` 文件夹，不会覆盖已有文件。

程序没有代码签名，Windows SmartScreen 可能提示风险，可以先核对 Release 页面列出的 SHA-256。其他系统可以运行 `python patch_gui.py`，或改用命令行。

### 命令行

需要 Python **3.10+** 和完整源码。以下命令在项目根目录运行（PowerShell）：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

- `.[sound]` 安装 Keystone 汇编器。除了单独构建 `m900` 或 `wifi-5ghz`，其他功能都需要它。
- 省略预设时会显示多选菜单：编号 1–15 依次是 `full`、上表十二项和下面两个可选预设；直接回车选 `full`。
- 也可以直接写预设名，多个用空格分隔，重复的功能只应用一次：

```powershell
python patch.py liveview-dual m900 mtp-fast --source "Z_30_0120.bin" --output "output/EG209020-custom.BIN"
```

- **macOS：** 如果安装后报 `fail to load the dynamic library`，在构建用的 Python 环境里执行：

```bash
brew install keystone
ln -s "$(brew --prefix keystone)/lib/libkeystone.dylib" "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/keystone/libkeystone.dylib"
```

更详细的步骤和常见错误见[离线构建指引](PATCH_GUIDE.md)。

### 产物

| 文件 | 用途 |
| --- | --- |
| `EG209020.BIN` | 修改后的独立 EG 模块，不是重新封装的 `Z_30_0120.bin` |
| 同名 `.json` | 输入/输出摘要、所选功能、每处修改的偏移、原字节与替换字节 |

- 构建器会先校验原厂包的大小和 SHA-256，内部解码并检查各级 CRC，再逐项核对原字节、边界和不重叠条件，最后重建 CRC。
- 它不修改输入，也拒绝覆盖已有输出。
- 输出 `Built` 只表示本地构建完成，不代表所有机身行为都已验证。

实现细节见[构建说明](docs/build.md)。

## 使用说明

### 菜单与语言

- **菜单入口：** 新功能集中在 **设定菜单 → 自定义菜单**（繁体“自訂選單”，英文 `Extensions`）；只构建声音时入口叫“快门声音”。
- **直达行：** 照片画幅、照片高光显示和日期水印也列在照片拍摄菜单末尾，视频码率列在视频拍摄菜单末尾，在列表第一行按一次上键即可到达。直达行右侧固定显示 `--`。这些直达行目前只有 QEMU 验证。
- **语言：** 菜单跟随原厂 **语言 (Language)** 设置，显示简体中文、繁体中文或英文；其他语言显示英文。
- **补字：** 原厂中文字库只有菜单用字。构建时会为原厂中文字体补上全部可打印 ASCII 和中文标签用到的缺字，字形由 Noto Sans CJK 转换而来（SIL OFL 1.1）。补字不提供任意中文输入。

### 自定义快门声音

把声音放在 SD 卡上：

```text
SD 卡根目录/
├─ SHUTTER.WAV
└─ SHUTTER/
   ├─ 1.WAV  …  4.WAV
```

- **格式：** 8000 Hz、单声道、16 位有符号 PCM，非空且最长 1.08 秒，标准 44 字节 WAV 头。可以用 `python tools/prepare_wav.py input.wav SHUTTER.WAV` 转换。
- **触发：** 需要同时开启原厂**静音拍摄**，在静音拍照时播放；录像和非静音拍照不触发。
- **记忆：** 选择和开关会保存到卡根目录的 `WL-SOUND.CFG`，重启后自动恢复。

详见[声音说明](docs/shutter-sound.md)。

### 视频码率与慢动作

在 **自定义菜单 → 视频码率** 中选择档位，默认“高质量”，保存在 `WL-VIDEO.CFG`。单位 Mbps，格式为“最大 / 平均”：

| 高画质模式 | 原厂 | 均衡 | 高质量（默认） |
| --- | --- | --- | --- |
| 4K 30p、25p、24p | 144 / 120 | 168 / 140 | 200 / 168 |
| 1080p 60p、50p | 56 / 48 | 84 / 70 | 108 / 90 |
| 1080p 30p、25p、24p | 28 / 24 | 48 / 40 | 72 / 60 |

- 慢动作录制上限从 3 分钟延长到 25 分钟：FAT32 最多分 8 个文件，exFAT 用单个文件。
- 其他模式的码率、帧率和 125 分钟上限保持原厂值。
- “原厂”档只恢复码率，不撤销慢动作扩展。

### 其他功能

- **视频方向**：开始录制时锁定 0°、90°、180° 或 270°，只写方向元数据，不旋转像素（[文档](docs/video-rotation.md)）。
- **照片画幅**：遮幅强度约 50%，选“关闭”即恢复原厂画幅（[文档](docs/aspect-profiles.md)）。
- **日期水印**：默认关闭，只作用于机内生成的 JPEG（[文档](docs/date-stamp.md)）。

## 验证状态

离线检查和机身反馈分开记录。下表只列已有证据，不推断所有模式或故障条件都已覆盖。

| 范围 | 已有证据 |
| --- | --- |
| 构建与单元测试 | 145 项单元测试通过；构建可逐字节重复，CRC 残差为零 |
| EG 安装 | 维护者确认可在 Z30 C 1.20 上安装 |
| 菜单、语言与补字 | QEMU 原生页面与 Unicorn 指令检查；中文补字、语言页（各地区列表）和固件升级页有 QEMU 检查与用户实机确认 |
| 同屏、快门声音、M900、多画幅 | 用户实机确认 |
| 日期水印 | 五种格式、四种颜色、光晕和左右竖拍已获实机确认 |
| 视频方向 | 横拍、两侧竖拍和录制中锁定有实机反馈；倒置横拍无单独记录 |
| 竖拍显示 | 布局、转动后自动补全（USB 计时约 0.3–0.5 秒）和横竖来回切换已获实机确认；正式构建去掉诊断读数后的版本另行实机确认 |
| 码率与慢动作 | 档位、均衡档和记忆功能已获实机确认；4K 200/168 码率行尚无实机记录 |
| 照片斑马纹 | 取景、原厂阈值和文件完整性有机身验证；独立开关只有 QEMU 检查 |
| MTP 加速 | 与原厂对照，590 条属性列表响应一致，查询更快 |
| Wi-Fi 5 GHz | 用户确认可连接 5 GHz；吞吐和长期稳定性未测 |

构建报告里的 `camera_verified: false` 表示**构建脚本本身没有做机身测试**，实机结果另行记录在各专题文档里。

## 文档

| 文档 | 内容 |
| --- | --- |
| [离线构建指引](PATCH_GUIDE.md) | 图形界面与命令行步骤、常见错误 |
| [贡献者开发文档](CONTRIBUTING.md) | 代码结构、补丁写法、测试与 QEMU 检查、提交与发布流程 |
| [构建说明](docs/build.md) | 输入校验、解码、预设组合与补丁流程 |
| [验证说明](docs/verification.md) | 单元测试、原厂指令执行与机身证据 |
| [UI 验证记录](docs/ui-qemu.md) | 菜单、中文显示、字库与补字、语言页与固件升级页检查 |
| 各功能专题 | 见“功能一览”中的链接 |
| [许可与第三方说明](THIRD_PARTY_NOTICES.md) | 按文件划分的许可证与资源来源 |

专题文档以中文为主；英文 README 与本文内容一致。

## 许可

- **项目身份：** 本项目属于 **WonderLantern** 组织，是独立实验项目，不代表 Nikon 支持或认可。
- **代码许可：** 原创代码和文档采用 [MIT License](LICENSE)。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 列出的声音、菜单与画幅实现文件采用 [Wonder Lantern Community License](licenses/Wonder-Lantern.txt)：允许使用、修改和免费分发，禁止收费分发。
- **补字字形：** 由 Noto 字体转换的补字字形按 [SIL OFL 1.1](licenses/OFL-Noto-CJK.txt) 授权。
- **以文件为准：** 各文件以 SPDX 标记和上述清单为准。
- **不涉及的权利：** 本项目许可证不授予 Nikon 固件、原厂资源或商标的任何权利。
