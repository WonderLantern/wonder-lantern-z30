# Wonder Lantern Z30 离线构建指引

目前只支持 **Z30 原版 C 1.20 固件**。默认 full 生成包含直方图＋水平仪同屏、自定义快门声音、M 档 900 秒曝光、MTP 加速、视频自动方向、高码率与慢动作扩展、多画幅、照片斑马纹、Wi-Fi 5 GHz 和日期水印的完整功能独立 EG 模块产物。

构建生成独立 EG 模块，不会重建完整 Nikon 更新包。维护者已确认该模块可在 Z30 C 1.20 机身安装；详细验证范围见[验证说明](docs/verification.md)。

## 图形界面（推荐）

Windows 用户不需要安装 Python：

1. 从 [Releases](https://github.com/WonderLantern/wonder-lantern-z30/releases/latest) 下载 `WonderLanternZ30-windows-x64.zip`，完整解压。
2. 把原版固件 **Z_30_0120.bin** 放进解压后的文件夹，程序会自动填入；也可以手动选择。
3. 打开 `WonderLanternZ30.exe`，等待显示校验通过。
4. **打包加载器 EG 固件**：点“打包加载器 EG”，到 `output/card` 取 **EG209020.BIN** 刷进相机（只需一次）。先不放卡文件，确认 设置 → 固件版本 正常。
5. **打包功能卡文件**：“加载器文件夹”会自动指向刚才的文件夹；勾选功能，点“打包卡文件”，把生成的 `Z30` 文件夹复制到存储卡根目录，重启相机。以后换功能只需重新打包卡文件（先移走旧的 `Z30/WL.PATCH`），不用重刷。

程序需要 Windows 10/11 自带的 Microsoft Edge WebView2 运行时。文件夹要整体保留，不能只复制 exe。需要命令行或其他系统时，继续看下面的步骤；安装依赖后也可以运行 `python patch_gui.py` 打开同一个界面。

## 命令行：准备文件

安装 Python 3.10 或更新版本，下载并解压本项目的完整源码。

把原版固件 **Z_30_0120.bin** 放进项目文件夹，与 `patch.py` 放在一起。不要只下载 patch.py，项目中的 `src` 文件夹也需要保留。

## 命令行：运行命令

在项目文件夹里打开 PowerShell，依次执行：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

第一条命令安装依赖，只需首次执行。运行 patch 后会显示预设菜单，可自由组合：

| 编号 | 预设 | 内容 |
| --- | --- | --- |
| 1 | `full` | 全部十六项：同屏＋快门声音＋M900＋MTP 加速＋视频方向＋高码率与慢动作扩展＋多画幅＋照片斑马纹＋Wi-Fi 5 GHz＋日期水印＋竖拍显示＋陀螺仪电子防抖＋3D 跟踪＋对焦框颜色＋焦距显示＋NEF 压缩菜单 |
| 2 | `liveview-dual` | 直方图与水平仪同屏，新增全信息 DISP 页 |
| 3 | `shutter-sound` | 自定义电子快门声音 |
| 4 | `m900` | M 档 900 秒曝光 |
| 5 | `mtp-fast` | MTP 属性列表加速 |
| 6 | `video-rot` | 实验性自动视频方向元数据，在新录像对象配置时锁定姿态 |
| 7 | `bitrate-slowmo-uncap` | 提高 4K 与 1080p 高画质码率；慢动作录制上限 25 分钟，支持分段或大文件 |
| 8 | `aspect-profiles` | 自定义画幅与半透明取景遮幅；包含在 full 中，具体实机范围见专题 |
| 9 | `photo-zebra` | 照片取景斑马纹和独立照片开关；包含在 full 中，也可单独选择，新增开关待机身复验 |
| 10 | `wifi-5ghz` | 全部 22 个有效配置请求 US/-1，5 GHz 地区解锁；包含在 full 中，也可单独选择 |
| 11 | `date-stamp` | JPEG 胶片风格日期水印，五种格式、四种颜色和柔光，竖拍自动转向；包含在 full 中，菜单里默认关闭，见[日期水印说明](docs/date-stamp.md) |
| 12 | `portrait-ui` | 竖拿相机时照片取景信息改为竖版，转动后自动重排；包含在 full 中，菜单里默认关闭，见[竖拍显示说明](docs/portrait-ui.md) |
| 13 | `gyro-evr` | 陀螺仪电子防抖：录像时打开电子减震即生效；扩展菜单选标准 / 增强 / 原厂裁切 / 关闭；包含在 full 中，见[陀螺仪电子防抖](docs/gyro-evr.md) |
| 14 | `3d-tracking` | 3D 跟踪：按 OK 或半按快门开始的跟踪，识别到人物或动物后像触摸跟踪一样转为主体识别（细框）；照片拍摄菜单里选关闭 / 开启，默认关闭；包含在 full 中，见[3D 跟踪](docs/3d-tracking.md) |
| 15 | `af-frame-colour` | 对焦框颜色改为类似尼康 EXPEED 7 机型（Z9、Z8、Z6III 等）的风格：跟踪框和人物 / 动物框平时白色，半按对上绿色、没对上红色，AF-S 和 AF-C 相同；录像模式的人物 / 动物框为白色；AF-C 普通自动区域框对上时绿色；其他框不变；照片拍摄菜单里可选开启 / 关闭（默认开启），没有菜单；包含在 full 中，见[对焦框颜色](docs/af-frame-colour.md)  |
| 16 | `focal-length` | 照片取景对所有 CPU 镜头显示当前焦距（原厂只对电动变焦镜头显示），常开；包含在 full 中，见[焦距显示](docs/focal-length.md) |
| 17 | `nef-menu` | 扩展菜单里选择 NEF 压缩类型（无失真压缩／压缩／未压缩），保存到 `WL-NEF.CFG`；包含在 full 中，12/14 位下三个选项实机已确认，见[NEF 压缩说明](docs/nef-compression.md) |
| 18 | `nef-uncompressed` | 构建时把 NEF 固定为未压缩，没有菜单；不包含在 full 中，和 full 一起选时替换其中的 `nef-menu`；实机已确认 |
| 19 | `nef-compressed` | 构建时把 NEF 固定为压缩（有损），没有菜单；不包含在 full 中，和 full 一起选时替换其中的 `nef-menu`；实机已确认 |

例如输入 `2 4 5` 或 `liveview-dual,m900,mtp-fast`；输入 `1` 或直接回车选完整功能 full，重复功能自动去重。

`aspect-profiles` 是同一个自定义画幅预设，同时包含照片裁切和半透明取景遮幅；默认 `full` 包含它。遮幅按当前比例压暗成片范围外的区域，强度为 `128/255`（约 50%），内部网格与原厂图标保留；关闭网格仍显示遮幅，选择“关闭”恢复原厂画幅并清除遮幅。

菜单包含完整组合、十六个功能入口和两个可选预设。第 10 项 `wifi-5ghz` 可单独启用 5 GHz 地区解锁，默认 `full` 也包含它。覆盖无线配置 1–10、14–25 及未保存编号的回落请求为 US/-1，保留编号字段和保留槽，不改整机销售地区或 NVRAM；2.4 GHz 第 12、13 信道的网络可能受影响（推断，尚未实测）。输入 `1` 或直接回车启用全部十六项功能；第 8 项可单独选择多画幅，使用说明见[多画幅说明](docs/aspect-profiles.md)。第 9 项可单独选择照片斑马纹，验证范围见[照片斑马纹说明](docs/photo-zebra.md)。第 11 项可单独选择日期水印，第 12 项可单独选择竖拍显示。第 13–15 项是陀螺仪电子防抖、3D 跟踪和对焦框颜色，第 16 项是焦距显示，均包含在 full 中。第 17 项是 NEF 压缩菜单，包含在 full 中；第 18、19 项把 NEF 固定为未压缩或压缩，和 full 一起选时替换其中的第 17 项，三者互斥，说明见[NEF 压缩说明](docs/nef-compression.md)。旧名称 `modular` 仍可手动输入，现为同屏、M900 和快门声音三项组合，不占用菜单编号。

`full` 已包含 `video-rot`，旧别名 `modular` 不包含它。第二阶段读取原厂姿态，为每个新录像封装对象锁定方向；不改像素或已有文件，未知姿态回退横向。播放器需支持方向元数据；用户已反馈横拍、两侧竖拍及录制期间方向锁定实机正常。单独使用先安装 `python -m pip install '.[video]'`；原有 `.[sound]` 也包含所需汇编依赖。

选择 `full` 或菜单第 7 项也会改变录像行为：4K 30/25/24p 高画质平均码率提高到 168 Mbps（最大 200），1080p 60/50p 提高到 90 Mbps，30/25/24p 提高到 60 Mbps；4K 200 Mbps 需要持续写入 25 MB/s 以上的卡（V60 及以上）。慢动作录制上限从 3 分钟延长到 25 分钟，FAT32 最多生成 8 个文件，exFAT 使用单个大文件。码率菜单及设置保存需要 Keystone，可通过 `.[sound]` 安装。作者报告的实机测试使用包含该功能的 `full`；独立预设、普通模式 MP4、MP4/FAT32 分段、4K 码率行和其他码率尚未单独验证，详见[视频码率与慢动作限制](docs/bitrate-slowmo-uncap.md)。4K 50p/60p 帧率解锁的可行性结论见[专题分析](docs/uhd-4k60-feasibility.md)。

也可跳过菜单，在命令里写出所需组合：

```powershell
python patch.py full --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

脚本调用或重定向输入时，省略预设默认 full；加 `--interactive` 可强制显示菜单。程序会检查原版固件哈希并生成 EG 模块，原始文件不会被修改。

## 找到生成的固件

看到 `Built` 和输出文件路径，表示 EG 模块构建完成。该产物的机身安装已有实机验证；其他功能覆盖见[验证说明](docs/verification.md)。

打开 `output` 文件夹：

- **EG209020.BIN**：独立 EG 模块，可在 Z30 C 1.20 上安装；不是完整 Nikon 更新包。
- **EG209020.json**：构建信息，无需复制到相机。

## SD 卡加载（实验，命令行）

只刷一次常驻 EG，功能放在卡上，出问题时拔卡即可恢复（[说明](docs/card-loader.md)）：

```powershell
python tools/build_card.py --source "Z_30_0120.bin" --output "output/card"
```

1. 刷 `output/card/EG209020.BIN`，先不插卡文件，确认固件版本页正常、相机与原厂一致。
2. 把 `output/card/Z30/WL.PATCH` 复制到卡上的 `Z30` 文件夹（文件夹不存在就新建），重启。约 3 秒后功能出现。
3. 卡上的 `Z30/WL.LOG` 应为 `WL.PATCH S=01 …`；其他代码的含义见说明文档。
4. 以后只换卡文件：在同一个 `output/card` 目录运行同一命令并加 `--patch-only`（先移走旧的 `Z30/WL.PATCH`）。卡文件按这份已刷的常驻 EG 构建；提示需要更新常驻 EG 时，去掉 `--patch-only` 换一个新目录重新构建并重刷。

`--preset` 选择功能，默认 `full`。也可以用 `python patch.py full --card --source "Z_30_0120.bin" --output "output/card"`（加 `--patch-only` 只生成卡文件），或者图形界面。

## 常见问题

- **Windows 提示“已保护你的电脑”**：程序没有代码签名。请确认文件来自本项目 Releases 并核对 SHA-256，再决定是否运行。
- **程序打不开或提示缺少文件**：确认已完整解压，并且没有把 exe 单独移出文件夹。
- **找不到 python**：确认已安装 Python。Windows 下也可以把命令里的 `python` 换成 `py`。
- **SHA-256 mismatch**：固件不匹配，请使用未经修改的原版 Z30 C 1.20。
- **Output or report already exists**：移走 output 中之前生成的 BIN 和 JSON，再运行；程序不会覆盖旧文件。

这是实验性固件修改，离线检查不保证所有相机行为。
