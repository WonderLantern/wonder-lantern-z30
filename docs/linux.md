# 在 Z30 上启动 Linux

> 位于 `experimental` 分支，不包含在 `full` 中。
>
> **进展：主线 Linux 6.18.65 已可在 Z30 上经卡文件交接启动到双核用户态**，支持 LCD 叠加层控制台与帧缓冲、关闭 EX MCU 心跳保活、SD 卡只读挂载、USB 串口交互终端（DWC3 `g_serial`）、BCM43455 SDIO Wi-Fi，以及基于 BusyBox 的实时系统看板与工具集。

目标：从卡上启动 Linux 内核，接管整台相机；执行 `reboot` / `poweroff`（经 SP805 复位）或拔电池重启即可回到原厂系统（不改闪存）。思路是像 kexec 一样把原厂系统停掉，不是和它并存：原厂的两个 A7 核都在用（见下）。

## 现状

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 静态分析：看门狗、EX MCU 心跳、从核、GIC、计时器、内存映射、显示 | 完成（本页） |
| 1 | 交接：读文件 → 改 DTB → 关 EX MCU 心跳 → 停住 1 号核 → 关 SP805 → 关 MMU → 跳转 | 完成 |
| 1′ | 测试内核：按 zImage 的约定被启动，在 LCD 叠加层上画字、跑秒表 | 完成 |
| 2 | 真内核：Linux 6.18.65，tinyconfig + `z30.config`，无 VFP，BusyBox initramfs 内嵌 | 完成 |
| 3 | 叠加层帧缓冲驱动 `z30-overlay-fb`（RGB565 → YUV422 + alpha，fbcon） | 完成 |
| 3′ | 叠加层 earlycon `earlycon=z30ovl`，Z30 机器描述 | 完成 |
| 3″ | 双核 SMP：1 号核独立缓存行停车循环释放，`wonderlantern,z30-smp` | 完成 |
| 4 | SD 卡：Milbeaut SDHCI（SD0 `0x2A100000`，GIC 230）+ CRG 时钟门控，只读挂载 `/mnt/sd` | 完成 |
| 5 | USB 终端：Synopsys DWC3（`0x2AA00000`，GIC 236）`g_serial` + 主机端 `tools/z30_usb_term.py` | 完成 |
| 6 | Wi-Fi：SD1（`0x2A200000`，GIC 233）BCM43455 SDIO + `brcmfmac` + `tools/extract_wifi_fw.py` | 已接入 |
| 7 | 按键 / 触摸 | 未开始 |

## 用法

```powershell
python tools/build_card.py --source Z_30_0120.bin --output OUTDIR --patch-only --menu-tab --preset full linux
```

```powershell
python tools/build_linux_kit.py --output OUTDIR
```

第二条默认放测试内核；放真内核时加 `--kernel zImage`（initramfs 已经嵌在 zImage 里，`--initrd` 一般不需要）。把 `OUTDIR/Z30` 整个拷到卡上：`Z30/WL.PATCH`、`Z30/LINUX/ZIMAGE`、`Z30/LINUX/Z30.DTB`（可选 `Z30/LINUX/INITRD`）。

菜单第 8 个标签 → 实验 → Linux：

| 行 | 作用 |
| --- | --- |
| 启动 Linux | 交接。成功后不再返回，可执行 `reboot` / `poweroff` 或拔电池回到原厂 |
| 试运行 | 只读文件、读硬件参数、改 DTB 副本；值一栏显示 `OK K…K I…K` 或错误。不碰另一个核，不跳转。把读到的全部值写到 `Z30/LINUX/DRYRUN.TXT` |
| GIC … F… | GIC 分发器地址、计时器 MHz（只读 CPU 寄存器和原厂变量） |
| LCD … W… | 试运行后显示叠加层地址和看门狗 PrimeCell ID（应为 `1805`） |

