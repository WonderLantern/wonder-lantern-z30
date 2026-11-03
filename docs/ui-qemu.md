# UI 绘制修复与 QEMU 验证

## 菜单注册与原厂菜单直达行（2026-11-03）

**新增页面**：在 [shutter_sound.py](../src/z30_extensions/features/shutter_sound.py) 调用一次 `register()`，并在 [menu_slots.py](../src/z30_extensions/features/menu_slots.py) 的 `BLOCKS` 里预留文字槽。状态块、控件 ID、首页行和原生路由都由注册表生成。超过 8 行的页面会让构建失败，因为第 9 行已超出 480 像素高的屏幕，且自有页面的绘制循环不支持滚动。要出现在原厂菜单里，把页面加入 `NATIVE_MENUS`。

**文字槽**：原厂表共 300 个槽，每个 51 字节（QEMU 读取确认）。旧编号有 29 个与原厂按立即数写入的槽重合，现已全部移开，`STOCK_SLOTS` 在导入时检查。另有约 288 处原厂调用的槽号是算出来的，静态上无法排除，只在一次 QEMU 会话访问到的页面里确认过没有写入新范围，所以这不是完整证明。旧重合是否真的造成过可见错误也没有证实。

日期水印页加入“光晕”行后为 8 行，需要 19 个文字槽，所以 `stamp` 块扩为 126–144，声音块整体后移到 145–172（173–176 仍空闲）。这会改变所有含声音页的构建。挪动后在 `full` 上重跑了分组菜单（三种语言）、声音设置、高光显示和码率（简体）的 QEMU 检查，全部通过；`full date-stamp` 上的日期水印菜单和直达行三种语言也通过，直达行检查还覆盖了改色、切换光晕后再选格式。8 行的水印页构建较慢，`verify_date_stamp_menu_qemu.py` 聚焦后先等目标行的控件出现在页面控件表（`+36`）里再触摸；固定等待时偶尔会在页面建完前点错行。日期水印纳入 `full`、去掉模块宿主后，新 `full` 上的水印菜单（简体三遍、英文一遍）、直达行、分组菜单、声音设置、高光显示、码率和返回/侧栏检查全部通过。

**光标复位**：用户反馈进入自定义菜单后高亮在第一行，按下键却跳到上次所选行的下一行。首页和“快门声音”分组页的页面对象常驻内存，`+22`（光标行）和 `+24`（首个可见行）保留上次的值。QEMU 里重新进入时高亮跟随 `+22`，所以没有复现画面不一致；机身上的现象说明列表从顶部画起而光标仍是旧值。现在每次从入口打开这两页前都把两个字段清零，QEMU 确认返回键和切换标签页后重新进入时光标都在第一行，按一次下键到第二行，画面一致。从子页面返回首页仍走原厂历史栈，保留原来的行，未改动。

**直达行**：照片菜单（页面 `0x27`，34 行）末尾追加照片画幅、照片高光显示、日期水印，视频菜单（`0xCA`，26 行）末尾追加视频码率。行描述沿用设置入口行的格式，所以右列固定显示 `--`；不显示当前值，因为开机后状态块建立前读不到已保存的选择。

从直达行打开时，页面的父级是标签容器；从自定义菜单打开时，父级是自定义首页。确认选择后的返回走 `page_return`：标签容器不能作为目标（焦点会落到左侧标签栏），它的 `+8` 也不能用（进入叶子页后已指向叶子页本身），所以和原厂返回键一样，用 `0x1045f74c(0x3BC)` 取历史栈里的上一页。

**研究构建**（Wi-Fi、LUT、预拍探针）不加直达行，仍与码率共用页面 960，不能同时启用。

**验证**：`tools/verify_native_rows_qemu.py` 从原厂标签栏进入菜单，用原厂上键事件滚动列表、触摸打开直达行，再检查返回键、确认选择后焦点回到列表、从自定义菜单进入时的父级。下列结果针对 `full` 和 `full date-stamp`：

- 直达行：简体、繁体、英文，以及不含水印的 `full`（简体）全部通过。
- 直达行与水印颜色行的组合：颜色行会重开自身页面，所以另外检查了改色后按返回键（一次回到照片菜单）和连改两次颜色后再选格式（焦点回到照片菜单列表）。`full date-stamp` 三种语言和单独 `date-stamp`（简体）通过。
- 原有 QEMU：日期水印菜单三种语言、高光显示和码率（简体）、分组菜单三种语言通过。
- Unicorn：声音设置、码率、高光开关、分组菜单动作、日期水印、模块宿主通过；单元测试全部通过。
- 重构第一步（只改注册方式、不改槽号）与旧菜单代码逐字节一致，覆盖 12 种预设组合中的 9 种；其余 3 种含水印，当时另一会话正在改译文，改用相同文件搭配旧菜单代码重建后比对 `date-stamp` 一致。

`verify_ui_qemu.py` 的 `--verify-fixed`、`--verify-entry-state`、`--verify-tab-switch` 在重构前的代码上同样失败，它们还停留在分组菜单之前的假设，不能作为这次的证据。SD 服务是诊断模型，触摸和按键是合成输入。**以上都不是实机证据，直达行尚未上机确认**；“我的菜单”的添加列表也没有测试。

