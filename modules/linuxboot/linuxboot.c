/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Linux handoff: boot a Linux kernel (or the test kernel) from the card,
 * taking the whole camera over. Experimental branch (experimental).
 * docs/linux.md has the plan and the evidence for every address below.
 *
 *   1. read A:\Z30\LINUX\ZIMAGE, Z30.DTB and (optional) INITRD into pool memory;
 *   2. patch the DTB placeholders with what this camera reports at run time:
 *      GIC distributor / CPU interface, LCD overlay planes, initrd range;
 *   3. park core 1: its SGI 1 handler (stock IPI, slot 0xC2A1C7F0) is swapped for
 *      ours, an SGI 1 sent; it cleans its cache and waits in WFE with interrupts
 *      off. No answer within 300 ms: everything is put back and nothing happens;
 *   4. stop the SP805 watchdog (0x0426C000);
 *   5. copy the images to fixed addresses in the stock image-buffer area
 *      (kernel 0x92000000, initrd 0x98000000, DTB 0x9FF00000);
 *   6. lowlevel.S lb_jump: L1 clean, MMU / caches off, r0 = 0, r1 = ~0, r2 = DTB.
 *
 * "Dry run" does 1-2 and reports, without touching the other core or jumping.
 *
 * Entry: linuxboot(cmd, page, row)
 *   cmd 0: status text   cmd 1: row action (0 boot, 1 dry run)
 *   cmd 2, 3: info lines (rows 2, 3)
 */
#include <stdint.h>
#include <stddef.h>

typedef uint64_t u64; typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8; typedef int32_t s32;
#define FN(a, t) ((t)(uintptr_t)(a))
#define NATIVE_OK 0x04370080u
#define BOUNCE 4096u

/* Stock (Z30 C1.20) addresses, docs/linux.md */
#define GICC_VAR 0xc18eb074u          /* GIC CPU interface base (runtime) */
#define GICD_VAR 0xc18eb078u          /* GIC distributor base (runtime) */
#define IRQ_TABLE 0xc2a1c7ecu         /* handler per interrupt id */
#define SGI1_SLOT (IRQ_TABLE + 4u)
#define SGI1_STOCK 0x100527a8u        /* stock IPI handler (checked, as features/modules/cache.py) */
#define WDT 0x0426c000u               /* ARM SP805 */
#define EX_MCUMG_VTBL 0xc18e5270u     /* McuMg service vtable ptr (0xc18e5228 + 0x48) */
#define EX_HB_PARAMS 0xc2a1234cu      /* McuMg heartbeat config: {enable, interval|(timeout<<8), mode} */
#define LCD 0x20144000u               /* display controller, LCD instance */
/* SD host controllers: Socionext Milbeaut (F_SDH30) SDHCI 3.0 with bridge registers at
 * +0x200 (stock init 0x102AA25E). 0: card slot (IRQ 230), 1: inferred SDIO Wi-Fi (IRQ 233). */
#define SD0 0x2a100000u
#define SD1 0x2a200000u
/* USB 3.0 DRD controller: Synopsys DWC3 at 0x2AA00000 (IRQ 236 = SPI 204, stock ISR
 * 0x1031AD7E) with Socionext glue at 0x2AA20000 (stock init 0x1031B6BE). */
#define USB_DWC3 0x2aa00000u
#define USB_GLUE 0x2aa20000u
/* Destinations (stock image-buffer area 0x8A600000-0xBF2FFFFF, cached, identity mapped) */
#define KERNEL_AT 0x92000000u
#define INITRD_AT 0x98000000u
#define DTB_AT 0x9ff00000u
#define LIMIT_KERNEL (32u << 20)
#define LIMIT_INITRD (96u << 20)
#define LIMIT_DTB (64u << 10)

