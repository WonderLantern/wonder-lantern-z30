/* AF snapshot (research SD module, read only): how does a tracking started by
 * touch differ from one started with OK / a half-press?
 *
 * Camera (2026-11-14, user): tracking started by touching the screen turns into
 * the fine face / animal box (screen 3) as soon as a person comes into detection
 * range; tracking started with OK or a half-press keeps the plain tracking box.
 * The start method must be stored somewhere. For each tracking session
 * (0xC237F688 0 -> 1) the module copies WINDOWS DELAY IMU ticks (50 ms) after
 * the start, CHUNK bytes per tick, into one of SLOTS slots; the parser then
 * lists the words equal inside each start-method group and different between
 * the groups.
 *
 * -DSNAP_SHOWN (2026-11-14, 3D tracking: back to tracking mode after a handover):
 * the trigger is the tracking box shown flag 0xC237F684 instead. A slot is taken
 * DELAY ticks after it rises (screen 1, entered with OK) and after it falls
 * (left with the zoom-out key); the slot kind is bit 31 of its start time
 * (1 = shown). The parser then lists the words that hold one value in every
 * shown slot and another in every hidden one: the tracking-mode state the UI
 * sets on OK. The windows then also cover the AF mode chooser state 0xC18C7xxx.
 *
 * -DSNAP_FOCUS (2026-11-14, focus frame colour): the trigger is the AF focus
 * result 0xC25A1E70 (0 idle, 1 not in focus, 2 in focus). A slot is taken DELAY
 * ticks after every change and kept only if the result stays the same until the
 * copy ends; the result is stored in bits 30..31 of the slot start time. The
 * parser groups the slots by it and lists the words that differ: the frame
 * style / colour the UI draws for each result. The windows cover the live view
 * display info 0xC2016A68 (current copy 0xC2016DEC), the AF UI copies 0xC237C7B4,
 * the UI state and the AF result block.
 *
 * Run 1: arm.  Run 2: disarm and write A:\Z30\SNnnnnnn.BIN.  Power off disarms.
 * File: "AFSNAP1\0", u32 slots used, u32 windows, u32 ticks, u32 sessions seen,
 *       then per window u32 base, u32 bytes; per slot u32 start_ms, u32 done_ms,
 *       then slots x (window bytes, in window order).
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

#if defined(SNAP_SHOWN) && defined(SNAP_FOCUS)
#error "one variant"
#endif
#ifdef SNAP_FOCUS
#define TRIGGER ((volatile uint32_t *)0xC25A1E70u)
static const uint32_t WINDOWS[][2] = {
    { 0xC2000000u, 0x40000u },        /* live view display info 0xC2016A68 / 0xC2016DEC, GUI */
    { 0xC2370000u, 0x10000u },        /* AF UI copies 0xC237C7B4 */
    { 0xC1810000u, 0x20000u },        /* UI state */
    { 0xC25A0000u, 0x10000u },        /* AF result block 0xC25A1E60 */
};
#elif defined(SNAP_SHOWN)
#define TRIGGER ((volatile uint32_t *)0xC237F684u)
static const uint32_t WINDOWS[][2] = {
    { 0xC2360000u, 0x40000u },        /* AF records, UI copies */
    { 0xC23A0000u, 0x10000u },        /* tracker struct */
    { 0xC18C0000u, 0x10000u },        /* AF mode chooser state 0xC18C7128 / 0xC18C7228 */
    { 0xC1810000u, 0x20000u },        /* UI state */
};
#else
#define TRIGGER TRACKING
static const uint32_t WINDOWS[][2] = {
    { 0xC2360000u, 0x60000u },        /* AF records, UI copies, tracker struct, AF messages */
    { 0xC1810000u, 0x20000u },        /* UI state around the input index / page-0 records */
};
#endif
#define NWIN (sizeof WINDOWS / sizeof WINDOWS[0])
#if defined(SNAP_FOCUS)
#define SNAP_KIND "AF focus "
#elif defined(SNAP_SHOWN)
#define SNAP_KIND "AF mode "
#else
#define SNAP_KIND "AF "
#endif
#define SNAP_BYTES 0x80000u
#define SLOTS 8u
#define CHUNK 0x10000u
#define DELAY 6u                       /* ticks after the start: 300 ms */
#define HDR (24u + 8u * NWIN + 8u * SLOTS)
#define POOL_BYTES (HDR + SLOTS * SNAP_BYTES)
_Static_assert(SNAP_BYTES % CHUNK == 0, "chunk");

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; uint32_t *hdr;
    uint32_t used, ticks, sessions, was, wait, copying, off, file_no, kind;
    uint64_t t0;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

