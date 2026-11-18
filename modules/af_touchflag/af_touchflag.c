/* AF touch flag (research SD module, method B): make a tracking started with OK
 * or a half-press behave like one started by touch.
 *
 * AF000272 (log 6, camera 2026-11-14): a touch start runs the stock subject
 * search 0x10073C76 around the touched point; with no subject there it sets
 * 0xC18C7240 = 1 (0x10073ECC) and tracking goes on (tracker mode 0x202). When a
 * person later comes into range the AF mode chooser switches to 0x203 (face
 * AF), the tracker is cleared (0x100ADE54) and the flag goes back to 0: the
 * fine face box. A half-press / OK start leaves the flag 0 and never upgrades.
 *
 * This module sets the flag to 1, DELAY IMU ticks (50 ms) after a tracking
 * session starts (0xC237F688 0 -> 1), once per session, if it is still 0. It
 * writes nothing else. Every IMU tick while tracking is logged.
 *
 * Run 1: arm.  Run 2: disarm and write A:\Z30\TFnnnnnn.BIN.  Power off disarms.
 * Needs an EG with the IMU hook (tools/build_af_log.py --module touchflag).
 * File: "AFTFLG1\0", u32 count, u32 sets, u32 ticks, u32 rec_bytes (24), then records.
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
#define IMU_HOOK_CELL ((volatile uint32_t *)0xC1832D48u)

#define RD(a) (*(const volatile uint32_t *)(a))
#define TRACKING 0xC237F688u
#define AF_ON 0xC237F680u
#define SUBJECTS 0xC237E3E4u
#define FACE_STATE 0xC237E2C0u
#define TOUCH_FLAG ((volatile uint32_t *)0xC18C7240u)
#define TRK_MODE 0xC23A9CB4u
#define TRK_STRUCT 0xC23AA024u
#define DELAY 2u

struct rec {                          /* 24 bytes */
    uint32_t t_ms;
    uint8_t tracking, af_on, subjects, act;      /* act: 1 set the flag */
    uint32_t face_state, flag, mode, trk;
};
_Static_assert(sizeof(struct rec) == 24, "rec");
#define HDR 32u
#define CAP 40000u
#define POOL_BYTES (HDR + CAP * sizeof(struct rec))

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; struct rec *log;
    uint32_t count, sets, ticks, was, wait, done, file_no;
    uint64_t t0;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

static void tick(const void *stock_result)
{
    (void)stock_result;
    if (!G.on || G.busy) return;
    G.busy = 1; barrier();
    G.ticks++;
    uint32_t tr = RD(TRACKING) != 0, act = 0;
    if (tr && !G.was) { G.wait = DELAY; G.done = 0; }
    G.was = tr;
    if (tr) {
        if (G.wait) G.wait--;
        else if (!G.done) {
            G.done = 1;
            if (*TOUCH_FLAG == 0) { *TOUCH_FLAG = 1; G.sets++; act = 1; }
        }
        if (G.count < CAP) {
            struct rec *r = &G.log[G.count++];
            r->t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
            r->tracking = 1; r->af_on = (uint8_t)RD(AF_ON); r->subjects = (uint8_t)RD(SUBJECTS); r->act = (uint8_t)act;
            r->face_state = RD(FACE_STATE); r->flag = *TOUCH_FLAG; r->mode = RD(TRK_MODE); r->trk = RD(TRK_STRUCT);
        }
    }
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
    if (*IMU_HOOK_CELL == (uint32_t)tick) *IMU_HOOK_CELL = 0;
    uint32_t h[8] = { 0x46544641u, 0x0031474Cu, G.count, G.sets, G.ticks, sizeof(struct rec), DELAY, 0 };
    memcpy(G.pool, h, HDR);
    char path[24] = "A:\\Z30\\TF000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.count * sizeof(struct rec);
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    char *p = c->text;
    if (!ok) { put_s(&p, "Save failed "); put_u(&p, (uint32_t)fd); }
    else { put_s(&p, "Saved, flag set "); put_u(&p, G.sets); put_s(&p, "x"); }
    *p = 0;
    return api->set_text(c->text);
}

static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    c->runs++;
    if (!G.pool) {
        G.pool = POOL_ALLOC(POOL_BYTES, 0);
        if (!G.pool) return say_s(api, c, "No memory");
        G.log = (struct rec *)(G.pool + HDR);
    }
    if (G.on) return save(api, c);
    if (*(const volatile uint16_t *)0x1021C360u == 0xF7FFu) return say_s(api, c, "No IMU hook: flash retarget EG");
    if (*IMU_HOOK_CELL && *IMU_HOOK_CELL != (uint32_t)tick) return say_s(api, c, "IMU hook in use");
    G.count = G.sets = G.ticks = G.wait = G.done = 0;
    G.was = RD(TRACKING) != 0; G.t0 = time_us();
    barrier();
    G.on = 1; barrier();
    *IMU_HOOK_CELL = (uint32_t)tick;
    return say_s(api, c, "Touch flag armed, Run=save");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, "AF touch flag ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