/* DTB placeholders (big-endian words written by tools/build_linux_kit.py). */
#define PH_GICD 0x5a300001u
#define PH_GICC 0x5a300002u
#define PH_COLOR 0x5a300003u
#define PH_ALPHA 0x5a300004u
#define PH_WIDTH 0x5a300005u
#define PH_HEIGHT 0x5a300006u
#define PH_CSTRIDE 0x5a300007u
#define PH_ASTRIDE 0x5a300008u
#define PH_INITRD_START 0x5a300009u
#define PH_INITRD_END 0x5a30000au
#define PH_MPIDR 0x5a30000bu          /* the cpu node's reg: MPIDR[23:0] of the booting core */
#define PH_MPIDR1 0x5a30000cu         /* core 1's: the same with Aff0 = 1 */
#define PH_RELEASE 0x5a30000du        /* core 1's cpu-release-addr: lb_park_state[1] */

#pragma GCC visibility push(hidden)
void lb_clean_dcache(void);
void lb_jump(u32 entry, u32 dtb) __attribute__((noreturn));
void lb_sgi1(void);                    /* ARM, lowlevel.S */
extern volatile u32 lb_park_state[4];  /* parked, release address, armed, GICC base */
#pragma GCC visibility pop

enum { P_IDLE, P_LOADED, P_DRY, P_BOOTING, P_ERROR };
enum { E_NONE, E_MEMORY, E_KERNEL, E_DTB, E_INITRD, E_SIZE, E_CORE, E_SGI, E_PARK, E_DISPLAY, E_PLACEHOLDER };

struct lb {
    u32 phase, error, detail;
    /* images (pool memory) */
    u8 *kernel, *dtb, *initrd; u32 kernel_size, dtb_size, initrd_size;
    u32 injected;                      /* test only: the QEMU probe placed the images itself */
    /* what this camera reports */
    u32 gicd, gicc, cbar, cntfrq, wdt_id, wdt_control;
    u32 lcd_size, color, alpha, cstride, astride, overlay_enable, overlay_format;
    u32 patched;                       /* placeholders replaced */
    u32 park_wait_ms;
    u32 skip_wdt;                      /* test only: z30-qemu models no SP805 and no SD bridge; set by the QEMU probe */
    u8 *work; u32 work_size;           /* patched copy of the DTB: the loaded one stays pristine */
    u8 *bounce;
    /* per controller: present state, capabilities, CR_SET, CDR_SET, WP_CD_LED_SET, then the words
     * holding host control (0x28), clock control (0x2C), host control 2 (0x3E) and version (0xFE) */
    u32 sd[2][9];
    u32 ex_hb[3];
    u32 usb[4];                        /* DWC3 GSNPSID, GCTL, GUSB2PHYCFG, glue 0x2AA20000 */
    u32 report_ok;
    char status[32], line[2][32];
};
static struct lb L;

/* ---- runtime ------------------------------------------------------------ */
__attribute__((optimize("no-tree-loop-distribute-patterns")))
void *memcpy(void *d, const void *s, size_t n) {
    u8 *a = d; const u8 *b = s;
    if (!(((uintptr_t)a | (uintptr_t)b | n) & 3)) {
        u32 *x = d; const u32 *y = s;
        for (n >>= 2; n; n--) *x++ = *y++;
        return d;
    }
    while (n--) *a++ = *b++;
    return d;
}
__attribute__((optimize("no-tree-loop-distribute-patterns")))
void *memset(void *d, int c, size_t n) { u8 *a = d; while (n--) *a++ = (u8)c; return d; }
void abort(void) { for (;;) __asm__ volatile("wfi"); }