static uint32_t now_ms(void) { return (uint32_t)((time_us() - G.t0) / 1000u); }

/* copy CHUNK bytes of the snapshot at offset off (across the windows) */
static void copy_chunk(uint8_t *dst, uint32_t off)
{
    uint32_t w = 0, at = off;
    while (w < NWIN && at >= WINDOWS[w][1]) { at -= WINDOWS[w][1]; w++; }
    const volatile uint32_t *s = (const volatile uint32_t *)(WINDOWS[w][0] + at);
    uint32_t *d = (uint32_t *)(dst + off);
    for (uint32_t i = 0; i < CHUNK / 4u; i++) d[i] = s[i];
}

static void tick(const void *stock_result)
{
    (void)stock_result;
    if (!G.on || G.busy) return;
    G.busy = 1; barrier();
    G.ticks++;
#ifdef SNAP_FOCUS
    uint32_t tr = *TRIGGER & 0xFFu;
    if (tr > 3u) tr = 3u;
    if (tr != G.was) {                               /* the focus result changes */
        G.sessions++;
        if (G.copying) G.copying = 0;                /* not stable: drop the slot */
        if (G.used < SLOTS) { G.wait = DELAY; G.copying = 1; G.off = 0; G.kind = tr; G.hdr[6 + 2 * NWIN + 2 * G.used] = (now_ms() & 0x3FFFFFFFu) | (tr << 30); }
    }
#else
    uint32_t tr = *TRIGGER != 0;
#endif
#if defined(SNAP_FOCUS)
#elif defined(SNAP_SHOWN)
    if (tr != G.was) {                               /* shown rises or falls */
        G.sessions++;
        if (G.used < SLOTS && !G.copying) { G.wait = DELAY; G.copying = 1; G.off = 0; G.hdr[6 + 2 * NWIN + 2 * G.used] = now_ms() | (tr << 31); }
    }
#else
    if (tr && !G.was) {                              /* a tracking session starts */
        G.sessions++;
        if (G.used < SLOTS && !G.copying) { G.wait = DELAY; G.copying = 1; G.off = 0; G.hdr[6 + 2 * NWIN + 2 * G.used] = now_ms(); }
    }
#endif
    G.was = tr;
    if (G.copying) {
        if (G.wait) G.wait--;
        else {
            copy_chunk(G.pool + HDR + G.used * SNAP_BYTES, G.off);
            G.off += CHUNK;
            if (G.off >= SNAP_BYTES) { G.hdr[6 + 2 * NWIN + 2 * G.used + 1] = now_ms(); G.used++; G.copying = 0; }
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
    G.hdr[0] = 0x4E534641u; G.hdr[1] = 0x00315041u;
    G.hdr[2] = G.used; G.hdr[3] = NWIN; G.hdr[4] = G.ticks; G.hdr[5] = G.sessions;
    for (uint32_t w = 0; w < NWIN; w++) { G.hdr[6 + 2 * w] = WINDOWS[w][0]; G.hdr[7 + 2 * w] = WINDOWS[w][1]; }
    char path[24] = "A:\\Z30\\SN000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.used * SNAP_BYTES;
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    char *p = c->text;
    if (!ok) { put_s(&p, "Save failed "); put_u(&p, (uint32_t)fd); }
    else { put_s(&p, "Saved, snapshots "); put_u(&p, G.used); put_s(&p, " of "); put_u(&p, G.sessions); }
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
        G.hdr = (uint32_t *)G.pool;
    }
    if (G.on) return save(api, c);
    if (*(const volatile uint16_t *)0x1021C360u == 0xF7FFu) return say_s(api, c, "No IMU hook: flash retarget EG");
    if (*IMU_HOOK_CELL && *IMU_HOOK_CELL != (uint32_t)tick) return say_s(api, c, "IMU hook in use");
    memset(G.pool, 0, HDR);
    G.used = G.ticks = G.sessions = G.wait = G.copying = G.off = 0;
#ifdef SNAP_FOCUS
    G.was = *TRIGGER & 0xFFu; if (G.was > 3u) G.was = 3u;
#else
    G.was = *TRIGGER != 0;
#endif
    G.t0 = time_us();
    barrier();
    G.on = 1; barrier();
    *IMU_HOOK_CELL = (uint32_t)tick;
    return say_s(api, c, "Snapshots armed, Run=save");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, SNAP_KIND "snapshot ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
