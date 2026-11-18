/* AF scan (research SD module, read only): which RAM words change only while
 * the stock subject tracking runs? The tracking box must be among them.
 *
 * Log 2 (camera, 2026-11-13): 0xC237F688 is 1 only while tracking started with
 * OK (steps 4-5), 0xC237F680 also during a half-press. The scan covers
 * SCAN_BYTES from SCAN_BASE in CHUNK pieces, one piece per stock IMU-task tick
 * (50 ms in photo mode): each word is compared with its value at the previous
 * sweep, and a change is counted as "tracking" or "idle" by 0xC237F688 at that
 * tick. During tracking the minimum and maximum value are kept too. The first
 * sweep only takes the snapshot. Nothing in the camera is written.
 *
 * -DSCAN_BOX (2026-11-14, method B): the "key" state is instead the tracking
 * box shown but not tracking yet (0xC237F684 = 1, 0xC237F688 = 0, screen 1),
 * while the box is moved with the multi selector: the box position should
 * change only then. Header word 7 = 1 for this variant.
 *
 * Run 1: start.  Run 2: stop and write A:\Z30\SCnnnnnn.BIN.  Power off disarms.
 * File: "AFSCAN1\0", u32 base, u32 bytes, u32 chunk, u32 ticks, u32 ticks_tracking,
 *       u32 sweeps_tracking[NCHUNK], u32 sweeps_idle[NCHUNK] (per chunk),
 *       then u16 changes_tracking[W], u16 changes_idle[W], i32 min[W], i32 max[W].
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

#define TRACKING ((volatile uint32_t *)0xC237F688u)
#define SHOWN ((volatile uint32_t *)0xC237F684u)
#ifdef SCAN_BOX
#define KEY_STATE() (*SHOWN != 0 && *TRACKING == 0)
#define VARIANT 1u
#else
#define KEY_STATE() (*TRACKING != 0)
#define VARIANT 0u
#endif
#define SCAN_BASE 0xC2300000u
#define SCAN_BYTES 0x100000u
#define CHUNK 0x10000u
#define NCHUNK (SCAN_BYTES / CHUNK)
#define W (SCAN_BYTES / 4u)
#define HDR (32u + 8u * NCHUNK)
/* pool: header | cnt_t u16[W] | cnt_i u16[W] | min i32[W] | max i32[W] | snapshot u32[W] */
#define POOL_BYTES (HDR + 4u * W + 8u * W + 4u * W)

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; uint32_t *hdr; uint16_t *cnt_t, *cnt_i; int32_t *mn, *mx; uint32_t *snap;
    uint32_t chunk, sweeps, ticks, ticks_t, file_no;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

static void tick(uint32_t stock_result)
{
    (void)stock_result;
    G.busy = 1; barrier();
    if (!G.on) goto out;
    G.ticks++;
    uint32_t tr = KEY_STATE();
    if (tr) G.ticks_t++;
    uint32_t c = G.chunk, w0 = c * (CHUNK / 4u);
    const volatile uint32_t *src = (const volatile uint32_t *)(SCAN_BASE + c * CHUNK);
    if (G.sweeps == 0) {
        for (uint32_t i = 0; i < CHUNK / 4u; i++) G.snap[w0 + i] = src[i];
    } else {
        uint32_t *sweep = &G.hdr[8 + (tr ? 0 : NCHUNK) + c];
        (*sweep)++;
        for (uint32_t i = 0; i < CHUNK / 4u; i++) {
            uint32_t v = src[i], k = w0 + i;
            if (v != G.snap[k]) {
                G.snap[k] = v;
                if (tr) { if (G.cnt_t[k] != 0xFFFFu) G.cnt_t[k]++; }
                else if (G.cnt_i[k] != 0xFFFFu) G.cnt_i[k]++;
            }
            if (tr) {
                if ((int32_t)v < G.mn[k]) G.mn[k] = (int32_t)v;
                if ((int32_t)v > G.mx[k]) G.mx[k] = (int32_t)v;
            }
        }
    }
    if (++G.chunk == NCHUNK) { G.chunk = 0; G.sweeps++; }
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
    uint32_t h[8] = { 0x43534641u, 0x00314E41u, SCAN_BASE, SCAN_BYTES, CHUNK, G.ticks, G.ticks_t, VARIANT };
    memcpy(G.hdr, h, 32);
    char path[24] = "A:\\Z30\\SC000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + 4u * W + 8u * W;          /* the snapshot is not written */
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    if (!ok) return say(api, c, "Save failed ", (uint32_t)fd, "");
    return say(api, c, VARIANT ? "Saved, box ticks " : "Saved, tracking ticks ", G.ticks_t, "");
}

static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    c->runs++;
    if (!G.pool) {
        G.pool = POOL_ALLOC(POOL_BYTES, 0);
        if (!G.pool) return say_s(api, c, "No memory");
        G.hdr = (uint32_t *)G.pool;
        G.cnt_t = (uint16_t *)(G.pool + HDR); G.cnt_i = G.cnt_t + W;
        G.mn = (int32_t *)(G.cnt_i + W); G.mx = G.mn + W; G.snap = (uint32_t *)(G.mx + W);
    }
    if (G.on) return save(api, c);
    if (*IMU_HOOK_CELL && *IMU_HOOK_CELL != (uint32_t)tick) return say_s(api, c, "IMU hook in use");
    memset(G.pool, 0, HDR + 4u * W);
    for (uint32_t k = 0; k < W; k++) { G.mn[k] = 0x7FFFFFFF; G.mx[k] = (int32_t)0x80000000; }
    G.chunk = G.sweeps = G.ticks = G.ticks_t = 0;
    barrier();
    *IMU_HOOK_CELL = (uint32_t)tick;
    G.on = 1; barrier();
    return say_s(api, c, "Scanning, Run=save");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, VARIANT ? "AF box scan ready" : "AF scan ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