static void *alloc(u32 n) { return FN(0x10211f4d, void *(*)(u32, u32))(n, 0); }
static void pool_free(void *p) { if (p) FN(0x10211f5f, void (*)(void *))(p); }
static int cache(void *p, u32 n, u32 op) {
    int r = FN(0x10035aac, int (*)(void *, u32, u32, u32))(p, n, op, 2);
    __asm__ volatile("dsb sy" ::: "memory");
    return r > 0;
}
static u32 ms(void) { u32 t[2] = {0, 0}; FN(0x1021cea3, void (*)(void *))(t); return t[1]; }
static inline u32 rd(u32 a) { return *(volatile u32 *)(uintptr_t)a; }
static inline void wr(u32 a, u32 v) { *(volatile u32 *)(uintptr_t)a = v; }
#define MRC(op1, crn, crm, op2) ({ u32 v_; __asm__ volatile("mrc p15, " #op1 ", %0, " #crn ", " #crm ", " #op2 : "=r"(v_)); v_; })
static inline u32 irq_off(void) { u32 f; __asm__ volatile("mrs %0, cpsr\n\tcpsid if" : "=r"(f) :: "memory"); return f; }
static inline void irq_restore(u32 f) { __asm__ volatile("msr cpsr_c, %0" :: "r"(f) : "memory"); }

static char *put(char *p, const char *s) { while (*s) *p++ = *s++; *p = 0; return p; }
static char *hex(char *p, u32 v, u32 digits) {
    for (u32 i = digits; i; i--) { u32 d = (v >> ((i - 1) * 4)) & 15; *p++ = (char)(d < 10 ? '0' + d : 'A' + d - 10); }
    *p = 0; return p;
}
static char *dec(char *p, u32 v) {
    char t[10]; u32 n = 0;
    do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v && n < 10);
    while (n) *p++ = t[--n];
    *p = 0; return p;
}

/* ---- files -------------------------------------------------------------- */
struct file { u32 obj[16]; };
static int f_close(struct file *f) {
    if ((s32)f->obj[2] < 0) return 1;
    int ok = FN(0x1033832f, u32 (*)(void *))(f->obj) == NATIVE_OK;
    f->obj[2] = ~0u;
    return ok;
}
static int f_open(struct file *f, const char *path) {
    for (u32 i = 0; i < 16; i++) f->obj[i] = 0;
    f->obj[2] = ~0u;
    FN(0x1033829f, void (*)(void *, const char *))(f->obj, path);
    u32 status = FN(0x103382a3, u32 (*)(void *, u32, u32))(f->obj, 0, 0);
    if (status != NATIVE_OK || (s32)f->obj[2] < 0) { f_close(f); return 0; }
    return 1;
}
static int f_read(struct file *f, u8 *dst, u32 n) {
    while (n) {
        u32 want = n > BOUNCE ? BOUNCE : n, got = 0;
        cache(L.bounce, BOUNCE, 3);
        u32 status = FN(0x10338415, u32 (*)(void *, void *, u32, u32 *))(f->obj, L.bounce, want, &got);
        if (status != NATIVE_OK || !got || got > want) return 0;
        cache(L.bounce, BOUNCE, 3);
        memcpy(dst, L.bounce, got);
        dst += got; n -= got;
    }
    return 1;
}
/* Read a whole file into fresh pool memory; 0 when missing (optional files), -1 on error. */
static int load(const char *path, u8 **out, u32 *size, u32 limit, u32 error) {
    struct file f;
    if (!f_open(&f, path)) return 0;
    u32 n = f.obj[6];
    if (f.obj[7] || !n || n > limit) { f_close(&f); L.error = E_SIZE; L.detail = error; return -1; }
    u8 *p = alloc(n + 64);
    if (!p) { f_close(&f); L.error = E_MEMORY; L.detail = n; return -1; }
    int ok = f_read(&f, p, n);
    if (!f_close(&f) || !ok) { pool_free(p); L.error = error; return -1; }
    *out = p; *size = n;
    return 1;
}
static void unload(void) {
    if (L.injected) return;
    pool_free(L.kernel); pool_free(L.dtb); pool_free(L.initrd);
    L.kernel = L.dtb = L.initrd = NULL; L.kernel_size = L.dtb_size = L.initrd_size = 0;
}
static int load_all(void) {
    if (L.injected) return L.kernel && L.dtb;
    unload();
    if (!L.bounce) {
        u32 raw = (u32)(uintptr_t)alloc(BOUNCE + 63);
        if (!raw) { L.error = E_MEMORY; return 0; }
        L.bounce = (u8 *)(uintptr_t)((raw + 63) & ~63u);
    }
    int r = load("A:\\Z30\\LINUX\\ZIMAGE", &L.kernel, &L.kernel_size, LIMIT_KERNEL, E_KERNEL);
    if (r <= 0) { if (!r) L.error = E_KERNEL; unload(); return 0; }
    r = load("A:\\Z30\\LINUX\\Z30.DTB", &L.dtb, &L.dtb_size, LIMIT_DTB, E_DTB);
    if (r <= 0) { if (!r) L.error = E_DTB; unload(); return 0; }
    r = load("A:\\Z30\\LINUX\\INITRD", &L.initrd, &L.initrd_size, LIMIT_INITRD, E_INITRD);
    if (r < 0) { unload(); return 0; }
    return 1;
}

