# 离线验证

> 当前：`aspect-profiles` 将自定义画幅与动态半透明遮幅合并为一个预设，`full` 默认包含。v42 本次回放问题已获用户机身正常反馈；其本地执行、QEMU 与实机范围见 [v42](aspect-lifecycle-v42.md)。以下逐版本条目保留当时状态。

> 最新状态：[v36](aspect-mask-menu-v36.md)补齐遮幅外缘、移除下层残留缺口，并按用途分类菜单。七档 LCD 遮幅、21 个控件前景、3736 个边缘像素及简/繁/英文原生菜单检查通过。本版机身效果待确认；以下保留历史记录。

> 最新反馈：v22 减细导致双线复现。新增 [v23](aspect-border-caps.md)，恢复长边参数 8、保留短端帽 2；实机效果未确认。以下为历史版本记录。

> 后续实机反馈：v21 单线已确认，但端部突出。较细笔画实验见 [v22 细线与端部调整](aspect-border-fine.md)，效果尚待真机确认。以下保留历史版本证据。

> 2026-10-18：用户屏幕照片确认 v20 黄色边框出现，同时暴露双线。v21 仅调整笔画宽度，原厂转换与驱动提交、资源保护检查通过，单线实机效果未验证。见 [v21](aspect-border-stroke.md)。

> 2026-10-17 最新复测更正：4:3、5:4 在 v8 下仍出现整幅横向压缩，且存在间歇性表现；撤回此前“v8 已修复”或“全部画幅实机通过”的结论。其余四种宽画幅据用户反馈正常。全尺寸 DX 横向裁切尚待真实拍摄管线验证，以下历史成功反馈不作为当前验收结论。


2026-10-17 状态更新：此前“全部八项功能实机检验通过”的结论已更正。多画幅中的 4:3、5:4 主图仍可能整幅横向压缩；独立 DX 4:3 实验输出为 4952×3712，但用户未观察到左右裁切。当前通过的本地下游配置与缩放参数检查不能替代实机像素验收。v10 诊断范围见[诊断说明](aspect-diagnostic.md)。其他功能的既有验证不因本次画幅失败而改写。

新增全信息 DISP 页的适配器差分、原厂控件回归和 QEMU 六页循环见[全信息 DISP 页](liveview-full-info.md)。

UI 标题、侧栏、返回按钮、双语文字、首次入口乱码，以及独立错误提示页和声音配置保存/恢复的回归见 [UI QEMU 验证](ui-qemu.md)。声音配置的文件服务测试使用明确模型，不能代替实机断电和 SD 写入验证。

机内码率档位通过 `tools/verify_bitrate_profiles.py` 检查三档 × 30 条原厂模式记录、完整记录字段、越界写入、首次读取恢复、配置损坏和写入失败；SD 服务为明确的指令测试模型。`tools/verify_bitrate_profiles_qemu.py` 在辅助 QEMU 中运行原厂模式索引、菜单和图形管线，SD 服务使用明确诊断覆盖。详细流程与实机缺口见[码率档位说明](bitrate-slowmo-uncap.md)。

## 单元测试

在项目根目录执行，无需原厂固件：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

覆盖输入身份、CRC、补丁边界与重叠、原字节校验、追加对齐、容量、Thumb 指令生成和合成输入的解码掩码恢复。

SD 卡加载（[说明](card-loader.md)）：
- `tests/test_card.py`：重定位与卡文件格式，参考加载器结果与直接在该基址链接的结果逐字节相同，各种不一致都不写入；
- `tests/test_card_loader.py`：在 Unicorn 中执行真正的 Thumb 加载器，`full` 加载结果与参考加载器逐字节相同，并覆盖无文件、CRC、常驻 id、截断、多余数据、内存不足和预期不符；
- `tests/test_mmu_probe.py`：MMU 探测模块的指令流；
- `tests/test_fonts.py`：用 `common=True` 扩展的字体里约 7700 个新字形逐个取回一致。

2026-11-24 实机：`full` 卡文件加载正常（`WL.LOG` 为 `S=01`），拔卡恢复原厂，损坏文件被拒绝（`S=07`），固件升级不受影响。