常用配套：
- **USB 串口终端**（`g_serial.use_acm=0`，Windows 绑定 WinUSB 驱动后使用）：`python tools/z30_usb_term.py`（支持 `--cmd` 单条命令与 `--push` 推送文件）。
- **Wi-Fi 固件提取**：`python tools/extract_wifi_fw.py` 从原厂固件提取 BCM43455 固件和 Nikon Type 1MW NVRAM；在卡上放入 `Z30/LINUX/WIFI.CONF`（`wpa_supplicant` 配置）后开机后台自动连接并启动 `telnetd`。
- **自定义脚本**：可选在卡上放置 `Z30/LINUX/AUTORUN.SH` 覆盖默认 LCD 看板（`z30-dash`）。

## 实机试运行（linux-v3-testkernel，2026-11-26）

第一次上机，只按了"试运行"。`DRYRUN.TXT`：

| 项 | 相机读数 | QEMU | 结论 |
| --- | --- | --- | --- |
| 状态 | `OK K18K I0K` | 同 | 读文件、读硬件、改 DTB 都成功 |
| MPIDR | `80000F00` | `80000000` | 和跑分一致 |
| CBAR / GICD / GICC | `04300000` / `04301000` / `04302000` | `2C000000` / `2C001000` / `2C002000` | **和 QEMU 不同**。GIC 在设备区 `0x04000000`–`0x043FFFFF` 里。交接时从原厂变量读、运行时填进 DTB 的做法是必要的 |
| CNTFRQ | `02DC6C00`（48 MHz） | 同 | 通用计时器假设成立 |
| SP805 PeriphID0/1 | `1805` | 未读 | **确认是 ARM SP805** |
| SP805 CONTROL | `0` | 未读 | 此刻计数器和复位都没开。原厂可能只在某些状态下才启用看门狗；交接时写 0 也无害 |
| LCD 尺寸、叠加层格式 | `01E002D0`（720×480）、`00030016` | 同 | |
| 颜色 / alpha 平面 | `ECCA7000` / `ECD4FC00` | 同 | **和 z30-qemu 完全一致**，叠加层驱动和 earlycon 的假设成立 |
| 行距 | `5A0` / `2D0`（1440 / 720） | 同 | |
| SD0 PRESENT | `01FF0000` | 未读 | 卡已插入且稳定，没有写保护，CMD、DAT0–3 线都是高电平 |
| SD0 CAPS | `21EA0000` | 未读 | 最大块 2048 字节，ADMA2、SDMA、高速、挂起 / 恢复、3.3 V，异步中断。**不支持 1.8 V**（所以没有 UHS）。**基础时钟和超时时钟字段都是 0** |
| SD0 CR_SET / CDR_SET / WP_CD_LED | `0` / `1` / `6` | 未读 | `CR_SET` 为 0，这正是 CAPS 基础时钟为 0 的原因；`WP_CD_LED` 的 bit2 和主线驱动写的 LED_INV 一致，bit1 是原厂插卡后置的 |
| SD1 | 全 0，`WP_CD_LED` 为 4 | 未读 | 控制器 1 此刻没时钟或在复位中，符合"Wi-Fi 没开"的推断 |
| DTB_PATCHED | `00001FFF` | 同 | 13 个占位值全部替换 |

**SD 时钟：** CAPS 和 `CR_SET` 都没有给出基础时钟，原厂驱动是用自己知道的频率算分频的。原厂 SD 驱动的文字池里没有直接的频率常数（25 / 50 / 100 / 200 MHz 等都没找到）；`+0x2C` 的几处 `strh` 写的是内存结构体（`0xC18015E8`），不是寄存器。

两个办法：
- **v4 的试运行额外读控制器 0 的主机控制（`+0x28`）、时钟控制（`+0x2C`）、主机控制 2（`+0x3E`）和版本（`+0xFE`）。** 原厂此刻正用某个分频驱动着卡，结合高速位就能反推基础时钟。
- **给 Linux 报一个偏大的基础时钟是安全的。** Linux 按它算出的分频偏大，实际 SD 时钟只会偏慢、不会超频；初始化时钟会低于 400 kHz，仍在 SD 规范允许的范围内。

