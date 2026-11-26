/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Handheld: a Game Boy (DMG) emulator on the extension menu's "Handheld" page.
 * Experimental branch only (experimental), not merged into main.
 *
 * Core: Peanut-GB (peanut_gb.h, MIT, Mahyar Koshkouei; parts from SameBoy).
 * Camera side: ported from the Doom port's menu game (analysis/doom,
 * camera_menu.c v8): a stock menu timer advances the emulator, the page's
 * last row widget paints the frame with vgWritePixels, and the AppMenu
 * input mode callbacks are wrapped while the game owns the page.
 *
 * Built position independent with no relocations (build.sh): one flat blob,
 * entry at offset 0. Its static state lives in the blob (the card tail is
 * RAM); the ROM, cart RAM, emulator state and screen come from the stock pool.
 *
 * Entry: handheld(cmd, page, row)
 *   cmd 0: status text (ASCII, for the Status row value)
 *   cmd 1: row action on the page object (row 0 Play, row 3 Save)
 *   cmd 2: key diagnostic text (for the Keys row value)
 */
#include <stdint.h>
#include <stddef.h>

typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8; typedef int32_t s32;
#define FN(a, t) ((t)(uintptr_t)(a))

#define ENABLE_SOUND 0
#define ENABLE_LCD 1
#define PEANUT_GB_12_COLOUR 0
#define PEANUT_GB_HIGH_LCD_ACCURACY 1
#include "peanut_gb.h"
#include "demo_rom.h"

#ifndef PAGE_INDEX
#define PAGE_INDEX 976u
#endif
#define ROWS 4u
#define PAINT_ROW (ROWS - 1u)   /* the last row composite, drawn after the others */
#define TIMER_SLOT 45u
#define TIMER_PERIOD 28u
#define NATIVE_OK 0x04370080u
#define MAX_ROM (4u * 1024u * 1024u)
#define BOUNCE 4096u
#define FRAME_US 16743u          /* 70224 cycles at 4.194304 MHz */
#define BUDGET_MS 18u
#define GB_W 160u
#define GB_H 144u
#define APPMENU 0xc182e688u
#define TIMERS 0xc20060b0u

enum { PHASE_IDLE, PHASE_LOADED, PHASE_RUNNING };
enum { E_NONE, E_FILE, E_SIZE, E_READ, E_MEMORY, E_ROM, E_PAGE, E_INPUT, E_TIMER, E_EVENTS,
       E_DRAW, E_SCREEN, E_EMU, E_SAVE, E_NOSAVE, E_SAVED };

struct hh {
    u32 phase, error, detail;
    struct gb_s *gb;
    u8 *rom, *ram, *fb, *bounce;
    u32 rom_size, ram_size, demo;
    u32 screen_raw, screen, screen_w, screen_h, screen_bytes, gx, gy, scale, zones_dirty;
    u32 page, active, timer, lock, vg_error, painted, ticks, paints, reason;
    u32 skip_vg;   /* test only (QEMU's GPU model never finishes vgWritePixels): set by the probe */
    u32 original_metadata, original_callbacks, original_leave, original_paint, draw_group, draw_widget;
    u32 callbacks[8], metadata[7], dispatch[ROWS];
    u32 events, input_original[5];
    u32 held, touch, touch_active, pulse_until;
    u32 bb_seen, snapshot_works, key_bits_seen;
    u32 last_raw, last_event, other_raw, other_event, touch_x, touch_y;
    u32 dial_seen, start_activity, rotation_buttons;
    u32 last_ms, acc_us, window_ms, window_frames, window_busy, fps, cpu, emu_error, emu_errors;
    char status[32], diag[32];
};
static struct hh H;

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

/* ---- files ------------------------------------------------------------- */
struct file { u32 obj[16]; };
static const char ROM_PATH[] = "A:\\Z30\\GAME.GB";
static const char SAVE_PATH[] = "A:\\Z30\\GAME.SAV";

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
static int f_read(struct file *f, u8 *dst, u32 n) {
    while (n) {
        u32 want = n > BOUNCE ? BOUNCE : n, got = 0;
        cache(H.bounce, BOUNCE, 3);
        u32 status = FN(0x10338415, u32 (*)(void *, void *, u32, u32 *))(f->obj, H.bounce, want, &got);
        if (status != NATIVE_OK || !got || got > want) return 0;
        cache(H.bounce, BOUNCE, 3);
        memcpy(dst, H.bounce, got);
        dst += got; n -= got;
    }
    return 1;
}
static int f_write(struct file *f, const u8 *src, u32 n) {
    while (n) {
        u32 part = n > BOUNCE ? BOUNCE : n, done = 0;
        memcpy(H.bounce, src, part);
        cache(H.bounce, BOUNCE, 3);
        /* Raw write (operation 9): 0 on success, count through r3. */
        u32 status = FN(0x102c8a6b, u32 (*)(u32, void *, u32, u32 *))(f->obj[2], H.bounce, part, &done);
        if (status || done != part) return 0;
        src += part; n -= part;
    }
    return 1;
}

