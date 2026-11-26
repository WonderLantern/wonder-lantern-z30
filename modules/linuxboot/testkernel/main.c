/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Test kernel for the Linux handoff (modules/linuxboot). It is started exactly
 * as a zImage would be and proves, on the screen and in memory, that the camera
 * is ours: it reads the LCD overlay planes from the DTB the handoff patched,
 * paints them (YUV 4:2:2 "Y0 Y1 U V" pairs plus an alpha plane, the stock GUI
 * overlay format), and keeps an uptime counter from the ARM generic timer.
 *
 * A signature block at 0x9FFFF000 lets the QEMU probe check the same without a
 * screen. No VFP (this core has none), no MMU, no interrupts.
 */
#include <stdint.h>
#include <stddef.h>
#include "font.h"

typedef uint64_t u64; typedef uint32_t u32; typedef uint8_t u8;
#define SIGNATURE 0x9ffff000u

struct overlay { u8 *color, *alpha; u32 w, h, cstride, astride; };

void *memset(void *d, int c, size_t n) { u8 *a = d; while (n--) *a++ = (u8)c; return d; }
void *memcpy(void *d, const void *s, size_t n) { u8 *a = d; const u8 *b = s; while (n--) *a++ = *b++; return d; }

static u32 be(const void *p) { const u8 *b = p; return (u32)b[0] << 24 | (u32)b[1] << 16 | (u32)b[2] << 8 | b[3]; }
static int same(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }

/* Value of the first property `name` (a single u32 cell) anywhere in the tree. */
static int fdt_u32(const u8 *fdt, const char *name, u32 *out) {
    if (be(fdt) != 0xd00dfeedu) return 0;
    const u8 *st = fdt + be(fdt + 8), *strings = fdt + be(fdt + 12);
    u32 size = be(fdt + 36), o = 0;
    while (o + 4 <= size) {
        u32 tok = be(st + o); o += 4;
        if (tok == 1) { while (st[o]) o++; o = (o + 4) & ~3u; }          /* BEGIN_NODE name */
        else if (tok == 3) {                                              /* PROP len nameoff value */
            u32 len = be(st + o), off = be(st + o + 4);
            if (same((const char *)strings + off, name) && len >= 4) { *out = be(st + o + 8); return 1; }
            o = (o + 8 + len + 3) & ~3u;
        } else if (tok == 9) break;                                       /* END */
    }
    return 0;
}

static u64 ticks(void) { u32 lo, hi; __asm__ volatile("isb\n\tmrrc p15, 0, %0, %1, c14" : "=r"(lo), "=r"(hi)); return (u64)hi << 32 | lo; }

/* ---- drawing -------------------------------------------------------------- */
struct yuv { u8 y, u, v; };
static const struct yuv BG = {28, 140, 118}, INK = {235, 128, 128}, ACCENT = {170, 166, 16}, DIM = {120, 128, 128};

static void pixel_pair(struct overlay *o, u32 x, u32 y, struct yuv c) {
    if (x + 1 >= o->w || y >= o->h) return;
    u8 *q = o->color + y * o->cstride + (x & ~1u) * 2;
    q[0] = c.y; q[1] = c.y; q[2] = c.u; q[3] = c.v;
    o->alpha[y * o->astride + (x & ~1u)] = 255; o->alpha[y * o->astride + (x & ~1u) + 1] = 255;
}
static void rect(struct overlay *o, u32 x0, u32 y0, u32 x1, u32 y1, struct yuv c) {
    for (u32 y = y0; y < y1 && y < o->h; y++) for (u32 x = x0 & ~1u; x < x1 && x + 1 < o->w; x += 2) pixel_pair(o, x, y, c);
}
/* Text at 2-pixel granularity (each font dot is `s` x `s`, s even). */
static u32 text(struct overlay *o, u32 x, u32 y, const char *t, u32 s, struct yuv c) {
    for (; *t; t++, x += 6 * s) {
        int ch = *t >= 'a' && *t <= 'z' ? *t - 32 : *t;
        if (ch < 32 || ch > 90) continue;
        for (u32 r = 0; r < 7; r++) for (u32 b = 0; b < 5; b++)
            if (FONT[ch - 32][r] & (16 >> b)) rect(o, x + b * s, y + r * s, x + b * s + s, y + r * s + s, c);
    }
    return x;
}
static char *dec(char *p, u32 v, u32 width) {
    char t[10]; u32 n = 0;
    do { t[n++] = (char)('0' + v % 10); v /= 10; } while ((v || n < width) && n < 10);
    while (n) *p++ = t[--n];
    *p = 0; return p;
}
static char *hex(char *p, u32 v) {
    for (int i = 7; i >= 0; i--) { u32 d = (v >> (i * 4)) & 15; *p++ = (char)(d < 10 ? '0' + d : 'A' + d - 10); }
    *p = 0; return p;
}
static char *put(char *p, const char *s) { while (*s) *p++ = *s++; *p = 0; return p; }