## 静态分析的结论

### 看门狗：ARM SP805，`0x0426C000`

- 喂狗：`0x10054C58` 往 `+0x00`（LOAD）写 `超时秒数 × 50 000 000`，计数时钟 50 MHz。
- 中断 44（`WATCH_DOG_TIMER`）的处理函数 `0x10054870`：打印 `**** !! watchdog !! ****`，写 `+0x0C`（INTCLR），再写 `+0xF00`（ITCR）= 1、`+0xF04`（ITOP）= 1，用集成测试模式强制复位。这是 SP805 的寄存器布局。
- 原厂调试命令行有 `watchdog 0/1`（命令表 `0x101E23C8`，处理函数 `0x101E1EDD`），但这一版固件里暂停 / 恢复都是空函数（`0x10054CE6`、`0x10054CE8`）。
- 原厂从不写锁寄存器 `+0xC00`。交接时先写 `0x1ACCE551` 解锁，再写 `CONTROL(+0x08) = 0`。主线 Linux 有 `sp805_wdt`，以后可以改成交给它。

### 两个核

- 原厂静态任务表 `0x1206AC88`：286 项，每项 28 字节（名字 12、核心、优先级、栈、属性）。核心字段 1 有 118 个（`APPMENU` 等菜单任务，跑分实测在 0 号核），2 有 164 个（`OPENVG`、`GRAPHIC`、`IMP*` 成像、`LV*` 取景），0 有 4 个。推断字段 1 = 0 号核，字段 2 = 1 号核。**两个核都被原厂占用。**
- 从核开机等待区：`0x1002BF9C`，在 `WFE` 里等 `[0x04008FFC] == 0xBABEBABE`，然后清零并跳到 EG 入口 `0x100200AC`。
- 核间中断：中断处理表 `0xC2A1C7EC`（每个中断号一项），SGI 1 一项在 `0xC2A1C7F0`，原厂入口 `0x100527A8`。这个入口是 ARM 汇编，自己切 IRQ / SVC 模式，不是普通函数。卡加载器的跨核缓存同步用的就是它（已上机）。
- 发 SGI：原厂写 `[0xC18EB078] + 0xF00`（GICD_SGIR）。

**停住 1 号核的做法：** 把 SGI 1 一项换成 `lowlevel.S` 的 `lb_sgi1`，向 1 号核发 SGI 1。`lb_sgi1` 只在"1 号核 + 已布防"时进入停车循环：关中断、按 set/way 清 L1、置"已停住"、在 `WFE` 里等释放地址（留给以后的 SMP）。其它情况所有寄存器原样恢复，跳回原厂入口。300 ms 内没停住就把原厂入口换回去，什么都不做。

### GIC、计时器

- GIC 地址在运行时变量里：`[0xC18EB074]` = CPU 接口，`[0xC18EB078]` = 分发器（初始化数据里是 0，启动时填）。原厂节拍定时器的中断号在 `[0xC18EB07C]`。交接时读这两个变量写进 DTB。QEMU 里是 `0x2C001000` / `0x2C002000`，CBAR `0x2C000000`。**相机上的值要试运行时读。**
- EG 入口 `0x100200D4` 设 `CNTFRQ = 48 000 000`；原厂到处读 CNTPCT 除以 48 当微秒用。所以 ARM 通用计时器在走，DTB 里用 `arm,armv7-timer`，48 MHz。
- 跑分 v4 已实测：处于安全态（SCR 可读）。Linux 在安全态 SVC 模式启动。

### 内存（开机页表 `0x10030000`）

