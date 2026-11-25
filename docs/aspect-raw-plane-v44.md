# 自定义画幅 NEF 使用原厂 RAW 尺寸（v44，issue #38）

适用 Nikon Z30 C1.20。2026-11-24 起，`aspect-profiles` 和默认 `full` 中的六种自定义画幅改为把 NEF 的 RAW 平面保持为原厂尺寸；JPEG、内嵌预览、回放、取景遮幅和参考线仍按所选比例处理。NEF 内嵌 XMP 另写入 Adobe `crs:` 裁切，使 Adobe 软件按所选比例显示。2026-11-24 用户以 `full` 构建（EG SHA-256 `468530846c807350c257fccb2efa7a6fd1d37df729152499fad5869241f90f1d`）完成下方机身复测清单，反馈全部正常。

## 问题

[issue #38](https://github.com/WonderLantern/wonder-lantern-z30/issues/38)：65:24（XPAN）拍摄的 NEF 可以导入 iOS 照片，但 Lightroom（云端版 10.0）提示 "Import failed"；同一固件的原厂画幅 NEF 可以正常导入。

v43 及之前，自定义画幅改写了 area 2 的 RAW 平面尺寸表和传感器读出偏移，NEF 的 RAW IFD 因此不是 Z30 原厂会写出的尺寸：

| 比例 | v43 RAW 平面 | v44 RAW 平面 |
|---|---|---|
| 4:3 | 4984×3728 | 5600×3728（DX） |
| 5:4 | 4672×3728 | 5600×3728（DX） |
| 2:1 | 5600×2800 | 5600×3144（16:9） |
| 2.35:1 | 5600×2384 | 5600×3144（16:9） |
| 2.39:1 | 5600×2344 | 5600×3144（16:9） |
| 65:24 | 5600×2072 | 5600×3144（16:9） |

Adobe 按机型识别 RAW 布局，非原厂尺寸很可能是导入失败的原因。其他比例未经用户试导入，但机制相同，因此一并修改。

## 依据

原厂 1:1 本身就是"RAW 平面与 JPEG 处理窗口分离"：RAW 平面 3728×3728 位于传感器第 936 列；处理配置使用 5568 宽的父区域并修边 928 列，两者都落在绝对第 944 列。由此确认：

- RAW 平面尺寸 `0x1020BFC8`、M 档 `0x1020C05C` 和读出原点 `0x1020C072` 描述 RAW 平面；
- 处理配置 `0x1014237A` 的垂直起点为"边距 y + 原点 y"（绝对坐标），水平裁切由修边表完成，不含原点 x；
- 描述符 `0x101D5B36` 中的裁切中心和裁切框相对 RAW 平面。

## 修改

`aspect_geometry.build_trial` 默认 `stock_raw_plane=True`：

- 宽幅保留原厂 16:9 RAW 平面与原点 (0,292)；边距表 y 增加 `(3128 − 自定义高度)/2`（XPAN 为 536），处理配置的绝对起点仍为第 836 行、高 2056，与 v43 相同。
- 窄幅（4:3、5:4）使用 DX RAW 平面与原点 (0,0)；处理配置不变。
- area 2 的处理边距与描述符边距在原厂共用一张表。窄幅描述符需要加上裁切原点 x，因此 `aspect_desc_edges` 在已转换的 area 2 指针上读取独立的每档描述符边距表；未选择画幅、其他区域或文件几何调用点保留原有路径。
- v43 回放识别表保留旧的自定义 RAW 尺寸（已拍照片），并加入 5600×3144 与 5600×3728。原厂主图尺寸的同形 NEF 仍得到与原厂完全相同的回放描述符；新拍自定义 NEF 按文件自身的预览尺寸回放。
- `stock_raw_plane=False` 复现 v43 几何，仅供回归比对。

报告字段：`features.aspect_profiles.stock_raw_plane_version` 为 1，`profiles[].raw_plane_wh` 记录每档 RAW 平面尺寸。

## 本地验证

```powershell
python patch.py full --source Z_30_0120.bin --output build/research/issue38/full.eg.bin
python tools/verify_aspect_raw_plane.py --deps ../tools/vendor --source Z_30_0120.bin --image build/research/issue38/full.eg.bin --report build/research/issue38/full.eg.json --output build/research/issue38/full-raw-plane.json
```

- `verify_aspect_raw_plane.py`：六档 × 默认/备用（`info+0x228 == 3`）分支 × L/M/S，共 36 组。原厂处理配置和缓冲区设置函数的输出与 v43 逐字节一致（独立几何与构建产物均比对）；RAW 平面、原点为原厂值；描述符裁切框的绝对传感器位置与 v43 相同，并且位于 RAW 平面内。另有 6 组"关闭"案例与原厂完全一致。`aspect-profiles` 与 `full` 构建均通过 42 组。
- `verify_aspect_raw_playback.py`：522 组原生预览生成案例、32 组元数据案例通过；新增原厂 16:9/DX 同形 NEF 不变、新拍自定义 NEF 取文件预览尺寸两类案例。
- 项目单元测试 182 项通过（`PYTHONPATH=src`）。

原生执行不模拟传感器读出、ISP、NEF 写入、取景或 JPEG 编码；`tools/verify_aspect_profiles.py` 的菜单操作夹具在当前 HEAD 上也无法运行（与本次修改无关），本次未用它验证。

## Adobe 裁切：NEF 内嵌 XMP（2026-11-24）

用原厂 Z30 DX NEF 只改文件头做对照试验（Adobe Camera Raw）：

| 试验 | 结果 |
|---|---|
| 原厂 RAW，Nikon makernote CropArea（`0x0045`）改为 XPAN 窗口 | 不裁切 |
| RAW IFD 高度 2072，裁切相关标签一致 / 保持原厂 | 均导入失败 |
| 原厂 RAW，IFD0 XMP 包追加 `crs:` 裁切（单独的 `rdf:Description`） | **按 65:24 裁切** |
| 同上，保留原样片竖拍方向（Orientation 8） | **按竖向 65:24 正确裁切** |

结论：Adobe 只接受原厂 RAW 平面，裁切需要写在内嵌 XMP 的 `crs:` 设置中。相机本身已在该包写入 `crd:`（Camera Raw 默认值）段，由 `0x101B862E` 生成到一个有界缓冲区 `{cursor, remaining, overflow}`；溢出时原厂会丢弃整个 `crd:` 段。

`features/aspect/xmp_crop.py` 在该函数写出结尾 `</rdf:Description>`（`0x101B86B4`）后，按当前画幅追加一个 `crs:` 段（`HasCrop`、四边坐标、`CropAngle 0`、`CropConstrainToWarp 0`，固定 372 字节）。条件：已选自定义画幅、原厂区域为 16:9、缓冲区未溢出且剩余空间足够；否则保持原厂文本不变。

裁切坐标按 Adobe 对原厂 RAW 的默认裁切归一化，假定 DX 为 5568×3712、16:9 为 5568×3128（相机自身 CropArea 尺寸）：

| 比例 | 左 | 上 | 右 | 下 |
|---|---|---|---|---|
| 4:3 | 0.055316 | 0 | 0.944684 | 1 |
| 5:4 | 0.083333 | 0 | 0.916667 | 1 |
| 2:1 | 0 | 0.054987 | 1 | 0.945013 |
| 2.35:1 | 0 | 0.121483 | 1 | 0.878517 |
| 2.39:1 | 0 | 0.127877 | 1 | 0.872123 |
| 65:24 | 0 | 0.171355 | 1 | 0.828645 |

`tools/verify_aspect_xmp_crop.py` 执行完整原厂 `0x101B862E`（合成上下文）：未选择或原厂区域不是 16:9 时与原厂文本逐字节一致；选择后为原厂文本加对应 `crs:` 段，整体 XML 合法；剩余空间不足时不写入、不溢出。共 37 组，`aspect-profiles` 与 `full` 均通过。报告字段 `features.aspect_profiles.xmp_crop`。

竖拍：`crs:` 坐标按传感器方向，与 Orientation 无关，固件无需区分横竖（ACR 文件头试验确认）。

离线阶段未确认、后经机身复测反馈正常：16:9 默认裁切假定、该缓冲区是否也进入机内 JPEG 的 XMP（若进入，Adobe 打开 JPEG 会再裁一次），以及真实 Picture Control 下 `crd:` 段长度。

## 结果与限制

- NEF 中的 RAW 数据为完整 16:9 或 DX 画面，Adobe 软件按内嵌 XMP 显示所选比例，可在裁切工具中恢复完整画面。其他不读取 `crs:` 的软件显示完整画面。
- 机内 JPEG 和内嵌预览仍为所选比例。
- 读出区域变大到原厂 16:9/DX，连拍速度和缓冲可能回到原厂该区域的水平。

## 机身复测清单

2026-11-24 用户反馈以下各项实际测试均正常；未提供逐档、逐尺寸或连拍速度明细，未列出的组合不计为已验收。


1. 设置 → 固件版本能打开，并能正常进行固件更新。
2. 每档画幅拍 RAW+JPEG：JPEG 构图与 v43 相同（不偏移、不拉伸），NEF RAW IFD 为上表 v44 尺寸。
3. ACR 与 Lightroom（云端版）打开 XPAN、2:1 与 4:3 NEF：按所选比例裁切，裁切框与 JPEG 构图一致；实拍竖拍同样正确。
4. ACR 打开同一次拍摄的机内 JPEG：不得被再次裁切。
5. 普通回放 → 放大 → 缩回：新拍 NEF、v43 旧 XPAN NEF、原厂 DX/16:9 NEF、普通 JPEG。
6. 取景遮幅和参考线、M/S 尺寸、关闭画幅后的原厂拍摄。