/* ---- what the camera reports ----------------------------------------- */
/* CPU registers and stock variables only: safe whenever the page is drawn. */
static void probe_cpu(void) {
    L.gicd = rd(GICD_VAR); L.gicc = rd(GICC_VAR);
    L.cbar = MRC(4, c15, c0, 0);
    __asm__ volatile("mrc p15, 0, %0, c14, c0, 0" : "=r"(L.cntfrq));
}
/* Peripheral registers: only for a dry run or a boot. The display offsets are the
 * ones stock code programs (and z30-qemu models); 0x2110 (overlay enable) is not
 * read, a non-zero format and both plane addresses stand for "overlay in use". */
static void probe(void) {
    probe_cpu();
    if (!L.skip_wdt) {
        L.wdt_id = rd(WDT + 0xfe0) & 0xff;           /* SP805 PeriphID0 = 0x05 */
        L.wdt_id |= (rd(WDT + 0xfe4) & 0xff) << 8;   /* PeriphID1 = 0x18 */
        L.wdt_control = rd(WDT + 0x08);
    }
    if (!L.skip_wdt) {
        static const u16 sd_regs[9] = {0x24, 0x40, 0x220, 0x230, 0x210, 0x28, 0x2c, 0x3c, 0xfc};
        for (u32 c = 0; c < 2; c++)
            for (u32 i = 0; i < 9; i++) L.sd[c][i] = rd((c ? SD1 : SD0) + sd_regs[i]);
        L.usb[0] = rd(USB_DWC3 + 0xc120u);           /* GSNPSID (expect 0x5533xxxx) */
        L.usb[1] = rd(USB_DWC3 + 0xc110u);           /* GCTL */
        L.usb[2] = rd(USB_DWC3 + 0xc200u);           /* GUSB2PHYCFG(0) */
        L.usb[3] = rd(USB_GLUE);                     /* Socionext USB glue */
    }
    L.lcd_size = rd(LCD + 0x204);
    L.overlay_format = rd(LCD + 0x2200);
    L.color = rd(LCD + 0x2440); L.alpha = rd(LCD + 0x24c0);
    L.cstride = rd(LCD + 0x2500); L.astride = rd(LCD + 0x2540);
    for (u32 i = 0; i < 3; i++) L.ex_hb[i] = rd(EX_HB_PARAMS + i * 4u);
}