void tk_main(const u8 *fdt) {
    volatile u32 *sig = (volatile u32 *)(uintptr_t)SIGNATURE;
    sig[0] = 0x4c30335a; sig[1] = 0x58554e49;    /* "Z30LINUX" */
    sig[2] = 0; sig[3] = (u32)(uintptr_t)fdt;
    u32 frq; __asm__ volatile("mrc p15, 0, %0, c14, c0, 0" : "=r"(frq));
    struct overlay o = {0, 0, 0, 0, 0, 0};
    u32 v[6];
    int ok = fdt_u32(fdt, "z30,overlay-color", &v[0]) && fdt_u32(fdt, "z30,overlay-alpha", &v[1]) &&
             fdt_u32(fdt, "z30,width", &v[2]) && fdt_u32(fdt, "z30,height", &v[3]) &&
             fdt_u32(fdt, "z30,color-stride", &v[4]) && fdt_u32(fdt, "z30,alpha-stride", &v[5]);
    u32 gicd = 0; fdt_u32(fdt, "z30,gicd", &gicd);
    sig[4] = ok; sig[5] = frq; sig[6] = v[0]; sig[7] = v[2] | v[3] << 16;
    if (!ok || !frq || v[2] < 320 || v[3] < 240 || v[4] < v[2] * 2 || v[5] < v[2]) { sig[2] = 0xbad0000du; for (;;) {} }
    o.color = (u8 *)(uintptr_t)v[0]; o.alpha = (u8 *)(uintptr_t)v[1];
    o.w = v[2]; o.h = v[3]; o.cstride = v[4]; o.astride = v[5];
    sig[2] = 0x600df00du;

    rect(&o, 0, 0, o.w, o.h, BG);
    rect(&o, 0, 0, o.w, 6, ACCENT);
    text(&o, 24, 28, "WONDER LANTERN Z30", 4, ACCENT);
    text(&o, 24, 76, "LINUX HANDOFF OK", 4, INK);
    char line[48], *p;
    text(&o, 24, 140, "CORE 0 ALONE, CORE 1 PARKED", 2, DIM);
    text(&o, 24, 162, "MMU OFF, WATCHDOG OFF", 2, DIM);
    p = put(line, "DTB "); p = hex(p, (u32)(uintptr_t)fdt); p = put(p, "  GIC "); hex(p, gicd);
    text(&o, 24, 184, line, 2, DIM);
    p = put(line, "TIMER "); p = dec(p, frq / 1000000, 1); put(p, " MHZ");
    text(&o, 24, 206, line, 2, DIM);
    text(&o, 24, o.h - 40, "PULL THE BATTERY TO RETURN", 2, DIM);

    u64 t0 = ticks();
    u32 shown = ~0u;
    for (;;) {
        u32 s = (u32)((ticks() - t0) / frq);
        sig[8] = s; sig[9]++;
        if (s != shown) {
            shown = s;
            p = put(line, "UPTIME "); p = dec(p, s, 5); put(p, " S");
            rect(&o, 24, 250, 24 + 6 * 4 * 16, 250 + 7 * 4, BG);
            text(&o, 24, 250, line, 4, INK);
        }
    }
}