```sh
python3 tools/verify_native_rows_qemu.py --qemu-root ../z30-qemu   --image <EG209020.BIN> --report <EG209020.json> --fixture <fixture.json>   --output-dir build/ui-qemu/native-rows --language zh-Hans
```

2026-10-18 v36：新增按用途分类的首页及“快门声音”子菜单，简体、繁体、英文均以原生 GPU 会话检查实际页面及返回；8 种功能组合以原生 ARM 动作验证。取景遮幅使用独立 LCD 会话验证，避免辅助取景扫描覆盖菜单截图。详见 [v36 说明](aspect-mask-menu-v36.md)。本次新层级尚待机身确认；下方实机反馈保留对应旧版本的范围。

2026-10-17 状态更新：用户确认当前全部八项功能均已在 Nikon Z30 C 1.20 实机检验通过，包括多画幅、全信息页、声音配置记忆和机内码率档位。下文旧版本的待实机表述保留当时的测试范围，以本条为当前功能状态；离线工具的验证字段仍仅描述工具自身证据，不表示穷举全部模式、组合或故障条件。

2026-10-13，最终冷入口修复后的机身复测正常。这确认了用户此次菜单使用的结果；下文 QEMU、Unicorn 和构建检查仍各自保留验证范围，不扩展为其他固件功能或完整测试矩阵的实机证明。

## 独立错误提示与声音记忆（2026-10-13）

此后用户反馈，长错误提示写入标题会影响返回按钮。当前版本不再用 `Invalid WAV`、读取失败等信息替换声音页面的三个标题。错误使用独立提示页的正文控件 `0xA19` 和“确定”行，保留正常标题、返回按钮与左栏。提示页使用经原厂扫描核对为空闲的控件 ID `0x23F8`。确认或原生返回退出时，独立清理回调清除错误状态，避免下次操作再次显示旧提示。进入提示后显式请求整体刷新，避免只出现正文却缺少标题与确定行。

声音开关与选中编号写入 SD 根目录 `WL-SOUND.CFG`。无有效配置时默认关闭；首次进入菜单或首次照片触发时建立私有状态并恢复配置，启用的选项会重新读取 WAV。初始化使用原子锁，配置检查版本、编号、开关、校验字段、读取长度；保存检查写入长度与关闭结果，失败提示“设置保存失败”。文件服务在任务上下文调用，不增加线程。这里的恢复时机为首次使用，并未新增相机启动任务。文件被删除、更换卡、记录损坏或恢复加载失败时不能保证此前的声音可用，默认保持关闭。

最终本地 `full` 研究候选与报告：`output/sound-memory/final/EG209020.BIN`、`EG209020.json`，SHA-256 `7c55da27111ab5605b6e3d6c13b3ab0b9074c8c8525ec975eaf7d17954ec5384`。本次不提交或推送这些产物。

验证分为以下范围：

- 34 项单元测试通过；三种声音相关预设的 CRC 和逐字节重复构建通过。原 SD 模块宿主的指令级加载/运行/失败回归通过。
- `tools/verify_sound_settings.py` 的 15 个指令级案例包含五个文件编号 × 两种开关的新 RAM 实例恢复、五类无效配置、短写、关闭失败、声音缺失和无效 WAV。SD、分配服务使用明确的模型，WAV 校验、转换及配置 ARM 代码实际执行。
- QEMU 配置诊断 `build/ui-qemu/sound-restore-delivery/session.json` 的 12 个检查包含未先进入菜单的首次照片触发恢复、十种配置保存与重载、无效记录拒绝。为了明确隔离硬件缺口，诊断临时替换命名的 SD 服务入口；这些模型修改只存在于运行中的模拟器，未进入候选固件。这里没有实际 SD 文件系统、扬声器播放或自然冷启动验证。
- 简体提示与返回诊断 `build/ui-qemu/sound-dialog-cleanup-hans/session.json` 使用原生触摸输入关闭提示，包含“确定”和提示页右上返回；两轮回到原厂菜单均检查左栏像素及按钮区域完全为黑色。正文、确定行与标题来自客体原厂控件和 GPU。繁体与英文对照分别位于 `build/ui-qemu/sound-dialog-delivery-hant/` 和 `sound-dialog-delivery-en/`，同样通过触摸确认、正文控件、确认行像素、侧栏和原厂菜单按钮区域检查。
- 五个页面的原生构造、侧栏和固定标题回归位于 `build/ui-qemu/sound-pages-delivery/`；注入错误并重入后，菜单标题仍保持正确槽位和 UTF-16 控件类型。

示例命令（先在 Windows 生成模型代码，再在 WSL 运行 QEMU）：

```powershell
python tools/verify_sound_settings_qemu.py --prepare-fixture build/sound-memory/qemu-sd-fixture.json
python tools/verify_sound_settings.py --source ../Z_30_0120.bin --image output/sound-memory/final/EG209020.BIN --report output/sound-memory/final/EG209020.json --output build/sound-memory/persistence-delivery.json
```