| 物理地址 | 原厂属性 | 推断用途 |
| --- | --- | --- |
| `0x04000000`–`0x043FFFFF` | 强序（设备） | 启动 SRAM、看门狗、系统寄存器 |
| `0x20000000`–`0x2FFFFFFF` | 强序（设备） | 外设：显示 `0x20140000`、GIC |
| `0x80000000`–`0x800FFFFF` | 强序 | QEMU 记录原厂把内嵌程序搬到 `0x80100000`、`0x80300000`（ARM926 / DSP 推断） |
| `0x8A600000`–`0xBF2FFFFF` | 有缓存 | 约 845 MB，推断是图像缓冲 → **给 Linux** |
| `0xC1800000`–`0xC79FFFFF` | 有缓存 | 原厂 RTOS 堆（我们的载荷、停住的 1 号核的代码和页表都在这里） |
| 其余 `0x80000000`–`0xFFFFFFFF` | 无缓存 / 有缓存 | DMA 区、EG 副本（`0xCC400000`） |

DRAM 是 `0x80000000`–`0xFFFFFFFF`，2 GB。Linux 的内存定为 `0x90000000`–`0xBCFFFFFF`（720 MB，128 MB 对齐，满足 AUTO_ZRELADDR）。固定位置：内核 `0x92000000`（解压到 `0x90008000`），initrd `0x98000000`，DTB `0x9FF00000`。

### 显示（z30-qemu 模型）

- 显示控制器基址 `0x20140000`，每个实例 `0x4000`；LCD 是 1 号实例 `0x20144000`。
- 视频层：NV12 / NV16，地址 `+0x210`、`+0x220`（按 `+0x260` 选组），行距 `+0x240`、`+0x250`。
- GUI 叠加层：格式 `+0x2200`（QEMU 里 `0x30016`），尺寸 `+0x204`（`0x01E002D0` = 720×480），颜色平面 `+0x2440`，每 2 像素 4 字节 `Y0 Y1 U V`；alpha 平面 `+0x24C0`，每像素 1 字节；行距 `+0x2500`、`+0x2540`。
- 交接时读这些寄存器写进 DTB。Linux 不改显示控制器，只往叠加层里写。

## 交接代码

| 文件 | 内容 |
| --- | --- |
| `modules/linuxboot/linuxboot.c` | 读文件、读硬件、改 DTB 副本、停 1 号核、关看门狗、搬运、跳转 |
| `modules/linuxboot/lowlevel.S` | set/way 清 L1、关 MMU 跳转、停车循环、SGI 1 入口（ARM） |
| `modules/linuxboot/testkernel/` | 测试内核（`entry.S`、`main.c`、5×7 字库生成器） |
| `tools/build_linuxboot.py` | 生成 `features/experimental/linuxboot_payload.py` |
| `tools/build_linux_kit.py` | 生成 DTB（自带写出器，不需要 dtc）和卡上的 `Z30/LINUX` |
| `features/experimental/linuxboot.py` | 菜单页，页号 978，文字槽 71、75、76、78、83、99、115 |
| `tools/probe_linux_qemu.py` | QEMU 验证 |
| `tests/test_linux_kit.py` | DTB 结构、占位值与载荷一致、内存布局 |

DTB 占位值是大端字 `0x5A3000xx`，交接时在 DTB 的**副本**上替换（原始的留着，可以反复试运行）。

## QEMU 验证（`tools/probe_linux_qemu.py`）

先 `--prepare`，再在 WSL 里运行。QEMU 没有卡，探针把测试内核和 DTB 放进原厂内存池，并打开载荷的测试字段 `injected`。z30-qemu 没有 SP805 模型，所以探针还打开 `skip_wdt`（相机上恒为 0）。

结果（2026-11-26）：

- 页面：`GIC 2C001000 F48`。
- 试运行：`OK K18K I0K`。10 个占位值全部替换（`patched=0x3FF`）。读到叠加层 720×480，颜色 `0xECCA7000`，alpha `0xECD4FC00`，行距 1440 / 720，格式 `0x30016`。
- 启动：0 号核的 PC 在测试内核里（`0x920005EC`），1 号核的 PC 在载荷的停车循环里（`0xC2C437F0`）。测试内核的签名显示：找到叠加层、DTB 在 `0x9FF00000`、CNTFRQ 48 MHz、主循环在跑。叠加层截图见 `z30-qemu/.local/linux-probe/overlay.png`。

