# 许可证与第三方说明

## 本项目代码

除以下声音、菜单与画幅/遮幅实现文件外，本项目原创代码与文档采用 [MIT License](LICENSE)：

- `src/z30_extensions/features/aspect_border.py`
- `src/z30_extensions/features/aspect_border_lease.py`
- `src/z30_extensions/features/aspect_frame.py`
- `src/z30_extensions/features/aspect_mask.py`
- `src/z30_extensions/features/aspect_mask_trace.py`
- `src/z30_extensions/features/aspect_trace.py`
- `src/z30_extensions/features/aspect_ui.py`
- `src/z30_extensions/features/bitrate_ui.py`
- `src/z30_extensions/date_stamp.py`
- `src/z30_extensions/features/date_stamp.py`
- `src/z30_extensions/features/date_stamp_ui.py`
- `src/z30_extensions/features/shutter_sound.py`
- `src/z30_extensions/features/sound_audio.py`
- `src/z30_extensions/features/sound_layout.py`
- `src/z30_extensions/features/sound_settings.py`
- `src/z30_extensions/features/sound_ui.py`
- `src/z30_extensions/features/ui_text.py`

上述文件采用 [Wonder Lantern Community License](licenses/Wonder-Lantern.txt)。该许可证允许使用、修改和免费分发，禁止收费分发，并要求公开分发的修改版本提供同许可证源码。完整条件以随附许可证为准。由于包含收费分发限制，该部分不是 OSI 意义上的开放源代码许可。

## Wonder-Lantern

[Wonder-Lantern](https://github.com/vic2349/Wonder-Lantern) 是自定义电子快门声音功能的参考项目。本仓库属于 WonderLantern 组织，提供面向 Z30 的独立实现，不包含该参考项目的 Z6 固件、SHCORE 二进制或示例音频。许可证中“不得自称为官方 Wonder Lantern 发布”的要求，指不得将受许可约束的衍生版本宣称为 Wonder Lantern 软件本身的官方发行版；这不影响本仓库属于 WonderLantern 组织。

随附许可证保留其原始名称与版权声明；本项目声音实现的版权归本项目贡献者所有。

## Nikon 固件

原厂固件和原厂资源由使用者自行提供。菜单模板在构建时从匹配输入读取，仓库不附完整固件或模板镜像。代码中的原字节窗口用于校验补丁目标，零填充位置用于匹配输入的解码。本项目许可证不授予 Nikon 固件、资源或商标的权利。

## Noto CJK 字形

中文菜单需要的、原厂字体缺少的字形（全部可打印 ASCII 和部分汉字）由 Noto Sans CJK SC/TC Regular 与 Noto Sans Mono CJK SC Regular（版本 2.004，© 2014-2021 Adobe，Reserved Font Name “Source”）的轮廓转换而来：缩放与平移到原厂字体坐标、三次曲线转二次曲线、套用原厂度量。转换结果为 `src/z30_extensions/platform/z30_120_extra_glyphs.json`，由 `tools/build_cjk_glyphs.py` 生成；构建时这些字形被加入原厂字体 0、2 的副本，写入输出固件。

这些字形属于 Noto 字体的修改版本，按 [SIL Open Font License 1.1](licenses/OFL-Noto-CJK.txt) 授权，不适用本项目的 MIT 或 Wonder Lantern Community License；不得以 “Source” 为名，也不得单独出售。仓库不附 Noto 字体文件本身，生成数据时需自行下载到 `build/fonts/noto-cjk/`。

## 依赖

Keystone 和 Unicorn 通过 Python 依赖安装，各自保留其许可证。本仓库不分发这些依赖的二进制，也不包含第三方固件工具的 XOR 表或密钥工具实现。

图形界面打包程序（`tools/build_gui_exe.py`）不包含 Keystone 或 Unicorn。其中的 `asm_cache.gz` 是维护者用 Keystone 汇编本项目源码得到的机器码，由 `tools/build_asm_cache.py` 生成。打包程序内置 Python、Tcl/Tk 和 PyInstaller 引导程序，发布包的 `licenses` 文件夹附有这些许可证文本。
