/* Auto-arm (research SD module for the Wonder Lantern module host).
 *
 * Goal (2026-11-17): on the shooting screen in Auto-area AF the camera
 * sits in the tracking-ready screen (the reticle OK brings up) without any
 * button press, so a half-press starts tracking and upstream's 3D tracking
 * can hand it to a face or animal. No button is remapped. When tracking ends
 * or the user backs out, the module brings the reticle back right away.
 *
 * It sends one OK press when all of these hold on an IMU tick:
 *   - photo live view with nothing open over it: UI phase byte 0xC181F6FC is
 *     1 or 2, UI state byte 0xC18292AC is 0 (3 while the i menu is open) and
 *     the photo backend word 0xC181B278 is 1. Upstream's portrait UI and
 *     aspect mask use this same context check, confirmed on a camera
 *     (docs/portrait-ui.md, upstream 8715c25);
 *   - photo, not movie: PHOTO_SEL byte 0xC237CBB1 is 0 (1 in movie mode; the
 *     backend word stayed 1 in movie mode on a Z30, 2026-11-17);
 *   - Auto-area AF: AF-area mode byte 0xC237C7F8 is 6 (upstream PR #36 rev 6);
 *   - idle: 0xC237F680 (AF working: tracking or half-press), 0xC237F684
 *     (tracking box shown, screen 1) and 0xC237F688 (tracking) are all 0
 *     (upstream af-tracking-research, camera logs);
 *   - those held for SETTLE consecutive ticks, and no OK is still pending.
 * After a press it waits until the tracking box shows or WAIT ticks pass.
 *
 * Pieces reused from upstream, run on a Z30 C 1.20 (PR #36):
 *   - the IMU-task hook cell 0xC1832D48 (imu_hook, about every 50 ms), which
 *     builds together with 3d-tracking (tools/build_af_log.py --module log);
 *   - the input ring 0xC2008EA0 (16 x 0x190; next slot in 0xC182004C) and the
 *     v6 OK press made from the IMU task: copy the newest real entry, set every
 *     key word (+4 / +0x18 / +0x2C / +0x40: cur, prev, changed, pressed,
 *     released) to "no change" except key word 2 bit 0 (OK), and pass it to
 *     0x102F0D14 then 0x102F1844, once down and once up.
 *
 * Unverified: that overlays other than the i menu also change the UI state,
 * and that an OK sent at live-view entry is accepted. Build without -DREMAP
 * first: it logs every tick on which it would press, with the state bytes,
 * and presses nothing.
 *
 * Run 1: arm.  Run 2: disarm and write A:\Z30\ARnnnnnn.BIN.  Power off disarms.
 * File: "AUTOARM1", u32 count, u32 presses, u32 ticks, u32 rec_bytes (12, 28 with DIAG),
 *       u32 flags (bit 0 = REMAP, bit 1 = DIAG, bit 2 = DIRECT build), u32 0, then count x rec.
 */
#include <stdint.h>
#include "z30_module_api.h"

typedef void *(*pool_alloc_fn)(uint32_t size, uint32_t flags);
#define POOL_ALLOC ((pool_alloc_fn)0x10211F4Du)
typedef int32_t (*fs_open_fn)(const char *path, uint32_t flags);
#define FS_OPEN   ((fs_open_fn)0x102C8AFDu)
typedef int32_t (*fs_write_fn)(int32_t fd, const void *buf, uint32_t len, uint32_t *done);
#define FS_WRITE  ((fs_write_fn)0x102C8A6Bu)
typedef int32_t (*fs_close_fn)(int32_t fd);
#define FS_CLOSE  ((fs_close_fn)0x102C89EFu)
typedef int32_t (*range_fn)(uint32_t addr, uint32_t len, uint32_t op, uint32_t scope);
#define RANGE_SERVICE ((range_fn)0x10035AACu)
typedef int32_t (*input_fn)(void *state);
#define INPUT_PUT  ((input_fn)0x102F0D15u)
#define INPUT_POST ((input_fn)0x102F1845u)
typedef void (*time_fn)(uint64_t *out);
#define TIME_US ((time_fn)0x101D8301u)

#define HOOK_CELL ((volatile uint32_t *)0xC1832D48u)   /* imu_hook */
#define UI_PHASE ((const volatile uint8_t *)0xC181F6FCu)
#define UI_STATE ((const volatile uint8_t *)0xC18292ACu)
#define PHOTO_BACKEND 0xC181B278u
#define AF_AREA ((const volatile uint8_t *)0xC237C7F8u)
/* AF display block 0xC237C7B4 + 0x3FD. Upstream tracking3d (DETECT_SELECT) reads
 * it as non-zero in photo, but on a Z30 (2026-11-17, DIAG log) it read
 * 0 in photo live view and 1 in movie mode, so the module requires 0. Added
 * because the photo backend word stayed 1 in movie mode and did not exclude it. */