调试中修掉的问题：
- 信息行最初在画页面时就读显示寄存器。现在只读 CPU 寄存器，外设寄存器留到试运行。
- 叠加层"已开启"位 `+0x2110` QEMU 没有建模。改成用格式寄存器和两个平面地址来判断。
- DTB 最初在原件上替换，第二次试运行就找不到占位值了。现在改在副本上替换。

**QEMU 证明不了的：** 相机上 SGI 1 是否送到 1 号核；GIC 地址、显示寄存器偏移是否和 QEMU 一样；SP805 的判断；副控制器会不会在主 CPU 不再理它之后断电。

## 真内核

### 构建（WSL，2026-11-26）

下载（已校验 SHA-256，未验 GPG 签名），都放在 WSL 的 `~/z30-linux`，不在仓库里：

| 文件 | 来源 | 大小 |
| --- | --- | --- |
| `linux-6.18.65.tar.xz`（最新长期支持版） | cdn.kernel.org | 155 MB |
| `armv5-eabi--musl--stable-2026.08-1.tar.xz`（GCC 15.3） | toolchains.bootlin.com | 82 MB |

**为什么是 ARMv5 软浮点工具链：** 这颗 A7 没有 FPU，常见的 `gnueabihf` 硬浮点用户态程序会触发未定义指令。ARMv5 软浮点的 musl 程序在 ARMv7 上照样能跑；内核自己用 `-march=armv7-a -msoft-float` 编译，不受工具链默认值影响。不需要 sudo，也不需要 cpio（initramfs 由内核自带的 `gen_init_cpio` 生成）。

```powershell
wsl -e sh modules/linuxboot/kernel/build.sh ~/z30-linux/src/linux-6.18.65 ~/z30-linux/toolchain/armv5-eabi--musl--stable-2026.08-1/bin/arm-buildroot-linux-musleabi- ~/z30-linux/build
```

| 文件 | 内容 |
| --- | --- |
| `modules/linuxboot/kernel/z30.config` | 合并到 `tinyconfig` 上的配置：ARMv7 多平台、`ARCH_VIRT`（带进 GIC 和通用计时器）、SMP（2 核）、无 VFP、AEABI、AUTO_ZRELADDR、XZ、initramfs、VT + fbcon、Tux |
| `modules/linuxboot/kernel/z30-overlay-fb.c` | 叠加层帧缓冲驱动，构建时拷进 `drivers/video/fbdev` 并加 Kconfig / Makefile 项 |
| `modules/linuxboot/kernel/z30-earlycon.c` | 叠加层 earlycon 和 Z30 机器描述（`.reserve`、`.map_io`） |
| `modules/linuxboot/kernel/z30-smp.c` | 1 号核的启动方法 `wonderlantern,z30-smp` |
| `modules/linuxboot/kernel/init.c` | `/init`：挂 proc / sysfs / devtmpfs，打印 `uname`、`/proc/cpuinfo`、内存、中断表，mmap `/dev/fb0` 画定点 Mandelbrot，然后每秒打印运行时间 |
| `modules/linuxboot/kernel/build.sh` | 拷驱动、编 `/init`、生成 initramfs 列表、tinyconfig + 片段、检查每一项配置都生效、编 zImage |

结果：zImage 约 810 KB（带 SMP 和 earlycon）；内核和 `/init` 里都没有一条 VFP 指令（`/init` 的属性是 ARMv5TEJ、无 FP）。

### 叠加层帧缓冲驱动

