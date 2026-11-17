/* AF handover (research SD module): when the stock subject tracking (started
 * with OK) runs and a face / pet is detected that was not there when the
 * tracking started, press OK once: the tracking ends and the stock auto-area AF
 * takes the detected subject (camera v6, HO000048: works).
 *
 * v7 (2026-11-13, user rules): no distance test any more -- tracking first,
 * subject later => hand over; a subject already detected when tracking starts
 * blocks the handover until it has been gone for CLEAR updates (the user chose
 * to track something else). Pets count (the subject count, not the face box).
 * It runs on the stock detection result writer (face_hook, 0x101944B0, every
 * detection update, ~10-20 Hz in live view) instead of the IMU task, so it can
 * share a firmware with gyro-evr (which owns the IMU hook). A half-press stays
 * held in the synthesized states (to be confirmed on the camera).
 *
 *   0xC237F688       1 while tracking (started with OK)
 *   0xC237E3E4       number of detected subjects (0..5), faces and pets
 *   0xC237E2C0       face state (logged only)
 *   0xC237D59C/5A0   tracking box (logged only)
 *
 * OK, history: v1/v2 GUI event 0x103CE96E(0x27E) -- ignored in the shooting
 * view; v3-v5 stock synthesizer 0x102F199A -- its states lack the input source
 * (+0 = 0xC1818814 in every real entry). v6 (works): a copy of the newest real
 * entry of the input ring (0xC2008EA0, 16 x 0x190; next write slot = byte 0 at
 * 0xC182004C) with its four key words (cur, prev, changed, pressed, released at
 * +4 / +0x18 / +0x2C / +0x40) set to "no change" except key word 2 bit 0 (OK,
 * key code 0x1B; table 0x1073BD68) going down, then up, each through the stock
 * input writer 0x102F0D14 and then 0x102F1844 (the stock synthesizer's calls).
 * The input ring is also logged whenever it changes.
 *
 * Run 1: arm.  Run 2: disarm and write A:\Z30\HOnnnnnn.BIN.  Power off disarms.
 * Needs the research EG with face_hook (tools/build_af_log.py --module handover).
 * File: "AFHAND7\0" (v1-v6: AFHAND1-6), u32 count, u32 presses, u32 ticks, u32 rec_bytes (40),
 *       u32 scale, u32 hold, then count x struct rec (face_state: low 7 bits, bit 7 = 0x100).
 */
#include "z30_research.h"

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
#define INPUT_PUT  ((input_fn)0x102F0D15u)       /* copies a 0x18C-byte input state into the ring */
#define INPUT_POST ((input_fn)0x102F1845u)       /* the stock synthesizer's next call after a put */
#define STATE_BYTES 0x18Cu
#define OK_BIT 0x1u                              /* key word 2 */
#define FACE_CELL ((volatile uint32_t *)0xC1832D40u)   /* face_hook */

#define RD(a) (*(const volatile uint32_t *)(a))
#define TRACKING 0xC237F688u
#define SUBJECTS 0xC237E3E4u
#define FACE_STATE 0xC237E2C0u
#define BOX0 0xC237D59Cu
#define BOX1 0xC237D5A0u
#define FACE 0xC237F624u            /* x, y, w, h (the debug record's face box +0xAC) */
#define SCALE 106                    /* tracking-box units per face-grid cell */
#define HOLD 3u                      /* updates with a subject (detection flickers) */
#define MISS 10u                     /* updates without one that clear the count */
#define CLEAR 10u                    /* a subject seen at tracking start must be gone this long */
#define REARM_TICKS 10u              /* updates without tracking before the next press */

struct rec {                          /* 40 bytes */
    uint32_t t_ms;
    uint8_t tracking, subjects, face_state, pressed;
    uint32_t box0, box1;
    int32_t fx, fy, fw, fh;
    int32_t dx, dy;                   /* face point minus box centre */
};
_Static_assert(sizeof(struct rec) == 40, "rec");
#define HDR 32u
#define CAP 8192u
#define RING 0xC2008EA0u
#define RING_IDX 0xC182004Cu
#define RING_N 16u
#define RING_STRIDE 0x190u
struct ringrec { uint32_t t_ms, idx, w[RING_N][4]; };   /* 264 bytes */
#define RCAP 2048u
#define POOL_BYTES (HDR + CAP * sizeof(struct rec) + RCAP * sizeof(struct ringrec))

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; struct rec *log; struct ringrec *rlog, rlast, rnow; uint32_t rcount,
    count, ticks, presses, near_run, miss, off_run, done, session, blocked, clear_run, file_no, last_ret, press_ms[8], held[4], state[STATE_BYTES / 4];
    uint64_t t0;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