```sh
python3 tools/verify_sound_settings_qemu.py \
  --fixture build/sound-memory/qemu-sd-fixture.json --qemu-root ../z30-qemu \
  --image output/sound-memory/final/EG209020.BIN \
  --report output/sound-memory/final/EG209020.json \
  --output-dir build/ui-qemu/sound-restore-delivery

python3 tools/verify_ui_qemu.py --qemu-root ../z30-qemu \
  --image output/sound-memory/final/EG209020.BIN \
  --report output/sound-memory/final/EG209020.json \
  --output-dir build/ui-qemu/sound-dialog-cleanup-hans \
  --back-cycles 2 --touch-back --verify-return --verify-sidebar \
  --verify-error-dialog --language zh-Hans
```

新增提示页和记忆功能尚无实机确认，不能沿用本文开头上一版的机身测试结论。下文错误标题截图和更早测试数量是历史修复阶段的记录，当前代码已改用独立提示正文。

## 弹窗复查与截图采样修正

进一步复查发现，旧截图采样可能在关闭提示后拍到“SD 卡功能”一行尚未画出文字的中间帧。灰色行背景已经占满该区域，补画白字不会增加非黑像素数量，因此仅按非黑像素选择样本可能把早期帧与完整帧视为相同，并选中早期帧。此次记录中，关闭后的白色像素从 2153、5729 逐步增加到 8318；另一次为 2153、3346、7175、8318，而非黑像素数量无法反映这些文字完成阶段。

采样现在依次比较非黑像素、白色文字像素，并在相同分数时选择更晚的样本；不补画或修改任何像素。增加提示关闭后模块入口文字的像素检查，以及文字槽/控件状态诊断。试验性的固件刷新和矩形调整没有稳定解决旧采样问题，已撤回；当前固件仍与上述 SHA-256 的候选逐字节一致。

`build/ui-qemu/popup-complete-frame-check/session.json` 记录三轮、九次提示操作，包含缺文件、无效 WAV 和保存失败提示，使用原生触摸确认或右上返回。九次关闭后的模块入口均有 1143 个白色文字像素；三次返回原厂菜单的按钮区域非黑像素均为零，左栏均为 16344。`error-dialog-0-3.png` 与 `error-dialog-0-3-closed.png` 分别显示独立错误正文及完整恢复的菜单。提示显示、关闭清理和返回检查通过；范围仍为辅助 QEMU 与明确错误状态输入。

## 重启后首次入口乱码

机身测试发现：重启后第一次进入菜单，入口乱码，操作刷新后恢复。上一版在设置初始化回调里切换行表，却未处理原厂调用方提前保存的旧入口行指针。首次构造可能仍按旧行标志创建控件，而槽位 39 已采用新语言编码；后续刷新重新读取元数据，因而恢复正常。

本次在行构造钩子识别入口 ID 后重新选择语言行表、填充文字，并把这次调用保存的 `r1` 替换成匹配的入口描述，之后才继续原生构造器。原厂其他行、自定义页的行参数保持原样；两套 ROM 行表均只读。

QEMU 对照向原厂行构造器显式传入预取的旧描述：上一版简体入口输出 `(slot=39, kind=2)`，与 UTF-16 缓冲不匹配，诊断失败；修复后为 `(39,3)`，英文为 `(39,2)`，均通过。报告分别为 `build/ui-qemu/entry-cold-before/`、`entry-cold-after-hans/`、`entry-cold-after-en/`。这是针对首次构造条件的原生执行诊断，不代表自动复现了相机完整冷启动时序，实机反馈单独记录在本文开头。

34 项测试通过，Unicorn 覆盖 36 个语言编号的旧描述替换，并将代码及行表设为只读，检查原厂其他行与自定义行参数不被替换。英文和简体四页回归位于 `build/ui-qemu/entry-cold-fix-en/`、`entry-cold-fix-hans/`。新构建及报告在 `output/ui-cold-entry-fixed/`，三种预设 CRC 为零、重复构建逐字节一致。

最终 full 繁体构建在 `build/ui-qemu/entry-cold-full-hant/` 通过旧描述构造检查、两轮返回、按钮残留检查和侧栏切换。

## 非中文入口乱码修复（实机反馈后）

用户实测此前版本其他功能正常，仅非简体/繁体语言的自定义菜单入口乱码。原因是入口固定 Unicode 控件和字体 15，而菜单内部的非中文回退已经使用 ASCII。现在入口与内部共用文字分支，并在原厂设置页初始化、创建行控件之前选择对应的只读行表；仅更换 RAM 元数据的控件表指针，不写入 ROM 行记录。中文保留 `0x3001` 行标志、UTF-16 和原厂中文字体；其他语言使用 `0x1201`、ASCII 和原厂样式 0。

更新构建在 `output/ui-entry-language-fixed/`，保留上一版 `output/ui-chinese/` 供对照。34 项测试通过；Unicorn 覆盖 0 到 35 的全部语言编号，核对实际执行的 setter、入口行表选择与编码配对。三个预设 CRC 为零，重复构建一致。

QEMU 报告 `build/ui-qemu/entry-language-fix-en-verified/` 和 `entry-language-fix-hans-verified/` 核对原厂设置页真实 RAM 行表、槽位 39 的缓冲内容和样式。英文为 `0x1201` / `Extensions` / 样式 0；简体为 `0x3001` / `自定义菜单` / 样式 120。同时回归四个页面和错误标题重绘。入口验证是客体状态检查，仍未通过稳定滚动拍到入口行；该阶段之后用户继续报告冷启动首次乱码，最终修复及实机反馈见本文开头。