- 对 Linux 来说是一块普通的 RGB565 帧缓冲（720×480），放在系统内存里（`FBINFO_VIRTFB`）。
- fbcon 绘制和 mmap 写入都走 fbdev 的延迟写回（`FB_GEN_DEFAULT_DEFERRED_SYSMEM_OPS`），记录脏矩形；工作队列每 40 ms 最多一次，只把脏矩形转换成叠加层的 `Y0 Y1 U V`（BT.601 有限范围，整数运算），alpha 平面开机时整屏写 255。
- 叠加层地址、尺寸、行距来自交接时打进 DTB 的实测值，用 `memremap(MEMREMAP_WC)` 映射。显示控制器完全不动。

### DTB 的 CPU 节点

`cpu` 节点的 `reg` 也是占位值（`0x5A30000B`），交接时填入启动核的 MPIDR[23:0]。相机上应为 `0xF00`（跑分实测 MPIDR `0x80000F00`），QEMU 里是 0。最初写死 `0xF00` 时，QEMU 里的内核会报 `DT missing boot CPU MPIDR` 并退回默认值。

### QEMU 结果（`--system-map` 模式）

```powershell
python tools/probe_linux_qemu.py --prepare --source Z_30_0120.bin --resident output/bench-v2 --out build/linux/card-kernel --kernel build/linux/zImage
```

然后在 WSL 里：`python3 tools/probe_linux_qemu.py --qemu-root ../z30-qemu --card build/linux/card-kernel --output-dir linux-kernel-probe --system-map build/linux/System.map --seconds 90`。探针按 `System.map` 找到 `__log_buf`（虚拟 `0xC0000000` = 物理 `0x90000000`）读内核日志，再把叠加层转成 PNG。

内核日志要点：

- `arch_timer: cp15 timer running at 48.00MHz (phys)`；
- `Memory: 726132K/737280K available`；
- `CPU: All CPU(s) started in SVC mode`；
- `z30-overlay-fb overlay-fb: fb0: 720x480 RGB565 -> overlay YUV422 at ecca7000, alpha ecd4fc00`；
- `Run /init as init process`。

`/proc/interrupts` 里 `arch_timer` 在 GIC 29 号中断上计数。修掉的问题：驱动最初漏了 `FBINFO_VIRTFB`，前几次 fbcon 绘制被跳过（`framebuffer is not in virtual address space`）。

### 叠加层 earlycon（`earlycon=z30ovl keep_bootcon`）

相机上没有串口。如果内核在 fbcon 起来之前出问题，就什么也看不到。earlycon 从解析启动参数那一刻起，就把内核日志直接画进叠加层。思路参考主线的 EFI 帧缓冲 earlycon（`drivers/firmware/efi/earlycon.c`）。

ARM 没有 late fixmap：`early_ioremap_reset()` 之后再调 `early_ioremap()` 会触发 `BUG()`。所以 earlycon 分三个阶段，由 Z30 机器描述（`DT_MACHINE_START`，匹配 `wonderlantern,z30`）的钩子切换：

| 阶段 | 时间 | 做法 |
| --- | --- | --- |
| 早期 | 注册到 `.reserve` | 每条扫描线 `early_ioremap` 一次（设备内存，不经缓存，显示控制器马上能看到） |
| 暂存 | `.reserve` 到 `.map_io` | 写进 8 KB 缓冲 |
| 静态 | `.map_io`（`paging_init` 里）之后 | `iotable_init` 在 `0xF8000000` 永久映射两个平面，先补画暂存的内容 |

- 写到底部后回到顶部，不做滚屏（滚屏要回读整个平面）。新写的一行下面清出一行空白，标出最新位置。
- 叠加层参数直接从扁平 DTB 的 `overlay-fb` 节点读，不需要 `reg`。
- `keep_bootcon` 让它一直工作，直到帧缓冲驱动 probe 时调用 `z30_earlycon_stop()` 注销它，两边不会同时写屏。
- 内核要选上 `SERIAL_CORE`、`SERIAL_CORE_CONSOLE`、`SERIAL_EARLYCON`（`build.sh` 写进 Kconfig 项）。
- `tools/build_linux_kit.py --earlycon-only` 把 `overlay-fb` 标成 `disabled`，帧缓冲驱动不加载，整个启动都留在 earlycon 上。QEMU 里用它验证过：整屏都是 earlycon 画的日志。

