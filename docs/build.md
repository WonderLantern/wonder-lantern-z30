# 从原始固件包构建功能扩展

2026-11-03 `date-stamp`（JPEG 胶片风格日期水印）纳入 `full`，机内默认关闭；SD 模块宿主不再作为构建预设提供，`full` 不再包含它。见[日期水印说明](date-stamp.md)。

2026-11-02 新增 `date-stamp`。

2026-10-20 `photo-zebra` 已纳入 `full` 默认组合，当前共十项功能，也可单独选择。机内照片高光显示开关默认关闭。
照片斑马纹复用原厂图案与阈值；机内独立开关及保存方式见[照片斑马纹说明](photo-zebra.md)。

各功能的机身验证覆盖范围见专题文档，离线结果与硬件验证分别记录。

当前用户流程只需准备原始 Z30 C 1.20 固件包和本项目 patch 程序。默认 full 包含同屏、自定义电子快门声音、M 档 900 秒曝光、MTP 加速、视频自动方向、高码率与慢动作扩展、多画幅、照片斑马纹、Wi-Fi 5 GHz 和日期水印，不包含状态页面。

## 发布 Windows 图形界面程序

Keystone 为 GPLv2，其 FOSS 例外未列入 Wonder Lantern Community License，因此发布包不包含 Keystone，只附带预先汇编的机器码。维护者在项目根目录运行：

```powershell
python -m pip install '.[gui-exe]'
python tools/build_asm_cache.py
python tools/build_gui_exe.py
```

`build_asm_cache.py` 汇编全部 1023 种功能组合，再禁用 Keystone 重建并比对哈希，不一致时删除缓存。补丁代码改动后必须重新生成，否则受影响的组合在打包程序中会报错。结果是 `dist/WonderLanternZ30-windows-x64.zip`，已附带许可证文本。

## 运行

`aspect-profiles` 与其他功能预设一样可单独选择，默认 full 已包含。同一预设默认包含动态半透明遮幅，关闭网格仍显示，内部网格及原厂图标保留；不增加单独遮幅预设。其菜单、构建与实机记录见[多画幅说明](aspect-profiles.md)。

