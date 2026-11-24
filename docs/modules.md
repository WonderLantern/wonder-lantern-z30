# SD 原生模块宿主原型

> 2026-11-24：要把整套功能放到卡上，用 [SD 卡加载](card-loader.md)：开机自动加载，不需要菜单操作，模块大小也不受 16 KiB 限制，已实机通过。本页的模块宿主仍是研究工具（例如 [MMU 探测](card-loader-probe.md)、AF 记录），只能通过研究构建（`module_host=True`）获得。

2026-10-17 状态更新：用户确认当前全部八项功能均已在 Nikon Z30 C 1.20 实机检验通过，包括多画幅、全信息页、声音配置记忆和机内码率档位。下文旧版本的待实机表述保留当时的测试范围，以本条为当前功能状态；离线工具的验证字段仍仅描述工具自身证据，不表示穷举全部模式、组合或故障条件。

`module-host` 是实验性 SD 模块宿主入口，自动包含所需的快门声音框架；默认 `full` 也包含该实验性功能。旧 `modular` 构建保留同屏、自定义声音、M900 和 SD 模块宿主的组合。宿主提供固定入口与 ABI，模块代码从 SD 卡读入 RAM。它不自动启动模块；成功加载的模块保留到关机。

这是实验性功能。维护者已确认 `modular` EG 模块可在 Z30 C 1.20 上安装，并已在机身测试 SD 宿主加载和运行 ZMOD。离线检查仍不覆盖所有故障条件；例如原厂跨核缓存服务无应答时，加载可能持续等待。下文的操作流程对应已实现的菜单和模块接口。

## 构建宿主

安装完整项目和依赖，准备匹配的原厂 Z30 C 1.20 固件：

```powershell
python -m pip install '.[sound]'
New-Item -ItemType Directory -Force output | Out-Null
python patch.py module-host --source "Z_30_0120.bin" --output "output/EG209020.BIN"
```

输出是独立 EG 模块，不是完整 Nikon 更新包。patch 程序不会写相机或 SD 卡。`modular` EG 模块已在 Z30 C 1.20 机身安装测试，SD 宿主也已通过机身加载和运行 ZMOD。

## 准备模块

先生成计数示例：

```powershell
python tools/prepare_module.py --example counter --output "output/CORE.ZMOD"
```

离线示例输出 `CORE.ZMOD`。将模块放入 SD 卡根目录下的 `Z30` 文件夹：

```text
SD 卡根目录/
└─ Z30/
   └─ CORE.ZMOD
```

机身中打开 **设置菜单 → Extensions → SD Modules**：

简体中文路径为 **设置菜单 → 自定义菜单 → SD 卡功能**，繁体中文入口为 **自訂選單**。加载与运行操作跟随机身语言显示。

1. 选择 `Load CORE.ZMOD`，按 OK。
2. 成功后 State 显示 `Loaded`，输出显示 `counter module ready`。
3. 选择 `Run module`，按 OK，输出依次显示 `Runs: 1`、`Runs: 2` 等。

计数是模块自己的 RAM 状态，不是相机快门次数。返回菜单再进入仍可运行该模块，关机后状态清空。

## 更换功能，保留同一份宿主固件

另一个示例读取相机原生网格设置：

```powershell
python tools/prepare_module.py --example grid --output "output/GRID.ZMOD"
```

更换示例模块时，关闭相机，取出 SD 卡，用 `GRID.ZMOD` 替换卡中的 `Z30/CORE.ZMOD`。重新开机，进入同一菜单，先 Load 再 Run，输出为 `Grid: 数值`。

已加载的模块不能在运行期间替换。再次 Load 会显示 `Reboot to reload`，防止释放仍被回调引用的代码。第一版使用一个核心模块槽位；核心模块可以包含自己的多项功能。

## v1 宿主的分页修复模块

如果使用初版宿主，可以生成专用模块：

```powershell
python tools/prepare_module.py --example menu-fix --output "output/MENU-FIX.ZMOD"
```