/* one key word: cur, prev, changed, pressed (cur & changed), released (prev & changed) */
static void key_word(uint32_t *f, uint32_t cur, uint32_t prev)
{
    uint32_t ch = cur ^ prev;
    f[0] = cur; f[1] = prev; f[2] = ch; f[3] = cur & ch; f[4] = prev & ch;
}

/* queue a copy of the ring entry at src with only `bit` of key word 2 going
 * down (down = 1) or up; 1 when the stock writer took it */
static uint32_t press_key(uint32_t src, uint32_t bit, int down)
{
    uint32_t *st = G.state;
    for (uint32_t i = 0; i < STATE_BYTES / 4u; i++) st[i] = RD(src + 4u * i);
    for (uint32_t w = 0; w < 4; w++) key_word(&st[1 + 5 * w], G.held[w], G.held[w]);
    if (down) key_word(&st[6], G.held[1] | bit, G.held[1]);
    else key_word(&st[6], G.held[1], G.held[1] | bit);
    if (!INPUT_PUT(st)) return 0;
    INPUT_POST(st);
    return 1;
}

static void tick(const void *result, const int16_t *xy, const int16_t *wh)
{
    (void)result; (void)xy; (void)wh;          /* RAM holds the previous update; enough here */
    G.busy = 1; barrier();
    if (!G.on) goto out;
    G.ticks++;
    {
        /* input ring snapshot: kept whenever an index or a key word changed */
        struct ringrec *n = &G.rnow;        /* not on the caller task's stack */
        n->idx = RD(RING_IDX);
        int changed = n->idx != G.rlast.idx;
        for (uint32_t i = 0; i < RING_N; i++) {
            uint32_t e = RING + i * RING_STRIDE;
            n->w[i][0] = RD(e); n->w[i][1] = RD(e + 4); n->w[i][2] = RD(e + 0x18); n->w[i][3] = RD(e + 0x24);
            for (int k = 0; k < 4; k++) if (n->w[i][k] != G.rlast.w[i][k]) changed = 1;
        }
        if (changed && G.rcount < RCAP) {
            n->t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
            G.rlog[G.rcount++] = *n;
        }
        G.rlast = *n;
    }
    uint32_t tracking = RD(TRACKING) != 0;
    if (!tracking) {
        if (G.off_run < REARM_TICKS) G.off_run++;
        if (G.off_run >= REARM_TICKS) G.done = 0;
        G.session = G.near_run = G.miss = 0;
        goto out;
    }
    G.off_run = 0;
    uint32_t subjects = RD(SUBJECTS), state = RD(FACE_STATE);
    uint32_t b0 = RD(BOX0), b1 = RD(BOX1);
    int32_t fx = (int32_t)RD(FACE), fy = (int32_t)RD(FACE + 4), fw = (int32_t)RD(FACE + 8), fh = (int32_t)RD(FACE + 12);
    int seen = subjects > 0 && subjects <= 5;
    if (!G.session) {                       /* first update of this tracking */
        G.session = 1; G.blocked = seen; G.clear_run = 0; G.near_run = G.miss = 0;
    }
    if (G.blocked) {                        /* tracking started with a subject in view */
        G.clear_run = seen ? 0 : G.clear_run + 1;
        if (G.clear_run >= CLEAR) G.blocked = 0;
    } else if (seen) { G.near_run++; G.miss = 0; }
    else if (++G.miss >= MISS) { G.near_run = 0; G.miss = 0; }
    uint32_t pressed = 0;
    if (!G.done && !G.blocked && G.near_run >= HOLD) {
        uint32_t newest = RING + ((RD(RING_IDX) - 1u) & 0xFu) * RING_STRIDE;
        G.held[0] = RD(newest + 4); G.held[1] = RD(newest + 0x18) & ~OK_BIT;
        G.held[2] = RD(newest + 0x2C); G.held[3] = RD(newest + 0x40);
        G.last_ret = press_key(newest, OK_BIT, 1) | press_key(newest, OK_BIT, 0) << 1;
        G.press_ms[G.presses & 7u] = (uint32_t)((time_us() - G.t0) / 1000u);
        G.done = 1; G.presses++; pressed = 1;
    }
    int32_t dx = (int32_t)G.blocked, dy = (int32_t)G.near_run;     /* v7: logged in place of the offset */
    if (G.count < CAP) {
        struct rec *r = &G.log[G.count++];
        r->t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
        r->tracking = pressed ? (uint8_t)(0x80u | (G.last_ret & 0x7Fu)) : 1u;   /* press: 0x80 | down 1 | up 2 */
        r->subjects = (uint8_t)subjects; r->face_state = (uint8_t)((state & 0x7Fu) | (state & 0x100u ? 0x80u : 0u)); r->pressed = (uint8_t)pressed;
        r->box0 = b0; r->box1 = b1; r->fx = fx; r->fy = fy; r->fw = fw; r->fh = fh; r->dx = dx; r->dy = dy;
    }
out:
    barrier(); G.busy = 0;
}

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

