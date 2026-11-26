/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Benchmark: measures the EXPEED 6 application CPU from the extension menu's
 * "Benchmark" page. Experimental branch only (experimental).
 *
 * A stock menu timer (slot 45, as the handheld page) advances a step list a
 * little each tick, so the menu stays responsive. Every timed chunk runs with
 * interrupts off and the PMU cycle counter on, and puts PMCR / PMCNTENSET back
 * afterwards, so nothing else on the camera sees a change. The VFP / NEON
 * chunks also enable CP10/CP11 for their duration only (stock code turns them
 * off) and restore every FP register (kernels.S).
 *
 * The result rows show a summary; Z30/BENCH.TXT gets everything, written once
 * after the integer and memory part (so a crash in the FPU part still leaves
 * numbers) and again at the end.
 *
 * Entry: bench(cmd, page, row)
 *   cmd 0: status text (row 0 value)    cmd 1: row action (row 0 starts a run)
 *   cmd 2..5: text of rows 1..4 (CPU, integer, FPU, memory)
 */
#include <stdint.h>
#include <stddef.h>

typedef uint64_t u64; typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8; typedef int32_t s32;
typedef int64_t s64;
#define FN(a, t) ((t)(uintptr_t)(a))

#ifndef PAGE_INDEX
#define PAGE_INDEX 977u
#endif
#define ROWS 5u
#define TIMER_SLOT 45u
#define TIMER_PERIOD 28u
#define BUDGET_MS 16u
#define NATIVE_OK 0x04370080u
#define TIMERS 0xc20060b0u
#define BOUNCE 8192u
#define TARGET_CYCLES 400000u      /* calibrated chunk length (~0.3 ms at 1.2 GHz) */
#define TARGET_US 8000u            /* without a PMU: ms-timed chunks, interrupts on */
#define REPS 7u
#define CLOCK_RUNS 5u
#define CLOCK_MS 10u
#define BLOCK 65536u               /* streaming unit in DRAM */

/* ---- kernels.S --------------------------------------------------------- */
u32 k_dep_add(u32 n); u32 k_ind_add(u32 n); u32 k_dep_mul(u32 n); u32 k_ind_mul(u32 n);
u32 k_chase(u32 p, u32 n);
void k_read(const void *p, u32 bytes); void k_write(void *p, u32 bytes); void k_copy(void *d, const void *s, u32 bytes);
struct fpu_save { u32 cpacr, fpexc, fpscr, d32, ok, pad[3]; u64 d[32]; };
u32 fpu_open(struct fpu_save *s); void fpu_close(struct fpu_save *s); void fpu_ids(u32 *out);
u32 k_vfp32(u32 n); u32 k_vfp64(u32 n); u32 k_neon32(u32 n); u32 k_neonfma(u32 n); u32 k_neon16(u32 n);
/* Hidden: their addresses are taken, and a default-visibility symbol would go through a GOT. */
#pragma GCC visibility push(hidden)
void sec_probe(u32 vbar, u32 *out); void sec_undef(void); void sec_hang(void);
#pragma GCC visibility pop
void k_nread(const void *p, u32 bytes); void k_nwrite(void *p, u32 bytes); void k_ncopy(void *d, const void *s, u32 bytes);

/* ---- tests ------------------------------------------------------------- */
enum { K_OPS, K_BYTES, K_LOADS };
enum { T_DEPADD, T_INDADD, T_DEPMUL, T_INDMUL, T_CRC, T_MANDEL,
       T_RD_L1, T_RD_L2, T_RD_RAM, T_WR_RAM, T_CP_RAM,
       T_LAT_L1, T_LAT_L2, T_LAT_RAM,
       T_VFP32, T_VFP64, T_NEON32, T_NEONFMA, T_NEON16, T_NRD_RAM, T_NWR_RAM, T_NCP_RAM, T_COUNT };
#define FIRST_FPU T_VFP32
/* No pointers in static data: the payload is linked without relocations. */
struct test { u8 kind, fpu; u32 work; };   /* work per unit (set at run time when 0) */
static struct test TESTS[T_COUNT] = {
    [T_DEPADD] = {K_OPS, 0, 128}, [T_INDADD] = {K_OPS, 0, 128},
    [T_DEPMUL] = {K_OPS, 0, 64}, [T_INDMUL] = {K_OPS, 0, 64},
    [T_CRC] = {K_BYTES, 0, 4096}, [T_MANDEL] = {K_OPS, 0, 0},
    [T_RD_L1] = {K_BYTES, 0, 8192}, [T_RD_L2] = {K_BYTES, 0, 0},
    [T_RD_RAM] = {K_BYTES, 0, BLOCK}, [T_WR_RAM] = {K_BYTES, 0, BLOCK}, [T_CP_RAM] = {K_BYTES, 0, BLOCK},
    [T_LAT_L1] = {K_LOADS, 0, 16}, [T_LAT_L2] = {K_LOADS, 0, 16}, [T_LAT_RAM] = {K_LOADS, 0, 16},
    [T_VFP32] = {K_OPS, 1, 64}, [T_VFP64] = {K_OPS, 1, 64}, [T_NEON32] = {K_OPS, 1, 256},
    [T_NEONFMA] = {K_OPS, 1, 256}, [T_NEON16] = {K_OPS, 1, 512},
    [T_NRD_RAM] = {K_BYTES, 1, BLOCK}, [T_NWR_RAM] = {K_BYTES, 1, BLOCK}, [T_NCP_RAM] = {K_BYTES, 1, BLOCK},
};
static int have_l2(void);
static const char *test_name(u32 id) {
    switch (id) {
    case T_DEPADD: return "add dependent"; case T_INDADD: return "add 4 chains";
    case T_DEPMUL: return "mul dependent"; case T_INDMUL: return "mul 4 chains";
    case T_CRC: return "crc32 table"; case T_MANDEL: return "mandelbrot q16";
    case T_RD_L1: return "read 8K (L1)"; case T_RD_L2: return have_l2() ? "read L2" : "read 128K (no L2)";
    case T_RD_RAM: return "read DRAM"; case T_WR_RAM: return "write DRAM"; case T_CP_RAM: return "copy DRAM";
    case T_LAT_L1: return "latency L1"; case T_LAT_L2: return have_l2() ? "latency L2" : "latency 128K"; case T_LAT_RAM: return "latency DRAM";
    case T_VFP32: return "vfp f32 vmla"; case T_VFP64: return "vfp f64 vmla";
    case T_NEON32: return "neon f32 vmla"; case T_NEONFMA: return "neon f32 vfma"; case T_NEON16: return "neon i16 vmla";
    case T_NRD_RAM: return "neon read DRAM"; case T_NWR_RAM: return "neon write DRAM"; case T_NCP_RAM: return "neon copy DRAM";
    }
    return "?";
}

