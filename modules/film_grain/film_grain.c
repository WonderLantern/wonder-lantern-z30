/* SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Film grain on the luma plane just before the stock JPEG encoder (Z30 C1.20).
 *
 * The algorithm, fields and curves are ported from the Wonder Lantern Z5 FILM
 * feature (A52, MIT licence, Z5 Extensions contributors). The pixel contract is
 * z30_extensions.host.film_grain.grain_plane; tools/verify_film_grain.py runs
 * this payload in Unicorn and compares every byte.
 *
 * Built position independent with no data or relocations (build.sh): the
 * tables arrive through the call block, the state block is the extension
 * menu's RAM. Only stock addresses are absolute.
 */
#include <arm_acle.h>

typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

/* The call block the encode gate builds on its stack. */
struct call {
    const u32 *args;     /* stock encode argument block; args[0] = image descriptor */
    const u8 *ctx;       /* capture context (r4 of the step family) */
    u32 site;            /* return address of the encode call (site + 5) */
    u32 *state;          /* this page's words in the extension menu state */
    const u8 *data;      /* host.film_grain.data_blob() */
};

/* State words (features/photo/film_grain_ui.py lays them out). */
enum { ST_STYLE = 0, ST_FD = 1, ST_STRENGTH = 2, ST_CODE = 3, ST_MS = 4, ST_COUNT = 5,
       ST_PAR = 6, ST_CALLS = 7, ST_SIZE = 8, ST_SITE = 9, ST_TEXT = 10, ST_WORDS = 16 };
/* Result codes, also shown as "skip N" on the menu page. */
enum { R_OK = 1, R_DESCRIPTOR = 3, R_MODE = 4, R_TRANSLATION = 5, R_MEMORY = 6 };

/* Return addresses of the five capture-context encode calls (features/photo/date_stamp.CALL_SITES). */
#define SITE_MAIN    0x100A619Du   /* JPEG main image */
#define SITE_SECOND  0x100A6309u   /* full-size image embedded in the NEF */
#define SITE_PREV_A  0x100A6545u
#define SITE_PREV_B  0x100A66B3u
#define SITE_PREV_C  0x100A684Fu
/* Capture info [ctx + 0x3A4], recording kind at +0x228 (stock 0x101D5AD0 / 0x101D5AE0). */
#define INFO_FIELD   0x3A4u
#define KIND_OFFSET  0x228u
#define KIND_RAW_JPEG 0u
#define KIND_RAW      1u
#define KIND_JPEG     3u
#define MS_CLOCK     (*(volatile u32 *)0xC18EB098u)
/* Stock line maintenance (r0 address, r1 length); each row stays below 0x45000 bytes. */
#define CACHE_CLEAN_INVALIDATE ((void (*)(void *, u32))0x10204B7Du)
#define CACHE_CLEAN            ((void (*)(void *, u32))0x10204B0Du)

#define STRENGTHS     6u
#define FIELD_BYTES   65536u
#define LUT_BYTES     1024u
#define LUTS_OFFSET   (2u * FIELD_BYTES)
#define SEED_ROW      0x9E3779B1u
#define SEED_COLUMN   0x85EBCA6Bu

static inline __attribute__((always_inline)) u32 hash32(u32 v)
{
    v ^= v >> 16;
    v *= 0x7FEB352Du;
    v ^= v >> 15;
    v *= 0x846CA68Bu;
    return v ^ (v >> 16);
}

/* Four luma bytes v, four field levels n (0..63, 32 = zero). */
static inline __attribute__((always_inline)) u32 grain_word(u32 v, u32 n, const u8 *lut)
{
    u32 step = lut[__usad8(v, 0u)];
    u32 even = ((__uxtb16(n) * step + 0x00080008u) >> 4) & 0x00FF00FFu;
    u32 odd = ((__uxtb16((n >> 8) | (n << 24)) * step + 0x00080008u) << 4) & 0xFF00FF00u;
    u32 delta = __usub8(even | odd, step * 0x02020202u);
    return (u32)__qadd8((int)(v ^ 0x80808080u), (int)delta) ^ 0x80808080u;
}