`build/ui-qemu/entry-language-fix-full-hant-verified/` 核对最终 full 预设繁体入口为 `0x3001` / `自訂選單` / 样式 120，并通过两轮触摸返回、按钮残留检查、原厂菜单移动和侧栏切换。

## 中文界面

自定义菜单读取原厂语言配置 `0x1400`：`26` 使用简体中文，`27` 使用繁体中文，其余保持英文。覆盖设置菜单入口、自定义菜单、声音选择、声音开关、SD 功能页和固定状态/错误提示。文件路径、WAV 文件名和 ZMOD ABI 保持 ASCII。

中文字符串使用 UTF-16 LE，每个标签最多 24 个码元。原生 setter `0x10454f5a` 只复制文字；控件也必须使用 Unicode 类型 `3`，ASCII 类型 `2` 会把 UTF-16 当成单字节文字。绑定时通过空类型 `0` 切换。设置菜单入口在原厂创建控件前选择匹配的行表：简繁中文使用 Unicode 类型 `3` 和 UTF-16；其他语言使用 ASCII 类型 `2` 和英文文字。

原厂动态 Unicode 样式 `120` 默认字体 `15` 不包含所需汉字。代码保留样式的大小、颜色和布局，只在 setter 写出的运行时样式副本中选择原厂简体字体 `2` 或繁体字体 `0`；原始样式表不修改，原厂再次调用 setter 会重新复制它的样式。原厂字库缺少“扩、展、听、块”等字，因此可见菜单使用“自定义菜单”“播放声音”“SD 卡功能”等用词。当前视觉验证覆盖四页和 WAV 错误提示；入口的快速按键滚动与 InitTask 直接重绘测试未能可靠显示新增行，尚未完成入口视觉验证，不保证原厂字库包含任意外部中文文本。

模块输出仍为 ASCII；中文模式转换为有界 UTF-16，最多显示 24 个字符。英文模式保留原有 50 字节上限。

### 字库覆盖表

原厂字体是 `hif` 数据块，指针数组位于 `0x109F62E4`（字面量 `0x104530F4` 引用），下标即样式中的字体号。字体 `0`（繁体，`0x107646D1`）和 `2`（简体，`0x107D06B3`）的头部 `+0x40` 依次是 u16 段数 n、u32 描述长度 `5(n-1)+6`、n-1 个 5 字节段描述、u16 条目数，之后是按码点排序的 `(首码点, 字形, 连续个数)` 条目，各约 750 条。不在任何区间内的码点会画成方框占位。

`src/z30_extensions/platform/z30_120_fonts.json` 只保存这两种字体的码点区间，不含字形位图。`TextResources.alloc` 在构建时检查每个标签：简繁译文以及无译文时回退使用的英文都必须在对应字体内，否则构建报错并列出缺字。`tests/test_fonts.py` 检查全部译文，并在仓库根目录有 `Z_30_0120.bin` 时确认 JSON 与固件一致。

```
python tools/font_coverage.py 橙色 光暈                                # 查哪些字缺失，用于挑选用词
python tools/font_coverage.py --firmware Z_30_0120.bin --check          # 核对 JSON
```

提取结果与此前截图发现的缺字一致（简体缺“橙、橘、晕、厂、扩、展、听、块”，繁体缺“橙、橘、找”）；`sound-dialog-delivery-hant/error-dialog-0-1.png` 中“找不到檔案”的“找”即为方框。检查还发现已有 11 处标签含缺字，曾改为可显示的用词（补字接入构建后已恢复原用词，见下文）：简体“SD LUT 可用”“等待释放内存”“已加载，等待释放内存”“加载失败”“暂存不可用”“无处理程序”，繁体“SD LUT 可用”“檔案不存在”“載入失敗”“無處理程式”“記憶體仍在使用”。两种字体的 ASCII 也不完整，均缺 ``$ & ' , < = @ Q Y \ ^ _ ` j q w { | } ~``（繁体另缺 `?`），因此中文模式下的英文回退文字和模块输出同样受影响；预捕获诊断状态 “Capture rejected” 改为 “Capture refused”。

### 字形格式（为补字做准备）

`tools/probe_font_glyph_qemu.py` 在原厂固件、简体中文下打开设定菜单，打开调用期间监视字体读取。原厂有完整的字体引擎：经字节流读取器（`0x10507E52`，虚表 `+0x18` u32、`+0x1C` u16、`+0x20` u8、`+0x30` 定位、`+0x38` 跳过）解析 `hif`，字体对象经 getter `0x104ED66E` 取得，轮廓在机内栅格化后由 `0x104785EC` 经 `vgDrawImage`/`vgDrawPath` 绘制。证据在 `build/ui-qemu/font-glyph-probe*/session.json`。