/* ---- emulator callbacks ------------------------------------------------ */
static uint8_t rom_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < H.rom_size ? H.rom[a] : 0xff; }
static uint8_t ram_read(struct gb_s *gb, const uint_fast32_t a) { (void)gb; return a < H.ram_size ? H.ram[a] : 0xff; }
static void ram_write(struct gb_s *gb, const uint_fast32_t a, const uint8_t v) { (void)gb; if (a < H.ram_size) H.ram[a] = v; }
static void emu_error(struct gb_s *gb, const enum gb_error_e e, const uint16_t a) {
    (void)gb; H.emu_errors++;
    if (!H.emu_error) { H.emu_error = (u32)e + 1; H.detail = a; }
}
static void draw_line(struct gb_s *gb, const uint8_t *pixels, const uint_fast8_t line) {
    (void)gb;
    if (line >= GB_H) return;
    u8 *row = H.fb + line * GB_W;
    for (u32 x = 0; x < GB_W; x++) row[x] = pixels[x] & 3;
}

/* ---- loading ----------------------------------------------------------- */
static int load_save(void) {
    struct file f;
    if (!H.ram_size || !f_open(&f, SAVE_PATH, 0)) return 0;
    int ok = f.obj[6] == H.ram_size && f_read(&f, H.ram, H.ram_size);
    f_close(&f);
    return ok;
}
static int write_save(void) {
    struct file f;
    if (!H.ram_size) { H.error = E_NOSAVE; return 0; }
    if (!f_open(&f, SAVE_PATH, 0x301)) { H.error = E_SAVE; return 0; }
    int ok = f_write(&f, H.ram, H.ram_size);
    ok = f_close(&f) && ok;
    H.error = ok ? E_SAVED : E_SAVE;
    return ok;
}
static int load_game(void) {
    if (H.phase != PHASE_IDLE) return 1;
    if (!H.bounce) {
        u32 raw = (u32)(uintptr_t)alloc(BOUNCE + 63);
        if (!raw) { H.error = E_MEMORY; return 0; }
        H.bounce = (u8 *)(uintptr_t)((raw + 63) & ~63u);
    }
    struct file f;
    H.demo = 0;
    if (!f_open(&f, ROM_PATH, 0)) {
        /* No game on the card: run the built-in demo (tools/make_handheld_demo.py). */
        H.demo = 1;
        H.rom = (u8 *)DEMO_ROM; H.rom_size = DEMO_ROM_BYTES;
        goto start;
    }
    u32 size = f.obj[6];
    if (f.obj[7] || size < 0x8000 || size > MAX_ROM) { f_close(&f); H.error = E_SIZE; H.detail = size; return 0; }
    if (!H.rom || H.rom_size < size || H.rom == DEMO_ROM) {
        H.rom = alloc(size);
        if (!H.rom) { f_close(&f); H.error = E_MEMORY; return 0; }
    }
    int ok = f_read(&f, H.rom, size);
    if (!f_close(&f) || !ok) { H.error = E_READ; return 0; }
    H.rom_size = size;
start:
    if (!H.gb) H.gb = alloc(sizeof(struct gb_s));
    if (!H.fb) H.fb = alloc(GB_W * GB_H);
    if (!H.gb || !H.fb) { H.error = E_MEMORY; return 0; }
    memset(H.fb, 0, GB_W * GB_H);
    enum gb_init_error_e e = gb_init(H.gb, rom_read, ram_read, ram_write, emu_error, &H);
    if (e != GB_INIT_NO_ERROR) { H.error = E_ROM; H.detail = e; return 0; }
    size_t ram = 0;
    if (gb_get_save_size_s(H.gb, &ram) < 0) ram = 0;
    if (ram && !H.demo) {
        H.ram = alloc(ram);
        if (!H.ram) { H.error = E_MEMORY; return 0; }
        H.ram_size = ram;
        memset(H.ram, 0xff, ram);
        load_save();
    }
    gb_init_lcd(H.gb, draw_line);
    H.gb->direct.joypad = 0xff;
    H.phase = PHASE_LOADED;
    H.error = E_NONE;
    return 1;
}

/* ---- screen ------------------------------------------------------------ */
static const u32 PALETTE[4] = {0x00e0f8d0u, 0x0088c070u, 0x00346856u, 0x00081820u};
#define BORDER 0x00101418u
#define ZONE   0x00303840u
#define ZONE_ON 0x0070808cu
#define EDGE   0x00606c74u
#define INK    0x00e8eef0u