static void grain_rows(u8 *luma, u32 pitch, u32 words, u32 rows, u32 seed,
                       const u8 *field, const u8 *lut, u32 maintain)
{
    u32 block[8];
    for (u32 y = 0; y < rows; ++y) {
        u32 *row = (u32 *)(luma + y * pitch);
        const u32 *tile = (const u32 *)field;
        u32 offset = 0u;
        if (maintain)
            CACHE_CLEAN_INVALIDATE(row, 4u * words);
        for (u32 x = 0; x < words; x += 8u) {
            if (!(x & 63u)) {
                /* One phase per 256x256 region; neighbours differ, which hides the tile. */
                u32 phase = hash32(seed ^ ((y >> 8) * SEED_ROW) ^ (x * SEED_COLUMN));
                tile = (const u32 *)(field + (((y + (phase >> 8)) & 255u) << 8));
                offset = phase & 56u;
            }
            /* x and offset are multiples of 8, so a group never wraps inside the field row. */
            const u32 *noise = tile + ((x + offset) & 63u);
            u32 *p = row + x;
            if (x + 8u <= words) {
                /* Eight words per bus transfer: the plane may be uncached (Z5 measured
                 * 309 ms word by word for 512 rows, 41 ms with LDM/STM). */
                __asm__ volatile ("ldmia %0, {r2-r8, r12}\n\tstmia %1, {r2-r8, r12}"
                                  :: "r"(p), "r"(block) : "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r12", "memory");
                for (u32 i = 0; i < 8u; ++i)
                    block[i] = grain_word(block[i], noise[i], lut);
                __asm__ volatile ("ldmia %1, {r2-r8, r12}\n\tstmia %0, {r2-r8, r12}"
                                  :: "r"(p), "r"(block) : "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r12", "memory");
            } else {
                for (u32 i = 0; x + i < words; ++i)
                    p[i] = grain_word(p[i], noise[i], lut);
            }
        }
        if (maintain)
            CACHE_CLEAN(row, 4u * words);
    }
}

static u32 translate(u32 va)
{
    u32 cpsr, par = va;
    __asm__ volatile ("mrs %0, cpsr" : "=r"(cpsr));
    __asm__ volatile ("cpsid i" ::: "memory");
    __asm__ volatile ("mcr p15, 0, %0, c7, c8, 1\n\tisb\n\tmrc p15, 0, %0, c7, c4, 0" : "+r"(par) :: "memory");
    if (!(cpsr & 0x80u))
        __asm__ volatile ("cpsie i" ::: "memory");
    return par;
}

/* 0 = refuse, 1 = uncached normal memory, 2 = cacheable; *first = PAR of the first page. */
static u32 check_pages(u32 begin, u32 end, u32 *first)
{
    u32 cpsr, sctlr, ttbcr, kind = 1u;
    __asm__ volatile ("mrs %0, cpsr" : "=r"(cpsr));
    if ((cpsr & 31u) != 0x13u && (cpsr & 31u) != 0x1Fu)
        return 0u;
    __asm__ volatile ("mrc p15, 0, %0, c1, c0, 0" : "=r"(sctlr));
    __asm__ volatile ("mrc p15, 0, %0, c2, c0, 2" : "=r"(ttbcr));
    if (!(sctlr & 1u) || (ttbcr & 0x80000000u))
        return 0u;
    for (u32 va = begin & ~0xFFFu; va < end; va += 0x1000u) {
        u32 par = translate(va);
        if (va == (begin & ~0xFFFu))
            *first = par;
        /* Fault, or the CPU address is not the address the encoder hardware reads. */
        u32 pa = (par & 2u) ? (par & 0xFF000000u) | (va & 0x00FFF000u) : par & 0xFFFFF000u;
        if ((par & 1u) || pa != va)
            return 0x100u;
        u32 inner = (par >> 4) & 7u, outer = (par >> 2) & 3u;
        if (inner == 1u || inner == 3u)
            return 0x200u;                       /* strongly ordered or device */
        if (inner || outer)
            kind = 2u;
    }
    return kind;
}

static char *put_number(char *out, u32 value)
{
    char digits[10];
    u32 n = 0;
    do {
        digits[n++] = (char)('0' + value % 10u);
        value /= 10u;
    } while (value && n < 10u);
    while (n)
        *out++ = digits[--n];
    return out;
}

