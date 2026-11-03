# Wonder Lantern Z30



**简体中文** · [English](README.en.md)

Wonder Lantern Z30 是 WonderLantern 组织面向 **Nikon Z30 C 1.20** 的实验性底层宿主与固件扩展研究项目，探索类似 Magic Lantern 的机身端扩展能力。当前实现是 Z30 专用原型，不是跨机型通用开发模板，也不代表 Nikon 支持或认可。

项目在用户提供的原厂固件上应用经过原字节校验的补丁，生成独立 **EG 模块**和 JSON 构建报告。维护者已有该模块在 Z30 C 1.20 上安装的实机记录；本地构建程序不重建完整 Nikon 更新包，不连接相机，也不写 SD 卡。各功能覆盖范围见下文验证状态。

## 功能与预设

`full` 包含下表除 `date-stamp` 外的十项功能，包括 Wi-Fi 5 GHz 地区解锁。每项仍可独立选择或组合；`module-host` 自动包含 `shutter-sound`。

| 预设 | 功能 |
| --- | --- |
| `liveview-dual` | 保留现有 DISP 页，在水平仪页后增加“顶部信息＋直方图＋水平仪”全信息页。[说明](docs/liveview-full-info.md) |
| `shutter-sound` | 从 SD 卡读取自定义电子快门 WAV，提供开关、文件选择、预听和重新加载。 |
| `m900` | 启用原厂已有的 M 档长曝光路径，最长 900 秒，保留 M 档判断。 |
| `mtp-fast` | 加速 MTP 属性列表查询，保留返回数据语义。 |
| `module-host` | **实验性** SD 原生模块宿主，加载并运行 ZMOD，复用声音菜单框架。 |
| `video-rot` | 为新录像写入方向元数据，锁定开始录制时的方向。 |
| `bitrate-slowmo-uncap` | 机内选择原始、均衡、高质量码率并记忆；慢动作录制上限 25 分钟，支持 FAT32 分段或 exFAT 大文件。 |
| `aspect-profiles` | 同一菜单切换六种照片比例，包含半透明取景遮幅并保留原厂内部网格。[说明](docs/aspect-profiles.md) |
| `photo-zebra` | 照片取景斑马纹；独立照片开关，复用原厂图案与阈值。[说明](docs/photo-zebra.md) |
| `wifi-5ghz` | Wi-Fi 5 GHz 地区解锁：全部 22 个有效无线配置请求 US/-1，包含在 full 中，也可单独选择。 |
| `date-stamp` | **实验性**：在 JPEG 右下角打胶片风格的日期水印，可选五种格式、四种颜色和柔光晕，竖拍时随照片方向转向，默认关闭；不含在 `full` 中。[说明](docs/date-stamp.md) |

`wifi-5ghz` 替换无线配置 1–10、14–25（含未保存编号的回落）的国家代码和修订号，保留各自编号字段和 12 个保留槽，不写整机销售地区或 NVRAM。原厂绕过这张表的初始化路径保持原样。原本可用 2.4 GHz 第 12、13 信道的机身可能无法再连接这些信道的网络（此为推断，尚未实测）；不等于开放全部信道，也不保证相机自身建立 5 GHz 热点、提速或实际协商带宽。驱动的有效信道与功率需适合使用地点。单独构建只需 Python 标准库。

`photo-zebra`：将原厂实时斑马纹扩展到照片取景，复用原厂图案与八档阈值设置。已包含在 `full` 中，也可独立选择。通过“自定义菜单 → 照片高光显示”独立开启或关闭，默认关闭。照片取景、原厂样式/阈值和 JPEG/NEF 文件完整性已有机身验证；独立开关的机身验证尚未完成。[实现与验证范围](docs/photo-zebra.md)。

`aspect-profiles` 是同一个自定义画幅预设，同时包含照片裁切和半透明取景遮幅；默认 `full` 包含它。遮幅按当前比例压暗成片范围外的区域，强度为 `128/255`（约 50%），内部网格与原厂图标保留；关闭网格仍显示遮幅，选择“关闭”恢复原厂画幅并清除遮幅。

声音与模块菜单支持简体中文、繁体中文和英文回退。当前构建不增加 Camera Status 页面。

## 快速开始

### 图形界面（推荐，Windows）

不需要安装 Python，也不需要输入命令：