- **字符表**（`0x105071CA` 二分查找）：字形号 = 字形字段 + (码点 − 首码点)。未命中且对象 `+0x58` bit 2 置位时，以 `码点 | 0x800000` 查第二张表；“橙、展”等缺字在第二张表中，但仍显示方框。
- **字形索引**（`0x10506D6E`）：位于字体头 `+0x2C` 偏移处。u16 区段数，每段 u16 首字形、u32 基址、u16 个数、个数 × u16 偏移；记录地址 = 字体 + 头 `+0x30` 数据偏移 + 基址 + 偏移，偏移为 0 表示空（区段首个除外）。
- **字形记录**（`0x10507288`）：u16 标志；bit 1 时 u16 轮廓数；bits 7–8 为 0–2 时取头 `+0x18` 的默认步进（2048/1024，简体另有 1170），为 3 时读 int16；bits 5–6 为 3 时多一个 int16；随后总有包围盒 u16 宽、u16 高、int16 x_min、int16 y_max，bit 10 未置位时再有竖排方位 `(x_min − 步进/2, 1760 − y_max)`；bit 9 时多一个 int16。轮廓：u16 点数、除最后一个外的轮廓终点（点数 > 255 用 u16，否则 u8）、按 LSB 优先打包的 on-curve 位、u16 描述符数与描述符。每个描述符覆盖 `(d & 0x3F) + 1` 个点，bit `0x80`/`0x40` 分别表示 x/y 增量为 int8，否则为 int16；首点相对 (0, 0)。坐标为二次曲线（TrueType 规则），每 em 2048 单位，y 向上。

`platform/fonts.py` 的 `decode_glyph`/`encode_glyph` 实现上述格式。字体 0、2 共 1663 条记录全部恰好首尾相接，并逐字节重新编码一致（`tests/test_fonts.py`，需仓库根目录的固件）。`tools/render_stock_glyphs.py` 用解码出的轮廓离线渲染文字，结果与 QEMU 中的设定菜单和自订选单一致。

字体数据块按顺序连续存放：头部、主字符表、带标签的字符表（`0x80`，简体另有 `0x82`）、字形索引、特性表、字形数据；段描述符偏移相对 `+0x40`，索引、特性、数据偏移相对字体起点，块内没有其他指针。`extend_font` 据此重建字体：新字形取库存最大编号之后的编号，新增一个索引区段，记录追加在字形数据末尾，主字符表按 `(码点, 字形)` 连续性重新合并游程，然后平移头部偏移与段描述符。不加字时重建结果与原厂逐字节相同。

`tools/probe_font_extend_qemu.py` 在 QEMU 开机前把重建的字体 0、2 和复制的指针数组写入镜像尾部，并把字面量 `0x104530F4` 改为指向新数组；设、設改映射到新追加的镜像“定”，另插入“橙”。`build/ui-qemu/font-extend-zh-Hans/`、`font-extend-zh-Hant/` 的设定菜单截图中，所有设、設都显示新字形，其余文字不变；运行时字体全局变量指向复制的数组 `0x1207E038`。原厂引擎接受插入的字符表条目、新索引区段、追加的记录和平移后的偏移。

补字来源为 Noto Sans CJK（SIL OFL 1.1，下载到被忽略的 `build/fonts/noto-cjk/`，含许可证）。在原厂与 Noto 共有的 300 个常用汉字上：Regular 字重的笔画面积为原厂的 1.02–1.05 倍，包围盒尺寸比约 1.00，x 偏移约 −3 单位，整体比原厂高 42 单位（Noto 上升高度 880 × 2.048 ≈ 1802，原厂为 1760）；Medium 字重的面积为原厂的 1.26–1.30 倍，偏粗。按像素计算的重合度中位数为 0.67，说明 Noto 与原厂字形相近，但不是同一套设计。`platform/font_source.py` 把轮廓乘以 2.048、下移 42 单位，用 fontTools `cu2qu` 转成二次曲线（容差 1 单位，整体顺时针，与原厂一致），并设为原厂全角度量：标志 `0x12`，竖排方位 `(x_min − 1024, 1760 − y_max)`；原厂 764 个全角字全部符合这一公式。`tools/render_stock_glyphs.py --fill-from` 可把原厂字与补字混排预览。

`probe_font_extend_qemu.py --glyphs` 读取预先编码的字形记录（WSL 中无需 fontTools）。`build/ui-qemu/font-noto-zh-Hans/` 中，设定菜单的标题和行文字改映射到 Noto 的“橙、晕、厂、扩”；`font-noto-zh-Hant/` 中改为“橙、橘、找、厂”。原厂引擎在标题和行两种字号下都能正确绘制，字重、大小、基线与相邻原厂字一致。

**有没有更完整的原厂字库？** 没有。在 0–23 号原厂字体里（第 24 号见下文），中文和日文菜单字体（0、1、2、3、13、14、15）都带一张很大的标签 `0x80` 字符表：字体 0 有 12744 个码点，字体 2 有 27774 个，字体 3 有 28610 个。但这些表指向的字形编号在字形索引中一个都不存在，可见原始字库被裁剪过，字符表原样保留了，字形数据却只留下菜单用字。所有字体合计约 2.5 MB。英文键盘、SSID 等用的是西文字体（4–12、16–23），它们 ASCII 齐全，但没有找到汉字。英文菜单的行用字体 16（`--language en` 时 getter 收到的 40 次请求中 39 次是 16），它与中文字体同为 `hif` 字形格式（1178 条记录全部逐字节往返一致），但设计是等比宽度、字形偏大；中文字体自带的英文字母则是半角等宽设计（步进 1024）。