需要 Python 3.10+。声音功能和 `mtp-fast` 需要 Keystone，首次安装依赖；固件不需要手动解包：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py --source path/to/Z_30_0120.bin --output output/EG209020.BIN
```

可自由组合 `full`、`liveview-dual`、`shutter-sound`、`m900`、`mtp-fast`、`video-rot`、`bitrate-slowmo-uncap`、`aspect-profiles`、`photo-zebra`、`wifi-5ghz` 和 `date-stamp`。菜单第 6 项为视频方向，第 7 项为高码率与慢动作扩展，第 11 项为日期水印。旧别名 `modular` 保留命令行和手动输入兼容，不在菜单中显示。终端中省略预设会显示多选菜单，输入编号或名称（空格或逗号分隔）；直接回车选择 full。非交互脚本省略预设时仍默认 full，可用 `--interactive` 强制显示菜单。命令行的多个预设用空格分隔：

```powershell
python patch.py liveview-dual m900 mtp-fast --source path/to/Z_30_0120.bin --output output/EG209020.BIN
```

`full` 展开为全部十项功能：同屏、M900、快门声音、MTP 加速、视频方向、高码率与慢动作扩展、多画幅、照片斑马纹、Wi-Fi 5 GHz 和日期水印。`modular` 现为同屏、M900 和快门声音；`modular mtp-fast video-rot bitrate-slowmo-uncap aspect-profiles photo-zebra wifi-5ghz date-stamp` 与 full 启用同一功能集合。SD 模块宿主的代码保留给研究工具，通过 `build(..., module_host=True)` 构建，不再出现在 patch 程序和图形界面中。重复功能只应用一次，构建顺序固定，同一功能集合的输出不会随输入顺序改变。报告记录 `selected_presets` 和展开后的 `enabled_features`。

### 视频码率与慢动作限制

默认 `full` 已包含 `bitrate-slowmo-uncap`。重新构建默认固件会使用以下高画质码率，单位为 Mbps，写作“最大 / 平均”：

| 模式 | 原厂 | 修改后 |
| --- | --- | --- |
| 1080p 60p、50p 高画质 | 56 / 48 | 108 / 90 |
| 1080p 30p、25p、24p 高画质 | 28 / 24 | 72 / 60 |

慢动作录制上限由 3 分钟延长到 25 分钟；FAT32 最多分成 8 个文件，exFAT 使用单个大文件。4K、120p/100p、慢动作和标准画质的码率保持原厂值，分辨率、帧率、GOP 和其他模式的 125 分钟上限不变。25 分钟上限为固定容量的封装索引表保留余量。

机内 **自定义菜单 → 视频码率** 提供原始、均衡、高质量三档，默认高质量。均衡档最大 / 平均为 1080p 60p、50p 的 84 / 70 Mbps，以及 1080p 30p、25p、24p 的 48 / 40 Mbps。选择保存到 `WL-VIDEO.CFG`，用于后续录像；原始档不撤销慢动作时长扩展。

该预设自动包含声音菜单框架，需要 Keystone，除原有 8 处数据/分支替换外还追加参数读取、菜单和设置保存代码。单独构建：

```powershell
python patch.py bitrate-slowmo-uncap --source path/to/Z_30_0120.bin --output output/EG209020.BIN
```

要同时保持原厂码率和慢动作限制，可明确选择其余六项，例如 `modular mtp-fast video-rot`。仅恢复原厂码率可在机内选择“原始”。报告的 `features.bitrate_slowmo_uncap` 记录默认高质量码率和慢动作上限，`features.bitrate_profiles` 记录三档菜单、运行时函数及独立配置文件。

作者报告使用包含该功能的 `full`，验证了五种 1080p 高画质码率和三种慢动作的 25 分钟停止行为，覆盖 MOV/exFAT 单文件、MOV/FAT32 分段及慢动作 MP4/exFAT。独立预设尚未单独刷机；普通模式 MP4、MP4/FAT32 分段和默认值以外的码率尚未测试。实机记录及索引检查方法见[视频码率与慢动作限制](bitrate-slowmo-uncap.md)。离线构建报告仍保持 `camera_verified: false`，表示脚本没有执行机身测试，不扩展为所有组合的实机验证。

### 自动视频方向（第二阶段）

`video-rot` 在新录像封装对象的原厂视频配置函数成功返回后读取原厂姿态，将方向锁定在该对象的视频 `tkhd` 矩阵缓冲内；结束录制时构造视频轨头只读这份快照。原厂姿态 ID 0/1/2/3 映射为 0°/90°/270°/180°，未知或不可用回退 0°。共享 0° 矩阵、`mvhd` 和音轨不改动，也不在停止时重新采样方向。需要 Keystone，可通过 `.[video]` 或原有 `.[sound]` 安装：

```powershell
python -m pip install '.[video]'
python patch.py video-rot --source path/to/Z_30_0120.bin --output output/EG209020.BIN
```

默认 `full` 已包含视频方向；`full video-rot` 的重复选择不会改变固件输出。补丁包含两处带 expected 校验的 10 字节跳转和 280 字节矩阵/包装函数追加区，沿用身份、边界、不重叠和 CRC 检查。报告 `features.video_rot` 记录原厂姿态接口、钩子、矩阵表、对象内快照位置及 `camera_verified: false`，该字段表示离线构建流程没有执行实机验证。用户已反馈独立视频预设的横拍、两侧竖拍及录制期间方向锁定实机正常；不将其扩大为所有组合的实机验证。复用原厂已经维护的姿态状态，不直接调用 issue 所列的三轴转换函数，也不添加未经标定的传感器阈值。

只改新视频的元数据，不改像素或已有文件，播放器需尊重方向矩阵。锁定点是封装对象成功配置时，不是实测的 REC 按钮硬件时间；跨文件分段会创建新对象时重新采样。第二阶段已有横拍、两侧竖拍、录制中方向锁定以及机身/电脑播放的实机反馈；倒置、分段时机及未列出的模式仍需独立验证，具体边界见 [视频方向说明](video-rotation.md)。

单独 M900 只需要标准库。同屏新增全信息 DISP 页，需要 Keystone，详见[全信息 DISP 页](liveview-full-info.md)。同屏、声音、码率档位、日期水印和 `mtp-fast` 通过 `.[sound]` 安装 Keystone 汇编，视频方向可使用 `.[video]` 或 `.[sound]`。输出和同名 JSON 必须不存在，程序拒绝覆盖。CLI 通过 Python 运行。

原始包大小为 34,496,798 字节，SHA-256 为：

```text
1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a
```

固件由用户自行准备，仓库不附原始或修改版完整固件。

## 程序执行顺序

1. 检查整个原始包的大小和 SHA-256；不匹配则退出，不解码或生成文件。
2. 从该版本已知零填充位置恢复等价周期 XOR 掩码，内部解码；不附第三方 XOR 表。
3. 检查容器结构、整包 CRC、模块边界和各模块 CRC，提取 EG 并再次检查其 SHA-256。
4. 每个补丁包含 offset、expected bytes 和 replacement bytes；检查原字节、等长、边界和不重叠后替换。
5. 对需要代码钩子的功能，在原 EG 尾部 CRC 前追加代码并修补跳转地址；纯数据补丁不追加。所有构建均重建 CRC 并验证残差为零。
6. 输出 EG209020.BIN 和报告，列出修改 offset、原字节、替换字节、追加地址和输入/输出摘要。

offset 使用**解包后的 EG 模块**坐标，不是编码外层包的同一位置。输出是独立 EG 模块，不重新封装 Z_30_0120.bin。维护者已确认该 EG 模块在 Z30 C 1.20 上可安装；具体记录见[验证说明](verification.md)。

本解码器仅支持哈希匹配的原始包。已知零位置索引是一组版本限定的观察数据；列/行/高位掩码通过 XOR 方程恢复，所得掩码不要求与任何上游表逐项相同，只需整体解码等价。解码后的 EG 大小为 33,939,514 字节，SHA-256：

```text
8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa
```

开发者可用 --extracted-eg 传入该模块，普通用户无需准备中间模块。

## 验证

普通测试不需要原厂固件：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

安装 Unicorn 后可执行原厂与补丁跳转窗口：

```powershell
python -m pip install '.[sound,verify]'
python tools/verify_liveview_gates.py --source path/to/Z_30_0120.bin --preset full
python tools/verify_extended_features.py --source path/to/Z_30_0120.bin
```

同屏检查遍历全部 256 种 DISP，核对续接地址和栈、R4、LR；在原厂后续服务之前停止。扩展检查执行 M 档判断、上限分支、速度枚举/请求解析和 WAV/PCM 转换，使用明确的属性 RAM 和可用列表输入。它们不执行完整相机或真实曝光。执行方法与覆盖范围见 [验证说明](verification.md)。

## 输出与功能范围

输出为独立 EG 模块，不重建外层 Z_30_0120.bin。固件版本菜单保留原厂 1.20，构建身份通过 JSON 报告的 SHA-256 核对。该模块的机身安装已完成实机测试。

默认组合包含同屏、自定义声音、M900、MTP 加速、视频自动方向、高码率与慢动作扩展、多画幅、照片斑马纹、Wi-Fi 5 GHz 和日期水印。900 秒补丁改变扩展标志读取，不写入其保存值；原厂模式判断和曝光路径保留。视频快门窗口在构建后核对为原厂字节。

默认 `full` 构建不包含 SD 模块宿主，不添加状态页、计数、CFW 版本标记或视频慢快门。离线检查不能保证真实显示、声音或曝光行为。

## 预设整合更新

自定义画幅与半透明遮幅现在属于同一个 `aspect-profiles` 预设，`full` 默认包含。普通构建不带诊断记录；默认模块摘要、组合一致性与本次回归见 [预设整合](aspect-preset-integration.md)。旧版本待确认或未提交表述保留当时范围。