/* Steps of a run, in order. */
enum { S_SETUP, S_CLOCK, S_TEST, S_RING, S_REPORT, S_FPU, S_DONE };
struct step { u8 kind, arg; };
static const struct step STEPS[] = {
    {S_SETUP, 0}, {S_CLOCK, 0},
    {S_TEST, T_DEPADD}, {S_TEST, T_INDADD}, {S_TEST, T_DEPMUL}, {S_TEST, T_INDMUL},
    {S_TEST, T_CRC}, {S_TEST, T_MANDEL},
    {S_TEST, T_RD_L1}, {S_TEST, T_RD_L2}, {S_TEST, T_RD_RAM}, {S_TEST, T_WR_RAM}, {S_TEST, T_CP_RAM},
    {S_RING, 0}, {S_TEST, T_LAT_L1}, {S_RING, 1}, {S_TEST, T_LAT_L2}, {S_RING, 2}, {S_TEST, T_LAT_RAM},
    {S_REPORT, 0}, {S_FPU, 0},
    {S_TEST, T_VFP32}, {S_TEST, T_VFP64}, {S_TEST, T_NEON32}, {S_TEST, T_NEONFMA}, {S_TEST, T_NEON16},
    {S_TEST, T_NRD_RAM}, {S_TEST, T_NWR_RAM}, {S_TEST, T_NCP_RAM},
    {S_REPORT, 1}, {S_DONE, 0},
};
#define STEP_COUNT (sizeof(STEPS) / sizeof(STEPS[0]))

enum { P_IDLE, P_RUNNING, P_DONE, P_STOPPED, P_ERROR };
enum { E_NONE, E_MEMORY, E_TIMER, E_PAGE, E_LEFT, E_FILE };
enum { F_UNKNOWN, F_OK, F_REFUSED, F_NO_NEON, F_NO_PMU };
enum { SEC_NOT_RUN, SEC_SECURE, SEC_NONSECURE, SEC_SKIPPED, SEC_NO_CATCH };

struct result { u32 n, best, runs; u8 done, skipped; };
/* Identification: read once, kept across runs. */
struct ident { u32 probed, midr, mpidr, clidr, ccsidr[3], ctr, pfr0, pfr1, dfr0, isar0, l2ctlr, cores, pmu, pmcr; };
static struct ident I;
static int have_l2(void) { return I.ccsidr[2] != 0; }
struct bench {
    u32 phase, error, detail, page, timer, step, ticks, started_ms, run_ms, mpidr_end;
    u32 fpu, fpsid, mvfr0, mvfr1, cpacr0, fpexc0, d32, fma, nsacr, cpacr_try;
    u32 sec, scr, sctlr, vbar0;
    /* clock */
    u32 clock[CLOCK_RUNS], clock_runs, mhz_x10, clock_bad;
    /* state of the current step */
    u32 calibrating;
    u8 *raw, *buf; u32 buf_size, pos, l2_bytes, ring[3], ring_lines[3];
    u8 *bounce; u32 file_ok, file_writes;
    u32 crc_table[256], mandel_iter, sink, shown_hash;
    struct result r[T_COUNT];
    char status[32], line[4][48];
};
static struct bench B;
static struct fpu_save FS __attribute__((aligned(8)));

/* ---- freestanding runtime --------------------------------------------- */
__attribute__((optimize("no-tree-loop-distribute-patterns")))
void *memcpy(void *d, const void *s, size_t n) {
    u8 *a = d; const u8 *b = s;
    while (n--) *a++ = *b++;
    return d;
}
__attribute__((optimize("no-tree-loop-distribute-patterns")))
void *memset(void *d, int c, size_t n) {
    u8 *a = d;
    while (n--) *a++ = (u8)c;
    return d;
}
void abort(void) { for (;;) __asm__ volatile("wfi"); }

static void *alloc(u32 n) { return FN(0x10211f4d, void *(*)(u32, u32))(n, 0); }
static void pool_free(void *p) { if (p) FN(0x10211f5f, void (*)(void *))(p); }
static int cache(void *p, u32 n, u32 op) {
    int r = FN(0x10035aac, int (*)(void *, u32, u32, u32))(p, n, op, 2);
    __asm__ volatile("dsb sy" ::: "memory");
    return r > 0;
}
static u32 ms(void) {
    u32 t[2] = {0, 0};
    FN(0x1021cea3, void (*)(void *))(t);   /* SYSTIM {high, low} */
    return t[1];
}

/* ---- CP15 ------------------------------------------------------------- */
#define MRC(op1, crn, crm, op2) ({ u32 v_; __asm__ volatile("mrc p15, " #op1 ", %0, " #crn ", " #crm ", " #op2 : "=r"(v_)); v_; })
#define MCR(op1, crn, crm, op2, v) __asm__ volatile("mcr p15, " #op1 ", %0, " #crn ", " #crm ", " #op2 :: "r"((u32)(v)) : "memory")
#define ISB() __asm__ volatile("isb sy" ::: "memory")

static inline u32 irq_off(void) { u32 f; __asm__ volatile("mrs %0, cpsr\n\tcpsid i" : "=r"(f) :: "memory"); return f; }
static inline void irq_restore(u32 f) { __asm__ volatile("msr cpsr_c, %0" :: "r"(f) : "memory"); }

struct pmu_state { u32 pmcr, cnten; };
static inline void pmu_on(struct pmu_state *s) {
    s->pmcr = MRC(0, c9, c12, 0); s->cnten = MRC(0, c9, c12, 1);
    MCR(0, c9, c12, 0, (s->pmcr | 1u) & ~8u);   /* enable, count every cycle (no /64) */
    MCR(0, c9, c12, 1, 1u << 31);
    ISB();
}
static inline void pmu_off(const struct pmu_state *s) {
    if (!(s->cnten & (1u << 31))) MCR(0, c9, c12, 2, 1u << 31);
    MCR(0, c9, c12, 0, s->pmcr);
    ISB();
}
static inline u32 cycles(void) { u32 v; __asm__ volatile("isb sy\n\tmrc p15, 0, %0, c9, c13, 0" : "=r"(v) :: "memory"); return v; }

static u32 part(void) { return (I.midr >> 4) & 0xfff; }
static int arm_part(u32 p) { return (I.midr >> 24) == 0x41 && part() == p; }

