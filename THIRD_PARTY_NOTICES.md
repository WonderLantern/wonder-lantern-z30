# 许可证与第三方说明

## 本项目代码

除以下文件（声音、菜单框架、画幅与遮幅、日期水印、竖拍显示、对焦与跟踪、取景显示、LUT 及相关研究实现）外，本项目原创代码与文档采用 [MIT License](LICENSE)：

- `src/z30_extensions/features/aspect/border.py`
- `src/z30_extensions/features/aspect/border_lease.py`
- `src/z30_extensions/features/aspect/frame.py`
- `src/z30_extensions/features/aspect/mask.py`
- `src/z30_extensions/features/aspect/mask_trace.py`
- `src/z30_extensions/features/aspect/raw_playback.py`
- `src/z30_extensions/features/aspect/trace.py`
- `src/z30_extensions/features/aspect/ui.py`
- `src/z30_extensions/features/aspect/xmp_crop.py`
- `src/z30_extensions/features/card/firmware_banner.py`
- `src/z30_extensions/features/card/ready_banner.py`
- `src/z30_extensions/features/card/ready_banner_image.py`
- `src/z30_extensions/features/gyro/ui.py`
- `src/z30_extensions/features/lut/core.py`
- `src/z30_extensions/features/lut/failure.py`
- `src/z30_extensions/features/lut/retouch.py`
- `src/z30_extensions/features/lut/sdload.py`
- `src/z30_extensions/features/lut/session.py`
- `src/z30_extensions/features/lut/ui.py`
- `src/z30_extensions/features/menu/framework.py`
- `src/z30_extensions/features/menu/layout.py`
- `src/z30_extensions/features/menu/slots.py`
- `src/z30_extensions/features/menu/tab.py`
- `src/z30_extensions/features/menu/text.py`
- `src/z30_extensions/features/menu/ui.py`
- `src/z30_extensions/features/photo/af_frame_colour.py`
- `src/z30_extensions/features/photo/af_frame_colour_ui.py`
- `src/z30_extensions/features/photo/battery_percent.py`
- `src/z30_extensions/features/photo/date_stamp.py`
- `src/z30_extensions/features/photo/date_stamp_ui.py`
- `src/z30_extensions/features/photo/film_grain.py`
- `src/z30_extensions/features/photo/film_grain_ui.py`
- `src/z30_extensions/features/photo/focal_length.py`
- `src/z30_extensions/features/photo/zebra.py`
- `src/z30_extensions/features/photo/zebra_ui.py`
- `src/z30_extensions/features/portrait/layout.py`
- `src/z30_extensions/features/portrait/menu.py`
- `src/z30_extensions/features/research/cmos_dump.py`
- `src/z30_extensions/features/research/cmos_spi.py`
- `src/z30_extensions/features/research/cmos_trace.py`
- `src/z30_extensions/features/research/cmos_window.py`
- `src/z30_extensions/features/research/ptp_read.py`
- `src/z30_extensions/features/sound/audio.py`
- `src/z30_extensions/features/sound/settings.py`
- `src/z30_extensions/features/sound/shutter_sound.py`
- `src/z30_extensions/features/sound/ui.py`
- `src/z30_extensions/features/tracking3d/boxes.py`
- `src/z30_extensions/features/tracking3d/tracking.py`
- `src/z30_extensions/features/tracking3d/ui.py`
- `src/z30_extensions/features/video/bitrate_ui.py`
- `src/z30_extensions/host/date_stamp.py`
- `src/z30_extensions/host/film_grain.py`
- `src/z30_extensions/host/lut.py`
- `modules/film_grain/film_grain.c`
- `tools/verify_mask_firmware_qemu.py`

上述文件采用 [Wonder Lantern Community License](licenses/Wonder-Lantern.txt)。该许可证允许使用、修改和免费分发，禁止收费分发，并要求公开分发的修改版本提供同许可证源码。完整条件以随附许可证为准。由于包含收费分发限制，该部分不是 OSI 意义上的开放源代码许可。

## Wonder-Lantern