#define PHOTO_SEL ((const volatile uint8_t *)0xC237CBB1u)
#define AUTO_AREA 6u
#define AF_BUSY 0xC237F680u
#define BOX_SHOWN 0xC237F684u
#define TRACKING 0xC237F688u
#define RING 0xC2008EA0u
#define RING_IDX 0xC182004Cu
#define RING_STRIDE 0x190u
#define STATE_BYTES 0x18Cu
#define OK_BIT 0x1u                     /* key word 2 */
#define SETTLE 2u                       /* idle ticks before a press (~100 ms) */
#ifdef BUILTIN
/* BUILTIN (2026-11-17): copied out of the EG and started at power-on by the
 * upstream builder's auto-arm option (features/auto_arm.py). It reads the photo
 * shooting menu row "Auto tracking box" (0 Enable, 1 Disable) about once a
 * second through api->get_property(AUTOARM_KEY), never disarms and keeps no log. */
#if !defined(REMAP) || !defined(DIRECT) || defined(DIAG)
#error "BUILTIN needs REMAP and DIRECT, without DIAG"
#endif
#define AUTOARM_KEY 0x41524D31u        /* 'ARM1', upstream features/auto_arm.py KEY */
#define CHOICE_EVERY 20u               /* ticks between menu reads (~1 s) */
#endif
#ifdef DIRECT
#ifndef REMAP
#error "DIRECT needs REMAP"
#endif
/* DIRECT (2026-11-17): instead of a synthetic OK, call the stock "enter the
 * tracking mode" function the live-view OK handler 0x10368BA0 calls in
 * tracking state 0 / 1: 0x1045A1AE(&byte 0). Upstream tracking3d calls it from
 * the detection writer task (camera AF000161: box ~0.32 s later, 3 of 3). This
 * module calls it from the IMU task, which is not confirmed. */
typedef void (*enter_fn)(uint8_t *zero);
#define ENTER_TRACKING ((enter_fn)0x1045A1AFu)
#define WAIT 30u                        /* ticks to wait for the box (~1.5 s) before calling again */
#else
#define WAIT 10u                        /* ticks to wait for the box (~0.5 s; was 30 before 2026-11-17 fast-retry test) */
#endif
/* Idle ticks needed after another screen (~300 ms). 2026-11-17, DIRECT: with 2
 * ticks (~100 ms) the box still came ~0.3-0.4 s after arrival (the camera
 * takes that long), and a call right after Run or after closing the i menu was
 * ignored, costing a 1.5 s retry. 6 ticks: box ~0.4 s, one call each time. */
#define ENTRY_SETTLE 6u
#define HOLD 2u                         /* REMAP: ticks OK stays down (~100 ms) */
#define RD(a) (*(const volatile uint32_t *)(a))

struct rec {                            /* 12 bytes (28 with DIAG), one per (would-be) press */
    uint32_t t_ms;
    uint8_t phase, area, flags, ui_state;   /* flags: bit 0 OK sent, bit 1 DIAG sample, bits 2-3 writer results */
    uint32_t backend;                   /* photo backend word */
#ifdef DIAG
    uint32_t af_busy, shown, tracking;  /* the three idle-check words */
    uint32_t photo_sel;                 /* PHOTO_SEL byte: 0 photo, 1 movie */
#endif
};
#define HDR 32u
#ifdef DIAG
/* Diagnostic dry run: also writes a sample record every SAMPLE ticks (~1 s)
 * whatever the state, so a log shows which check keeps the camera from
 * counting as idle. Presses nothing unless built with -DREMAP too. */
_Static_assert(sizeof(struct rec) == 28, "rec");
#define CAP 4096u
#define SAMPLE 20u
#define SAMPLE_FLAG 0x2u
#define BOX_EVENT_FLAG 0x10u            /* record written when the box-shown word changes */
#else
_Static_assert(sizeof(struct rec) == 12, "rec");
#ifdef BUILTIN
#define CAP 64u                         /* no log is ever saved */
#else
#define CAP 8192u
#endif
#endif
#define POOL_BYTES (HDR + CAP * sizeof(struct rec))

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static inline void barrier(void) { __asm__ volatile("dmb" ::: "memory"); }
static uint64_t time_us(void) { uint64_t t = 0; TIME_US(&t); return t; }