static u32 be(u32 v) { return __builtin_bswap32(v); }
/* Replace every big-endian placeholder word in the DTB copy; returns how many kinds were found. */
static u32 patch_dtb(u8 *dtb, u32 size) {
    if (size < 40 || be(*(u32 *)(uintptr_t)dtb) != 0xd00dfeedu) { L.error = E_DTB; L.detail = 1; return 0; }
    u32 initrd_end = INITRD_AT + L.initrd_size;
    const u32 from[] = {PH_GICD, PH_GICC, PH_COLOR, PH_ALPHA, PH_WIDTH, PH_HEIGHT, PH_CSTRIDE, PH_ASTRIDE,
                        PH_INITRD_START, PH_INITRD_END, PH_MPIDR, PH_MPIDR1, PH_RELEASE};
    const u32 to[] = {L.gicd, L.gicc, L.color, L.alpha, L.lcd_size & 0xffff, L.lcd_size >> 16, L.cstride, L.astride,
                      L.initrd_size ? INITRD_AT : 0, L.initrd_size ? initrd_end : 0,
                      MRC(0, c0, c0, 5) & 0xffffffu, (MRC(0, c0, c0, 5) & 0xffff00u) | 1u,
                      (u32)(uintptr_t)&lb_park_state[1]};
    u32 found = 0;
    for (u32 o = 0; o + 4 <= size; o += 4) {
        u32 *w = (u32 *)(uintptr_t)(dtb + o), v = be(*w);
        if ((v & 0xffffff00u) != 0x5a300000u) continue;
        for (u32 k = 0; k < sizeof(from) / sizeof(from[0]); k++)
            if (v == from[k]) { *w = be(to[k]); found |= 1u << k; }
    }
    return found;
}

static int check(void) {
    if ((MRC(0, c0, c0, 5) & 3) != 0) { L.error = E_CORE; L.detail = MRC(0, c0, c0, 5); return 0; }
    if (!L.gicd || !L.gicc) { L.error = E_SGI; L.detail = 1; return 0; }
    if (rd(SGI1_SLOT) != SGI1_STOCK) { L.error = E_SGI; L.detail = rd(SGI1_SLOT); return 0; }
    if (!L.overlay_format || !L.color || !L.alpha || (L.lcd_size & 0xffff) < 320) {
        L.error = E_DISPLAY; L.detail = L.lcd_size; return 0;
    }
    return 1;
}

/* ---- core 1 ------------------------------------------------------------- */
/* lowlevel.S lb_sgi1 replaces the stock SGI 1 entry for as long as it takes core 1
 * to take the interrupt and park; on a timeout the stock entry goes back. */
static int park_core1(void) {
    lb_park_state[0] = 0; lb_park_state[1] = 0; lb_park_state[3] = L.gicc; lb_park_state[2] = 1;
    __asm__ volatile("dsb sy" ::: "memory");
    wr(SGI1_SLOT, (u32)(uintptr_t)lb_sgi1);
    __asm__ volatile("dsb sy" ::: "memory");
    wr(L.gicd + 0xf00, (1u << (16 + 1)) | 1u);      /* GICD_SGIR: SGI 1 to core 1 */
    u32 t0 = ms();
    while (!lb_park_state[0] && ms() - t0 < 300) {}
    L.park_wait_ms = ms() - t0;
    if (!lb_park_state[0]) {
        lb_park_state[2] = 0;
        wr(SGI1_SLOT, SGI1_STOCK);                    /* nothing happened: put it back */
        __asm__ volatile("dsb sy" ::: "memory");
        L.error = E_PARK; L.detail = L.park_wait_ms;
        return 0;
    }
    return 1;
}

/* ---- boot --------------------------------------------------------------- */
static int prepare(void) {
    probe();
    if (!load_all()) return 0;
    if (!check()) return 0;
    if (!L.work || L.work_size < L.dtb_size) {
        pool_free(L.work);
        L.work = alloc(L.dtb_size + 64); L.work_size = L.work ? L.dtb_size : 0;
        if (!L.work) { L.error = E_MEMORY; L.detail = L.dtb_size; return 0; }
    }
    memcpy(L.work, L.dtb, L.dtb_size);
    L.patched = patch_dtb(L.work, L.dtb_size);
    if ((L.patched & 0x1fff) != 0x1fff) { if (!L.error) L.error = E_PLACEHOLDER; L.detail = L.patched; return 0; }
    return 1;
}

