# 全信息 DISP 页

2026-10-17 状态更新：用户确认当前全部八项功能均已在 Nikon Z30 C 1.20 实机检验通过，包括多画幅、全信息页、声音配置记忆和机内码率档位。下文旧版本的待实机表述保留当时的测试范围，以本条为当前功能状态；离线工具的验证字段仍仅描述工具自身证据，不表示穷举全部模式、组合或故障条件。

`liveview-dual` 保留现有五页及水平仪页上的双控件显示，在水平仪页之后额外增加一页：原厂顶部信息、直方图和水准仪同时启用。正常循环为 `0 → 1 → 2 → 3 → 5 → 4 → 0`；原厂直方图不可用或照片信息页不适用时，仍按原厂条件跳过对应原厂页面。

单独构建现在也需要 Keystone：

```powershell
python -m pip install '.[sound,verify]'
python patch.py liveview-dual --source Z_30_0120.bin --output output/EG209020-full-info.BIN
```

`full`、`modular` 以及显式包含 `liveview-dual` 的组合均包含新页。没有改动照片或录像像素；直方图仍遵守原厂“将设置应用于即时取景”、有效采样和输出模式限制。此精确镜像尚无实机验证，报告保留 `camera_verified: false`。

## 实现边界

- 新页使用独立 DISP 状态 5，通过原厂设置服务保存和读取。切换钩子只在原水平仪页后插入新页；退出新页时读取原厂 `table[3]`，不越界读取五项切换表。
- 原厂快照始终保留真实状态 5。呈现入口把新页解释为信息页或水平仪页；当前/上一帧比较仍保留不同状态，避免用临时改写快照实现显示。
- 直方图采样和控件门控增加状态 5。两个原厂描述项过滤函数中，仅在新页且控件编号为 31 时加入信息页显示位。直方图第一个显示掩码（`0xC1826718`）为 0 时不加：原厂掩码不会是 0，只有竖拍显示（portrait-ui）竖拿时清零掩码、自己画竖版直方图，这样第 5 页不会同时出现原厂横版直方图（2026-11-17）。其他控件使用原信息页掩码；九个原厂输出掩码的非 DISP 字段保留。
- 原厂水平仪初始化、刷新和组合控件入口在新页使用水平仪页语义。顶部信息相关控件继续走原厂信息页控制器。
- 每个替换窗口校验原字节；重放被覆盖的条件分支、PC 相对常量读取和原厂调用。需要消费前序比较结果的入口保留条件标志、临时寄存器和栈。入口附近的原厂分支落点必须保留在被覆盖区域之外。

## 验证

2026-10-17 本地候选 SHA-256：`57e833a1d64d295b11364645123239496f672918491abf44db16bd2cc16500a4`。35 项已纳入版本库的单元测试、1,024 次门控执行、38,912 次适配器执行、1,755 组原厂描述项比较、全信息页属性重载、动态直方图/水准仪检查以及 QEMU 六页循环通过。`full`、`modular`、`liveview-dual+m900`、`liveview-dual+aspect-profiles` 四种组合的构建及 CRC 检查通过。报告和 QEMU guest 截图保存在 `build/liveview-full-info/`；完整实机显示排布未验证。

```powershell
$env:PYTHONPATH = 'src'
python tools/verify_liveview_gates.py --source Z_30_0120.bin
python tools/verify_liveview_full_info.py --source Z_30_0120.bin --output build/liveview-full-info/adapters.json
```

门控检查覆盖原厂与候选的两个入口、全部 256 个 DISP 值。呈现适配器差分检查覆盖 38 个入口、256 个值、两组条件标志，比较续接地址、寄存器和栈；新页分别对照原信息页/水平仪页及直方图描述项条件。这是函数执行证据，不能证明最终屏幕排布。

本地既有研究夹具可用于原厂服务回归（需显式提供研究工具目录）：

```powershell
python tools/verify_liveview_full_info_native.py --source Z_30_0120.bin --research-tools ../tools --output build/liveview-full-info/native.json
```

其锁、通知完成、姿态、直方图采样输入和快照 RAM 是明确模型；原厂属性读写、切换、直方图缩放、控件描述项过滤与水准仪变化判断执行原指令。它不验证 SD 持久化或传感器。

WSL 下的既有辅助 QEMU 可复现原厂取景界面和六页切换：

```sh
python3 tools/verify_liveview_full_info_qemu.py --qemu-root ../z30-qemu \
  --image build/liveview-full-info/EG209020.BIN \
  --report build/liveview-full-info/EG209020.json \
  --output-dir build/liveview-full-info/qemu
```

截图来自 guest 图形缓冲区，预览是诊断色条。该夹具缺少完整机身/传感器状态，部分原厂文字、图标、直方图内容并不完整；六页状态回读和 guest 截图不等于“全部信息最终像素已验证”。实机仍须核对顶部信息完整性、重叠、亮度和姿态变化、照片/视频取景、各 DISP 页返回、录像开始/结束、休眠/唤醒及 HDMI 输出。

## 发布范围

对应 issue #8；发布内容为 DISP 页实现、验证工具与说明。只发布源码和去标识化的验证结论，不发布固件、guest 截图、原始执行报告、个人路径、设备身份或样片。画幅菜单同步去掉简繁中文“实验”和英文“trial”后缀；画幅的实机验收边界仍见[多画幅说明](aspect-profiles.md)。
