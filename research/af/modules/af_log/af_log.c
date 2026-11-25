/* AF log (research SD module, read only): where do the live AF and face boxes live?
 *
 * The stock debug screen at 0x101953xx prints, from the record at 0xC237F578,
 *   +0x90  "AF:%d X:%d Y:%d W:%d H:%d"   (5 words)
 *   +0xA8  "Fa:%d X:%d Y:%d W:%d H:%d"   (5 words)
 * Log 1 (camera, 2026-11-13): the face box at +0xA8 is live (~10-20 Hz), the AF
 * box stays 0. Its writer 0x101944B0 (called from 0x10071D36) first copies a
 * 0xF0-byte detection result to 0xC237E2E8, then the face x/y/w/h. Log 2 adds
 * that result: two windows (WINDOWS) are sampled on every stock IMU-task tick
 * (50 ms in photo mode, also without the gyro module); a copy is kept whenever
 * any byte changed. Nothing in the camera is written.
 * Log 3: the af_scan result (SC000174.BIN) adds the words that change only
 * while tracking: a ring of nine 12-byte entries 0x630 apart from 0xC237F944
 * (packed int16 pairs in sensor-like units), 0xC237D59C, 0xC23A9CE4 and
 * 0xC23AA000..0xC23AA034.
 *
 * Run 1: start logging.  Run 2: stop and write A:\Z30\AFnnnnnn.BIN.
 * Further Runs start a new log. Power off disarms.
 *
 * Log 4 (AFLOG4): tracking start / stop / face windows, see WINDOWS.
 * Log 5 (AFLOG5, -DLOG5): touch vs half-press tracking around a face arriving.
 * Log 6 (AFLOG6, -DLOG6): the AF mode chooser's inputs and the chosen mode.
 * Log 7 (AFLOG7, -DLOG7): subject-lost timeout and the AF in-focus result.
 * Log 8 (AFLOG8, -DLOG8): the OK key code in live view, and the UI AF frame lists.
 * Log 9 (AFLOG9, -DLOG9): how near the subject must be when stock hands a
 *   tracking over (3D tracking on the firmware with the switch on).
 * File: header "AFLOG3/4/5/6/7/8/9\0\0", u32 rec_bytes, u32 count, u32 ticks, u32 dropped,
 *       u32 windows, then windows x { u32 base, u32 bytes } (header HDR bytes);
 *       then count x { u32 t_ms, u32 tick, the windows' bytes back to back }.
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

#if defined(LOG12)
/* log 12 (2026-11-15, grey box position): the AF subject list (all entries) next
 * to the stock AF display block 0xC237C7B4 (the white frame's screen coordinates
 * at +0xC0..), to fit list units -> screen pixels from the selected subject. */
static const uint32_t WINDOWS[][2] = {
    { 0xC259EF18u, 0x240u },   /* AF subject list: count +0x38, eight 0x40-byte entries */
    { 0xC237C7B4u, 0x468u },   /* stock AF display block (frame lists) */
    { 0xC237F620u, 0x20u },    /* selected face box */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
};
#define REC_BYTES (0x240u + 0x468u + 0x20u + 0x10u)
#define LOG_VERSION 0x00323147u
#elif defined(LOG11)
/* log 11 (2026-11-14, 3D tracking grey boxes not shown on the camera): the box
 * drawer's own diagnostics (calls, stages reached, last stage, boxes drawn, last
 * surface) in the 3D tracking state, found through trk3d_get (TRK3D_GET, passed
 * by tools/build_af_log.py from the EG report), next to its gate inputs. */
