# 跑分：卡文件里的处理器跑分

> 位于 `experimental` 分支，不包含在 `full` 中。

目的：摸清 EXPEED 6 里跑原厂固件的那颗应用处理器的底：什么核、几个核、主频、每周期能做几条整数运算、VFP/NEON 有多快、内存带宽和延迟。整个跑分是 `WL.PATCH` 里一段约 20 KB 的 C 加汇编代码，常驻 EG 不变、不重刷；拔卡或换回普通卡文件就回到原样。

## 用法

1. 按相机上的常驻 EG 打卡文件：`tools/build_card.py --source Z_30_0120.bin --output OUTDIR --patch-only --menu-tab --preset full bench`（`OUTDIR` 里放相机上那份 `EG209020.BIN` 和 `.json`），把 `Z30/WL.PATCH` 放到卡上。
2. 菜单第 8 个标签 → 实验 → 跑分（Benchmark）→ 开始跑分。跑的时候“开始跑分”一行显示进度（`Running 12/22`），大约几秒；跑完显示 `Done 4s, BENCH.TXT`。
3. 全部数字在卡上的 `Z30/BENCH.TXT`。

| 行 | 内容 |
| --- | --- |
| 开始跑分 | 进度和结果；离开页面会停下（`Stopped`），再按重新跑 |
| CPU | 核心型号和版本、簇里的核数（L2CTLR）、实测主频；没跑之前显示当前任务所在的核 |
| Integer | 4 条独立加法链的每周期指令数（IPC），查表 CRC32 的 MB/s |
| FPU / NEON | NEON 和标量 VFP 单精度乘加的 GFLOPS |
| Memory | DRAM 读 / 写带宽（ldm 和 NEON 中取较快的）和随机访问延迟 |

## BENCH.TXT 里有什么

- 识别：MIDR、MPIDR、CTR、CLIDR、ID_PFR0/1、ID_DFR0、ID_ISAR0、L2CTLR、PMCR；L1D / L1I / L2 的容量、路数、行长；是否有硬件除法。
- 主频：PMU 周期计数器对 SYSTIM 毫秒计时 10 ms，测 5 次取中位数。
- 整数：加法依赖链 / 4 链、乘法依赖链 / 4 链（周期每条、每周期条数、Mop/s），CRC32 查表，Q16.16 定点 Mandelbrot（Miter/s）。
- 内存：L1（8 KB）、L2（L2 的一半，最多 512 KB）、DRAM 的读带宽，DRAM 写和拷贝带宽；L1 / L2 / DRAM 的指针追逐延迟（随机环，64 字节一行），周期和纳秒。
- FPU / NEON：FPSID、MVFR0/1、进入时的 CPACR / FPEXC；VFP f32、f64 乘加，NEON f32 乘加（有 VFPv4 时也测融合乘加 vfma），NEON i16 乘加，NEON 读 / 写 / 拷贝 DRAM。

整数和内存部分跑完先写一次文件，FPU 部分跑完再写一次。万一 FPU 部分出问题，卡上仍有前半部分的结果（文件第二行写着 `partial`）。

## 怎么做到不影响相机

- **分步跑**：和掌机一样借原厂菜单定时器第 45 号槽，每个节拍最多干 16 ms 的活再交还菜单任务，所以菜单不会卡死。
- **计时**：每个计时块关中断，临时打开 PMU 周期计数器，测完把 PMCR、PMCNTENSET 恢复原值。每个测试先把工作量翻倍校准到约 40 万周期，再跑 7 次取最好的一次。没有 PMU 时退回毫秒计时（不关中断），主频按加法依赖链 1 条/周期估算，FPU 部分跳过。
- **FPU / NEON**：原厂代码在 `0x100356E4` 把 CP10/CP11 关掉（CPACR 清零 bit 20–23，并置位 ASEDIS/D32DIS），EG 里也找不到懒启用 VFP 的处理，所以不能指望内核替我们打开。每个 FPU 计时块在关中断时：打开 CPACR 和 FPEXC.EN → 保存 FPSCR 和全部 D 寄存器 → 跑内核 → 恢复寄存器、FPEXC、CPACR。CPACR 写不进去（非安全态被拒）就整段跳过。关中断也保证块内不会被换到另一个核（CPACR 是每核的）。
- **C 代码不碰 FPU**：C 部分按 ARMv7-A、无 FPU、无硬件除法编译，VFP/NEON 指令只在 `kernels.S` 的内核函数和开关函数里（构建后用反汇编确认过）。
- **内存**：从原厂内存池借 8 MB（不够就 4 MB、2 MB）做测试缓冲，跑完或停下就还掉；8 KB 报告缓冲留到关机。

## 实现

| 部分 | 位置 |
| --- | --- |
| 跑分状态机、计时、报告 | `modules/bench/bench.c` |
| 计时内核、FPU 开关 | `modules/bench/kernels.S` |
| 构建 | `modules/bench/build.sh`（检查无重定位、位置无关），`tools/build_bench.py` 生成 `features/experimental/bench_payload.py` |
| 菜单页 | `features/experimental/bench.py`，页号 977；文字槽 25、27–29、56–58、69（空闲槽不连续，框架新增按行指定标签槽 `LABEL_SLOTS`） |
| 预设 | `bench`，在 `EXPERIMENTAL_FEATURES`，不在 `full` |

## 验证