[Wonder-Lantern](https://github.com/vic2349/Wonder-Lantern) 是自定义电子快门声音功能的参考项目。本仓库属于 WonderLantern 组织，提供面向 Z30 的独立实现，不包含该参考项目的 Z6 固件、SHCORE 二进制或示例音频。许可证中“不得自称为官方 Wonder Lantern 发布”的要求，指不得将受许可约束的衍生版本宣称为 Wonder Lantern 软件本身的官方发行版；这不影响本仓库属于 WonderLantern 组织。

随附许可证保留其原始名称与版权声明；本项目声音实现的版权归本项目贡献者所有。

## Wonder Lantern Z5 胶片颗粒

胶片颗粒（`film-grain`）移植自 [wonder-lantern-z5](https://github.com/WonderLantern/wonder-lantern-z5) 的 FILM 功能（A52）：颗粒场的生成方法和滤波系数、亮度强度曲线、每 4 像素的运算和分块相位都沿用该项目；“NEF 写入”沿用其 A48/A49 的做法（在 NEF XMP 的 crd: 区块收尾前追加 Lightroom 颗粒）和 Lightroom 数值校准。原项目按 [MIT License](licenses/Z5-Extensions-MIT.txt) 授权（Copyright (c) 2026 Z5 Extensions contributors），许可文本随附。Z30 上的挂点、调用方判断、页表检查、缓存维护、菜单和设置文件为本项目实现。

## Nikon 固件

原厂固件和原厂资源由使用者自行提供。菜单模板在构建时从匹配输入读取，仓库不附完整固件或模板镜像。代码中的原字节窗口用于校验补丁目标，零填充位置用于匹配输入的解码。本项目许可证不授予 Nikon 固件、资源或商标的权利。

## Noto CJK 字形

中文菜单需要的、原厂字体缺少的字形（全部可打印 ASCII 和部分汉字；SD 卡加载的常驻 EG 还包括 GB2312 一级字和 Big5 常用字，约 7700 个字形）由 Noto Sans CJK SC/TC Regular 与 Noto Sans Mono CJK SC Regular（版本 2.004，© 2014-2021 Adobe，Reserved Font Name “Source”）的轮廓转换而来：缩放与平移到原厂字体坐标、三次曲线转二次曲线、套用原厂度量。转换结果为 `src/z30_extensions/platform/z30_120_extra_glyphs.json`，由 `tools/build_cjk_glyphs.py` 生成；构建时这些字形被加入原厂字体 0、2 的副本，写入输出固件。

这些字形属于 Noto 字体的修改版本，按 [SIL Open Font License 1.1](licenses/OFL-Noto-CJK.txt) 授权，不适用本项目的 MIT 或 Wonder Lantern Community License；不得以 “Source” 为名，也不得单独出售。仓库不附 Noto 字体文件本身，生成数据时需自行下载到 `build/fonts/noto-cjk/`。

## 依赖

Keystone 和 Unicorn 通过 Python 依赖安装，各自保留其许可证。本仓库不分发这些依赖的二进制，也不包含第三方固件工具的 XOR 表或密钥工具实现。

图形界面打包程序（`tools/build_gui_exe.py`）有 Windows、Linux 和 macOS 三个版本，都内置 Keystone 汇编器（GPLv2，`keystone-engine` 0.9.2，源码见 <https://github.com/keystone-engine/keystone>）、pywebview（BSD）、Python 和 PyInstaller 引导程序，不包含 Unicorn。各版本的界面后端不同：

- **Windows：** 内置 pythonnet（MIT）；界面由系统的 Microsoft Edge WebView2 运行时显示，该运行时不随程序分发。
- **macOS：** 内置 PyObjC（[MIT](licenses/PyObjC-MIT.txt)）；界面由系统的 WebKit 显示。
- **Linux：** 内置 PySide6、Shiboken6 和 Qt 6（含 Qt WebEngine），按 [LGPL-3.0](licenses/LGPL-3.0.txt)（以及它引用的 [GPL-3.0](licenses/GPL-3.0.txt)）使用，未经修改，以独立的共享库形式随程序分发，可以替换。源码见 <https://code.qt.io/> 和 <https://download.qt.io/official_releases/QtForPython/>；QtPy（MIT）。

发布包的 `licenses` 文件夹附有这些许可证文本。
