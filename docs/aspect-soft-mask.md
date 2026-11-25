# 取景辅助改为轻微压暗裁切区

> 2026-10-18：[v42](aspect-lifecycle-v42.md) 增加原厂取景阶段限制，并在绘制目标筛选之前清除旧遮幅。本次回放修复机身复测正常，记录为本次问题获机身确认；未提供逐档或完整生命周期覆盖。v37、v40 的失败及 v41 定位记录保留。

日期：2026-10-18。遮幅样式为轻微压暗裁切区并保留内部网格。该方向替代继续缩细黄色矩形框的试验。目标是只对投影成片范围之外的可见区域叠加淡黑色，范围内不覆盖，保留原厂网格、状态图标与对焦框。早期建议起始值为 `48/255`；后续版本加深，当前值为 `128/255`。

> 技术索引：[aspect-handoff.md](aspect-handoff.md)。旧黄框路线（v17/v23 参数）保留在源码中，遮幅作为独立的实验开关存在，当前 `aspect-profiles` 与 `full` 已默认包含动态遮幅，无需单独预设。下文为显式试验工具及历史记录。

## 历史状态（v36，2026-10-18，以页首最新结果为准）

动态遮幅继续使用既有成片投影且不依赖网格记录。[v36](aspect-mask-menu-v36.md)保留约 50% 强度，补齐外缘并在受控合成边界完整填充下层，避免逐像素跳过残留产生小缝；原厂 UI 在其上合成。七档与边缘连续性检查通过，本版机身效果待确认。v31 的边界、网格及图标机身反馈保留为对应版本证据。下面 v25/v27 段落均为历史记录。

## v27 历史状态（2026-10-18）

静态 trial 模式已接入真实构建并在 QEMU 端到端通过；动态模式已能构建，但几何来源尚未完成；尚未写相机验证。当前实现见 [多画幅说明](aspect-profiles.md)。

- **v27 trial**：保留追加区 ROM state 供 QEMU 回放；sha256 `b61710121d396a97c70beda9aaa4bce74ce29a0ca6561954d416d381ce0791d6`（size 33,988,826，crc 0）。回放三阶段（inactive 基线 → active 带区 `[0,207,1]` → erased 基线）通过，证据 `build/research/mask-firmware-v27-trial-replay/`。
- **v27 dynamic（未验证）**：历史实现尝试在 GPU submit 钩子里读原厂网格记录 `0xC201A7F4` 现算 4 个外区矩形，不持有 RAM 状态。但 QEMU 取景 fixture 没有启用原厂网格显示，网格钩子不运行、记录恒为 0，因此 `build/research/mask-firmware-v27-menu/` 的 PASS 是**空洞通过**（`mask-observations.json` 中 `grid_record` 全 0、`expected_regions` 全空），不能视为已验证。dynamic 构建 sha256 `96500acac62dd48a1518330dc738a5c3fcd76e48c55ed48661b2f2f71e76272f`。

### v25 历史验证（保留）

遮幅已经接入真实构建并跑通 QEMU 端到端生命周期；尚未写相机验证。

- 实现位置：`src/z30_extensions/features/aspect/mask.py`（新增固件钩子）+ `features/aspect/ui.py` 的 mask 段 + `tools/build_aspect_mask_trial.py` 构建入口。
- **绘制层**：填充原厂取景 RGBA 图像表面（image surface，运行时地址取自显示状态槽 `0xC18200C0`，QEMU 中为 `0xd0959600`），而不是 UI 画布。显示管线每周期先把该表面合成到 overlay，再叠加 UI 画布，因此状态图标、对焦括号与内部网格保持在遮幅之上。填充发生在 GPU submit 边界（`0x1055D112` 入口钩子），紧贴 overlay 合成；在 UI renderer 时点或 UI 画布内填充均已被实验否决（前者会被后续显示管线擦掉，后者会破坏图标/括号）。
- **幂等**：每周期用 SRC 语义写入预乘黑字（黑 + alpha），每个矩形先比较首/末字，相同则跳过，所以重复提交不会越叠越黑，停用后只需一次清零填充即可回到基线。
- **v25 trial 构建**（静态 2.35:1 样条，alpha 48）：
  - 产物 `build/research/mask-build/aspect-mask-v25-trial/EG209020.BIN`，size `33,989,722`，crc_residue `0`，sha256 `2380cd0946bfbdae617a6f727f506031d9ed2b93e85edb0104e4692b51fb53d1`
  - 报告 `EG209020.json` 的 `features.aspect_profiles.soft_mask` 记录 state=`0x12088848`、fill=`0x12088149`、submit=`0x12088349`、submit_site=`0x1055d112`、alpha=48、样条矩形与限制。
