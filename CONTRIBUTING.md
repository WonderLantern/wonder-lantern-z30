# 贡献者开发文档

**简体中文** · [English](CONTRIBUTING.en.md)

本文面向想修改或新增功能的开发者。使用说明见 [README](README.md)。

## 基本原则

1. **升级路径必须可用。** 每个交给他人上机的构建，都必须仍能进入 **设定菜单 → 固件版本** 并正常升级。这是刷回原厂固件的唯一途径。凡是改动原厂共享数据（字体、样式、菜单表）的功能，都要先跑[交付前检查](#交付前检查)。
2. **补丁只针对原厂字节。** 每处修改都带原字节；原字节不符就拒绝构建。不改动原厂数据本体：需要改的表先复制到尾部，再把引用重定向过去。
3. **不分发原厂内容。** 仓库和发布包里不放原厂固件、解码后的模块、生成的 EG 或原厂字形位图。需要原厂数据时，在构建时从用户提供的固件读取。
4. **证据分开记录。** 区分离线检查、QEMU 检查和实机反馈；文档只写有证据的结论，没验证的就写“未验证”。

## 开发环境

```powershell
python -m pip install -e '.[sound,verify]'
```

- **Python 3.10+。** `sound` 安装 Keystone（汇编补丁代码），`verify` 安装 Unicorn（执行原厂与补丁指令的测试）。
- **原厂固件。** 把未修改的 `Z_30_0120.bin` 放在仓库根目录（已被 `.gitignore` 忽略），依赖固件的测试和工具会自动使用它；没有它时这些测试会跳过。
- **QEMU（可选）。** 把 z30-qemu（基于 WonderLantern/z7-qemu 的 Z30 模拟机）放在本仓库旁边（`../z30-qemu`），按它的 README 构建，并把解码后的原厂模块放到 `../z30-qemu/.local/firmware/eg2090_0120a0.bi`。QEMU 工具在 Linux 或 WSL 中用 `python3` 运行，传 `--qemu-root ../z30-qemu`。
- **补字字体（只在增加中文标签时需要）。** `python -m pip install fonttools`，再把 [noto-cjk](https://github.com/notofonts/noto-cjk) 的 `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`、`Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf` 和 `Sans/Mono/NotoSansMonoCJKsc-Regular.otf` 下载到 `build/fonts/noto-cjk/`。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `patch.py`、`patch_gui.py` | 从源码直接运行命令行或图形界面 |
| `src/z30_extensions/cli.py`、`gui.py` | 命令行与图形界面；预设列表来自 `build.py` |
| `src/z30_extensions/build.py` | `PRESETS`、`FEATURE_ORDER`、预设展开与功能组合 |
| `src/z30_extensions/container.py` | 校验并解码 `Z_30_0120.bin`，提取 EG 模块 |
| `src/z30_extensions/plan.py`、`patch.py` | 补丁计划（尾部分配、原字节校验）与应用、CRC 重建、报告 |
| `src/z30_extensions/assembly.py` | Keystone 汇编、预汇编缓存和常用指令片段 |
| `src/z30_extensions/platform/z30_120.py` | 原厂模块身份、地址映射（`StockImage`） |
| `src/z30_extensions/platform/fonts.py` | 原厂字体格式：字符表、字形索引、轮廓编解码、字体重建 |
| `src/z30_extensions/features/` | 各功能实现；`ui_text.py` 管菜单文字，`cjk_fonts.py` 管补字 |
| `tests/` | 单元测试（`unittest`） |
| `tools/` | 研究与验证工具：`inspect_*`、`probe_*` 用于分析，`verify_*` 用于检查，`build_*` 用于生成数据和发布 |
| `docs/` | 各功能的设计、证据与验证记录（以中文为主） |
| `examples/modules/` | SD 模块接口头文件（研究用） |

`build/`、`output/`、`analysis/`、`dist/` 用于存放本地产物，都已被 Git 忽略。

## 补丁是怎么写的

每个功能是 `features/` 下的一个模块，提供 `add(plan, ...)`。`Plan` 封装了原厂镜像和追加在模块末尾的“尾部”：

```python
from ..assembly import assemble, constant, invoke

def add(plan):
    code = plan.alloc(bytes(64))                     # 在尾部预留空间，返回运行地址
    plan.put(code, assemble(f'''
        push {{r4,lr}}; {constant('r0', 0x1400)} {invoke(0x10465984)}
        pop {{r4,pc}};
    ''', code, 64))                                  # Thumb 汇编，不得超出预留容量
    plan.edit(SITE, new_bytes, 'my-feature-hook',
              expected=STOCK_BYTES)                  # 原厂位置的等长替换，原字节必须一致
    plan.features['my_feature'] = dict(hook=hex(code))  # 写入 JSON 报告
```

- **地址：** 都是运行时地址。ROM 从 `0x10020000` 开始；初始化数据区映射在 `0xC1800000` 起，`StockImage` 会换算到文件偏移。读原厂内容用 `plan.image.read(address, size)` 和 `plan.image.words(address, count)`。
- **跳转：** 挂钩原厂代码时用 `assembly.jump(site, target, length)` 生成长跳转。被覆盖的指令要在补丁里重新执行，再跳回原处。
- **原厂数据表：** 不要直接改。先复制到尾部，再把引用它的字面量改过去。改之前要找全所有引用，例子见 `features/cjk_fonts.py`：字体指针表和大小表是两张表，只复制一张会让原厂语言页卡死。
- **汇编缓存：** `assemble()` 先查 `asm_cache.gz`，没命中才调用 Keystone。改动汇编源码后，Windows 发布包需要重新生成缓存（见[发布](#发布)）。

## 新增功能或预设

1. 在 `src/z30_extensions/features/` 写实现，并在 `build.py` 的 `build()` 里按顺序调用。
2. 公开预设：同时加入 `PRESETS` 和 `FEATURE_ORDER`。`FULL_FEATURES` 目前就等于 `FEATURE_ORDER`，所以新功能会自动进入 `full`。如果要做成不在 `full` 里的可选预设，就把 `FULL_FEATURES` 改成显式列表；图形界面会把不在 `full` 里的预设显示为附加选项。用到菜单框架的功能，还要在 `resolve_presets()` 里带上 `shutter-sound`。
3. 命令行菜单和图形界面直接读取 `PRESETS`，但 `tests/test_presets.py` 里的编号与组合预期要更新。
4. 为新代码写单元测试。能用 Unicorn 执行的补丁代码，尽量执行一遍（参考 `tests/test_ui_text.py`）。
5. 在 `docs/` 写专题文档，写明挂点、证据和未验证范围，并更新两份 README 的功能表。
6. 如果新文件采用 Wonder Lantern Community License，加上 SPDX 头，并列入 `THIRD_PARTY_NOTICES.md`；其他新文件默认 MIT。

## 菜单文字与中文字库

- **标签与翻译：** 菜单标签通过 `ui_text.TextResources.alloc('English text')` 分配；中文翻译写在 `ui_text.TRANSLATIONS`，每条最多 24 个 UTF-16 码元。
- **原厂字体：** 原厂中文字体（简体字体 2、繁体字体 0）只含菜单用字。构建时 `cjk_fonts` 会补上 `platform/z30_120_extra_glyphs.json` 里的字形：全部可打印 ASCII，加上标签需要的缺字。
- **缺字检查：** `check_glyphs()` 在构建时检查每个标签，用到字体里没有的字就报错。新增标签缺字时，运行：

  ```powershell
  python tools/build_cjk_glyphs.py --firmware Z_30_0120.bin --write
  ```

  它会从 Noto 字体转换缺的字，并更新 JSON。这份 JSON 要和代码一起提交。
- **查询与预览：** `tools/font_coverage.py 某词` 查原厂字体缺哪些字；`tools/render_stock_glyphs.py --fill-from <Noto OTF> 文字 -o out.png` 预览原厂字和补字混排的效果。
- **格式细节：** 字体格式和相关证据见 [UI 验证记录](docs/ui-qemu.md)。

## 测试

### 单元测试

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests
```

- 根目录没有原厂固件时，依赖固件的测试会跳过。
- 没装 Unicorn 时，执行指令的测试会跳过。
- 提交前全部测试必须通过。

### QEMU 检查

在 WSL 中运行，`--image`/`--report` 指向构建产物。常用的几个：

| 工具 | 检查内容 |
| --- | --- |
| `verify_firmware_update_qemu.py --expect-page 0xc1839680` | 能打开与原厂相同的“固件版本”页 |
| `probe_language_page_qemu.py --region N --scroll 35` | 语言页在指定地区码下的完整列表能打开和滚动 |
| `verify_ui_qemu.py` | 自定义菜单、中文显示、返回与首次进入 |
| `verify_<功能>_qemu.py` | 各功能菜单流程（日期水印、码率、画幅、斑马纹等） |

注意 QEMU 的局限：
- **地区码：** 模拟机默认是无效值 `0xFF`，语言页只显示 English。要用 `--region` 指定 0–12 才能覆盖真实的语言列表（地区 9 是中文简繁，地区 5、6、7、12 是含中文的长列表）。
- **SD 卡：** 模拟机不挂载虚拟 SD 卡，升级本身只能在相机上验证。
- **启动方式：** QEMU 是辅助启动，不能代替真实开机、录制、SD/USB 等实机测试。

### 交付前检查

把构建交给他人上机之前：

1. 全部单元测试通过。
2. 构建输出到新的 `output/<名称>/` 目录（构建器不会自己建目录，也不覆盖已有文件），并记下 SHA-256，供对方核对。
3. `verify_firmware_update_qemu.py` 在简体、繁体、英文三种语言下都打开原厂固件版本页。
4. 改动涉及原厂共享数据（字体、样式、菜单）时，用多个地区码检查语言页，并跑受影响的菜单验证。
5. 实机清单的第一项永远是：能进入固件版本页并正常升级。

## 实机记录

- 实机结果写进对应专题文档，注明日期、构建（目录名或 SHA-256）和具体现象。
- 用户反馈的问题，先尝试在 QEMU 中复现，再修复。修复后重新走一遍交付前检查。
- 有问题的构建目录改名标记（例如 `-BAD-...`），避免被再次拿去测试。

## 文档、许可与提交

- **文档：** 专题文档以中文为主，两份 README 内容保持一致。
- **提交信息：** 用英文祈使句作标题，例如 `Add missing CJK and ASCII glyphs to the stock UI fonts`，正文说明原因与证据。每个提交只做一件事。
- **不要提交：** 原厂固件、生成的 BIN/EG、`asm_cache.gz`、Noto 字体文件或私人测试素材，`.gitignore` 已覆盖这些类型。
- **第三方素材：** 引入新的第三方代码、字体或数据时，必须在 `THIRD_PARTY_NOTICES.md` 写明来源和许可证，并把许可证全文放进 `licenses/`。

## 发布

Windows 发布包不包含 Keystone（GPLv2），改为附带预汇编的机器码：

```powershell
python -m pip install '.[gui-exe]'
python tools/build_asm_cache.py
python tools/build_gui_exe.py
```

- **生成缓存：** `build_asm_cache.py` 汇编全部功能组合，再在禁用 Keystone 的情况下重建并比对哈希。
- **打包：** `build_gui_exe.py` 生成 `dist/WonderLanternZ30-windows-x64.zip`，附带全部许可证文本，包括 MIT、Wonder Lantern Community License、OFL、Python、Tcl/Tk 和 PyInstaller。