static void report(u32 *st, u32 code, u32 ms, u32 cached)
{
    char *text = (char *)(st + ST_TEXT), *out = text;
    st[ST_CODE] = code;
    if (code == R_OK) {
        st[ST_MS] = ms;
        st[ST_COUNT] += 1u;
        out = put_number(out, ms);
        *out++ = ' '; *out++ = 'm'; *out++ = 's'; *out++ = ' ';
        *out++ = cached ? 'c' : 'n';
        *out++ = ' '; *out++ = '#';
        out = put_number(out, st[ST_COUNT]);
    } else {
        *out++ = 's'; *out++ = 'k'; *out++ = 'i'; *out++ = 'p'; *out++ = ' ';
        out = put_number(out, code);
    }
    *out = 0;
}

__attribute__((section(".text.entry"))) u32 film_grain(const struct call *c)
{
    u32 *st = c->state;
    u32 style = st[ST_STYLE];
    if (style < 1u || style > 2u)
        return 0u;
    u32 code = st[ST_STRENGTH];
    u32 strength = (code < STRENGTHS ? code + 2u : 2u) % STRENGTHS + 1u;   /* 3, 4, 5, 6, 1, 2 */
    const u8 *info = *(const u8 *const *)(c->ctx + INFO_FIELD);
    if (!info)
        return 0u;
    u32 kind = info[KIND_OFFSET], site = c->site, preview = 0u;
    /* Z5 policy: JPEG only -> main image and its previews; RAW+JPEG -> the main image,
     * whose plane the NEF preview shares; RAW only -> the NEF's full-size preview. */
    if (site == SITE_MAIN) {
        if (kind != KIND_JPEG && kind != KIND_RAW_JPEG)
            return 0u;
    } else if (site == SITE_SECOND) {
        if (kind != KIND_RAW)
            return 0u;
    } else if (site == SITE_PREV_A || site == SITE_PREV_B || site == SITE_PREV_C) {
        if (kind != KIND_JPEG)
            return 0u;
        preview = 1u;
    } else {
        return 0u;
    }
    u32 begin = MS_CLOCK;
    u32 calls = st[ST_CALLS] += 1u;
    const volatile u8 *d = (const volatile u8 *)c->args[0];
    u32 result = R_DESCRIPTOR, cached = 0u, par = 0u;
    if (d) {
        u32 format = d[0];
        u32 pitch = d[2] | (u32)d[3] << 8;
        u32 width = d[10] | (u32)d[11] << 8;
        u32 rows = d[12] | (u32)d[13] << 8;
        u32 yb = d[20] | (u32)d[21] << 8 | (u32)d[22] << 16 | (u32)d[23] << 24;
        u32 cb = d[24] | (u32)d[25] << 8 | (u32)d[26] << 16 | (u32)d[27] << 24;
        u32 end = yb + (rows - 1u) * pitch + width;
        if (!preview) {
            st[ST_SIZE] = width | rows << 16;
            st[ST_SITE] = site;
        }
        if ((format == 1u || format == 2u) && width >= 16u && rows >= 8u && width <= 8192u && rows <= 8192u &&
            pitch >= width && yb && !((yb | pitch) & 3u) && end > yb && !(cb > yb && end > cb)) {
            u32 pages = check_pages(yb, end, &par);
            if (!pages) {
                result = R_MODE;
            } else if (pages == 0x100u) {
                result = R_TRANSLATION;
            } else if (pages == 0x200u) {
                result = R_MEMORY;
            } else {
                cached = pages == 2u;
                u32 lut = LUTS_OFFSET + (((style - 1u) * STRENGTHS + strength - 1u) * 2u + preview) * LUT_BYTES;
                u32 seed = (MS_CLOCK ^ yb ^ (calls * SEED_ROW)) | 1u;
                grain_rows((u8 *)yb, pitch, width / 4u, rows, seed,
                           c->data + (style - 1u) * FIELD_BYTES, c->data + lut, cached);
                result = R_OK;
            }
        }
    }
    if (!preview) {
        st[ST_PAR] = par;
        report(st, result, MS_CLOCK - begin, cached);
    }
    return result;
}