static void probe(void) {
    if (I.probed) return;
    I.probed = 1;
    I.midr = MRC(0, c0, c0, 0); I.mpidr = MRC(0, c0, c0, 5); I.ctr = MRC(0, c0, c0, 1);
    I.pfr0 = MRC(0, c0, c1, 0); I.pfr1 = MRC(0, c0, c1, 1); I.dfr0 = MRC(0, c0, c1, 2);
    I.isar0 = MRC(0, c0, c2, 0); I.clidr = MRC(1, c0, c0, 1);
    u32 l1 = I.clidr & 7, l2 = (I.clidr >> 3) & 7;
    u32 f = irq_off(), keep = MRC(2, c0, c0, 0);
    /* CSSELR 0 = L1 data/unified, 1 = L1 instruction, 2 = L2 data/unified. */
    if (l1 >= 2) { MCR(2, c0, c0, 0, 0); ISB(); I.ccsidr[0] = MRC(1, c0, c0, 0); }
    if (l1 == 1 || l1 == 3) { MCR(2, c0, c0, 0, 1); ISB(); I.ccsidr[1] = MRC(1, c0, c0, 0); }
    if (l2 >= 2) { MCR(2, c0, c0, 0, 2); ISB(); I.ccsidr[2] = MRC(1, c0, c0, 0); }
    MCR(2, c0, c0, 0, keep); ISB();
    irq_restore(f);
    /* L2CTLR (cores in [25:24]) exists on Cortex-A7, A12, A15 and A17 only. */
    if (arm_part(0xc07) || arm_part(0xc0d) || arm_part(0xc0e) || arm_part(0xc0f)) {
        I.l2ctlr = MRC(1, c9, c0, 2);
        I.cores = ((I.l2ctlr >> 24) & 3) + 1;
    }
    u32 perf = (I.dfr0 >> 24) & 15;   /* 2 PMUv1, 3 PMUv2, 4 PMUv3; 0 / 15 none */
    I.pmu = perf >= 2 && perf != 15;
    if (I.pmu) I.pmcr = MRC(0, c9, c12, 0);
}

static u32 cache_bytes(u32 c) {
    if (!c) return 0;
    return (1u << ((c & 7) + 4)) * (((c >> 3) & 0x3ff) + 1) * (((c >> 13) & 0x7fff) + 1);
}

/* ---- text -------------------------------------------------------------- */
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
/* v / 10^places, rounded, with that many decimals. */
static char *fix(char *p, u64 v, u32 places) {
    u32 scale = 1; for (u32 i = 0; i < places; i++) scale *= 10;
    p = dec(p, (u32)(v / scale));
    if (places) {
        *p++ = '.';
        u32 f = (u32)(v % scale);
        for (u32 s = scale / 10; s; s /= 10) { *p++ = (char)('0' + f / s % 10); }
        *p = 0;
    }
    return p;
}
static char *pad(char *p, char *start, u32 width) { while ((u32)(p - start) < width) *p++ = ' '; *p = 0; return p; }

/* ---- workloads in C ---------------------------------------------------- */
static void crc_init(void) {
    for (u32 i = 0; i < 256; i++) {
        u32 c = i;
        for (u32 k = 0; k < 8; k++) c = c & 1 ? 0xedb88320u ^ (c >> 1) : c >> 1;
        B.crc_table[i] = c;
    }
}
__attribute__((noipa)) static u32 crc32(const u8 *p, u32 n, u32 c) {
    c = ~c;
    while (n--) c = B.crc_table[(c ^ *p++) & 255] ^ (c >> 8);
    return ~c;
}
/* One 16 x 16 tile of the Mandelbrot set in Q16.16, up to 64 iterations; returns iterations. */
/* noipa: otherwise GCC sees a const function and folds n calls into one (v2 bug). */
__attribute__((noipa)) static u32 mandel_tile(void) {
    u32 total = 0;
    for (s32 y = 0; y < 16; y++) for (s32 x = 0; x < 16; x++) {
        s32 cr = -0x18000 + x * 0x1800, ci = -0xc000 + y * 0x1800, zr = 0, zi = 0;
        u32 i = 0;
        for (; i < 64; i++) {
            s32 rr = (s32)(((s64)zr * zr) >> 16), ii = (s32)(((s64)zi * zi) >> 16);
            if (rr + ii > 0x40000) break;
            zi = (s32)(((s64)zr * zi) >> 15) + ci;
            zr = rr - ii + cr;
        }
        total += i + 1;
    }
    return total;
}

/* ---- buffers and rings ------------------------------------------------- */
static u32 rng = 0x2545f491u;
static u32 next_rand(void) { rng = rng * 1664525u + 1013904223u; return rng; }

/* A random cyclic walk over `lines` 64-byte lines at `base`: line k's word 1 points
 * at the next line. `perm` is scratch for `lines` words outside the ring. */