#ifndef TRK3D_GET
#error "TRK3D_GET"
#endif
#define DYNAMIC_BASE 1
static const uint32_t WINDOWS[][2] = {
    { 0u, 0x80u },             /* 3D tracking state + box history + diagnostics (trk3d_get) */
    { 0xC259EF18u, 0x80u },    /* AF subject list: count +0x38, first entry */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
    { 0xC18200C0u, 0x4u },     /* live view image surface */
    { 0xC181F6FCu, 0x4u },     /* UI service phase */
    { 0xC181A27Cu, 0x80u },    /* AF frame style lookup input: +1 focus mode, +2 column, +3 result, +9 state code */
    { 0xC1F8F12Cu, 0x10u },    /* its output: +0xA colour code, +0xB display mode */
    { 0xC237C7F8u, 0x24u },    /* AF-area mode, focus mode bytes, tracking bytes C815 / C816 */
};
#define REC_BYTES (0x80u + 0x80u + 0x8u + 0x10u + 0x4u + 0x4u + 0x80u + 0x10u + 0x24u)
#define LOG_VERSION 0x00313147u
#elif defined(LOG10)
/* log 10 (2026-11-14, all subjects): AF000095 shows that the 0xF0 detection
 * result 0xC237E2E8 is not a per-subject list (same layout with 1..5 subjects).
 * The touch subject search 0x10073C76 walks a list at 0xC259EF18: count at
 * +0x38, 0x40-byte entries from +0x3C (+0x40 kind, +0x42 x/y, +0x46 w/h). */
static const uint32_t WINDOWS[][2] = {
    { 0xC259EF18u, 0x300u },   /* AF subject list */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F620u, 0x20u },    /* selected face box */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
};
#define REC_BYTES (0x300u + 0x8u + 0x20u + 0x10u)
#define LOG_VERSION 0x00303147u
#elif defined(LOG9)
/* log 9 (2026-11-14, 3D tracking): with the firmware's 3D tracking switch on, a
 * half-press tracking hands over only when the tracking box is almost on the
 * subject, for Standard and Close alike. The stock decision is not the touch
 * flag 0xC18C7240 alone. Logged: the tracking box, the tracker grid position, the
 * detection result (all subjects) and the face box, the flag, the tracker mode
 * and the AF mode chooser's state, to measure the stock distance at the handover. */
static const uint32_t WINDOWS[][2] = {
    { 0xC237D590u, 0x40u },    /* tracking box (D59C / D5A0) */
    { 0xC237E2B8u, 0x20u },    /* face state */
    { 0xC237E2E8u, 0xF0u },    /* detection result (all subjects) */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F620u, 0x20u },    /* face box x y w h */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
    { 0xC23A9CA0u, 0x20u },    /* tracker previous mode */
    { 0xC23AA020u, 0x20u },    /* tracker live / grid position */
    { 0xC18C7220u, 0x40u },    /* touch flag 0xC18C7240 */
    { 0xC25A1E50u, 0x70u },    /* AF mode chooser state 0xC25A1E5C..0xC25A1EB0, focus result */
};
#define REC_BYTES (0x40u + 0x20u + 0xF0u + 0x8u + 0x20u + 0x10u + 0x20u + 0x20u + 0x40u + 0x70u)
#define LOG_VERSION 0x00003947u
#elif defined(LOG8)
/* log 8 (2026-11-14): (a) the key code the shooting screen reads for OK (input
 * state 0xC182004C: +0x58 = last code returned by the key reader 0x102F1AB6), to
 * find the stock code that enters the tracking mode; (b) the UI's AF frame lists
 * 0xC237C300.. (box corners, per-box flags) next to the focus result 0xC25A1E70
 * (0 idle, 1 not in focus, 2 in focus), to find what selects the frame colour. */
static const uint32_t WINDOWS[][2] = {
    { 0xC1820040u, 0x80u },    /* input state: ring index, last key code +0x58 */
    { 0xC237C300u, 0x900u },   /* UI AF frame lists */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
    { 0xC25A1E60u, 0x20u },    /* AF active 0xC25A1E6C, focus result 0xC25A1E70 */
};
#define REC_BYTES (0x80u + 0x900u + 0x8u + 0x10u + 0x20u)
#define LOG_VERSION 0x00003847u
#elif defined(LOG7)
/* log 7 (2026-11-14): (a) how long the subject box survives once the face / eyes
 * are lost (subject turns away) before it falls back to the tracker box, and
 * which counter runs meanwhile; (b) the AF result (in focus / not) behind the
 * focus box colour in AF-S and AF-C. */