`video-rot` 单元测试使用合成 EG（仅替换测试中的身份常量），执行真实 Plan、预设构建和 CRC 路径，核对四份大端矩阵、两处钩子、追加区、共享矩阵不变、expected 不匹配拒绝及报告。构建测试需要 Keystone，未安装时明确跳过。预设/CLI 测试核对显式接入、重复选择、菜单第 7 项和默认范围不变。

## 自动视频方向离线检查

准备哈希匹配的原版固件，在项目根目录执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m pip install '.[video,verify]'
python tools/verify_video_rot.py --source path/to/Z_30_0120.bin
# 安装 .[sound,verify] 后可检查组合与原厂 tkhd 指令：
python tools/verify_video_rot.py --source path/to/Z_30_0120.bin --combinations --emulate
```

也支持 `--extracted-eg`。基础检查使用 Keystone 构建：共享 0° 矩阵不变，原厂矩阵等值替换逐字节还原 stock；`video-rot` 只有两处 10 字节钩子及矩阵/函数追加区，CRC 残差为零。`--combinations` 检查与同屏、M900、声音、MTP、full、modular 和 module-host 组合：撤销两个视频钩子和新增尾部后逐字节等于去掉视频功能的基线，其他功能的尾部不变，顺序和重复选择不影响输出。full 已包含视频方向，额外选择 video-rot 必须与 full 单独构建逐字节一致。

`--emulate` 使用 `video_rot_fixture.py` 和 Unicorn 执行原厂视频配置、原厂姿态 getter、memcpy 和完整视频 `tkhd` 构造器，不替换这些函数。测试四种方向、未知值 4、异常值 255、不可用回退、空参数和配置校验失败、配置后改变姿态、重复构造以及多个录像对象隔离；除对象的视频矩阵外，构造出的头数据逐字节与原厂一致，并检查保存寄存器和栈。姿态 RAM、参数和封装对象是明确的合成输入，不证明传感器、文件写入或机身播放；报告保留 `camera_verified: false`。

### 现有 Z30 QEMU 的构造器检查

已构建的独立 `video-rot` EG 可用 `tools/verify_video_rot_qemu.py` 在 Linux/WSL 测试。WSL 的 Python 也需要 Keystone；例如两个项目位于同一目录时，在 WSL 的扩展项目根目录运行：

```sh
python3 -m pip install '.[video]'
python3 tools/verify_video_rot_qemu.py \
  --qemu-root ../z30-qemu \
  --stock-eg ../z30-qemu/.local/firmware/eg2090_0120a0.bi \
  --candidate build/video-rot-stage2/EG209020.BIN \
  --output-dir build/video-rot-stage2-qemu