关机后将它复制为卡中的 `Z30/CORE.ZMOD`，重新开机并 Load。成功时显示 `Menus fixed`；返回后再进入各页，四个自定义菜单分别显示 5、5、2、4 项。Run 显示修复模块自身的运行次数。

修复在每次 Load 后生效，开机首次加载前仍使用宿主自身的布局。宿主源码也已按自己的几何布局设置每屏容量；新版宿主不需要这个模块来修正分页。

该模块不适用于当前带侧栏修复或码率档位的菜单布局：其旧版布局校验会拒绝初始化。当前 `full` 的自定义根页包含六项，第六项为视频码率；不要使用旧分页模块修改新菜单。

该模块是针对初版 ABI 2 宿主的特定布局适配器。它检查私有页面类型和条目数，将描述复制到模块持久数据，使用 C 1.20 原生缓存服务同步，再发布副本及重置滚动位置。副本保留至关机。它没有写闪存，也不通过通用 ABI 承诺任意宿主布局兼容。

## 模块 ABI 2

接口定义见 [z30_module_api.h](../examples/modules/z30_module_api.h)。模块入口为 `init(api, context)`，成功返回 1，并通过 `set_run_handler` 登记运行回调。回调为 `run(api, context)`，同样以 1 表示成功。

| 接口 | 合同 |
| --- | --- |
| `version` / `size` | ABI 2，接口表至少 20 字节 |
| `get_property(key)` | 调用原厂属性读取；属性编号和有效运行条件由模块作者核对 |
| `set_text(text)` | ABI 接受最长 40 个可打印 ASCII 字符，复制后由宿主显示；中文界面显示前 24 个字符，英文最多 40 个；来源必须在模块载荷或私有 context 内 |
| `set_run_handler(callback)` | 仅在 init 期间登记，必须是载荷范围内的 Thumb 入口 |
| `context` | 64 字节，初始全零，成功加载后保留至关机 |

宿主函数表始终指向常驻代码，模块回调单独保存。失败初始化会先撤销回调再释放代码；释放失败保留所有权供重试。文件关闭失败保留原始 fd，阻止直接打开下一文件。成功模块没有热卸载接口。

模块载荷必须是无外部未解析符号、可在不同地址执行的 ARM Thumb 程序；原厂服务通过接口表导入。示例由 Thumb 汇编生成器产生，不依赖固定 Nikon 地址。普通 ELF、DLL、Python 或 Lua 文件不能直接放入该槽。

已有裸 PIC 代码可以包装为模块：

```powershell
python tools/prepare_module.py --code "my_module.bin" --entry 0 --output "output/MY_MODULE.ZMOD"
```

64 字节头绑定 Z30 C 1.20、ABI 2、入口偏移、载荷长度、CRC32 和原始 EG 身份。载荷最多 16 KiB，长度需四字节对齐。CRC32 检测损坏，不提供签名或沙箱；原生模块必须来自可信来源。模块不得创建无法撤销的异步回调、保留宿主栈指针或修改自身指令。

## 执行顺序与限制

宿主在用户确认后执行：只读打开文件 → 累积短读取 → 长度、平台和 CRC 校验 → 关闭文件 → 原厂跨核缓存服务 → init → 登记回调。文件不存在、损坏、内存不足或缓存服务不可用时，不发布运行入口。

原厂缓存服务依赖另一个执行核的应答。应答缺失时可能持续等待，此时仍存在引用中的请求和代码分配，宿主不会超时释放它们。加载功能没有接入开机自动执行路径。

第一版接口覆盖菜单内同步功能。拍摄操作、后台任务、独立绘制和任意新页面需要进一步的接口与生命周期实现；宿主更新仍可能需要刷写。Lua 解释器尚未接入。

## 离线检查

```powershell
python -m pip install '.[sound,verify]'
python tools/verify_module_host.py --source "Z_30_0120.bin"
```

检查器执行候选中的加载器、校验器和模块指令，覆盖短读、损坏输入、回调、文字边界和清理恢复。SD、内存池及缓存成功结果在该公开检查器中是模型，不代表真机运行。