### 双核

1 号核停在交接的停车循环里。释放流程：

1. **停车前先 EOI。** 原厂中断分发器已经应答了 SGI 1，但永远不会把它结束掉。如果不补这一步，这个核的 GIC CPU 接口会一直停在 SGI 的运行优先级，以后 Linux 在这个核上收不到任何中断。所以停车前先写 `GICC_EOIR = 1`（停车状态字的第 4 个字保存 GICC 基址）。
2. **等释放。** 在 WFE 里等释放字非零；之后清 L1，关 MMU、D-cache、I-cache，失效 TLB、I-cache 和分支预测器，跳到释放地址（ARM 状态）。
3. **DTB 描述。** DTB 里有两个 `cpu` 节点，`reg` 分别是 MPIDR[23:0] 和它的 Aff0 = 1 版本（占位 `0x5A30000B`、`0x5A30000C`），`enable-method = "wonderlantern,z30-smp"`。1 号核的 `cpu-release-addr` 是停车状态字里的释放字（占位 `0x5A30000D`）。
4. **内核这边。** `z30-smp.c` 用 `CPU_METHOD_OF_DECLARE` 注册这个方法：`ioremap` 释放字所在的页（在原厂堆里，Linux 内存之外），写入 `secondary_startup_arm` 的物理地址（内核是 Thumb-2 编译，ARM 状态入口用这个），然后 `dsb_sev()`。

QEMU 日志：`CPU1: thread -1, cpu 1, socket 0, mpidr 80000001`、`smp: Brought up 1 node, 2 CPUs`。`/proc/interrupts` 里两个核的 `arch_timer` 都在计数，说明 1 号核的 GIC 是好的。`/proc/cpuinfo` 的 Hardware 一行是 `Nikon Z30 (Wonder Lantern)`，说明机器描述被选中了。`/init` 把同一块 Mandelbrot 先用 1 个进程、再用 2 个进程（fork，奇偶行分开）各算一遍，打印加速比（QEMU 里的时间没有参考意义）。

探针改动：两个核都开着 MMU 以后，GDB 不能再按物理地址读内存，所以探针在读日志前打开 QEMU 的 `Qqemu.PhyMemMode:1`。

### SD 卡控制器（静态分析）

| 项 | 结论 | 依据 |
| --- | --- | --- |
| IP | Socionext Milbeaut 的 SDHCI 3.0（F_SDH30 加 Milbeaut 桥接寄存器），即主线的 `sdhci-milbeaut`（`socionext,milbeaut-m10v-sdhci-3.0`） | 原厂初始化 `0x102AA25E` 依次写 `+0x200`（MLB_SOFT_RESET：0，最后写 1）、`+0x230`（MLB_CDR_SET）、`+0x210`（MLB_WP_CD_LED_SET），和主线驱动的桥接寄存器一致 |
| 控制器 0 | `0x2A100000`，GIC 230（SPI 198），**卡槽** | 原厂 30 处引用；`0x102AA2D4` 注册中断 `0xE6`；z30-qemu 把卡接在这里，原厂插卡检测函数读它 |
| 控制器 1 | `0x2A200000`，GIC 233（SPI 201），推断是 SDIO 接的 Wi-Fi（BCM43455） | 原厂 28 处引用；`0x102AA270` 注册中断 `0xE9`；初始化走另一条路径（`0x102C5DB4`）。Wi-Fi 的判断只是推断 |
| 插卡检测 | present-state（`+0x24`）bit16；插卡后把 `+0x210` 的 bit1 置上 | `0x104157DC` |
| DMA | ADMA2，原厂只在厂商版本号为 1 时启用 | z30-qemu 对 `0x10415E12` 一带的注释 |

**和主线驱动不一样的地方：**