- **QEMU 端到端证据** `build/research/mask-firmware-v25-replay/`（QEMU 显示模型，不是相机）：
  - `session/00-trial-inactive.png`：未激活，色条基线 `[0,255,1]`。
  - `session/01-trial-active.png`：激活后上下全宽带区变 `[0,207,1]`，恰好等于 `255×(1-48/255)`；成片区内仍 `[0,255,1]`；相机/存储卡/信息图标与右下黄色对焦括号保持原亮度未被压暗。
  - `session/02-trial-erased.png`：置擦除位后带区回到 `[0,255,1]` 基线，图标与括号不变。
  - 采样数值见 `context.json`（inactive/active/erased 三组）；三帧均为 720×480。
- **QEMU 运行方式**：QEMU 机型 `nikon-z30-display` 会校验 `-bios` 整文件 sha256/长度，只接受原厂 C1.20 模块，因此构建产物不能直接作为 `-bios` 启动。改用 `tools/run_built_firmware_qemu.py`：先以原厂镜像启动，暂停时通过 gdbstub 把构建产物与镜像的差字节和追加尾部回放进 ROM 别名（`tools/probe_rom_write_qemu.py` 验证过该写入可行），回读 0 差异后再进入原厂取景流程。这只是 QEMU 研究手段，不是安装方式。
- **诊断/研究探针**（均只写 `build/research/`，不进产物）：`probe_mask_inplace_qemu.py`（层与生命周期等价验证）、`probe_mask_renderer_hook_qemu.py`（否决 renderer 时点）、`probe_rom_write_qemu.py`、`probe_submit_pairs_qemu.py` 等。

## 构建与运行

```powershell
python tools/build_aspect_mask_trial.py --source Z_30_0120.bin `
  --output build/research/mask-build/aspect-mask-v27-trial/EG209020.BIN `
  --deps build/linux-verify-deps --trial
```

WSL 中运行构建需要 Keystone（`--deps build/linux-verify-deps`）。QEMU 回放验证在 WSL 中执行：

```bash
python3 tools/run_built_firmware_qemu.py --report <EG209020.json> --firmware <EG209020.BIN> \
  --stock-firmware ../z30-qemu/.local/firmware/eg2090_0120a0.bi \
  --output-dir build/research/mask-firmware-v27-trial-replay
```

## v25/v27 历史验证与边界

- 已验证（QEMU 显示模型 + 实际构建产物回放）：遮幅位于 UI 之下、图标/括号/网格保留；alpha 48 的合成比例正确；提交级重复填充幂等；停用擦除后恢复基线；失败关闭时不会写入无关表面（image surface 指针校验高 4 位为 0xd）。
- QEMU 色条是模型 fixture，不是传感器画面；模型不实现原厂 42 记录矩形硬件光栅，因此黄框双线/端帽与此处无关。
- **未验证**：真机 alpha 观感与校准、真机缓存一致性、休眠/回放/HDMI/放大/对焦等生命周期、该历史动态模式的实际接线、真机网格开启/关闭与遮幅共存。动态模式当前不写 RAM state；trial 模式的 state 在追加区（相机会只读，仅用于回放验证）。
- 当前普通构建仍走旧路线，未自动启用遮幅；本阶段没有新增可交付的遮幅 EG，也没有写真实相机或存储卡。