**按标签改用西文字体不可行。** 原生文字对象在绘制时读取样式 120 的共享运行时副本（`0x104B4862` 读取 `0xC20148F8`），因此同一页最后一次写入的字体号对所有标签生效。`verify_aspect_menu_qemu.py --observe-fonts` 的记录显示，照片画幅页的 48 次读取全部为字体 2，截图与改动前逐像素相同。样式表只有 0–120 项，没有空闲样式号；借用原厂样式会影响同屏的原厂控件。中文模式下缺的 ASCII 字母因此也要用补字方式解决，并且应补半角等宽字形，才能与中文字体自带的英文字母一致。

### 补字接入构建

只要构建用到中文标签（`ui_text.prepare`），`features/cjk_fonts.py` 就用 `extend_font` 重建字体 0、2，与复制的字体指针表和大小表一起放在镜像尾部，并把字面量 `0x104530F4`、`0x104530F8` 改为指向新表（报告中 `features.cjk_fonts`）。原厂字体数据不变。

启动代码 `0x10452F34` 交给字体引擎两张表：`0x109F62E4` 处是 25 个字体指针（0–24），紧跟其后的 `0x109F6348` 处是这 25 个字体的大小，二者分别由字面量 `0x104530F4`、`0x104530F8` 引用。第一版补字固件（`output/cjk-glyphs-v1`）只复制了 24 个指针、没有复制大小表，用户实机打开“语言”界面时卡死。在 QEMU 中复现：原厂固件正常，v1 固件打开语言页后另一个核跳进 RAM 执行（PC `0xC2B1065C`）。`probe_language_page_qemu.py --observe-fonts` 显示语言页会请求字体 24，而 v1 副本的第 24 项是其他数据。v2 复制全部 25 个指针和 25 个大小，并更新字体 0、2 的大小。补上的字形来自 `platform/z30_120_extra_glyphs.json`，由 `tools/build_cjk_glyphs.py` 从 Noto 字体生成，构建本身不需要 fontTools 或 Noto 文件。

生成工具为每种字体补上全部可打印 ASCII，以及 `TRANSLATIONS` 中该字体缺少的字：汉字等全角字取自 Noto Sans CJK SC/TC Regular，标志 `0x12`；ASCII 取自半角的 Noto Sans Mono CJK SC Regular，标志 `0x92`，步进 1024，与中文字体自带的英文字母一致。每种字体的汉字和字母各自用最小二乘拟合缩放与平移，拟合数据是原厂与 Noto 共有字的包围盒。拟合结果：汉字缩放约 2.04–2.06、平移在 −36 至 −4 单位之间；字母缩放约 1.93–1.97，繁体字体的字母比简体整体低约 36 单位。当前两种字体各补 30 个字：简体补 ASCII 符号与字母 ``$ & ' , < = @ Q Y \ ^ _ ` j q w { | } ~`` 及“事函厂晕橙绪缓缺读资”，繁体另补 `?` 及“事函占廠找橙緒缺讀”。

`check_glyphs` 和 `missing_glyphs` 默认以补字后的字体为准，`font_coverage.py` 查询仍显示原厂字体缺哪些字。新增标签如用到未补的字，构建会报错；有 Noto 字体时重新运行 `tools/build_cjk_glyphs.py --write` 即可补上。被迫改掉的用词已恢复：“橙色”“光晕/光暈”“原厂/原廠”“SD LUT 已就绪”“等待释放资源”“读取失败”“缓存不可用”“缺少事件处理函数”“找不到檔案”“記憶體仍被占用”等。补字为 Noto 字体的修改版本，按 SIL OFL 1.1 授权，见 `THIRD_PARTY_NOTICES.md` 与 `licenses/OFL-Noto-CJK.txt`。

QEMU：用 `full` 构建（报告 `features.cjk_fonts`），运行 `verify_date_stamp_menu_qemu.py` 和 `verify_bitrate_profiles_qemu.py`，简体、繁体各一遍，全部通过。截图在 `build/ui-qemu/glyphs-*`：日期水印页显示“颜色 橙色”“光晕/光暈”，码率页显示“原厂/原廠”，与相邻原厂字协调。当前菜单里没有用到补上的 ASCII 字母，它们只做了离线渲染对比：字宽、x 高度和基线与原厂字体自带的字母一致；尚未在 QEMU 中显示。

v2（`output/cjk-glyphs-v2`）回归：`probe_language_page_qemu.py` 在简、繁、英三种语言下都能打开语言页，界面持续响应，截图与原厂逐像素相同；日期水印、码率菜单的简繁验证全部通过。`verify_firmware_update_qemu.py --expect-page 0xc1839680` 在三种语言下都打开了与原厂相同的“固件版本”页（`C 1.20 / 完成`）。原厂只在已挂载的存储卡上有升级包时显示更新项，而这台 QEMU 模拟机不挂载虚拟卡：会收到插卡事件，但不读取任何扇区。因此实际升级必须在相机上确认。

QEMU 原本只显示 English 一种语言，因为语言列表按地区字节决定（`0x101F4FBC` 读取，地址由 `0x101F51E4` 处的字面量给出），而这台模拟机里该字节是无效值 `0xFF`。`probe_language_page_qemu.py --region N` 可设置地区：0–12 中，地区 9 只有中文简繁，地区 4 是欧洲语言，地区 5、6、7、12 包含中文、阿拉伯文、印地文和泰文。v2 在地区 0–12 下打开语言页全部正常，在地区 3、4、6、7、12 下用 `--scroll 35` 滚动整个列表也全部正常，各语言名称都正确显示。