/* 5x7 glyphs for the zone labels. */
static const u8 GLYPHS[8][7] = {
    {14, 17, 17, 31, 17, 17, 17},   /* A */
    {30, 17, 17, 30, 17, 17, 30},   /* B */
    {14, 17, 16, 16, 16, 17, 14},   /* C */
    {31, 16, 16, 30, 16, 16, 31},   /* E */
    {16, 16, 16, 16, 16, 16, 31},   /* L */
    {30, 17, 17, 30, 20, 18, 17},   /* R */
    {15, 16, 16, 14, 1, 1, 30},     /* S */
    {31, 4, 4, 4, 4, 4, 4},         /* T */
};
static int glyph(char c) {
    switch (c) { case 'A': return 0; case 'B': return 1; case 'C': return 2; case 'E': return 3;
                 case 'L': return 4; case 'R': return 5; case 'S': return 6; case 'T': return 7; }
    return -1;
}
struct zone { u16 x0, y0, x1, y1; u8 bit; char label[7]; };
#define ZONES 5u
static struct zone zones[ZONES];

static void fill(u32 x0, u32 y0, u32 x1, u32 y1, u32 c) {
    u32 *s = (u32 *)(uintptr_t)H.screen;
    if (x1 > H.screen_w) x1 = H.screen_w;
    if (y1 > H.screen_h) y1 = H.screen_h;
    for (u32 y = y0; y < y1; y++) for (u32 x = x0; x < x1; x++) s[y * H.screen_w + x] = c;
}
static void text(u32 cx, u32 cy, const char *t, u32 scale) {
    u32 n = 0; while (t[n]) n++;
    if (!n) return;
    u32 w = (n * 6 - 1) * scale, x = cx - w / 2, y = cy - 7 * scale / 2;
    u32 *s = (u32 *)(uintptr_t)H.screen;
    for (u32 i = 0; i < n; i++) {
        int g = glyph(t[i]);
        if (g < 0) continue;
        for (u32 r = 0; r < 7; r++) for (u32 b = 0; b < 5; b++) if (GLYPHS[g][r] & (16 >> b))
            for (u32 dy = 0; dy < scale; dy++) for (u32 dx = 0; dx < scale; dx++) {
                u32 px = x + (i * 6 + b) * scale + dx, py = y + r * scale + dy;
                if (px < H.screen_w && py < H.screen_h) s[py * H.screen_w + px] = INK;
            }
    }
}
static void set_zone(u32 i, u32 x0, u32 y0, u32 x1, u32 y1, u8 bit, const char *label) {
    zones[i].x0 = (u16)x0; zones[i].y0 = (u16)y0; zones[i].x1 = (u16)x1; zones[i].y1 = (u16)y1;
    zones[i].bit = bit;
    u32 n = 0; while (label[n] && n < 6) { zones[i].label[n] = label[n]; n++; }
    zones[i].label[n] = 0;
}
static void draw_zones(void) {
    for (u32 i = 0; i < ZONES; i++) {
        struct zone *z = &zones[i];
        if (z->x1 <= z->x0) continue;
        fill(z->x0, z->y0, z->x1, z->y1, EDGE);
        fill(z->x0 + 3, z->y0 + 3, z->x1 - 3, z->y1 - 3, (H.touch & z->bit) ? ZONE_ON : ZONE);
        u32 w = z->x1 - z->x0, scale = w >= 112 ? 3 : 2;
        if (z->label[0] && z->label[1] && (u32)(6 * 6 - 1) * scale > w - 8) scale = 2;
        text((z->x0 + z->x1) / 2, (z->y0 + z->y1) / 2, z->label, scale);
    }
}
static int screen_setup(void) {
    u32 dim = 0;
    if (FN(0x103bd331, int (*)(int, void *))(2, &dim) != 0) { H.error = E_SCREEN; H.detail = 1; return 0; }
    u32 w = dim & 0xffff, h = dim >> 16;
    if (w < 320 || h < 240 || w > 1024 || h > 768) { H.error = E_SCREEN; H.detail = dim; return 0; }
    u32 bytes = w * h * 4;
    if (!H.screen_raw) {
        /* Own pixel buffers must be 256-byte aligned or the camera draws nothing. */
        H.screen_raw = (u32)(uintptr_t)alloc(bytes + 255);
        if (!H.screen_raw) { H.error = E_MEMORY; return 0; }
        H.screen = (H.screen_raw + 255) & ~255u;
        H.screen_bytes = bytes;
    }
    if (bytes > H.screen_bytes) { H.error = E_SCREEN; H.detail = dim; return 0; }
    H.screen_w = w; H.screen_h = h;
    H.scale = (w >= GB_W * 3 && h >= GB_H * 3) ? 3 : 2;
    H.gx = (w - GB_W * H.scale) / 2; H.gy = (h - GB_H * H.scale) / 2;
    fill(0, 0, w, h, BORDER);
    for (u32 i = 0; i < ZONES; i++) zones[i].x1 = 0;
    if (H.gx >= 64) {
        u32 l = H.gx, r = H.gx + GB_W * H.scale;
        set_zone(0, 0, 0, l, h / 2, JOYPAD_SELECT, "SELECT");
        set_zone(1, 0, h / 2, l, h, JOYPAD_B, "B");
        set_zone(2, r, 0, w, h / 3, JOYPAD_START, "START");
        set_zone(3, r, h / 3, w, 2 * h / 3, JOYPAD_A, "A");
        set_zone(4, r, 2 * h / 3, w, h, JOYPAD_B, "B");
    }
    draw_zones();
    cache((void *)(uintptr_t)H.screen, bytes, 1);
    return 1;
}
static void compose(void) {
    u32 *s = (u32 *)(uintptr_t)H.screen, sc = H.scale, w = H.screen_w;
    for (u32 y = 0; y < GB_H; y++) {
        const u8 *src = H.fb + y * GB_W;
        u32 *row = s + (H.gy + y * sc) * w + H.gx;
        u32 *p = row;
        for (u32 x = 0; x < GB_W; x++) {
            u32 c = PALETTE[src[x]];
            for (u32 k = 0; k < sc; k++) *p++ = c;
        }
        for (u32 k = 1; k < sc; k++) memcpy(row + k * w, row, GB_W * sc * 4);
    }
    if (H.zones_dirty) { H.zones_dirty = 0; draw_zones();
        cache((void *)(uintptr_t)H.screen, H.screen_w * H.screen_h * 4, 1);
    } else {
        cache(s + H.gy * w, GB_H * sc * w * 4, 1);
    }
}