static void disable_ex_heartbeat(void) {
    /* EX MCU (Cortex-M, McuMgCy2 1 s timer) pings EG on EXMCU channel 1 (cmd 0x0b)
     * and triggers NVIC_SystemReset (~16 s) if EG stops replying. Sending channel 1
     * cmd 0x0a with enable=0 via stock McuMg API (0x10059821) atomically clears
     * EX's heartbeat enable, in-flight wait and expired flags (EX 0x08029d94). */
    if (!rd(EX_MCUMG_VTBL)) return;
    u32 off[3] = {0, 0, 0};
    FN(0x10059821u, u32 (*)(const u32 *))(off);
    FN(0x1021994bu, void (*)(u32))(100u);  /* allow McuMg + EXMCUSRV SPI0 TX to finish */
}

static void boot(void) {
    L.error = E_NONE;
    if (!prepare()) { L.phase = P_ERROR; return; }
    L.phase = P_BOOTING;
    disable_ex_heartbeat();
    if (!park_core1()) { L.phase = P_ERROR; return; }
    /* From here on this camera belongs to Linux: no way back but a restart. */
    irq_off();
    if (!L.skip_wdt) {
        wr(WDT + 0xc00, 0x1acce551u);               /* unlock */
        wr(WDT + 0x08, 0);                          /* counter and reset off */
    }
    memcpy((void *)(uintptr_t)KERNEL_AT, L.kernel, (L.kernel_size + 3) & ~3u);
    if (L.initrd_size) memcpy((void *)(uintptr_t)INITRD_AT, L.initrd, (L.initrd_size + 3) & ~3u);
    memcpy((void *)(uintptr_t)DTB_AT, L.work, (L.dtb_size + 3) & ~3u);
    lb_jump(KERNEL_AT, DTB_AT);
}

static const char *status_text(void);
/* A dry run leaves everything it read in A:\\Z30\\LINUX\\DRYRUN.TXT (docs/linux.md). */
static char *line_hex(char *p, const char *name, u32 v) { p = put(p, name); p = put(p, " "); p = hex(p, v, 8); return put(p, "\r\n"); }
static void write_report(void) {
    if (!L.bounce) return;
    char *p = (char *)L.bounce;
    p = put(p, "Wonder Lantern Z30 Linux handoff dry run\r\n");
    p = put(p, "status "); p = put(p, status_text()); p = put(p, "\r\n");
    p = line_hex(p, "MPIDR", MRC(0, c0, c0, 5)); p = line_hex(p, "CBAR", L.cbar);
    p = line_hex(p, "GICD", L.gicd); p = line_hex(p, "GICC", L.gicc); p = line_hex(p, "CNTFRQ", L.cntfrq);
    p = line_hex(p, "SP805_PERIPHID01", L.wdt_id); p = line_hex(p, "SP805_CONTROL", L.wdt_control);
    p = line_hex(p, "LCD_SIZE", L.lcd_size); p = line_hex(p, "OVERLAY_FORMAT", L.overlay_format);
    p = line_hex(p, "OVERLAY_COLOR", L.color); p = line_hex(p, "OVERLAY_ALPHA", L.alpha);
    p = line_hex(p, "OVERLAY_CSTRIDE", L.cstride); p = line_hex(p, "OVERLAY_ASTRIDE", L.astride);
    static const char sdn[9][10] = {"PRESENT", "CAPS", "CR_SET", "CDR_SET", "WP_CD_LED",
                                    "HOSTCTL", "CLOCKCTL", "HOSTCTL2", "VERSION"};   /* no pointers: no relocations */
    for (u32 c = 0; c < 2; c++)
        for (u32 i = 0; i < 9; i++) {
            p = put(p, c ? "SD1_" : "SD0_"); p = line_hex(p, sdn[i], L.sd[c][i]);
        }
    p = line_hex(p, "DTB_PATCHED", L.patched);
    p = line_hex(p, "EX_HB0", L.ex_hb[0]);
    p = line_hex(p, "EX_HB1", L.ex_hb[1]);
    p = line_hex(p, "EX_HB2", L.ex_hb[2]);
    p = line_hex(p, "USB_GSNPSID", L.usb[0]);
    p = line_hex(p, "USB_GCTL", L.usb[1]);
    p = line_hex(p, "USB_GUSB2PHY", L.usb[2]);
    p = line_hex(p, "USB_GLUE0", L.usb[3]);
    p = line_hex(p, "KERNEL_BYTES", L.kernel_size); p = line_hex(p, "INITRD_BYTES", L.initrd_size);
    u32 n = (u32)(p - (char *)L.bounce);
    struct file f;
    L.report_ok = 0;
    for (u32 i = 0; i < 16; i++) f.obj[i] = 0;
    f.obj[2] = ~0u;
    FN(0x1033829f, void (*)(void *, const char *))(f.obj, "A:\\Z30\\LINUX\\DRYRUN.TXT");
    if (FN(0x103382a3, u32 (*)(void *, u32, u32))(f.obj, 0x301, 0) != NATIVE_OK || (s32)f.obj[2] < 0) return;
    u32 done = 0;
    cache(L.bounce, BOUNCE, 3);
    u32 st = FN(0x102c8a6b, u32 (*)(u32, void *, u32, u32 *))(f.obj[2], L.bounce, n, &done);
    L.report_ok = f_close(&f) && !st && done == n;
}