static struct {
    uint8_t *pool; struct rec *log;
    uint32_t count, ticks, presses, idle_run, waiting, file_no, held[4], state[STATE_BYTES / 4];
    uint32_t hold, pend;                /* REMAP: ticks until OK goes up; log index + 1 of that press */
    uint32_t away, last_shown;          /* left photo live view since the last press; box word last tick */
#ifdef BUILTIN
    const struct z30_module_api *api;   /* BUILTIN: for the menu choice */
    uint32_t enabled;                   /* BUILTIN: menu row says Enable */
#endif
    uint64_t t0;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

#if defined(REMAP) && !defined(DIRECT)
static void key_word(uint32_t *f, uint32_t cur, uint32_t prev)
{
    uint32_t ch = cur ^ prev;
    f[0] = cur; f[1] = prev; f[2] = ch; f[3] = cur & ch; f[4] = prev & ch;
}

/* Upstream v6: a copy of the entry at src, nothing changing except OK. */
static uint32_t press_ok(uint32_t src, int down)
{
    uint32_t *st = G.state;
    for (uint32_t i = 0; i < STATE_BYTES / 4u; i++) st[i] = RD(src + 4u * i);
    for (uint32_t w = 0; w < 4; w++) key_word(&st[1 + 5 * w], G.held[w], G.held[w]);
    if (down) key_word(&st[6], G.held[1] | OK_BIT, G.held[1]);
    else key_word(&st[6], G.held[1], G.held[1] | OK_BIT);
    if (!INPUT_PUT(st)) return 0;
    INPUT_POST(st);
    return 1;
}
#endif

static inline __attribute__((always_inline))
struct rec *add_rec(struct rec *scratch, uint32_t phase, uint32_t area, uint32_t shown, uint8_t flags)
{
    struct rec *r = G.count < CAP ? &G.log[G.count++] : scratch;
    r->t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
    r->phase = (uint8_t)phase; r->area = (uint8_t)area; r->flags = flags; r->ui_state = *UI_STATE; r->backend = RD(PHOTO_BACKEND);
#ifdef DIAG
    r->af_busy = RD(AF_BUSY); r->shown = shown; r->tracking = RD(TRACKING); r->photo_sel = *PHOTO_SEL;
#else
    (void)shown;
#endif
    return r;
}

static void tick(uint32_t stock_result)
{
    (void)stock_result;                 /* the stock accel read; never changed */
    G.busy = 1; barrier();
#if defined(REMAP) && !defined(DIRECT)
    if (G.hold) {                       /* OK is down: release it, even after disarm */
        G.hold -= 1u;
        if (G.hold == 0u) {
            uint32_t newest = RING + ((RD(RING_IDX) - 1u) & 0xFu) * RING_STRIDE;
            uint32_t up = press_ok(newest, 0);
            if (G.pend) G.log[G.pend - 1u].flags |= (uint8_t)(up << 3);
            G.pend = 0;
            G.waiting = WAIT;           /* retry wait starts at the release */
        }
        goto out;
    }
#endif
    if (!G.on) goto out;
    G.ticks++;
#ifdef BUILTIN
    if (G.ticks % CHOICE_EVERY == 0u) G.enabled = G.api->get_property(AUTOARM_KEY) == 0u;
    if (!G.enabled) { G.idle_run = 0; G.away = 1; goto out; }   /* first read after ~1 s */
#endif
    uint32_t phase = *UI_PHASE, area = *AF_AREA, shown = RD(BOX_SHOWN);
    struct rec scratch;                 /* sink once the log is full */
#ifdef DIAG
    if (G.ticks % SAMPLE == 0u) add_rec(&scratch, phase, area, shown, SAMPLE_FLAG);
    if ((shown != 0u) != (G.last_shown != 0u)) add_rec(&scratch, phase, area, shown, BOX_EVENT_FLAG);
#endif
    G.last_shown = shown;
    if (phase != 1u && phase != 2u) G.away = 1;
    if (G.waiting) {                    /* an OK was sent: let it land */
        if (shown || --G.waiting == 0) G.waiting = 0;
        G.idle_run = 0;
        goto out;
    }
    int idle = (phase == 1u || phase == 2u) && *UI_STATE == 0u && RD(PHOTO_BACKEND) == 1u &&
               *PHOTO_SEL == 0u &&
               area == AUTO_AREA &&
               !RD(AF_BUSY) && !shown && !RD(TRACKING);
    G.idle_run = idle ? G.idle_run + 1u : 0u;
    /* After another screen, wait longer: on return the box word can read 0
     * for a tick or two although the box is still up (2026-11-17 log). */
    if (G.idle_run < (G.away ? ENTRY_SETTLE : SETTLE)) goto out;
    G.idle_run = 0;
    G.away = 0;
    struct rec *r = add_rec(&scratch, phase, area, shown, 0);
    (void)r;                            /* used by REMAP below */
    G.presses++;
    G.waiting = WAIT;
#ifdef DIRECT
    {
        uint32_t zero[2] __attribute__((aligned(8))) = { 0u, 0u };
        ENTER_TRACKING((uint8_t *)zero);
        r->flags = (uint8_t)(1u | 0x20u);   /* bit 5: direct call */
    }
#elif defined(REMAP)
    uint32_t newest = RING + ((RD(RING_IDX) - 1u) & 0xFu) * RING_STRIDE;
    G.held[0] = RD(newest + 4); G.held[1] = RD(newest + 0x18) & ~OK_BIT;
    G.held[2] = RD(newest + 0x2C); G.held[3] = RD(newest + 0x40);
    /* Held press (2026-11-17): down now, up HOLD ticks later, like a real button.
     * Down and up in the same tick brought the box up for about 1 press in 10. */
    uint32_t down = press_ok(newest, 1);
    r->flags = (uint8_t)(1u | down << 2);
    G.pend = r == &scratch ? 0u : G.count;
    G.hold = HOLD;
#endif
out:
    barrier(); G.busy = 0;
}

#ifndef BUILTIN                         /* saving the log: SD module builds only */
static int shared_clean(const void *p, uint32_t n)
{
    volatile uint32_t *smp = (volatile uint32_t *)0xC2ABC8B8u;
    if (smp[0] != 1 || *(volatile uint32_t *)0xC18EB038u != 2 || smp[5] != 3 || smp[1] != 1 ||
        *(volatile uint32_t *)0xC2A1C7F0u != 0x100527A8u || !n)
        return 0;
    uint32_t a = (uint32_t)p & ~63u;
    n += (uint32_t)p - a;
    if (RANGE_SERVICE(a, n, 1, 2) <= 0) return 0;
    __asm__ volatile("dsb" ::: "memory");
    return 1;
}

static int write_all(int32_t fd, const uint8_t *p, uint32_t n)
{
    while (n) {
        uint32_t chunk = n > 65536u ? 65536u : n, done = 0;
        FS_WRITE(fd, p, chunk, &done);
        if (done == 0 || done > chunk) return 0;
        p += done; n -= done;
    }
    return 1;
}
#endif

static void put_u(char **p, uint32_t v)
{ char b[10]; int n = 0; do { b[n++] = (char)('0' + v % 10u); v /= 10u; } while (v); while (n) *(*p)++ = b[--n]; }
static void put_s(char **p, const char *s) { while (*s) *(*p)++ = *s++; }

static uint32_t say(const struct z30_module_api *api, struct context *c, const char *a, uint32_t n, int num)
{
    char *p = c->text;
    put_s(&p, a); if (num) put_u(&p, n); *p = 0;
    return api->set_text(c->text);
}

#ifndef BUILTIN
static uint32_t save(const struct z30_module_api *api, struct context *c)
{
    G.on = 0; barrier();
    for (uint32_t spin = 0; G.busy && spin < 50000000u; spin++) barrier();
    if (G.busy) return say(api, c, "Busy, Run again", 0, 0);
#ifdef REMAP
    uint32_t flags = 1;
#else
    uint32_t flags = 0;
#endif
#ifdef DIRECT
    flags |= 4;                         /* header bit 2: DIRECT build */
#endif
#ifdef DIAG
    flags |= 2;                         /* header bit 1: DIAG, 28-byte records */
#endif
    uint32_t h[8] = { 0x4F545541u, 0x314D5241u, G.count, G.presses, G.ticks, sizeof(struct rec), flags, 0 };
    memcpy(G.pool, h, HDR);
    char path[24] = "A:\\Z30\\AR000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.count * sizeof(struct rec);
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    if (!ok) return say(api, c, "Save failed ", (uint32_t)fd, 1);
    return say(api, c, "Saved, presses ", G.presses, 1);
}
#endif

static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    c->runs++;
    if (!G.pool) {
        G.pool = POOL_ALLOC(POOL_BYTES, 0);
        if (!G.pool) return say(api, c, "No memory", 0, 0);
        G.log = (struct rec *)(G.pool + HDR);
    }
#ifdef BUILTIN
    G.api = api;
    if (G.on) return 1;                 /* built in: stays armed until power off */
#else
    if (G.on) return save(api, c);
#endif
    if (*HOOK_CELL && *HOOK_CELL != (uint32_t)tick) return say(api, c, "IMU hook in use", 0, 0);
    G.count = G.ticks = G.presses = G.idle_run = G.waiting = 0; G.t0 = time_us();
    G.away = 1; G.last_shown = RD(BOX_SHOWN);
    barrier();
    *HOOK_CELL = (uint32_t)tick;
    G.on = 1; barrier();
#ifdef BUILTIN
    return say(api, c, "Auto-arm built in", 0, 0);
#elif defined(DIRECT)
    return say(api, c, "Auto-arm direct, Run=save", 0, 0);
#elif defined(REMAP)
    return say(api, c, "Auto-arm on, Run=save", 0, 0);
#elif defined(DIAG)
    return say(api, c, "Auto-arm diag dry run, Run=save", 0, 0);
#else
    return say(api, c, "Auto-arm dry run, Run=save", 0, 0);
#endif
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, "Auto-arm ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