static u32 ring(u8 *base, u32 lines, u32 *perm) {
    for (u32 i = 0; i < lines; i++) perm[i] = i;
    for (u32 i = lines - 1; i; i--) { u32 j = next_rand() % (i + 1), t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
    for (u32 i = 0; i < lines; i++)
        *(u32 *)(base + perm[i] * 64 + 4) = (u32)(uintptr_t)(base + perm[(i + 1) % lines] * 64);
    return (u32)(uintptr_t)(base + perm[0] * 64);
}

static void release(void) {
    pool_free(B.raw);
    B.raw = B.buf = NULL; B.buf_size = 0;
}

static int setup(void) {
    if (!B.bounce) {
        u32 raw = (u32)(uintptr_t)alloc(BOUNCE + 63);
        if (!raw) return 0;
        B.bounce = (u8 *)(uintptr_t)((raw + 63) & ~63u);
    }
    static const u32 sizes[] = {8u << 20, 4u << 20, 2u << 20};
    for (u32 i = 0; i < 3 && !B.raw; i++) {
        B.raw = alloc(sizes[i] + 256);
        if (B.raw) B.buf_size = sizes[i];
    }
    if (!B.raw) return 0;
    B.buf = (u8 *)(uintptr_t)(((u32)(uintptr_t)B.raw + 255) & ~255u);
    memset(B.buf, 0x5a, B.buf_size);
    crc_init();
    B.mandel_iter = mandel_tile();
    TESTS[T_MANDEL].work = B.mandel_iter;
    u32 l2 = cache_bytes(I.ccsidr[2]);
    B.l2_bytes = l2 ? l2 / 2 : 128u * 1024;
    if (B.l2_bytes > 512u * 1024) B.l2_bytes = 512u * 1024;
    B.l2_bytes &= ~127u;
    TESTS[T_RD_L2].work = B.l2_bytes;
    B.pos = 0;
    return 1;
}

static void make_ring(u32 which) {
    /* 0: 4 KiB at buf (L1); 1: l2_bytes at buf + 1 MiB (L2); 2: the upper half (DRAM).
     * The permutation scratch sits outside each ring. */
    u8 *base; u32 bytes; u32 *perm;
    u32 *tail = (u32 *)(uintptr_t)(B.buf + B.buf_size - 65536);
    if (which == 0) { base = B.buf; bytes = 4096; perm = tail; }
    else if (which == 1) { base = B.buf + (1u << 20); bytes = B.l2_bytes; perm = tail; }
    else { base = B.buf + B.buf_size / 2; bytes = B.buf_size / 2; perm = (u32 *)(uintptr_t)B.buf; }
    B.ring_lines[which] = bytes / 64;
    B.ring[which] = ring(base, bytes / 64, perm);
}

/* ---- running a test ---------------------------------------------------- */
static u8 *stream(u32 n) {   /* the next n blocks of the DRAM buffer, wrapping at the end */
    if (B.pos + n * BLOCK > B.buf_size) B.pos = 0;
    u8 *p = B.buf + B.pos;
    B.pos += n * BLOCK;
    return p;
}
static void work(u32 id, u32 n) {
    u32 half = B.buf_size / 2;
    switch (id) {
    case T_DEPADD: B.sink += k_dep_add(n); break;
    case T_INDADD: B.sink += k_ind_add(n); break;
    case T_DEPMUL: B.sink += k_dep_mul(n); break;
    case T_INDMUL: B.sink += k_ind_mul(n); break;
    case T_CRC: for (u32 i = 0; i < n; i++) B.sink = crc32(B.buf + (i & 3) * 4096, 4096, B.sink); break;
    case T_MANDEL: for (u32 i = 0; i < n; i++) B.sink += mandel_tile(); break;
    case T_RD_L1: for (u32 i = 0; i < n; i++) k_read(B.buf, 8192); break;
    case T_RD_L2: for (u32 i = 0; i < n; i++) k_read(B.buf + (1u << 20), B.l2_bytes); break;
    case T_RD_RAM: k_read(stream(n), n * BLOCK); break;
    case T_WR_RAM: k_write(stream(n), n * BLOCK); break;
    case T_CP_RAM: {
        if (n * BLOCK > half) n = half / BLOCK;
        if (B.pos + n * BLOCK > half) B.pos = 0;
        k_copy(B.buf + half + B.pos, B.buf + B.pos, n * BLOCK); B.pos += n * BLOCK; break;
    }
    case T_LAT_L1: B.ring[0] = k_chase(B.ring[0], n); break;
    case T_LAT_L2: B.ring[1] = k_chase(B.ring[1], n); break;
    case T_LAT_RAM: B.ring[2] = k_chase(B.ring[2], n); break;
    case T_VFP32: B.sink += k_vfp32(n); break;
    case T_VFP64: B.sink += k_vfp64(n); break;
    case T_NEON32: B.sink += k_neon32(n); break;
    case T_NEONFMA: B.sink += k_neonfma(n); break;
    case T_NEON16: B.sink += k_neon16(n); break;
    case T_NRD_RAM: k_nread(stream(n), n * BLOCK); break;
    case T_NWR_RAM: k_nwrite(stream(n), n * BLOCK); break;
    case T_NCP_RAM: {
        if (n * BLOCK > half) n = half / BLOCK;
        if (B.pos + n * BLOCK > half) B.pos = 0;
        k_ncopy(B.buf + half + B.pos, B.buf + B.pos, n * BLOCK); B.pos += n * BLOCK; break;
    }
    }
}
/* Largest n a test can take in one call. */
static u32 max_units(u32 id) {
    switch (id) {
    case T_RD_RAM: case T_WR_RAM: case T_NRD_RAM: case T_NWR_RAM: return B.buf_size / BLOCK;
    case T_CP_RAM: case T_NCP_RAM: return B.buf_size / 2 / BLOCK;
    }
    return 1u << 22;
}

/* One timed chunk: cycles (PMU) or microseconds (no PMU). 0 when the FPU refused. */
static u32 chunk(u32 id, u32 n) {
    const struct test *t = &TESTS[id];
    if (!I.pmu) {
        u32 t0 = ms(); work(id, n); return (ms() - t0) * 1000 + 1;
    }
    struct pmu_state s;
    u32 f = irq_off();
    if (t->fpu) {
        FS.d32 = B.d32;
        if (!fpu_open(&FS)) { irq_restore(f); B.fpu = F_REFUSED; return 0; }
    }
    pmu_on(&s);
    u32 c0 = cycles();
    work(id, n);
    u32 c1 = cycles();
    pmu_off(&s);
    if (t->fpu) fpu_close(&FS);
    irq_restore(f);
    return c1 - c0;
}

/* Calibrate n, then keep the best of REPS chunks; returns 1 when the test is done. */
static int test_step(u32 id, u32 t0) {
    struct result *r = &B.r[id];
    u32 target = I.pmu ? TARGET_CYCLES : TARGET_US, limit = max_units(id);
    if (TESTS[id].fpu && B.fpu != F_OK) { r->skipped = 1; return 1; }
    if (id == T_NEONFMA && !B.fma) { r->skipped = 1; return 1; }
    if (!r->n) {
        r->n = 1; B.calibrating = 1;
        if (!chunk(id, 1)) { r->skipped = 1; return 1; }   /* warm-up, inside the same guards */
    }
    while (ms() - t0 < BUDGET_MS) {
        u32 c = chunk(id, r->n);
        if (!c) { r->skipped = 1; return 1; }
        if (B.calibrating) {
            if (c < target && r->n < limit) { r->n = r->n * 2 > limit ? limit : r->n * 2; continue; }
            B.calibrating = 0; r->best = c; r->runs = 1;
            continue;
        }
        if (c < r->best) r->best = c;
        if (++r->runs >= REPS) { r->done = 1; return 1; }
    }
    return 0;
}

/* ---- clock --------------------------------------------------------------- */
/* PMU cycles across CLOCK_MS of SYSTIM, interrupts on (the counter keeps
 * counting while preempted). The core must not change in between. */
static void clock_done(void) {
    u32 v[CLOCK_RUNS], n = 0;
    for (u32 i = 0; i < CLOCK_RUNS; i++) if (B.clock[i]) v[n++] = B.clock[i];
    for (u32 i = 1; i < n; i++) for (u32 j = i; j && v[j - 1] > v[j]; j--) { u32 x = v[j]; v[j] = v[j - 1]; v[j - 1] = x; }
    B.mhz_x10 = n ? v[n / 2] : 0;    /* median of the good runs */
    B.clock_runs = CLOCK_RUNS;
}
static void clock_step(void) {
    if (!I.pmu) { clock_done(); return; }
    u32 core = MRC(0, c0, c0, 5);
    struct pmu_state s;
    u32 f = irq_off(); pmu_on(&s); irq_restore(f);
    u32 t = ms(), guard = 0;
    while (ms() == t && ++guard < 2000000u) {}
    u32 c0 = cycles(), t1 = ms();
    while (ms() - t1 < CLOCK_MS && ++guard < 20000000u) {}
    u32 c1 = cycles(), t2 = ms();
    f = irq_off(); pmu_off(&s); irq_restore(f);
    u32 dt = t2 - t1;
    if (MRC(0, c0, c0, 5) != core || dt < CLOCK_MS || dt > CLOCK_MS + 2 || c1 == c0) {
        if (++B.clock_bad >= 10) clock_done();
        return;
    }
    B.clock[B.clock_runs++] = (u32)((u64)(c1 - c0) * 10 / (dt * 1000u));   /* MHz x 10 */
    if (B.clock_runs >= CLOCK_RUNS) clock_done();
}
/* MHz x 10 to turn chunk counts into time: measured, or (no PMU) from the dependent adds at one per cycle. */
static u32 mhz10(void) {
    if (B.mhz_x10) return B.mhz_x10;
    const struct result *r = &B.r[T_DEPADD];
    if (!I.pmu && r->done && r->best) return (u32)((u64)r->n * 128 * 10 / r->best);
    return 0;
}

/* Cycles per unit of work x 1000. */
static u32 cpu_x1000(u32 id) {
    const struct result *r = &B.r[id];
    if (!r->done || !r->best || !TESTS[id].work) return 0;
    u64 counts = (u64)r->best * 1000;
    if (!I.pmu) counts = counts * mhz10() / 10;     /* microseconds -> cycles */
    return (u32)(counts / ((u64)r->n * TESTS[id].work));
}
/* Units per second / 10^6, x 100 (MB/s or Mops/s x 100). */
static u32 rate_x100(u32 id) {
    u32 c = cpu_x1000(id), m = mhz10();
    if (!c || !m) return 0;
    return (u32)((u64)m * 10000 / c);   /* (MHz*10) * 1000 / c * 10 */
}
/* Nanoseconds per unit x 10. */
static u32 ns_x10(u32 id) {
    u32 c = cpu_x1000(id), m = mhz10();
    if (!c || !m) return 0;
    return (u32)((u64)c * 100 / m);     /* c/1000 cycles / (m/10 MHz) * 1000 ns * 10 */
}

/* ---- security state --------------------------------------------------- */
/* SCR can only be read in the secure world; in the non-secure world the read is
 * undefined. kernels.S sec_probe reads it under a temporary vector table that
 * catches the Undefined Instruction exception (and first proves the catch with
 * UDF), all with IRQ and FIQ off, then restores VBAR. */
static void security_probe(void) {
    B.sctlr = MRC(0, c1, c0, 0);
    B.vbar0 = MRC(0, c12, c0, 0);
    /* High vectors (V) ignore VBAR; TE would take exceptions in Thumb state. */
    if (B.sctlr & ((1u << 13) | (1u << 30))) { B.sec = SEC_SKIPPED; return; }
    u8 *raw = alloc(64 + 31);
    if (!raw) { B.sec = SEC_SKIPPED; return; }
    u32 *v = (u32 *)(uintptr_t)(((u32)(uintptr_t)raw + 31) & ~31u);
    for (u32 i = 0; i < 8; i++) {
        v[i] = 0xe59ff018u;                        /* ldr pc, [pc, #24]: jump through v[8 + i] */
        v[8 + i] = (u32)(uintptr_t)sec_hang;
    }
    v[9] = (u32)(uintptr_t)sec_undef;              /* entry 1: Undefined Instruction */
    for (u32 a = (u32)(uintptr_t)v; a < (u32)(uintptr_t)v + 64; a += 32) MCR(0, c7, c11, 1, a);   /* DCCMVAU */
    __asm__ volatile("dsb sy" ::: "memory");
    MCR(0, c7, c5, 0, 0);                          /* ICIALLU */
    MCR(0, c7, c5, 6, 0);                          /* BPIALL */
    __asm__ volatile("dsb sy\n\tisb sy" ::: "memory");
    u32 out[3] = {0, 0, 0};
    sec_probe((u32)(uintptr_t)v, out);
    pool_free(raw);
    B.scr = out[1];
    B.sec = !out[0] ? SEC_NO_CATCH : out[2] ? SEC_NONSECURE : SEC_SECURE;
}

/* ---- FPU ------------------------------------------------------------- */
static void fpu_probe(void) {
    security_probe();
    if (!I.pmu) { B.fpu = F_NO_PMU; return; }
    u32 f = irq_off(), ids[3] = {0, 0, 0};
    B.cpacr0 = MRC(0, c1, c0, 2);
    /* NSACR (readable at PL1 in either state): bits 10/11 clear deny CP10/CP11 to the
     * non-secure world. Refused with them set means no FPU in this core at all. */
    B.nsacr = MRC(0, c1, c1, 2);
    FS.d32 = 0;                           /* only D0-D15 until MVFR0 says more */
    if (!fpu_open(&FS)) {
        MCR(0, c1, c0, 2, B.cpacr0 | 0x00f00000u); ISB();
        B.cpacr_try = MRC(0, c1, c0, 2);           /* what sticks of a plain write */
        MCR(0, c1, c0, 2, B.cpacr0); ISB();
        irq_restore(f); B.fpu = F_REFUSED; return;
    }
    B.fpexc0 = FS.fpexc;
    fpu_ids(ids);
    fpu_close(&FS);
    irq_restore(f);
    B.fpsid = ids[0]; B.mvfr0 = ids[1]; B.mvfr1 = ids[2];
    B.d32 = (B.mvfr0 & 15) == 2;
    u32 simd = (B.mvfr1 >> 8) & 0xfff;    /* A_SIMD load/store, integer, single */
    B.fma = ((B.mvfr1 >> 28) & 15) != 0;
    B.fpu = B.d32 && simd == 0x111 ? F_OK : F_NO_NEON;
}

/* ---- files ------------------------------------------------------------- */
struct file { u32 obj[16]; };
static const char REPORT_PATH[] = "A:\\Z30\\BENCH.TXT";
static int f_close(struct file *f) {
    if ((s32)f->obj[2] < 0) return 1;
    int ok = FN(0x1033832f, u32 (*)(void *))(f->obj) == NATIVE_OK;
    f->obj[2] = ~0u;
    return ok;
}
static int f_open(struct file *f, const char *path, u32 mode) {
    for (u32 i = 0; i < 16; i++) f->obj[i] = 0;
    f->obj[2] = ~0u;
    FN(0x1033829f, void (*)(void *, const char *))(f->obj, path);
    u32 status = FN(0x103382a3, u32 (*)(void *, u32, u32))(f->obj, mode, 0);
    if (status != NATIVE_OK || (s32)f->obj[2] < 0) { f_close(f); return 0; }
    return 1;
}
static int f_write(struct file *f, u32 n) {
    u32 done = 0;
    cache(B.bounce, BOUNCE, 3);
    u32 status = FN(0x102c8a6b, u32 (*)(u32, void *, u32, u32 *))(f->obj[2], B.bounce, n, &done);
    return !status && done == n;
}

/* ---- report ------------------------------------------------------------ */
static const char *cpu_name(void) {
    if ((I.midr >> 24) != 0x41) return "CPU";
    switch (part()) {
    case 0xc05: return "Cortex-A5"; case 0xc07: return "Cortex-A7"; case 0xc08: return "Cortex-A8";
    case 0xc09: return "Cortex-A9"; case 0xc0d: return "Cortex-A12"; case 0xc0e: return "Cortex-A17";
    case 0xc0f: return "Cortex-A15"; case 0xd03: return "Cortex-A53"; case 0xd04: return "Cortex-A35";
    }
    return "ARM";
}
static char *kb(char *p, u32 bytes) {
    if (bytes >= 1u << 20 && !(bytes & 0xfffff)) { p = dec(p, bytes >> 20); return put(p, " MiB"); }
    p = dec(p, bytes >> 10); return put(p, " KiB");
}
static char *cache_line(char *p, const char *name, u32 c) {
    p = put(p, name);
    if (!c) return put(p, " -\r\n");
    p = put(p, " "); p = kb(p, cache_bytes(c)); p = put(p, ", "); p = dec(p, ((c >> 3) & 0x3ff) + 1);
    p = put(p, "-way, "); p = dec(p, 1u << ((c & 7) + 4)); return put(p, " B lines\r\n");
}
static char *reg(char *p, const char *name, u32 v) { p = put(p, name); p = put(p, "="); p = hex(p, v, 8); return put(p, " "); }

static char *test_line(char *p, u32 id) {
    const struct result *r = &B.r[id];
    char *start = p;
    p = put(p, test_name(id)); p = pad(p, start, 18);
    if (r->skipped) return put(p, "skipped\r\n");
    if (!r->done) return put(p, "-\r\n");
    u32 c = cpu_x1000(id);
    switch (TESTS[id].kind) {
    case K_OPS:
        if (id == T_MANDEL) { p = fix(p, rate_x100(id), 2); p = put(p, " Miter/s  "); p = fix(p, c, 3); return put(p, " c/iter\r\n"); }
        if (id >= FIRST_FPU) {
            u32 rate = rate_x100(id);
            p = fix(p, rate, 2); p = put(p, id == T_NEON16 ? " Mops/s  " : " MFLOPS  ");
            p = fix(p, c ? 1000000u / c : 0, 3); return put(p, id == T_NEON16 ? " op/c\r\n" : " flop/c\r\n");
        }
        p = fix(p, c, 3); p = put(p, " c/op  ");
        p = fix(p, c ? 1000000u / c : 0, 3); p = put(p, " op/c  ");
        p = fix(p, rate_x100(id), 2); return put(p, " Mop/s\r\n");
    case K_BYTES:
        p = fix(p, rate_x100(id), 2); p = put(p, " MB/s  ");
        p = fix(p, c ? 1000000u / c : 0, 3); return put(p, " B/c\r\n");
    case K_LOADS:
        p = fix(p, c, 3); p = put(p, " c  "); p = fix(p, ns_x10(id), 1); return put(p, " ns\r\n");
    }
    return put(p, "\r\n");
}

static u32 render_report(char *out) {
    char *p = out;
    p = put(p, "Wonder Lantern Z30 benchmark 3\r\n");
    p = put(p, "Run "); p = dec(p, B.run_ms); p = put(p, " ms, buffer "); p = kb(p, B.buf_size);
    p = put(p, B.phase == P_DONE ? ", complete\r\n\r\n" : ", partial (FPU part pending)\r\n\r\n");
    p = put(p, cpu_name()); p = put(p, " r"); p = dec(p, (I.midr >> 20) & 15); p = put(p, "p"); p = dec(p, I.midr & 15);
    if (I.cores) { p = put(p, ", "); p = dec(p, I.cores); p = put(p, " cores in this cluster"); }
    p = put(p, ", this task on core "); p = dec(p, I.mpidr & 0xff);
    if (B.mpidr_end != I.mpidr) { p = put(p, " (later "); p = dec(p, B.mpidr_end & 0xff); p = put(p, ")"); }
    p = put(p, "\r\n");
    p = reg(p, "MIDR", I.midr); p = reg(p, "MPIDR", I.mpidr); p = reg(p, "CTR", I.ctr); p = reg(p, "CLIDR", I.clidr); p = put(p, "\r\n");
    p = reg(p, "PFR0", I.pfr0); p = reg(p, "PFR1", I.pfr1); p = reg(p, "DFR0", I.dfr0); p = reg(p, "ISAR0", I.isar0); p = put(p, "\r\n");
    p = reg(p, "L2CTLR", I.l2ctlr); p = reg(p, "PMCR", I.pmcr);
    p = put(p, (I.isar0 >> 24) & 15 ? "udiv/sdiv yes" : "udiv/sdiv no"); p = put(p, "\r\n");
    p = cache_line(p, "L1D", I.ccsidr[0]); p = cache_line(p, "L1I", I.ccsidr[1]); p = cache_line(p, "L2 ", I.ccsidr[2]);
    p = put(p, "\r\nClock ");
    if (B.mhz_x10) { p = fix(p, B.mhz_x10, 1); p = put(p, " MHz (PMU cycles over SYSTIM ms; runs"); }
    else p = put(p, "unknown (runs");
    for (u32 i = 0; i < CLOCK_RUNS; i++) { p = put(p, " "); p = fix(p, B.clock[i], 1); }
    p = put(p, ")\r\n");
    if (!I.pmu) p = put(p, "No PMU: times from SYSTIM, clock from the dependent adds\r\n");
    p = put(p, "\r\nInteger (one core, interrupts off, best of 7)\r\n");
    for (u32 id = T_DEPADD; id <= T_MANDEL; id++) p = test_line(p, id);
    p = put(p, "\r\nMemory\r\n");
    for (u32 id = T_RD_L1; id <= T_LAT_RAM; id++) {
        p = test_line(p, id);
        if (id == T_RD_L2) { p = put(p, "  (L2 block "); p = kb(p, B.l2_bytes); p = put(p, ")\r\n"); }
    }
    p = put(p, "\r\nSecurity state: ");
    switch (B.sec) {
    case SEC_SECURE: p = put(p, "secure (SCR readable, SCR="); p = hex(p, B.scr, 8); p = put(p, ")"); break;
    case SEC_NONSECURE: p = put(p, "non-secure (reading SCR is undefined)"); break;
    case SEC_SKIPPED: p = put(p, "not measured (high vectors / Thumb exceptions / no memory)"); break;
    case SEC_NO_CATCH: p = put(p, "not measured (the UDF self-test was not caught)"); break;
    default: p = put(p, "not run"); break;
    }
    p = put(p, ", catcher self-test "); p = put(p, B.sec == SEC_SECURE || B.sec == SEC_NONSECURE ? "ok" : "-");
    p = put(p, "\r\n"); p = reg(p, "SCTLR", B.sctlr); p = reg(p, "VBAR", B.vbar0); p = put(p, "\r\n");
    p = put(p, "\r\nFPU / NEON: ");
    switch (B.fpu) {
    case F_OK: p = put(p, "CP10/CP11 enabled for each chunk only"); break;
    case F_REFUSED: p = put(p, "CPACR refused CP10/CP11 access"); break;
    case F_NO_NEON: p = put(p, "VFP only, no NEON"); break;
    case F_NO_PMU: p = put(p, "skipped (needs the PMU)"); break;
    default: p = put(p, "not run"); break;
    }
    p = put(p, "\r\n");
    if (B.fpu == F_REFUSED) {
        p = reg(p, "CPACR before", B.cpacr0); p = reg(p, "after write", B.cpacr_try); p = reg(p, "NSACR", B.nsacr);
        p = put(p, "\r\n");
        if (B.sec == SEC_SECURE || (B.nsacr & 0xc00) == 0xc00)
            p = put(p, "Verdict: CP10/CP11 refused where nothing can lock them: this core has no FPU / NEON\r\n");
        else if (B.sec == SEC_NONSECURE)
            p = put(p, "Verdict: non-secure world and NSACR denies CP10/CP11: an FPU may exist, locked by the secure world\r\n");
        else
            p = put(p, "Verdict: unknown (security state not measured)\r\n");
    }
    if (B.fpu == F_OK || B.fpu == F_NO_NEON) {
        p = reg(p, "FPSID", B.fpsid); p = reg(p, "MVFR0", B.mvfr0); p = reg(p, "MVFR1", B.mvfr1); p = put(p, "\r\n");
        p = reg(p, "CPACR before", B.cpacr0); p = reg(p, "FPEXC before", B.fpexc0); p = put(p, "\r\n");
    }
    for (u32 id = FIRST_FPU; id < T_COUNT; id++) p = test_line(p, id);
    return (u32)(p - out);
}

static void write_report(void) {
    if (!B.bounce) return;
    u32 n = render_report((char *)B.bounce);
    if (n > BOUNCE) n = BOUNCE;
    struct file f;
    B.file_ok = 0;
    if (!f_open(&f, REPORT_PATH, 0x301)) return;
    int ok = f_write(&f, n);
    B.file_ok = f_close(&f) && ok;
    B.file_writes++;
}

/* ---- page and timer ---------------------------------------------------- */
static u32 *own_page(void) {
    u32 *p = FN(0x1046109b, u32 *(*)(void))();
    if (!p || (u32)(uintptr_t)p != B.page || p[0] != (PAGE_INDEX << 16 | 2)) return NULL;
    return p;
}
static const char *status_text(void);
static const char *row_text(u32 row);
static u32 text_hash(void) {
    u32 h = 2166136261u;
    for (u32 i = 0; i < 5; i++)
        for (const char *t = i ? row_text(i - 1) : status_text(); *t; t++) h = (h ^ (u8)*t) * 16777619u;
    return h;
}
/* Repaint the page, only when a text changed (each repaint briefly blanks rows). */
static void refresh(void) {
    u32 *p = own_page();
    if (!p || !p[4]) return;
    u32 h = text_hash();
    if (h == B.shown_hash) return;
    B.shown_hash = h;
    FN(((u32 *)(uintptr_t)p[4])[0], void (*)(void *))(p);          /* framework render: labels and texts */
    /* Outside an input event nothing repaints by itself: mark every row composite
     * and the row group dirty first (as the handheld / Doom menu games do). */
    u32 *group = (u32 *)(uintptr_t)p[3];
    if (p[9] && group) {
        for (u32 r = 0; r < ROWS; r++) {
            u32 *w = FN(0x1045745f, u32 *(*)(u32, u32, u32))(p[9], 1, r);
            if (w && w[3] == 2) w[17] = 1;
        }
        group[17] = 1;
        FN(0x104546df, int (*)(void *, const void *))(group, group + 4);
        FN(0x10454219, u32 (*)(void *, u32))(group, 0x10453ac3);
    }
    FN(0x10453fad, void (*)(u32, u32, u32))(0x10453ac3, 3, 7);
}
static volatile u32 *timer_slot(void) { return (volatile u32 *)(uintptr_t)(TIMERS + TIMER_SLOT * 24); }

static void tick(void);
static void stop(u32 phase, u32 error) {
    volatile u32 *slot = timer_slot();
    if (B.timer && slot[0] && slot[4] == (u32)(uintptr_t)tick) FN(0x10460d57, void (*)(u32))(TIMER_SLOT);
    B.timer = 0;
    B.run_ms = ms() - B.started_ms;
    release();
    B.phase = phase; B.error = error;
}

static void advance(void) { B.step++; B.calibrating = 0; }

static void tick(void) {
    B.timer = 0;
    if (B.phase != P_RUNNING) return;
    if (!own_page()) { stop(P_STOPPED, E_LEFT); return; }
    B.ticks++;
    u32 t0 = ms();
    while (B.step < STEP_COUNT && ms() - t0 < BUDGET_MS) {
        const struct step *s = &STEPS[B.step];
        if (s->kind == S_SETUP) {
            if (!setup()) { stop(P_ERROR, E_MEMORY); refresh(); return; }
            advance();
        } else if (s->kind == S_CLOCK) {
            clock_step();
            if (B.clock_runs >= CLOCK_RUNS) advance();
            break;                                   /* one 10 ms measurement per tick */
        } else if (s->kind == S_TEST) {
            if (test_step(s->arg, t0)) advance();
        } else if (s->kind == S_RING) {
            make_ring(s->arg); advance();
            break;                                   /* building the DRAM ring takes a while */
        } else if (s->kind == S_REPORT) {
            B.mpidr_end = MRC(0, c0, c0, 5);
            B.run_ms = ms() - B.started_ms;
            if (s->arg) B.phase = P_DONE;
            write_report();
            if (s->arg) B.phase = P_RUNNING;
            advance();
            break;
        } else if (s->kind == S_FPU) {
            fpu_probe(); advance();
        } else {
            stop(P_DONE, E_NONE); refresh(); return;
        }
    }
    refresh();
    if (B.phase != P_RUNNING) return;
    if (FN(0x10460d1d, int (*)(u32, u32, void (*)(void)))(TIMER_SLOT, TIMER_PERIOD, tick)) B.timer = 1;
    else stop(P_ERROR, E_TIMER);
}

static void start(u32 *page) {
    if (B.phase == P_RUNNING) {
        volatile u32 *slot = timer_slot();
        if (B.timer && slot[0] && slot[4] == (u32)(uintptr_t)tick) return;   /* still going */
        stop(P_STOPPED, E_LEFT);                                              /* stale: the menu closed */
    }
    B.page = (u32)(uintptr_t)page;
    if (!own_page()) { B.phase = P_ERROR; B.error = E_PAGE; return; }
    if (timer_slot()[0]) { B.phase = P_ERROR; B.error = E_TIMER; B.detail = 1; return; }
    probe();
    u8 *bounce = B.bounce;
    memset(&B, 0, sizeof(B));
    B.bounce = bounce; B.page = (u32)(uintptr_t)page; B.mpidr_end = I.mpidr;
    B.started_ms = ms();
    B.phase = P_RUNNING;
    if (FN(0x10460d1d, int (*)(u32, u32, void (*)(void)))(TIMER_SLOT, TIMER_PERIOD, tick)) B.timer = 1;
    else { B.phase = P_ERROR; B.error = E_TIMER; B.detail = 2; }
}

/* ---- row texts --------------------------------------------------------- */
static u32 total_tests(void) { return T_COUNT; }
static u32 tests_done(void) { u32 n = 0; for (u32 i = 0; i < T_COUNT; i++) n += B.r[i].done || B.r[i].skipped; return n; }

static const char *status_text(void) {
    char *p = B.status;
    switch (B.phase) {
    case P_IDLE: put(p, "Ready"); break;
    case P_RUNNING:
        p = put(p, "Running "); p = dec(p, tests_done()); p = put(p, "/"); dec(p, total_tests()); break;
    case P_DONE:
        p = put(p, "Done "); p = dec(p, (B.run_ms + 500) / 1000); put(p, B.file_ok ? "s, BENCH.TXT" : "s, no file"); break;
    case P_STOPPED: put(p, "Stopped (left page)"); break;
    case P_ERROR:
        if (B.error == E_MEMORY) put(p, "No memory");
        else if (B.error == E_TIMER) { p = put(p, "Timer busy "); dec(p, B.detail); }
        else put(p, "Page error");
        break;
    }
    return B.status;
}
static const char *row_text(u32 row) {
    char *p = B.line[row], *start = p;
    probe();
    switch (row) {
    case 0: {   /* CPU */
        const char *n = cpu_name();
        p = put(p, n[0] == 'C' && n[1] == 'o' ? n + 7 : n);   /* "Cortex-A7" -> "A7" */ p = put(p, " r"); p = dec(p, (I.midr >> 20) & 15); p = put(p, "p"); p = dec(p, I.midr & 15);
        if (I.cores) { p = put(p, " x"); p = dec(p, I.cores); }
        if (B.mhz_x10) { p = put(p, " "); p = dec(p, (B.mhz_x10 + 5) / 10); p = put(p, "MHz"); }
        else if (B.phase == P_IDLE) { p = put(p, " core "); p = dec(p, I.mpidr & 0xff); }
        break;
    }
    case 1: {   /* integer */
        u32 ipc = cpu_x1000(T_INDADD), crc = rate_x100(T_CRC);
        p = put(p, "IPC ");
        if (ipc) p = fix(p, 1000000u / ipc, 2); else p = put(p, "-");
        p = put(p, " CRC ");
        if (crc) { p = dec(p, (crc + 50) / 100); p = put(p, "MB/s"); } else p = put(p, "-");
        break;
    }
    case 2: {   /* FPU */
        u32 neon = rate_x100(B.r[T_NEONFMA].done ? T_NEONFMA : T_NEON32), vfp = rate_x100(T_VFP32);
        if (B.fpu == F_REFUSED) {
            put(p, B.sec == SEC_SECURE ? "No FPU/NEON (secure)" : B.sec == SEC_NONSECURE ? "FPU locked (non-secure)"
                                       : "FPU: CPACR refused");
            break;
        }
        if (B.fpu == F_NO_PMU) { put(p, "FPU: needs PMU"); break; }
        p = put(p, B.fpu == F_NO_NEON ? "No NEON" : "NEON ");
        if (B.fpu != F_NO_NEON) { if (neon) p = fix(p, neon / 1000, 2); else p = put(p, "-"); }
        p = put(p, " VFP ");
        if (vfp) p = fix(p, vfp / 1000, 2); else p = put(p, "-");
        p = put(p, " GF/s");
        break;
    }
    case 3: {   /* memory */
        u32 rd = rate_x100(T_RD_RAM), wr = rate_x100(T_WR_RAM), lat = ns_x10(T_LAT_RAM);
        if (B.r[T_NRD_RAM].done && rate_x100(T_NRD_RAM) > rd) rd = rate_x100(T_NRD_RAM);
        if (B.r[T_NWR_RAM].done && rate_x100(T_NWR_RAM) > wr) wr = rate_x100(T_NWR_RAM);
        p = put(p, "RAM ");
        /* MB/s x 100 / 1000 = GB/s x 100 */
        if (rd) { p = fix(p, rd / 1000, 2); p = put(p, "/"); if (wr) p = fix(p, wr / 1000, 2); else p = put(p, "-"); p = put(p, "GB/s"); } else p = put(p, "-");
        if (lat) { p = put(p, " "); p = dec(p, (lat + 5) / 10); p = put(p, "ns"); }
        break;
    }
    }
    if ((u32)(p - start) > 24) start[24] = 0;
    return B.line[row];
}

__attribute__((section(".text.entry")))
u32 bench(u32 cmd, u32 *page, u32 row) {
    switch (cmd) {
    case 0: return (u32)(uintptr_t)status_text();
    case 1: if (row == 0) start(page); return 0;
    case 2: case 3: case 4: case 5: return (u32)(uintptr_t)row_text(cmd - 2);
    }
    return 0;
}