static void put_u(char **p, uint32_t v)
{ char b[10]; int n = 0; do { b[n++] = (char)('0' + v % 10u); v /= 10u; } while (v); while (n) *(*p)++ = b[--n]; }
static void put_s(char **p, const char *s) { while (*s) *(*p)++ = *s++; }
static uint32_t say(const struct z30_module_api *api, struct context *c, const char *a, uint32_t n, const char *b)
{
    char *p = c->text;
    put_s(&p, a); put_u(&p, n); put_s(&p, b); *p = 0;
    return api->set_text(c->text);
}

static uint32_t say_s(const struct z30_module_api *api, struct context *c, const char *a)
{
    char *p = c->text;
    put_s(&p, a); *p = 0;
    return api->set_text(c->text);
}

static uint32_t save(const struct z30_module_api *api, struct context *c)
{
    G.on = 0; barrier();
    for (uint32_t spin = 0; G.busy && spin < 50000000u; spin++) barrier();
    if (G.busy) return say_s(api, c, "Busy, Run again");
    uint32_t h[8] = { 0x41484641u, 0x0037444Eu, G.count, G.presses, G.ticks, sizeof(struct rec), SCALE, HOLD };
    memcpy(G.pool, h, HDR);
    /* v4: the ring snapshots follow the tick records: u32 rcount, u32 press_ms[8], then rcount x ringrec */
    uint8_t *tail = G.pool + HDR + G.count * sizeof(struct rec);
    memcpy(tail, &G.rcount, 4); memcpy(tail + 4, G.press_ms, 32);
    memcpy(tail + 36, G.rlog, G.rcount * sizeof(struct ringrec));
    memcpy(tail + 36 + G.rcount * sizeof(struct ringrec), G.held, 16);      /* v5: held words of the last press */
    char path[24] = "A:\\Z30\\HO000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.count * sizeof(struct rec) + 36u + G.rcount * sizeof(struct ringrec) + 16u;
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    if (!ok) return say(api, c, "Save failed ", (uint32_t)fd, "");
    return say(api, c, "Saved, OK presses ", G.presses, "");
}

static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    c->runs++;
    if (!G.pool) {
        G.pool = POOL_ALLOC(POOL_BYTES, 0);
        if (!G.pool) return say_s(api, c, "No memory");
        G.log = (struct rec *)(G.pool + HDR);
        G.rlog = (struct ringrec *)(G.pool + HDR + CAP * sizeof(struct rec));
    }
    if (G.on) return save(api, c);
    if (*FACE_CELL && *FACE_CELL != (uint32_t)tick) return say_s(api, c, "Detection hook in use");
    G.count = G.ticks = G.presses = G.near_run = G.miss = G.rcount = 0; memset(&G.rlast, 0, sizeof G.rlast); memset(G.press_ms, 0, sizeof G.press_ms); G.off_run = REARM_TICKS; G.done = G.session = G.blocked = G.clear_run = 0; G.t0 = time_us();
    barrier();
    *FACE_CELL = (uint32_t)tick;
    G.on = 1; barrier();
    return say_s(api, c, "Handover armed, Run=save");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, "AF handover ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