1. 从 [Releases](https://github.com/WonderLantern/wonder-lantern-z30/releases/latest) 下载 `WonderLanternZ30-windows-x64.zip`，**完整解压**，不要在压缩包里直接运行。
2. 自行准备未经修改的 `Z_30_0120.bin`（要求见下表），放到解压后的文件夹里，或在程序里手动选择。
3. 打开 `WonderLanternZ30.exe`，等待显示固件校验通过。界面语言可在中文和英文之间切换。
4. 勾选功能（默认 `full`），点击“开始构建”。产物写入程序旁边的 `output` 文件夹，不会覆盖已有文件。

程序没有代码签名，Windows SmartScreen 可能提示风险；可先核对 Release 页面列出的 SHA-256。程序与下方命令行使用同一套校验和补丁流程，不连接相机，也不写 SD 卡。

其他系统或需要修改源码时，可运行 `python patch_gui.py`，或使用下面的命令行。

### 命令行

详细说明见[离线构建指引](PATCH_GUIDE.md)。以下命令在项目根目录的 PowerShell 中运行。

### 1. 准备源码与原厂固件

需要 Python **3.10+**、完整项目源码（保留 `src/`），以及未经修改的 **`Z_30_0120.bin`**。固件由用户自行提供，仓库不附原厂固件或完整修改版更新包。

构建器接受的原厂输入：

| 项目 | 值 |
| --- | --- |
| 相机 / 固件 | Nikon Z30 / C 1.20 |
| 文件大小 | 34,496,798 字节 |
| SHA-256 | `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a` |

### 2. 安装依赖并构建

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

`.[sound]` 提供 `full` 所需的 Keystone 汇编依赖。`liveview-dual` 新增 DISP 页也需要 Keystone；单独构建 `m900` 只需要 Python 标准库；声音、模块宿主、码率档位和 MTP 加速需要 Keystone。码率预设自动包含声音菜单框架，但声音默认关闭。单独视频方向可安装 `.[video]`，与 `.[sound]` 提供相同的汇编依赖。

macOS 若安装后仍报 `fail to load the dynamic library`，请激活构建所用的 Python 环境，再安装并链接原生库：

```bash
brew install keystone
ln -s "$(brew --prefix keystone)/lib/libkeystone.dylib" "$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/keystone/libkeystone.dylib"
python -c "from keystone import Ks; print('Keystone OK')"
```

### 3. 选择预设

终端中省略预设时显示多选菜单：编号 **1–12** 依次为 `full` 和上表十一项预设（第 **10** 项为 `photo-zebra`，第 **11** 项为 `wifi-5ghz`，第 **12** 项为 `date-stamp`）。输入编号或名称，以空格或逗号分隔；直接回车选择 `full`。

- `2 4 5` 或 `liveview-dual,m900,mtp-fast`：同屏、M900、MTP 加速。
- `6` 或 `module-host`：实验性模块宿主及其必需的声音框架。
- `1` 或 `full`：全部十项功能，包含视频方向与高码率/慢动作扩展。

也可在命令行直接指定预设，多个名称用空格分隔：

```powershell
python patch.py liveview-dual m900 mtp-fast --source "Z_30_0120.bin" --output "output/EG209020-custom.BIN"
python patch.py wifi-5ghz --source "Z_30_0120.bin" --output "output/EG209020-wifi-5ghz.BIN"
python patch.py full wifi-5ghz --source "Z_30_0120.bin" --output "output/EG209020-full-wifi-5ghz.BIN"
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020-full.BIN"
```

重复功能只应用一次，构建顺序固定。`full video-rot` 或 `full bitrate-slowmo-uncap` 与单独 `full` 启用相同功能集合。非交互脚本或重定向输入时，省略预设默认 `full`；`--interactive` 可强制显示菜单。

旧别名 `modular` 仍可在命令行或菜单中手动输入，但不占菜单编号。它仅包含 `liveview-dual`、`m900`、`shutter-sound` 和 `module-host`，不包含 MTP 加速、视频方向或码率/慢动作扩展。当前完整组合为 `full`。

### 4. 查看产物

构建器检查原始包大小与 SHA-256，内部解码、检查容器和模块 CRC、核对 EG 身份，再逐项检查原字节、边界和不重叠条件，应用补丁并重建 CRC。offset 是**解包后 EG 模块**的坐标。

| 文件 | 用途 |
| --- | --- |
| `EG209020.BIN`（或指定输出名） | 独立 EG 模块；不是重新封装的 `Z_30_0120.bin`。 |
| 同名 `.json` | 输入/输出摘要、所选预设、展开功能、修改位置、原字节与替换字节、追加代码及功能信息。 |

程序不修改输入，拒绝覆盖已有输出或报告。每次构建使用新路径，或先移走上一份 BIN 和 JSON。`Built` 表示本地构建完成；CRC 成功不等于所有机身行为通过验证。具体实现和开发者用的 `--extracted-eg` 模式见[构建说明](docs/build.md)。

## 菜单与语言

菜单跟随原厂 **语言 (Language)** 设置：简体中文、繁体中文分别使用原厂中文字库，其余语言显示英文，不宣称已翻译其他语言。

| 构建内容 | 英文入口 | 简体中文入口 | 繁体中文入口 |
| --- | --- | --- | --- |
| 仅声音框架 | `Shutter sound` | 快门声音 | 快門聲音 |
| 包含画幅、斑马纹、码率或模块功能（如 `full`） | `Extensions` | 自定义菜单 | 自訂選單 |
| 模块页 | `SD Modules` | SD 卡功能 | SD 卡功能 |

照片画幅、照片高光显示和日期水印也直接列在**照片拍摄菜单**末尾，视频码率列在**视频拍摄菜单**末尾，在列表第一行按一次上键即可到达；构建里没有的功能不会出现。这些行只是入口，右侧固定显示 `--`，当前值请进入页面查看。自定义菜单里的同名入口继续保留。原厂菜单直达行只有 QEMU 验证，尚未上机确认。

已修复标题缺失、标签被空值栏裁剪、左侧栏消失、返回后按钮残留，以及非中文入口和重启后首次进入的乱码。此前绘制和语言修复的机身复测正常；新增提示页和声音记忆也已获用户实机确认；QEMU 和原厂构造器的对照证据见 [UI 验证记录](docs/ui-qemu.md)。

WAV 文件名、路径和模块输出仍使用 ASCII。中文字库只覆盖原厂已有字符，菜单使用已核对的用词，不提供任意中文文本输入或完整新字库。

## 自定义快门声音

包含画幅、码率或模块功能时，自定义菜单先按用途分类；声音的开关、文件、预听和重新加载集中在“快门声音”子菜单。仅声音预设直接显示这些操作。文件选择页以 `*` 标记当前选项，按一次 OK 应用并返回声音菜单；选择文件不会自动开启声音或播放。

支持以下 SD 卡路径：

```text
SD 卡根目录/
├─ SHUTTER.WAV
└─ SHUTTER/
   ├─ 1.WAV
   ├─ 2.WAV
   ├─ 3.WAV
   └─ 4.WAV
```

推荐准备根目录 `SHUTTER.WAV`，编号文件及 `SHUTTER` 目录可以缺省。启用时若没有已加载声音，会读取当前选择（初始为 `SHUTTER.WAV`）；也可先加载已有编号文件，再开启声音，因此根目录文件并非每种使用路径都必需。

声音要求：**8000 Hz、单声道、16 位有符号小端 PCM、非空且最长 1.08 秒**，使用标准 44 字节 WAV 头，不包含额外块。可规范化短整数 PCM WAV：

```powershell
python tools/prepare_wav.py input.wav SHUTTER.WAV
```

转换器使用线性插值；高采样率音频可先用带抗混叠滤波的重采样器处理。已有声音文件无需因更新固件而覆盖。仓库不附第三方音频，脚本中的 `example(slot)` 可生成原创合成示例。

没有有效保存记录时默认 OFF。开启功能并开启相机原厂静音拍摄后，在静音照片拍摄时触发声音；录像和非静音照片不触发，预听可在 OFF 时使用。成功选择文件或改变开关后，自动保存到 SD 卡根目录 `WL-SOUND.CFG`，重启后在首次进入菜单或首次照片触发时恢复此前的选择和启用状态，无需先打开菜单。声音文件必须仍在原路径；首次拍照可能增加一次读取的延迟。配置损坏或声音加载失败时保持关闭，保存失败则提示当前设置未保存。加载失败保留此前有效声音与开关，错误显示在独立提示页的正文，标题保持菜单名称。新增提示页与记忆功能已获用户实机确认，并有指令级和辅助 QEMU 检查。详见[声音说明](docs/shutter-sound.md)。

## SD 模块宿主（实验性）

`module-host` 和 `full` 从 **`Z30/CORE.ZMOD`** 加载一个原生模块。宿主不自动启动模块；用户先加载，再运行，成功模块及其 RAM 状态保留至关机。维护者已有实机加载和运行记录。

生成计数示例：

```powershell
python tools/prepare_module.py --example counter --output "output/CORE.ZMOD"
```

将其复制到 SD 卡的 `Z30/CORE.ZMOD`，进入 **设置菜单 → 自定义菜单 / Extensions → SD 卡功能 / SD Modules**，先选择 `Load CORE.ZMOD`（加载），再选择 `Run module`（运行）。其他示例、替换流程、初版宿主的历史分页适配器见[模块说明](docs/modules.md)。

当前接口为 **ABI 2**，载荷最多 **16 KiB**，私有 context 为 **64 字节**。模块输出合同为最多 **40 个可打印 ASCII 字符**；中文界面目前最多显示前 **24** 个字符。模块是可信 ARM Thumb 原生代码，CRC32 只检查损坏，不提供签名或沙箱。没有热卸载或开机自动执行接口；更换成功加载的模块需重启。跨核缓存服务无应答时可能持续等待，Lua 解释器尚未接入。

## 视频自动方向

`video-rot` 在新录像封装对象的原厂配置成功后读取姿态，为该对象锁定 **0°、90°、270° 或 180°**；未知或不可用姿态回退 0°。录制中转动机身不会改变当前对象的方向。该方案对应 [issue #3](https://github.com/WonderLantern/wonder-lantern-z30/issues/3) 的自动方向实现。

仅修改新录像的视频轨方向元数据，不旋转像素、不修改已有文件；共享矩阵、电影头和音轨保持原厂行为。播放器需支持方向元数据。用户已确认横拍、两侧竖拍和录制中方向锁定正常；倒置横拍没有单独结果记录，原始 MP4 矩阵尚未在仓库逐字节检查。详见[视频方向说明](docs/video-rotation.md)。

## 视频码率与慢动作

在 **设置 → 自定义菜单 → 视频码率** 中选择档位，`*` 标记当前选择。默认“高质量”，通过 SD 卡根目录 `WL-VIDEO.CFG` 保存；重启后首次打开菜单或读取录像参数时恢复。缺失或无效配置使用高质量档。保存失败显示独立提示页，当前选择仍可用于后续录像。

单位 **Mbps，最大 / 平均**：

| 高画质模式 | 原始（原厂） | 均衡 | 高质量（默认） |
| --- | --- | --- | --- |
| 1080p 60p、50p | 56 / 48 | 84 / 70 | 108 / 90 |
| 1080p 30p、25p、24p | 28 / 24 | 48 / 40 | 72 / 60 |

慢动作**录制时间**上限从 3 分钟延长到 **25 分钟**；FAT32 最多 8 个文件，exFAT 使用单个大文件。4K、120p/100p、慢动作和标准画质的码率保持原厂值；分辨率、帧率、GOP 和其他模式的 125 分钟上限保持原厂值。25 分钟上限考虑了固定容量索引表，不是无限时录制。

档位用于后续录像配置，不写入正在运行的编码器；“原始”只恢复码率，慢动作仍保留 25 分钟扩展。固定高质量码率和慢动作文件完整性已有检查；运行时切换、均衡档和配置持久化也已获用户实机确认。参数、QEMU 测试边界和此前实机记录见[码率与慢动作说明](docs/bitrate-slowmo-uncap.md)。

## 验证状态

此前全部功能正常的概括反馈，后经 4:3／5:4 及回放问题复测更正。成片、取景和遮幅的对应修正分别记录在专题文档；最新 v42 回放修复获机身复测正常的记录。离线结果与机身反馈分开，不推断全部模式或故障条件均已覆盖。

| 范围 | 已记录证据 |
| --- | --- |
| 构建与回归 | 当前 54 项单元测试通过；画幅整合的六种构建组合检查见[整合记录](docs/aspect-preset-integration.md)；历史 `shutter-sound`、`module-host`、`full` 的 CRC 与逐字节重复构建检查通过。 |
| 菜单与语言 | QEMU 原生页面/构造器和 Unicorn 指令检查；此前绘制与语言修复有用户实机反馈。新增提示页与声音记忆已获用户实机确认。 |
| 自定义画幅与遮幅 | v11 成片、v16 取景、v17 边界与网格共存及后续遮幅有对应用户反馈；v42 本次回放问题复测正常。详见[当前说明](docs/aspect-profiles.md)。 |
| EG 安装、SD 宿主 | 维护者确认 Z30 C 1.20 安装以及 ZMOD 加载/运行。 |
| 视频方向 | 独立预设有实机方向与录制中锁定反馈；覆盖边界见专题文档。 |
| MTP 加速 | 已记录原厂对照的 590 条属性列表响应一致及查询耗时下降，数字与条件见验证文档。 |
| 照片斑马纹 | 照片取景、原厂阈值/开关及 JPEG/NEF 文件完整性有机身验证；独立照片开关有 ARM 与三种语言 QEMU 菜单检查，尚待机身验证。 |
| 码率与慢动作 | 历史固定高质量版本有实机文件检查；新增机内档位、均衡档和记忆功能已获用户实机确认。 |

同屏、快门声音播放、M 档长曝光和多画幅均已获用户实机确认。构建报告中的 `camera_verified: false` 表示**脚本本身没有执行机身测试**，实机反馈另行记录。EG 安装记录不等于任意生成产物均已验证；本工具也不构建完整 Nikon 更新包。

运行测试（原厂固件不是单元测试的必需输入）：

```powershell
python -m pip install '.[sound,verify]'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

固件身份、CRC、边界、原厂指令执行、MTP 对照和外部 QEMU 环境的复现步骤见[验证说明](docs/verification.md)及 [UI 记录](docs/ui-qemu.md)。QEMU 辅助启动和诊断输入不能替代完整实机启动、录制、SD/USB 或所有故障场景测试。

## 文档索引

专题文档目前以中文为主；本 README 的[英文版](README.en.md)覆盖同样的使用与范围说明。

| 文档 | 内容 |
| --- | --- |
| [离线构建指引](PATCH_GUIDE.md) | 图形界面与命令行步骤、产物与常见错误。 |
| [构建说明](docs/build.md) | 输入身份、解码、预设组合与补丁流程。 |
| [照片取景斑马纹](docs/photo-zebra.md) | 独立开关、原厂图案与阈值、偏好保存及验证范围。 |
| [日期水印](docs/date-stamp.md) | 胶片风格 JPEG 日期水印的挂点、验证范围和上机步骤。 |
| [画幅与半透明遮幅](docs/aspect-profiles.md) | 六种比例、输出尺寸、取景遮幅与验证边界。 |
| [v42 回放修复](docs/aspect-lifecycle-v42.md) | 生命周期限制、旧遮幅清理及用户复测。 |
| [声音说明](docs/shutter-sound.md) | 声音菜单、触发条件、文件格式和状态。 |
| [模块说明](docs/modules.md) | 宿主操作、ABI 2、示例与实验性限制。 |
| [视频方向](docs/video-rotation.md) | 姿态锁定、元数据和实机记录。 |
| [码率与慢动作](docs/bitrate-slowmo-uncap.md) | 参数、索引容量和实机文件检查。 |
| [验证说明](docs/verification.md) | 单元测试、原厂指令执行、MTP 与机身证据。 |
| [UI 验证记录](docs/ui-qemu.md) | 中文、绘制、返回与首次入口修复。 |
| [许可与第三方说明](THIRD_PARTY_NOTICES.md) | 按文件划分的许可证及资源来源。 |

## 许可与项目身份

本项目属于 **WonderLantern** 组织，是独立实验项目；不代表 Nikon 支持或认可。

原创代码和文档采用 [MIT License](LICENSE)，[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 列出的声音、菜单与画幅/遮幅实现文件采用 [Wonder Lantern Community License](licenses/Wonder-Lantern.txt)；以各文件 SPDX 标记及清单为准。

该社区许可证允许使用、修改和免费分发，要求公开分发的修改版本提供同许可证源码，禁止收费分发软件；允许用软件从事收费摄影等专业工作。使用或分发组合代码须遵守各文件适用条款。其“衍生版本不得自称官方 Wonder Lantern 发布”的条款约束软件发布身份，不改变本仓库属于 WonderLantern 组织的事实。

本项目许可证不授予 Nikon 固件、原厂资源、第三方软件或商标的权利。原厂固件与菜单模板由匹配输入本地读取；参考项目与依赖的来源和许可见[第三方说明](THIRD_PARTY_NOTICES.md)。