static const uint32_t WINDOWS[][2] = {
    { 0xC237D590u, 0x40u },    /* live box, shown box */
    { 0xC237E2B8u, 0x20u },    /* face state */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F578u, 0x200u },   /* AF / face debug record (AF:%d X Y W H, Fa:..., F680..F688 at +0x108..) */
    { 0xC23A9CA0u, 0x20u },    /* tracker previous mode */
    { 0xC259FEE0u, 0x60u },    /* mode chooser inputs */
    { 0xC25A1E50u, 0x48u },    /* chosen mode +0xF84 */
};
#define REC_BYTES (0x40u + 0x20u + 0x8u + 0x200u + 0x20u + 0x60u + 0x48u)
#define LOG_VERSION 0x00003747u
#elif defined(LOG6)
/* log 6 (2026-11-14): the AF mode chooser 0x10073ED2 picks 0x201..0x206 per frame
 * (0x202 / 0x205 = tracking for the tracker 0x100AC8E2, leaving them clears the
 * tracker as in the touch upgrade) and sends it with 0x10204454. Its inputs and
 * the chosen mode, next to the tracker's previous-frame mode 0xC23A9CB4. */
static const uint32_t WINDOWS[][2] = {
    { 0xC237D590u, 0x40u },    /* live box, shown box, D5B0.. */
    { 0xC237E2B8u, 0x20u },    /* face state */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F620u, 0x10u },    /* face box */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
    { 0xC23A9CA0u, 0x20u },    /* tracker: previous frame no / mode 0xC23A9CB4 */
    { 0xC23AA020u, 0x10u },    /* tracker struct AA024.. */
    { 0xC18C7110u, 0x40u },    /* chooser state 0xC18C7128 (+0x10 +0x11 +0x1D) */
    { 0xC18C7220u, 0x40u },    /* chooser flags 0xC18C7228 (+0x14 +0x34) */
    { 0xC259FEE0u, 0x60u },    /* 0xC259FF0C (+0x10 mode byte), 0xC259FF20 (+8 +0xC +0x29) */
    { 0xC25A1E50u, 0x48u },    /* 0xC25A0F0C + 0xF50..0xF84 (0xF84 = chosen mode) */
};
#define REC_BYTES (0x40u + 0x20u + 0x8u + 0x10u + 0x10u + 0x20u + 0x10u + 0x40u + 0x40u + 0x60u + 0x48u)
#define LOG_VERSION 0x00003647u
#elif defined(LOG5)
/* log 5 (2026-11-14): what changes at the moment a touch-started tracking turns
 * into the face box (screen 3), versus a half-press tracking that does not
 * (SN000121: candidates of the start method). */
static const uint32_t WINDOWS[][2] = {
    { 0xC237D590u, 0x40u },    /* live box, shown box, D5B0..D5C0 (0 OK / 1-2 touch) */
    { 0xC237E2B8u, 0x20u },    /* face state 0xC237E2C0 */
    { 0xC237E3E0u, 0x8u },     /* subject count */
    { 0xC237F080u, 0x30u },    /* F084..F0A8: touch 6a/b4a.., button 4d/beb.. */
    { 0xC237F560u, 0x30u },    /* start box copy, F570, F584 */
    { 0xC237F620u, 0x10u },    /* face box */
    { 0xC237F680u, 0x10u },    /* AF working / shown / tracking */
    { 0xC2387200u, 0x90u },    /* state block: OK differs from touch / half-press */
    { 0xC238AF38u, 0x8u },
    { 0xC238B020u, 0x8u },
    { 0xC23A9FE0u, 0x60u },    /* tracker struct (AA024 ..) */
    { 0xC181E4C0u, 0x10u },    /* UI handlers set for touch / half-press */
};
#define REC_BYTES (0x40u + 0x20u + 0x8u + 0x30u + 0x30u + 0x10u + 0x10u + 0x90u + 0x8u + 0x8u + 0x60u + 0x10u)
#define LOG_VERSION 0x00003547u
#else
static const uint32_t WINDOWS[][2] = {
    /* log 4: what changes once when tracking starts (SC000174 re-read): */
    { 0xC237D590u, 0x20u },    /* live tracking box */
    { 0xC237E2B8u, 0x20u },    /* face state 0xC237E2C0 */
    { 0xC237E3E0u, 0x8u },     /* subject count 0xC237E3E4 */
    { 0xC237F680u, 0x10u },    /* AF working / tracking flags F680..F688 */
    { 0xC23A9FE0u, 0x60u },    /* tracker state, start request 0xC23A9FF4.. */
    { 0xC23AA280u, 0x40u },
    { 0xC23AA400u, 0x60u },    /* message-id list 0x18xx / 0x1Axx / 0x1Dxx */
    { 0xC23EED00u, 0x40u },
    { 0xC23FCD50u, 0x20u },
    { 0xC23FDD58u, 0x20u },
};
#define REC_BYTES (0x20u + 0x20u + 0x8u + 0x10u + 0x60u + 0x40u + 0x60u + 0x40u + 0x20u + 0x20u)
#define LOG_VERSION 0x00003447u
#endif
#define NWIN (sizeof WINDOWS / sizeof WINDOWS[0])
_Static_assert(7 + 2 * (sizeof WINDOWS / sizeof WINDOWS[0]) <= 160 / 4, "header holds the window list");
#define ENTRY (8u + REC_BYTES)
#define POOL_BYTES (4u << 20)
#define HDR 160u

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; uint32_t cap, count, ticks, dropped, file_no;
    volatile uint32_t on, busy;
    uint64_t t0;
    uint32_t dyn;
    uint8_t last[REC_BYTES], now[REC_BYTES];
} G;