| 寄存器 | 原厂 | 主线 `sdhci-milbeaut` |
| --- | --- | --- |
| `+0x230` MLB_CDR_SET | 低 4 位 = 1 | 3（CLK2POW16） |
| `+0x210` MLB_WP_CD_LED_SET | 清 bit8、bit[1:0]，置 bit16、17、9 | 只写 bit2（LED_INV） |
| `+0x220` MLB_CR_SET | 这段初始化里没写 | 按 `core` 时钟频率算出超时时钟和基础时钟后写入 |

### EX MCU 心跳保活、SD、USB 与 Wi-Fi

- **EX MCU 心跳关闭**：副控制器（Cortex-M，McuMgCy2 1 s 定时器）经 EXMCU channel 1（cmd `0x0b`）定期轮询主控，主控停止应答约 16 s 后会触发复位。交接前调用原厂 McuMg API（`0x10059821`）发送 channel 1 cmd `0x0a`（`enable=0`）并等待 100 ms 让 SPI0 发送完成，即可清除心跳监控。
- **双核缓存一致性**：`lowlevel.S` 的停车状态字 `lb_park_state` 独占 64 字节缓存行（`.p2align 6`），写入与轮询释放地址时执行 `DCCIMVAC` / `DCIMVAC`，确保 1 号核在实机缓存开启状态下可靠接收 Linux 写入的 `secondary_startup_arm`。
- **SD0（卡槽，`0x2A100000`，GIC 230 / SPI 198）**：Milbeaut SDHCI 3.0（`socionext,milbeaut-m10v-sdhci-3.0`），原厂 HS 50 MHz、`DIV=0` 对应 `core_clk = 200 MHz`（`fixed-clock`），`z30,cd-inv` 补丁在 CRG（`0x0428100C` / `0x0428123C`）打开时钟门控并保留原厂 `CDR_SET=1` 与 `WP_CD_LED_SET=6`。用户态默认以只读方式挂载到 `/mnt/sd`，防止断电损坏卡数据。
- **SD1（SDIO Wi-Fi，`0x2A200000`，GIC 233 / SPI 201）**：BCM43455 / Murata 1MW。交接前 `linuxboot.c` 自动调用原厂 `0x103c7c70` 通知 EX MCU 打开无线供电轨并等待 300 ms 稳定（无需在原厂菜单先开 Wi-Fi）；内核 `z30,sdio` 补丁保持 ARM926 复位（`0x0428105C` bit 0）、主动配置并拉动 `WL_REG_ON`（GPIO `0x5b`，`0x04282114` / `0x04282014` bit 14）做冷启动/硬复位脉冲并打开 SD1 时钟（`0x0428123C` bit 6），配合 `brcmfmac`、`tools/extract_wifi_fw.py` 提取的固件/NVRAM 以及用户态 `wifi-ctl`（`on`/`off`/`restart`/`status`）直接在 Linux 内开关 Wi-Fi。
- **USB 串口终端（`0x2AA00000`，GIC 236 / SPI 204）**：Synopsys DWC3 外设模式（USB 2.0 High-Speed），内核启用 `g_serial`（`g_serial.use_acm=0`），`rc_init.sh` 在 `/dev/ttyGS0` 上运行交互 shell，主机端使用 `tools/z30_usb_term.py` 连接。
- **用户态工具（`modules/linuxboot/kernel/tools/`）**：`rc_init.sh`（PID 1）、`z30-dash`（LCD 实时系统看板）、`htop`、`cmatrix`、`tty_clock`、`neofetch`、`z30-power`（`reboot`/`poweroff`/`halt`/`shutdown`，同步并卸载 `/mnt/sd` 后经 SP805 复位）。
- **QEMU 选项**：z30-qemu 未建模 Milbeaut SD 桥接寄存器与 DWC3，QEMU 回归时使用 `tools/build_linux_kit.py --no-mmc --no-usb`。

## 下一步

1. **按键 / 触摸输入**：逆向副控制器或 I2C 输入链路，接入 Linux `evdev`。
2. **Wi-Fi 实机稳定性与网络工具完善**。