实机（2026-11-03，v2）：用户此前报告的语言页卡死，是误装了 v1；改装 v2 后语言页不再卡死。用户确认固件升级正常，日期水印、码率页的新字显示正常，原厂中文界面没有方框或缺字。

尚未完成：在 QEMU 中显示补上的 ASCII（当前菜单里没有用到这些字母）。

构建与报告：`output/ui-chinese/`，包括 `shutter-sound`、`module-host` 和 `full`。三种预设 CRC 残差为零、重复构建逐字节一致；34 项单元测试通过，包含 Unicorn 执行语言分支、控件类型切换和模块文字边界测试。

最终页面证据：`build/ui-qemu/chinese-entry2-hans/`（简体），`build/ui-qemu/chinese-full-pages-final/`（繁体 full），`build/ui-qemu/chinese-en-final2/`（英文）。QEMU 使用 `--language` 设置原厂配置和字体，真实相机只需在原厂“语言 (Language)”中选择中文；用户后续实机反馈见本文开头。

```sh
python3 tools/verify_ui_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/ui-chinese/full.eg.bin \
  --report output/ui-chinese/full.json \
  --output-dir build/ui-qemu/chinese-full-pages-final \
  --language zh-Hant --verify-fixed --verify-sidebar
```

侧栏触摸验证在调用自定义菜单入口后先等待原生绘制并确认当前页面，再发触摸包，避免在异步切页尚未完成时触摸原厂页。

最终 `full` 繁体版本在 `--direct-setup` 诊断路径下连续两轮触摸返回通过：自定义页面左栏可见、返回后按钮区域无残留、原厂菜单可以上下移动，并能从自定义页面触摸切换到原厂拍摄菜单。报告和截图位于 `build/ui-qemu/chinese-full-return-final/`。

2026-10-13 使用已有 `z30-qemu` 的 `nikon-z30-display` 机器复现并修复：

- 扩展、声音选择、开关与模块页面缺少标题。布局记录的类型 `2` 不是动态文本，而是位图；保留原厂文本布局类型 `1`，再通过原生 setter 将标题控件从静态资源类型 `1` 经空类型 `0` 转为动态文本类型 `2`。原生 setter 不接受直接 `1 -> 2` 转换。后续更新保留动态类型。
- 模块页面的 `No module output` 被空值栏截断。原生行组合会按值栏边界裁剪标签，即使值为空。没有值的行将值栏宽度设为零，置于标签右边界；状态与声音选项继续使用正常值栏。

四个页面执行原厂控件创建、布局与 GPU 绘制，检查标题控件存在、动态类型与文字槽位；重复进入根页面并注入错误状态，检查错误提示和清除后的重绘。截图来自客体 GPU，未在主机上绘制文字或替换控件。`gui-latest.ppm` 可能在 GPU 工作过程中导出，因此截图取八次原生缓冲采样中非黑像素最多的一帧，采样记录保存在报告内。

WSL 在项目根目录运行（不需要 WSL Keystone；先用项目构建命令生成 EG 和 JSON）：

```sh
python3 tools/verify_ui_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/ui-fixed/module-host.eg.bin \
  --report output/ui-fixed/module-host.json \
  --output-dir build/ui-qemu/final-lifecycle --verify-fixed
```

本次本地截图与调用报告：`build/ui-qemu/final-lifecycle/`；修复前对照：`build/ui-qemu/parent-before/`。`error-title.png` 显示 `Invalid WAV`，清除错误后的 `restored-title.png` 恢复 `Extensions`。修复后的 `shutter-sound`、`module-host`、`full` 构建及 JSON 位于 `output/ui-fixed/`，CRC 残差均为零，重复构建逐字节一致；32 项单元测试通过。

最初从 My Menu 直接调用扩展页面出现黑屏和等待，而同一候选沿原生设置标签路径能够返回；该现象不能当作实机黑屏证据。

## 返回原厂菜单：栏目消失与按钮边框残留

自定义根页面原先把设置页 `0xC1837160` 直接作为父页面。原厂设置子菜单使用外层标签容器 `0xC1832D50`。直接返回设置页时，只重建设置页内容，可能跳过外层栏目和背景重建；控件释放成功仍会留下右上角按钮边框，左侧栏目也可能消失。

修复从输入固件读取设置页的原厂父容器，检查它与原厂子菜单的父容器相同，再用于自定义根页面。声音选择、开关和模块页面仍返回自定义根页面。未扩大原厂标题的绘制区域；上一轮局部擦除尝试已经撤掉。

同一路径修复前后截图位于 `build/ui-qemu/container-before/back-0-stock.png` 和 `build/ui-qemu/container-fixed/back-0-stock.png`。该路径使用 `--direct-setup`，直接进入设置页而不先选中设置标签，作为没有预先绘制父栏目的诊断条件；不是物理按键/相机启动证据。旧版复现左栏消失和按钮部分残留；修复版恢复栏目并清除按钮。

修复构建与报告位于 `output/ui-parent-fixed/`。`full` 在同一诊断条件下连续两轮触摸返回、原厂菜单上下移动与重复进入通过；报告为 `build/ui-qemu/full-container-fixed/session.json`。先选中原生设置标签的两轮对照也通过，报告为 `build/ui-qemu/full-natural-parent-fixed/session.json`。`--verify-return` 要求客体截图中左侧图标区域有像素，且右上角原按钮矩形完全为黑色；同时检查当前设置页面与自定义页面注册表释放。原生触摸入口接收明确的按下/松开合成包。