/* ---- page ownership (from the Doom menu game) -------------------------- */
static void pause_game(void);
static void game_timer(void);

static u32 game_paint(void *widget, u32 flags);
static u32 *own_page(void) {
    u32 *p = FN(0x1046109b, u32 *(*)(void))();
    if (!p || (u32)(uintptr_t)p != H.page || p[0] != (PAGE_INDEX << 16 | 2)) return NULL;
    return p;
}
static u32 *row_widget(u32 *p) {
    if (!p || !p[9]) return NULL;
    u32 *w = FN(0x1045745f, u32 *(*)(u32, u32, u32))(p[9], 1, PAINT_ROW);
    /* +8 is the widget's sequence on the page (79 on the Doom port's page, 0x10 + row
     * here) and +12 the text kind (2 ASCII, 3 Unicode in Chinese), so identify the
     * row composite by its painter: stock, or ours while the game runs. */
    return w && (w[19] == 0x104b3855u || w[19] == (u32)(uintptr_t)game_paint) ? w : NULL;
}
static int lock(u32 v) {
    u32 expected = 0;
    return __atomic_compare_exchange_n(&H.lock, &expected, v, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}
static void unlock(void) { __atomic_store_n(&H.lock, 0, __ATOMIC_RELEASE); }

static u32 game_paint(void *widget, u32 flags) {
    H.paints++;
    u32 result = FN(H.original_paint, u32 (*)(void *, u32))(widget, flags);
    if (!H.active || !own_page() || !H.screen || !lock(2)) return result;
    if (H.skip_vg) { H.painted++; unlock(); return result; }
    int scissor = FN(0x1050a47d, int (*)(int))(0x1131);
    FN(0x1050a407, void (*)(int, int))(0x1131, 0);
    (void)FN(0x1050a2d1, int (*)(void))();
    u32 w = H.screen_w, h = H.screen_h;
    const void *last = (const void *)(uintptr_t)(H.screen + (h - 1) * w * 4);
    /* 0x00RRGGBB is VG_sXRGB_8888 (64); a negative stride flips the top-first buffer. */
    FN(0x1050afb9, void (*)(const void *, int, int, int, int, int, int))(last, -(int)(w * 4), 64, 0, 0, (int)w, (int)h);
    u32 error = FN(0x1050a2d1, u32 (*)(void))();
    FN(0x1050a307, void (*)(void))();     /* vgFinish before the buffer is reused */
    u32 finish = FN(0x1050a2d1, u32 (*)(void))();
    FN(0x1050a407, void (*)(int, int))(0x1131, scissor);
    if (error || finish) H.vg_error = error ? error : finish;
    else H.painted++;
    unlock();
    return result;
}

static void update_joypad(void) {
    if (H.gb) H.gb->direct.joypad = (u8)~(H.held | H.touch);
}
static void release_keys(void) { H.held = 0; update_joypad(); }

static u32 event_bit(u32 event) {
    switch (event) {
    case 0x253: return JOYPAD_UP;
    case 0x254: return JOYPAD_DOWN;
    case 0x255: return JOYPAD_LEFT;
    case 0x256: return JOYPAD_RIGHT;
    case 0x25b: return JOYPAD_A;
    }
    return 0;
}

static int touch_record(void *record) {
    u8 *p = record;
    if (*(u32 *)p != 1 || p[8] > 1 || p[9] > 10) return 0;
    u32 before = H.touch;
    if (!p[8] || p[9] >= 9) {
        H.touch = 0; H.touch_active = 0;
    } else {
        FN(0x102f2683, void (*)(void *))(record);   /* stock coordinate normalization */
        u32 x = *(u16 *)(p + 0x12), y = *(u16 *)(p + 0x14);
        H.touch_x = x; H.touch_y = y; H.touch_active = 1;
        u32 bits = 0;
        for (u32 i = 0; i < ZONES; i++) {
            struct zone *z = &zones[i];
            if (z->x1 > z->x0 && x >= z->x0 && x < z->x1 && y >= z->y0 && y < z->y1) bits |= z->bit;
        }
        H.touch = bits;
    }
    if (H.touch != before) { H.zones_dirty = 1; update_joypad(); }
    return 1;
}

static volatile u32 *input_slot(u32 mode) { return (volatile u32 *)(uintptr_t)(APPMENU + 0x78 + mode * 4); }

static int rotation_sample(const u32 *sample) {
    H.rotation_buttons = 1;
    if (!sample) return 0;
    H.rotation_buttons = (sample[1] & 0xfff3fcffu) | sample[6] | sample[11] | sample[16];
    s32 turn = 0;
    for (u32 i = 0; i < 4; i++) {
        s32 d = (s32)sample[0x54 / 4 + i];
        if (d < -127 || d > 127) return 0;
        if (!turn) turn = d;
    }
    if (!turn || H.rotation_buttons) return 0;
    return turn < 0 ? -1 : 1;
}
static int rotation(void) { return rotation_sample(FN(0x10356e1f, const u32 *(*)(void))()); }

static u32 call_original(u32 original, u32 type, void *data) {
    return original ? FN(original, u32 (*)(u32, void *))(type, data) : 0;
}
static void ack_queue(void) {
    if (FN(0x102f2219, int (*)(void))())
        FN(0x10462895, void (*)(u32, u32))(FN(0x1046283d, u32 (*)(void))(), 2);
}

static u32 game_input(u32 type, void *data) {
    u32 mode = *(volatile u8 *)(uintptr_t)(APPMENU + 0x74);
    u32 original = mode < 5 ? H.input_original[mode] : 0;
    if (!H.active || !own_page()) {
        if (H.active) H.reason = 3;
        pause_game();
        return call_original(original, type, data);
    }
    if (mode == 1 && type == 1) {
        /* A dial detent arrives as an activity notification first; swallow it
         * so it cannot hand the interface over and close the menu (Doom v7/v8). */
        u32 activity = FN(0x10356f2b, u32 (*)(void))();
        int turn = rotation();
        if (!turn && !H.rotation_buttons) {
            const u32 *queued = FN(0x102f1413, const u32 *(*)(u32))(3);
            if (queued && queued[0] != 1) turn = rotation_sample(queued);
        }
        if (turn) { H.dial_seen = 1; return 0; }
        if (H.dial_seen && !H.rotation_buttons && activity == H.start_activity) { H.dial_seen = 0; return 0; }
        H.dial_seen = 0;
    }
    if (mode != 1 || type != 3 || !data) return call_original(original, type, data);
    u32 state[18], pending[2], display = 0;
    FN(0x10199c41, void (*)(void *, u32))(state, sizeof(state));
    pending[0] = 0; pending[1] = 0;
    FN(0x1046309d, void (*)(void))();
    if (!*(u32 *)data && !FN(0x102f2219, int (*)(void))()) return 0;
    u32 raw = FN(0x102f1ab7, u32 (*)(void *, int, void *))(data, 0, state);
    FN(0x1045ac9d, void (*)(void *))(&display);
    if (!FN(0x1045b5dd, int (*)(void))() && (display & 255) == 3 && raw == 0x1a) return 0;
    if (raw == 0xbb) {
        /* The decoder's idle code: no key is down any more. */
        H.bb_seen = 1; release_keys(); return 0;
    }
    if (raw == 0x56) {
        if (touch_record(state)) {
            FN(0x10460d57, void (*)(u32))(0);
            *(u32 *)data = 0;
            ack_queue();
            return 0;
        }
        goto ordinary;
    }
ordinary:
    FN(0x102f14df, void (*)(void *))(pending);
    ack_queue();
    int turn = (raw >= 0x45 && raw <= 0x48) ? 1 : rotation();
    if (turn && (raw == 0 || (raw >= 6 && raw <= 9) || (raw >= 0x45 && raw <= 0x48))) {
        H.dial_seen = 1;
        FN(0x10460d57, void (*)(u32))(0);
        return 0;
    }
    u32 event = FN(0x1045933f, u32 (*)(u32))(raw);
    u32 bit = event_bit(event);
    H.last_raw = raw; H.last_event = event;
    if (bit) {
        H.held |= bit; update_joypad();
        H.pulse_until = ms() + 250;
        FN(0x10460d57, void (*)(u32))(0);    /* no stock auto-repeat */
        return 0;
    }
    H.other_raw = raw; H.other_event = event;
    /* Anything else (MENU, playback, shutter) keeps its stock handling. */
    if (!FN(0x1042ade5, int (*)(u32))(event)) return 0;
    if (event != 0x249 && FN(0x1042ab21, int (*)(void))()) FN(0x1042acc5, void (*)(void))();
    FN(0x103ce96f, void (*)(u32))(event);
    if (pending[0]) { FN(0x103ce96f, void (*)(u32))(0x249); raw = 0; }
    FN(0x10460d57, void (*)(u32))(0);
    FN(0x1045ffc3, void (*)(u32))(raw);
    FN(0x10460d1d, int (*)(u32, u32, void *))(0, FN(0x10460ebf, u32 (*)(void))(), NULL);
    return 0;
}

static void redraw(void) {
    u32 *p = own_page();
    if (!p || !p[9]) return;
    FN(H.callbacks[0], void (*)(void *))(p);
    u32 *group = (u32 *)(uintptr_t)p[3], *w = row_widget(p);
    if (group && (u32)(uintptr_t)group == H.draw_group && w &&
        (u32)(uintptr_t)w == H.draw_widget && w[19] == (u32)(uintptr_t)game_paint) {
        w[17] = 1; group[17] = 1;
        FN(0x104546df, int (*)(void *, const void *))(group, group + 4);
        FN(0x10454219, u32 (*)(void *, u32))(group, 0x10453ac3);
    }
    FN(0x10453fad, void (*)(u32, u32, u32))(0x10453ac3, 3, 7);
}

static void keys_tick(void) {
    if (!H.held) return;
    const u32 *sample = FN(0x10356e1f, const u32 *(*)(void))();
    u32 bits = sample ? ((sample[1] & 0xfff3fcffu) | sample[6] | sample[11] | sample[16]) : 0;
    if (bits) { H.snapshot_works = 1; H.key_bits_seen |= bits; }
    else if (H.snapshot_works) { release_keys(); return; }
    /* Neither release signal seen yet on this camera: fall back to a pulse. */
    if (!H.bb_seen && !H.snapshot_works && (s32)(ms() - H.pulse_until) >= 0) release_keys();
}

static void run_frames(void) {
    u32 now = ms(), elapsed = now - H.last_ms, dt = elapsed > 100 ? 100 : elapsed;
    H.last_ms = now;
    H.acc_us += dt * 1000;
    u32 n = 0, t0 = ms();
    /* At most BUDGET_MS of emulation per tick: the menu task must stay responsive,
     * so a slow CPU drops frames instead of starving the other tasks. */
    while (H.acc_us >= FRAME_US && n < 3 && !H.emu_error) {
        gb_run_frame(H.gb);
        H.acc_us -= FRAME_US; n++;
        if (ms() - t0 >= BUDGET_MS) break;
    }
    if (H.acc_us > 2 * FRAME_US) H.acc_us = 0;   /* too slow: drop the backlog */
    u32 t1 = ms();
    /* fps: emulated frames per real second; cpu: share of real time spent emulating. */
    H.window_busy += t1 - t0; H.window_frames += n; H.window_ms += elapsed;
    if (H.window_ms >= 1000) {
        H.fps = H.window_frames * 1000 / H.window_ms;
        H.cpu = H.window_busy * 100 / H.window_ms;
        H.window_ms = H.window_frames = H.window_busy = 0;
    }
    if (n && lock(1)) { compose(); unlock(); }
}

static void game_timer(void) {
    H.timer = 0;
    if (!H.active) return;
    if (!own_page()) { H.reason = 2; pause_game(); return; }
    H.ticks++;
    keys_tick();
    if (!H.vg_error && !H.emu_error) run_frames();
    if (H.vg_error || H.emu_error) {
        if (H.vg_error) { H.error = E_DRAW; H.detail = H.vg_error; }
        else { H.error = E_EMU; }
        pause_game(); redraw(); return;
    }
    redraw();
    if (!H.active) return;            /* the page was left during this tick */
    if (FN(0x10460d1d, int (*)(u32, u32, void (*)(void)))(TIMER_SLOT, TIMER_PERIOD, game_timer)) H.timer = 1;
    else { H.error = E_TIMER; H.detail = 3; pause_game(); }
}

static void pause_game(void) {
    H.active = 0;
    H.touch_active = 0; H.touch = 0; H.held = 0;
    update_joypad();
    for (u32 i = 0; i < 5; i++) {
        volatile u32 *input = input_slot(i);
        if (*input == (u32)(uintptr_t)game_input) *input = H.input_original[i];
    }
    volatile u32 *slot = (volatile u32 *)(uintptr_t)(TIMERS + TIMER_SLOT * 24);
    if (H.timer && slot[0] && slot[4] == (u32)(uintptr_t)game_timer) FN(0x10460d57, void (*)(u32))(TIMER_SLOT);
    H.timer = 0;
    u32 *p = (u32 *)(uintptr_t)H.page;
    if (p && p[0] == (PAGE_INDEX << 16 | 2)) {
        u32 *w = row_widget(p);
        if (w && (u32)(uintptr_t)w == H.draw_widget && w[19] == (u32)(uintptr_t)game_paint) w[19] = H.original_paint;
        if (p[4] == (u32)(uintptr_t)H.callbacks) p[4] = H.original_callbacks;
        if (p[10] == (u32)(uintptr_t)H.metadata) p[10] = H.original_metadata;
    }
    if (H.phase == PHASE_RUNNING) H.phase = PHASE_LOADED;
    __asm__ volatile("dmb ish" ::: "memory");
}
static u32 game_leave(void *page) {
    H.reason = 1;
    pause_game();
    return H.original_leave ? FN(H.original_leave, u32 (*)(void *))(page) : 0;
}
static int nop_event(void) { return 0; }

static int start_game(u32 *page) {
    H.page = (u32)(uintptr_t)page;
    u32 *p = own_page(), *w = row_widget(p);
    if (!p) { H.error = E_PAGE; H.detail = 1; return 0; }
    if (!w) { H.error = E_PAGE; H.detail = 4; return 0; }
    if (!p[3] || !p[4] || !p[10]) { H.error = E_PAGE; H.detail = 5; return 0; }
    u32 *group = (u32 *)(uintptr_t)p[3];
    if (group[19] != 0x102bd3bb || w[19] != 0x104b3855) { H.error = E_PAGE; H.detail = 2; return 0; }
    static const u32 stock_input[5] = {0x103cf513, 0x103cf78f, 0x103cf897, 0x103cf897, 0x103cf8bf};
    for (u32 i = 0; i < 5; i++) if (*input_slot(i) != stock_input[i]) { H.error = E_INPUT; H.detail = i; return 0; }
    volatile u32 *timer = (volatile u32 *)(uintptr_t)(TIMERS + TIMER_SLOT * 24);
    if (timer[0]) { H.error = E_TIMER; H.detail = 1; return 0; }
    u32 *metadata = (u32 *)(uintptr_t)p[10];
    u32 *dispatch = (u32 *)(uintptr_t)metadata[4];
    if (!dispatch || (metadata[5] & 0xffff) != ROWS) { H.error = E_PAGE; H.detail = 3; return 0; }
    const u32 *original = (const u32 *)(uintptr_t)dispatch[0];
    u32 count = 0;
    while (count < 200 && original[count * 4] != 0x31e) count++;
    if (count == 200) { H.error = E_EVENTS; return 0; }
    count++;
    static const u16 owned[] = {0x253, 0x254, 0x255, 0x256, 0x25b, 0x257, 0x258, 0x259, 0x25a,
                                0x26d, 0x26e, 0x26f, 0x27e};
    const u32 n = sizeof(owned) / sizeof(owned[0]);
    if (!H.events) H.events = (u32)(uintptr_t)alloc((count + n) * 16);
    if (!H.events) { H.error = E_MEMORY; return 0; }
    u32 *events = (u32 *)(uintptr_t)H.events;
    for (u32 i = 0; i < n; i++) {
        events[i * 4] = owned[i]; events[i * 4 + 1] = 0;
        events[i * 4 + 2] = (u32)(uintptr_t)nop_event; events[i * 4 + 3] = 0x3bb;
    }
    memcpy(events + n * 4, original, count * 16);
    for (u32 i = 0; i < ROWS; i++) H.dispatch[i] = H.events;
    if (!screen_setup()) return 0;
    H.original_callbacks = p[4]; H.original_metadata = p[10]; H.original_paint = w[19];
    H.draw_group = p[3]; H.draw_widget = (u32)(uintptr_t)w;
    memcpy(H.callbacks, (void *)(uintptr_t)p[4], 32); memcpy(H.metadata, metadata, 28);
    H.original_leave = H.callbacks[1]; H.callbacks[1] = (u32)(uintptr_t)game_leave;
    H.metadata[4] = (u32)(uintptr_t)H.dispatch;
    if (!cache(events, (count + n) * 16, 1)) { H.error = E_EVENTS; H.detail = 2; return 0; }
    if (!FN(0x10460d1d, int (*)(u32, u32, void (*)(void)))(TIMER_SLOT, TIMER_PERIOD, game_timer)) { H.error = E_TIMER; H.detail = 2; return 0; }
    H.timer = 1; H.active = 1; H.vg_error = 0; H.emu_error = 0; H.reason = 0;
    H.ticks = 0; H.paints = 0; H.painted = 0; H.held = 0; H.touch = 0;
    H.last_ms = ms(); H.acc_us = FRAME_US; H.window_ms = H.window_frames = H.window_busy = 0;
    H.start_activity = FN(0x10356f2b, u32 (*)(void))(); H.dial_seen = 0;
    update_joypad();
    p[4] = (u32)(uintptr_t)H.callbacks; p[10] = (u32)(uintptr_t)H.metadata;
    w[19] = (u32)(uintptr_t)game_paint; w[17] = 1; group[17] = 1;
    for (u32 i = 0; i < 5; i++) {
        volatile u32 *input = input_slot(i);
        H.input_original[i] = *input;
        *input = (u32)(uintptr_t)game_input;
    }
    H.phase = PHASE_RUNNING; H.error = E_NONE;
    __asm__ volatile("dmb ish" ::: "memory");
    return 1;
}

/* ---- menu rows --------------------------------------------------------- */
static void action(u32 *page, u32 row) {
    if (H.active) return;
    if (row == 0) {
        if (load_game()) start_game(page);
    } else if (row == 3) {
        if (H.phase == PHASE_IDLE) H.error = E_NOSAVE;
        else write_save();
    }
}
static const char *status_text(void) {
    char *p = H.status;
    switch (H.error) {
    case E_FILE: put(p, "No Z30/GAME.GB"); return H.status;
    case E_SIZE: p = put(p, "ROM size "); dec(p, H.detail); return H.status;
    case E_READ: put(p, "ROM read failed"); return H.status;
    case E_MEMORY: put(p, "No memory"); return H.status;
    case E_ROM: p = put(p, "Bad ROM "); dec(p, H.detail); return H.status;
    case E_PAGE: p = put(p, "Page busy "); dec(p, H.detail); return H.status;
    case E_INPUT: p = put(p, "Input busy "); dec(p, H.detail); return H.status;
    case E_TIMER: p = put(p, "Timer busy "); dec(p, H.detail); return H.status;
    case E_EVENTS: put(p, "Event table"); return H.status;
    case E_DRAW: p = put(p, "Draw err "); hex(p, H.detail, 4); return H.status;
    case E_SCREEN: p = put(p, "Screen "); hex(p, H.detail, 8); return H.status;
    case E_EMU: p = put(p, "Emu err "); p = dec(p, H.emu_error - 1); *p++ = ' '; hex(p, H.detail, 4); return H.status;
    case E_SAVE: put(p, "Save failed"); return H.status;
    case E_NOSAVE: put(p, "No save RAM"); return H.status;
    case E_SAVED: put(p, "Saved"); return H.status;
    }
    if (H.phase == PHASE_IDLE) { put(p, "Ready"); return H.status; }
    if (!H.ticks) { put(p, "Loaded"); return H.status; }
    if (!H.painted) { p = put(p, H.paints ? "Draw skipped " : "No draw "); dec(p, H.ticks); return H.status; }
    if (H.demo) p = put(p, "Demo ");
    p = dec(p, H.fps); p = put(p, "fps cpu "); p = dec(p, H.cpu); put(p, "%");
    return H.status;
}
static const char *diag_text(void) {
    char *p = H.diag;
    p = put(p, "R"); p = hex(p, H.last_raw, 2); p = put(p, " E"); p = hex(p, H.last_event, 3);
    p = put(p, " O"); p = hex(p, H.other_event, 3);
    p = put(p, H.snapshot_works ? " K" : H.bb_seen ? " B" : " P");
    if (H.snapshot_works) hex(p, H.key_bits_seen, 8);
    return H.diag;
}

__attribute__((section(".text.entry")))
u32 handheld(u32 cmd, u32 *page, u32 row) {
    switch (cmd) {
    case 0: return (u32)(uintptr_t)status_text();
    case 1: action(page, row); return 0;
    case 2: return (u32)(uintptr_t)diag_text();
    }
    return 0;
}
