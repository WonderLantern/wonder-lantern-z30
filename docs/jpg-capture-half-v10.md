# v10 实现说明

在 v9 的 worker 中，80 ms 延时与半按/全按检查之后、静音/原生 phase/回调槽检查之前，插入准入等待：

- `pre_gates(state, log)` 依原厂 0x1020A630 顺序读取：attr 0x1B2E（非0 → 0x20）、0x1020BBC4（==0x12 → 0x20）、0x1020BC2E、0x1006316E、0x10063166、0x10065964（非0 → 0x1D）、0x1020BAEC（非0 → 0xD）。均为互斥保护的只读 getter（已反汇编确认）。
- 另记录 0x101F5204(0x11B)：==1 时原厂入口不使用请求，只登记 slot 0x46 后返回 0；以及 0x1020BB8A 原生 phase。二者只记录，不参与等待。
- 日志：kind8 = (bbc4 | bc2e<<8 | baec<<16 | 65964<<24, attr1B2E)，kind9 = (6316E, 63166)，kind10 = (attr11B, BB8A)。第一次轮询和提交前各写一组。
- 未满足则 100 ms 后重查，最多 15 次；每次重查前确认仍启用、仍半按且未全按，否则按 v9 的 stale/disabled 退出，不提交。
- 满足记 kind5 step15，超时记 step14；两者之后都执行 v9 原有检查并只提交一次 20 字节请求（+0x10=1）。

DBG 格式、大小（1884 字节）、状态布局与 v9 相同；`tools/read_precapture_probe.py` 已能解析新事件。
参考：docs/z7ii-precapture-reference.md。