```sh
python3 tools/verify_ui_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/ui-parent-fixed/full.eg.bin \
  --report output/ui-parent-fixed/full.json \
  --output-dir build/ui-qemu/full-container-fixed \
  --back-cycles 2 --touch-back --direct-setup --verify-return
```

省略 `--direct-setup` 则先用原生触摸选择设置标签。本次返回回归针对自定义根菜单返回原厂设置菜单；不据此宣称其他子页面的全部返回路径均已验证。

## 自定义菜单内部保留左侧栏

原厂叶页面布局标志 `0x1008` 会覆盖父标签容器的绘制。自定义页面清除该标志的 bit 3，使用 `0x1000`，继续保留独立的控件、标题与返回按钮。根页面保留侧栏后，进一步进入子页面仍可能隐藏外层侧栏，因此共有绘制回调还通过原厂显示与重绘 API 恢复原厂标签容器的图形组；侧栏图标来自原厂控件，未在主机上绘制。

这一布局下，返回后的原生活动页面可能是标签容器 `0xC1832D50`，它的当前子页面指向设置页 `0xC1837160`。验证同时检查这种原生父/子关系，不能只要求活动指针直接等于设置页。

最终构建位于 `output/ui-sidebar-fixed/`，保留前述标题、文字裁剪与父容器修复。四个页面的侧栏和标题检查、错误标题与恢复截图使用 `--verify-fixed --verify-sidebar`；进入回调必须确实打开自定义根页，避免只发送合成确认通知但菜单尚未进入时错误通过检查。

```sh
python3 tools/verify_ui_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/ui-sidebar-fixed/full.eg.bin \
  --report output/ui-sidebar-fixed/full.json \
  --output-dir build/ui-qemu/sidebar-all-verified \
  --verify-fixed --verify-sidebar

python3 tools/verify_ui_qemu.py \
  --qemu-root ../z30-qemu \
  --image output/ui-sidebar-fixed/full.eg.bin \
  --report output/ui-sidebar-fixed/full.json \
  --output-dir build/ui-qemu/sidebar-full-verified \
  --back-cycles 2 --touch-back --verify-return --verify-sidebar --verify-tab-switch
```

后一个检查包含两轮自定义菜单侧栏显示、触摸返回、原厂菜单上下移动、注册表释放与按钮区域清除，并从自定义根页面触摸相机标签，核对原厂拍摄菜单编号 `0x27`。仍是辅助 QEMU 与合成触摸包验证，未据此标记实机验证通过。32 项单元测试及三种预设 CRC/逐字节重复构建检查通过。

验证使用辅助启动和暂停调试器注入经过原字节核对的 ROM 修改，保留 QEMU 原厂固件身份检查。调用和错误状态是明确的诊断输入，并非自然按键路径测试。该 QEMU 验证不证明实机 UI、触摸、声音播放、SD 模块运行或可刷性；用户实际菜单反馈单独记录在本文开头。输出 EG 是研究用主模块，不是完整相机更新包。

## 机内码率档位回归

新增功能并入 `bitrate-slowmo-uncap`，不新增预设。单独选择时自动包含声音菜单框架，`full` 的根菜单新增第六项“视频码率”。原始、均衡、高质量三档使用 `*` 标记，独立配置文件为 `WL-VIDEO.CFG`。详细参数与复现命令见[码率说明](bitrate-slowmo-uncap.md)。

完整组合 EG 的 SHA-256 为 `d1ff55a024b262eba12235f2fc07373a78e8d5a70f5bc5dd6e7c2a145137af61`。`build/ui-qemu/bitrate-flow-hans/session.json` 记录首次不进入菜单的配置恢复、三档共 90 条原厂参数检查、档位保存与重入标记、保存失败正文提示、确定及原厂触摸返回关闭、最终原厂菜单侧栏保留和返回图标区域清除。繁体中文及英文的对应报告为 `bitrate-flow-hant/session.json`、`bitrate-flow-en/session.json`，每档核对一条代表参数并执行相同菜单流程。单独 `bitrate-slowmo-uncap` 预设的相同 QEMU 流程也通过，报告为 `bitrate-standalone/session.json`；其产物摘要为 `9f5c71f97ba6c99ebee6dab4c1ee979fb881eba1b26e01f799cb2da7f0e6a026`。

当时原厂字体不含“厂”字，中文菜单曾使用“原始”；补字接入构建后改为“原厂”（繁体“原廠”），对应英文 Stock。截图直接来自客体图形缓冲。调试输入下页面重建和触摸分派异步进行，测试要求活动页面转换，并在下一轮操作前完成原厂绘制采样；返回图标检查矩形为 `(636,4)-(716,52)`，不包含原厂标题分隔线。

SD 服务由测试工具显式替换；错误分支通过独立诊断写入失败触发。没有测试物理 SD 卡或编码器输出，不据此标记实机通过。34 项单元测试、完整及单独预设各 96 项指令测试、15 项声音保存回归、完整组合的模块加载回归，以及重复构建/CRC 检查通过。