static void dry_run(void) {
    L.error = E_NONE;
    L.phase = prepare() ? P_DRY : P_ERROR;
    write_report();
}

/* ---- texts ---------------------------------------------------------------- */
static const char *status_text(void) {
    char *p = L.status;
    if (L.phase == P_IDLE) { put(p, "Ready"); return L.status; }
    if (L.phase == P_DRY) {
        p = put(p, "OK K"); p = dec(p, L.kernel_size >> 10); p = put(p, "K I"); p = dec(p, L.initrd_size >> 10);
        put(p, "K");
        return L.status;
    }
    switch (L.error) {
    case E_MEMORY: p = put(p, "No memory "); hex(p, L.detail, 8); break;
    case E_KERNEL: put(p, "No LINUX/ZIMAGE"); break;
    case E_DTB: p = put(p, "Bad Z30.DTB "); dec(p, L.detail); break;
    case E_INITRD: put(p, "INITRD read failed"); break;
    case E_SIZE: p = put(p, "File too big "); dec(p, L.detail); break;
    case E_CORE: p = put(p, "Not core 0 "); hex(p, L.detail, 8); break;
    case E_SGI: p = put(p, "IPI slot "); hex(p, L.detail, 8); break;
    case E_PARK: p = put(p, "Core 1 no park "); dec(p, L.detail); break;
    case E_DISPLAY: p = put(p, "Display "); hex(p, L.detail, 8); break;
    case E_PLACEHOLDER: put(p, "DTB placeholders"); break;
    default: put(p, "Error"); break;
    }
    return L.status;
}
static const char *info(u32 row) {
    char *p = L.line[row];
    if (!L.gicd) probe_cpu();
    if (row == 0) { p = put(p, "GIC "); p = hex(p, L.gicd, 8); p = put(p, " F"); dec(p, L.cntfrq / 1000000); }
    else if (!L.color) put(p, "LCD ? (dry run reads it)");
    else { p = put(p, "LCD "); p = hex(p, L.color, 8); p = put(p, " W"); hex(p, L.wdt_id, 4); }
    return L.line[row];
}

__attribute__((section(".text.entry")))
u32 linuxboot(u32 cmd, u32 *page, u32 row) {
    (void)page;
    switch (cmd) {
    case 0: return (u32)(uintptr_t)status_text();
    case 1: if (row == 0) boot(); else if (row == 1) dry_run(); return 0;
    case 2: case 3: return (u32)(uintptr_t)info(cmd - 2);
    }
    return 0;
}