struct context { uint32_t runs, pad; char text[40]; };

static void tick(uint32_t stock_result)
{
    (void)stock_result;
    G.busy = 1; barrier();
    if (!G.on) goto out;
    G.ticks++;
    uint8_t *now = G.now;              /* not on the stock IMU task's stack */
    int changed = G.count == 0;
    for (uint32_t w = 0, k = 0; w < NWIN; w++)
        for (uint32_t i = 0; i < WINDOWS[w][1]; i += 4, k += 4) {
#ifdef DYNAMIC_BASE
            uint32_t base = WINDOWS[w][0] ? WINDOWS[w][0] : G.dyn;
            uint32_t v = base ? *(const volatile uint32_t *)(base + i) : 0;
#else
            uint32_t v = *(const volatile uint32_t *)(WINDOWS[w][0] + i);
#endif
            memcpy(now + k, &v, 4);
            if (v != *(const uint32_t *)(G.last + k)) changed = 1;
        }
    if (!changed) goto out;
    if (G.count >= G.cap) { G.dropped++; goto out; }
    uint8_t *e = G.pool + HDR + G.count * ENTRY;
    uint32_t t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
    memcpy(e, &t_ms, 4); memcpy(e + 4, &G.ticks, 4);
    memcpy(e + 8, now, REC_BYTES); memcpy(G.last, now, REC_BYTES);
    G.count++;
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

static uint32_t save(const struct z30_module_api *api, struct context *c)
{
    G.on = 0; barrier();
    for (uint32_t spin = 0; G.busy && spin < 50000000u; spin++) barrier();
    if (G.busy) return say(api, c, "Busy ", 0, ", Run again");
    uint32_t h[HDR / 4] = { 0x4F4C4641u, LOG_VERSION, REC_BYTES, G.count, G.ticks, G.dropped, NWIN };
    for (uint32_t w = 0; w < NWIN; w++) { h[7 + 2 * w] = WINDOWS[w][0] ? WINDOWS[w][0] : G.dyn; h[8 + 2 * w] = WINDOWS[w][1]; }
    memcpy(G.pool, h, HDR);
    char path[24] = "A:\\Z30\\AF000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.count * ENTRY;
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    if (!ok) return say(api, c, "Save failed ", (uint32_t)fd, "");
    return say(api, c, "Saved ", G.count, " recs");
}

static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    c->runs++;
    if (!G.pool) {
        G.pool = POOL_ALLOC(POOL_BYTES, 0);
        if (!G.pool) return say(api, c, "No memory ", 0, "");
        G.cap = (POOL_BYTES - HDR) / ENTRY;
    }
    if (G.on) return save(api, c);
    if (*IMU_HOOK_CELL && *IMU_HOOK_CELL != (uint32_t)tick) return say(api, c, "IMU hook in use ", 0, "");
    G.count = G.ticks = G.dropped = 0; G.t0 = time_us();
#ifdef DYNAMIC_BASE
    G.dyn = ((uint32_t (*)(void))(TRK3D_GET))();
    if (!G.dyn) return say(api, c, "No 3D tracking state ", 0, "");
#endif
    barrier();
    *IMU_HOOK_CELL = (uint32_t)tick;
    G.on = 1; barrier();
    return say(api, c, "Logging, Run=save (cap ", G.cap, ")");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, "AF log ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