```

候选必须逐字节匹配由原厂 EG 构建的独立 `video-rot`，且 CRC 为零；不接受任意修改固件或 `full video-rot`。QEMU 保留原厂 BIOS 哈希检查，显式 debugger 写入并读回两个钩子及追加区/CRC；初始化 scatter RAM，提供合成姿态、视频参数、封装对象和调用上下文。为从复位状态调用原厂函数，额外提供正常、不可缓存的恒等映射页表及 CP15 初始化；这些是诊断输入，不是原厂启动证据。分别运行原厂与补丁，执行四种方向、未知值 4 和异常值 255 的配置及完整视频 `tkhd` 构造、录制期间改变姿态的快照保留、重复构造、不可用回退、空参数、原厂配置校验失败和对象隔离；逐字节对照头数据，仅允许视频矩阵变化。不提供配置、姿态或 memcpy 桩。

输出 `verification.json` 及两份 QEMU 日志。此测试不是自然开机或完整录像测试；现有模型不支持完整视频录制。机器加载方式和合成上下文在报告中明确记录，默认模拟器流程不变。

第一阶段的固定矩阵构建已收到实机反馈：横拍和竖拍视频均变为竖向播放。第二阶段随后已收到横拍、两侧竖拍方向正确及录制中方向保持锁定的实机反馈；原始视频尚未在本仓库逐字节检查实际文件矩阵。


## 原厂指令执行检查

准备匹配的原版固件并安装可选依赖：

```powershell
python -m pip install '.[sound,verify]'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python tools/verify_liveview_gates.py --source "Z_30_0120.bin" --preset full
python tools/verify_extended_features.py --source "Z_30_0120.bin"
python tools/verify_mtp_fast.py --source "Z_30_0120.bin"
```

工具使用 Unicorn 执行选定的 ARM Thumb 指令路径，输出 JSON 结果；`passed: true` 表示相应断言通过。

| 检查器 | 覆盖内容 | 限制 |
| --- | --- | --- |
| `verify_liveview_gates.py` | 两个原厂与补丁 DISP 判断窗口、256 种值、续接地址、栈及保存寄存器 | 使用合成 RAM，在原厂后续绘制服务前停止 |
| `verify_mtp_fast.py` | `mtp-fast` 包装函数：32 种 handle 类型、取值与仅计数两种模式、builder 失败、记录获取失败、栈及保存寄存器 | 句柄转换、记录获取/释放和 builder 主体为桩，不涉及文件系统、USB 或并发 |
| `verify_extended_features.py` | M 档长曝光标志、其他属性对照、模式与上限、速度枚举及请求解析、WAV 校验与 PCM 转换 | 属性、可用列表和缓冲区为明确输入，不执行完整相机 |

构建器还核对匹配固件、修改前字节、窗口重叠、容量、CRC 和视频快门原厂窗口。JSON 构建报告记录输入身份、补丁 offset 和输出摘要。

这些检查不验证完整启动、刷写、屏幕像素、触摸、扬声器或传感器真实曝光。相机表现需要独立验证，不能由离线结果推出。

## 真机验证

维护者确认已在 Nikon Z30 C 1.20 上完成以下实机验证：

- 独立 EG 模块可通过机身更新流程安装。
- `modular` 构建的 SD 宿主可加载并运行 ZMOD 模块。
- 第二阶段独立 `video-rot` 构建：横拍、两侧竖拍方向正确，录制中转动机身方向保持锁定。用户还确认连续录像切换方向、重启、所用 4K/慢动作模式以及声画、播放、照片基本回归正常。未单独记录倒置横拍结果或原始 MP4 字节检查；不将独立预设反馈扩大为所有组合的实机覆盖。
- `mtp-fast` 构建（SHA-256 `281ecaae…0dde`）在 47 JPG + 47 NEF 的卡上与原厂对照：590 条 GetObjectPropList 响应逐字节一致；文件夹属性列表约 2920 ms → 680 ms，全卡约 3220 ms → 980 ms，单对象均值约 44 ms → 19 ms。耗时随卡内文件变化；GetObjectInfo 和 GetThumb 未改动。

该确认覆盖上述安装及模块宿主流程；各常规功能（同屏显示、快门声音、M 档长曝光）的实机结果尚未在本页逐项记录。离线检查与真机验证是不同的证据来源。

### MTP 属性列表对照

`mtp-fast` 的返回内容需要在同一张卡、同一批文件上对照原厂固件。`tools/ptp_timing.py` 通过 Windows 自带 MTP 驱动发送只读操作，需要 `python -m pip install '.[timing]'`（comtypes，仅 Windows）：

```powershell
python tools/ptp_timing.py --dump stock.json      # 原厂固件
python tools/ptp_timing.py --dump patched.json    # 刷入 mtp-fast 后
python tools/ptp_timing.py --compare stock.json patched.json
```

转储覆盖每个对象的全部属性与单属性请求、文件夹 depth 1、全卡各 depth 与格式过滤，以及不存在的文件、无效 handle、不支持的属性和 group code 等失败响应；`--compare` 逐字节比较并以非零退出码报告差异。不带参数运行则测量耗时。查询期间拍摄、删除、拔卡或 USB 中断时的行为不在该对照范围内，尚未验证。

## v20 独立边框通道管理

v19 新增菜单数据中记录到 73 次通道未启用拦截、0 次生产器调用；该槽序号比文件头计数多 1，按异常菜单数据单独保留。v20 增加仅对空闲关闭通道的临时启用和恢复，见[实现、日志解释与验证](aspect-border-channel.md)，尚待实机确认。

## v19 独立边框定位

用户确认 v18 网格开启时也无黄色边框，关闭后亦无，因此物理目标未实现。v19 增加只读状态计数及格式 4 的事件 5 尾部扩展，详见[定位记录与采样步骤](aspect-border-diagnostic.md)。

## v18 独立边框试验（实机失败）

见[实现、边界与验证记录](aspect-independent-border.md)。显式启用，不改变默认 v17 构建；原先的启用通道夹具不足以证明机身显示，用户已复测失败。

## v17 框线与参考线验收

用户确认 v16 取景正常。v17 仅修改参考网格生成：36 项轴、120 项投影/样式、6 项无效输入保留原网格、36 项完整 updater/bank 参数和 36 项原厂驱动提交通过；驱动案例每次产生七项预期寄存器写入。648 项显示拟合与 84 项成片参数同 v16；另有主几何、预览、诊断与菜单回归。网格样式在成片范围内重排，不是独立 GPU 叠加层；屏外边界不伪装成屏内矩形。本地 viewport 仍有合成输入；2026-10-18 用户另就 4:3／5:4 左右边界与成片一致、3×3／4×4 内部线共存的机身复测结果均为正常，补充了限定范围的实机验收。详见[行为、映射与验证边界](aspect-frame-grid.md)。

## v16 实机查询证据与原厂显示拟合

两份格式 4 实机日志确认 `0x1018AE57` 的首次/重启初始化阶段和 WH 不同。v16 将该精确调用点加入 4:3/5:4 取景输出守卫。648 项完整原厂 `0x1018ADF0` 显示计算与 84 项 v15 成片参数对照通过，另有 162 项原取景算术路径和 72 项调用点守卫检查。没有硬写缓存为 720×480；之后用户确认 v16 取景正常。该反馈不证明所有 LCD/HDMI 模式；v17 框线的独立验收见上节。原始日志保留在本地忽略目录；详见[证据、参数夹具与限制](aspect-display-fit.md)。

## v15 取景诊断与已失败的修复假设

2026-10-18 用户确认 v14 与 v13 行为相同，首次启用仍挤压。下方 v14 合成验证不代表物理修复成功。v15 只扩展状态记录：格式 4、96 槽、15384 字节；保留高频查询的 first/latest 尺寸与定义明确的取景缓存字段，等待首次挤压/重启正常的实机对照日志。15 组调用点 ABI/尺寸与 v14 等价、first 槽保留、变化过滤、冻结、导出及旧格式解码通过；还核对了真实原厂窗口函数写入的缓存偏移。其余几何、成片参数、预览与菜单回归通过。详见[采样步骤、记录字段与验证限制](aspect-view-trace.md)。

## v11 成片与 v14 首次启用历史边界

用户确认 v11 拍摄画面正常。v13 首次设置仍取景挤压，重启后正常，随后切换 5:4 正常；这不同于 v12 的重启仍挤压。v14 将目标画幅发布提前到原厂区域 setter 前，失败时回退扩展选择字段，仅成功后保存。147 组重入通知切换、13 项失败/守卫、162 项取景矩形、54 项精确调用点守卫及 84 项成片参数等价检查通过；另通过 63 项主几何、1680 项预览选择器、36 项参考框、360 项完整预览构造、59 项单元测试和诊断导出。QEMU 正常菜单生命周期的 4:3/5:4/关闭三次切换、区域联动与日志导出通过。重入通知是明确夹具，真实首次启用、任务调度和 LCD 像素仍待实机验证；参考框范围与网格共存尚未解决，直方图确认正常。见[取景调查](aspect-liveview-investigation.md)。

## 预设整合更新

自定义画幅与半透明遮幅现在属于同一个 `aspect-profiles` 预设，`full` 默认包含。普通构建不带诊断记录；默认模块摘要、组合一致性与本次回归见 [预设整合](aspect-preset-integration.md)。旧版本待确认或未提交表述保留当时范围。