- 构建：`-Werror` 编译通过；无重定位、位置无关；FPU 指令只出现在 `kernels.S` 的函数里。
- 单元测试：`test_gui`、`test_menu_framework`、`test_card`、`test_card_loader` 通过。
- QEMU（`tools/probe_bench_qemu.py`，先 `--prepare` 再在 WSL 里运行）：原厂固件上进跑分页 → 开始跑分 → 由菜单定时器跑完全部 22 项，PASS。CP15 识别（QEMU 模型为双核 Cortex-A15 r4p0）、PMU 主频、CPACR 进入时为 `C0000000`（CP10/CP11 关闭，和静态分析一致）、FPU 保护块执行 VFP/NEON、定时器槽归还、报告生成都正常；QEMU 没有卡，所以写文件显示 `no file`，报告文本从内存取出。QEMU 的数字是模拟器的，没有参考意义。
  - 第一版只更新文字、不重画，屏幕停在 `Running 0/22`；现在节拍里先把各行和行组标脏再重画（同掌机），并且只在文字变化时重画。每秒截图里，某一行文字刚变化的那一帧偶尔是空的，下一帧恢复（重画中途被截到）。
- 实机：已验证（bench-v2 至 bench-v4，见下文实机结果）。

## 实机结果（bench-v2，2026-11-26）

整套跑完不死机，用时 1.2 s，`BENCH.TXT` 写出。要点：

| 项 | 读数 | 含义 |
| --- | --- | --- |
| 核心 | Cortex-A7 r0p5（MIDR `410FC075`），簇内 2 核，任务在 0 号核 | MPIDR `80000F00`：簇号 15 |
| 主频 | 400 MHz（5 次 398–401） | 加法依赖链 1.008 周期/条，互相印证 |
| 缓存 | L1D 32 KB 4 路，L1I 32 KB 2 路；**CLIDR 没有 L2** | 128 KB 块的读带宽和延迟与 DRAM 相同，证实 L1 之外直接是 DRAM |
| 整数 | 加法 4 链 1.32 条/周期，乘法延迟 3、吞吐 1/周期；L1 延迟 3 周期 | 都符合 A7 的规格（部分双发射） |
| DRAM | 读 172 MB/s、写 800 MB/s（正好 2 B/周期）、拷贝 150 MB/s；随机延迟约 136 周期 / 340 ns | 读带宽 ≈ 64 B ÷ 340 ns，读几乎不重叠；写走写缓冲合并 |
| FPU | **CPACR 写不进 CP10/CP11**，FPU 部分全部跳过 | v3 读 NSACR 区分“没有 FPU”和“非安全态被禁” |

问题：v2 的 Mandelbrot 显示 0。GCC 把 `mandel_tile()` 当成纯函数，把 n 次调用合成一次乘法，计时里没有工作，n 一路翻倍到上限。v3 用 `noipa` 修好（QEMU 那个 0.60 也是假数）。

v3 还把没有 L2 时的两项改名为 `read 128K (no L2)`、`latency 128K`，CPACR 被拒时报告 CPACR 写入前后的值和 NSACR。

## 实机结果（bench-v3，2026-11-26）：没有 FPU / NEON

v3 的数字和 v2 一致（主频 399.9 MHz）。Mandelbrot 修好后为 19.19 Miter/s（20.8 周期/迭代）。

FPU：`CPACR before=00000000 after write=00000000 NSACR=00000000`。结论：**这颗 Cortex-A7 没有实现 VFP / NEON**。

- 原厂把 CP10/CP11 关掉的函数 `0x10035708` 由 EG 复位入口 `0x100200A4` 调用，每个核开机都会执行。它写 NSACR，而 NSACR 只能在安全态写（非安全态写会 UNDEF）；EG 里也没有写 SCR 的指令，所以 EG 一直在安全态。安全态不受 NSACR 限制，CPACR 的 CP10/CP11 写入仍然读回 0，只能是没有实现浮点单元。
- 原厂开机写入的 ASEDIS/D32DIS（`0xC0000000`）在相机上读回 0，同样是“未实现”的读零写无效；QEMU（带 FPU 的 A15）读回 `C0000000`。
- 早先“固件大量使用 NEON（8,123+ 条）”的静态扫描是把数据当成了指令（本页在菜单代码区复查只有零星伪匹配）。**任何 VFP/NEON 代码在相机上都会 UNDEF**，例如 LUT 研究 v7 的 NEON 查表核心（未上机）。性能工作只能用整数 / 定点，或者交给专用硬件。

## v4：直接测安全态

v3 的“没有 FPU”有一环是推断（EG 在安全态）。v4 在 FPU 部分前直接测：关 IRQ/FIQ，把 VBAR 临时指向自己的 64 字节向量表（未定义指令入口 `sec_undef` 在 r12 记标记并跳过该条 ARM 指令，其它入口停住），先执行 `udf #0` 自检拦截是否生效，再读 SCR（非安全态读 SCR 未定义），然后恢复 VBAR。SCTLR 的 V（高向量）或 TE（Thumb 异常）置位时不测。

- 安全态 + CPACR 拒绝 → 没有 FPU / NEON（屏幕显示 `No FPU/NEON (secure)`）。
- 非安全态 + NSACR 拒绝 → 可能有 FPU，被安全世界锁住（`FPU locked (non-secure)`）。

QEMU：自检拦截成功，安全态，`SCR=00000000`，跑分 PASS。非安全态分支 QEMU 走不到，它和 UDF 自检是同一个拦截路径。

实机（bench-v4，2026-11-26）：屏幕显示 `No FPU/NEON (secure)`。拦截自检通过、SCR 可读，EG 运行在安全态，CPACR 仍拒绝 CP10/CP11。**确认：这颗 Cortex-A7 没有实现 VFP / NEON。**
