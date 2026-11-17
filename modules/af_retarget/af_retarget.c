/* AF retarget (research SD module, method B): when the stock subject tracking
 * (started with OK) runs and a face / pet shows up that was not there when the
 * tracking started, move the tracker onto the subject -- no key press, so the
 * tracking keeps running (and a half-press is not disturbed).
 *
 * Trigger: the v7 handover rule (camera-tested with the OK press, HO000048):
 * a subject already detected at tracking start blocks it until gone for CLEAR
 * updates; otherwise HOLD updates with a subject (MISS without one reset) fire
 * once per tracking session.
 *
 * Action (experiment): AF000078 / AF000299 show the tracker's live position as
 * a packed coarse grid (lo16 x, hi16 y) at 0xC23AA028 and 0xC23AA02C (always
 * equal, updated every frame, zeroed when tracking ends); box centre in
 * tracking units ~ grid x * 32.4, grid y * 34.4. The face box from the
 * detection writer (face_hook args, ~106 tracking units per cell) is converted
 * and written to both words, on WRITES consecutive detection updates while the
 * subject is still seen. Whether the tracker follows (it reads them as its
 * search centre) or overwrites them (output only) is what this run measures.
 *
 * v9: RT000095 (v8) had no update at all -- the camera ran an EG without the
 * detection hook (the AF000078 log EG). Arming now checks both hook sites and
 * says which are present; the retarget EG has the detection hook and the IMU
 * hook (no gyro-evr). The detection callback is used when it runs; the IMU
 * callback (every 50 ms) runs the same rule from the face box in RAM when no
 * detection update came for 200 ms. Each record also keeps 0xC23AA000..03C
 * and 0xC237F684 (box shown) to tell the face-tracking state (screen 3) apart.
 *
 * Run 1: arm.  Run 2: disarm and write A:\Z30\RTnnnnnn.BIN.  Power off disarms.
 * Needs the retarget EG (tools/build_af_log.py --module retarget).
 * File: "AFRTGT9\0" (v8: AFRTGT8), u32 count, u32 fires, u32 ticks (detection), u32 rec_bytes (104),
 *       u32 writes, u32 hold, u32 imu ticks, u32 hooks (bit0 detection, bit1 IMU), then count x struct rec.
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
#define FACE_CELL ((volatile uint32_t *)0xC1832D40u)   /* face_hook */
#define IMU_CELL ((volatile uint32_t *)0xC1832D48u)    /* imu_hook */
#define FACE_SITE 0x101944B0u                           /* stock: push {r4-r6,lr} = 0xB570 */
#define IMU_SITE 0x1021C360u                            /* stock: bl = 0xF7FF 0xFC6A */
#define FACE_BOX 0xC237F624u                            /* x, y, w, h words (last detection) */
#define QUIET_US 200000u

#define RD(a) (*(const volatile uint32_t *)(a))
#define WR(a, v) (*(volatile uint32_t *)(a) = (v))
#define TRACKING 0xC237F688u
#define SUBJECTS 0xC237E3E4u
#define FACE_STATE 0xC237E2C0u
#define BOX0 0xC237D59Cu
#define BOX1 0xC237D5A0u
#define TPOS0 0xC23AA028u
#define TPOS1 0xC23AA02Cu
#define HOLD 3u
#define MISS 10u
#define CLEAR 10u
#define REARM_TICKS 10u
#define WRITES 6u
/* face cell centre -> tracker grid, fitted on RT000114 (205 updates tracking a face):
 * x = 2.70 * (x + w/2), y = 2.08 * (y + h/2) + 8 (v8/v9 used 3.27 / 3.08: too far right / down) */
#define GX(fx, fw) ((uint32_t)(((2 * (fx) + (fw)) * 1350) / 1000))
#define GY(fy, fh) ((uint32_t)(((2 * (fy) + (fh)) * 1041) / 1000 + 8))

struct rec {                          /* 104 bytes */
    uint32_t t_ms;
    uint8_t tracking, subjects, face_state, act;   /* act: 1 fire, 2 write, 4 blocked, 8 from the IMU tick */
    uint32_t box0, box1;
    int16_t fx, fy, fw, fh;           /* this update's face box */
    uint32_t pos_before, pos_after, aux;           /* aux = 0xC23AA034 */
    uint32_t shown;                   /* 0xC237F684 */
    uint32_t trk[16];                 /* 0xC23AA000..0xC23AA03C */
};
_Static_assert(sizeof(struct rec) == 104, "rec");
#define HDR 40u
#define CAP 12000u
#define POOL_BYTES (HDR + CAP * sizeof(struct rec))

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }

static struct {
    uint8_t *pool; struct rec *log;
    uint32_t count, ticks, imu_ticks, fires, near_run, miss, off_run, done, session, blocked, clear_run, writes, file_no, hooks;
    uint64_t t0, last_face;
    volatile uint32_t on, busy;
} G;

struct context { uint32_t runs, pad; char text[40]; };

static void update(int32_t fx, int32_t fy, int32_t fw, int32_t fh, uint32_t src)
{
    uint32_t tracking = RD(TRACKING) != 0;
    if (!tracking) {
        if (G.off_run < REARM_TICKS) G.off_run++;
        if (G.off_run >= REARM_TICKS) G.done = 0;
        G.session = G.near_run = G.miss = G.writes = 0;
        return;
    }
    G.off_run = 0;
    uint32_t subjects = RD(SUBJECTS), state = RD(FACE_STATE);
    int seen = subjects > 0 && subjects <= 5;
    if (!G.session) { G.session = 1; G.blocked = seen; G.clear_run = 0; G.near_run = G.miss = 0; }
    if (G.blocked) {
        G.clear_run = seen ? 0 : G.clear_run + 1;
        if (G.clear_run >= CLEAR) G.blocked = 0;
    } else if (seen) { G.near_run++; G.miss = 0; }
    else if (++G.miss >= MISS) { G.near_run = 0; G.miss = 0; }
    uint32_t act = (G.blocked ? 4u : 0u) | src;
    if (!G.done && !G.blocked && G.near_run >= HOLD) { G.done = 1; G.fires++; G.writes = WRITES; act |= 1u; }
    uint32_t before = RD(TPOS0), after = before;
    int box_ok = fw > 0 && fh > 0 && fx >= 0 && fy >= 0 && fx + fw <= 64 && fy + fh <= 48;
    if (G.writes && seen && box_ok && before) {
        uint32_t p = GY(fy, fh) << 16 | GX(fx, fw);
        WR(TPOS0, p); WR(TPOS1, p);
        after = RD(TPOS0);
        G.writes--; act |= 2u;
    }
    if (G.count < CAP) {
        struct rec *r = &G.log[G.count++];
        r->t_ms = (uint32_t)((time_us() - G.t0) / 1000u);
        r->tracking = 1; r->subjects = (uint8_t)subjects;
        r->face_state = (uint8_t)((state & 0x7Fu) | (state & 0x100u ? 0x80u : 0u)); r->act = (uint8_t)act;
        r->box0 = RD(BOX0); r->box1 = RD(BOX1);
        r->fx = (int16_t)fx; r->fy = (int16_t)fy; r->fw = (int16_t)fw; r->fh = (int16_t)fh;
        r->pos_before = before; r->pos_after = after; r->aux = RD(0xC23AA034u);
        r->shown = RD(0xC237F684u);
        for (uint32_t i = 0; i < 16; i++) r->trk[i] = RD(0xC23AA000u + 4u * i);
    }
}

/* one caller at a time (the detection and IMU tasks may overlap): skip, never wait */
static int enter(void)
{
    if (!G.on || G.busy) return 0;
    G.busy = 1; barrier();
    return 1;
}
static void leave(void) { barrier(); G.busy = 0; }

static void face_tick(const void *result, const int16_t *xy, const int16_t *wh)
{
    (void)result;
    if (!enter()) return;
    G.ticks++; G.last_face = time_us();
    update(xy[0], xy[1], wh[0], wh[1], 0);
    leave();
}

static void imu_tick(const void *result)
{
    (void)result;
    if (!enter()) return;
    G.imu_ticks++;
    if (time_us() - G.last_face > QUIET_US)
        update((int32_t)RD(FACE_BOX), (int32_t)RD(FACE_BOX + 4), (int32_t)RD(FACE_BOX + 8), (int32_t)RD(FACE_BOX + 12), 8);
    leave();
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
    if (*FACE_CELL == (uint32_t)face_tick) *FACE_CELL = 0;
    if (*IMU_CELL == (uint32_t)imu_tick) *IMU_CELL = 0;
    uint32_t h[8] = { 0x54524641u, 0x00395447u, G.count, G.fires, G.ticks, sizeof(struct rec), WRITES, HOLD };
    memcpy(G.pool, h, 32); memcpy(G.pool + 32, &G.imu_ticks, 4); memcpy(G.pool + 36, &G.hooks, 4);
    char path[24] = "A:\\Z30\\RT000000.BIN";
    uint32_t sec = (uint32_t)(time_us() / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    uint32_t bytes = HDR + G.count * sizeof(struct rec);
    shared_clean(G.pool, bytes);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = fd >= 0 && write_all(fd, G.pool, bytes);
    if (fd >= 0) FS_CLOSE(fd);
    if (!ok) return say(api, c, "Save failed ", (uint32_t)fd, "");
    char *p = c->text;
    put_s(&p, "Saved D"); put_u(&p, G.ticks); put_s(&p, " I"); put_u(&p, G.imu_ticks);
    put_s(&p, " T"); put_u(&p, G.count); put_s(&p, " R"); put_u(&p, G.fires); *p = 0;
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
    G.hooks = (*(const volatile uint16_t *)FACE_SITE != 0xB570u ? 1u : 0u) |
              (*(const volatile uint16_t *)IMU_SITE != 0xF7FFu ? 2u : 0u);
    if (!G.hooks) return say_s(api, c, "No hooks: flash retarget EG");
    if ((G.hooks & 1u) && *FACE_CELL && *FACE_CELL != (uint32_t)face_tick) return say_s(api, c, "Detection hook in use");
    if ((G.hooks & 2u) && *IMU_CELL && *IMU_CELL != (uint32_t)imu_tick) return say_s(api, c, "IMU hook in use");
    G.count = G.ticks = G.imu_ticks = G.fires = G.near_run = G.miss = G.writes = 0; G.off_run = REARM_TICKS;
    G.done = G.session = G.blocked = G.clear_run = 0; G.t0 = G.last_face = time_us();
    barrier();
    G.on = 1; barrier();
    if (G.hooks & 1u) *FACE_CELL = (uint32_t)face_tick;
    if (G.hooks & 2u) *IMU_CELL = (uint32_t)imu_tick;
    return say_s(api, c, G.hooks == 3u ? "Retarget armed D+I, Run=save" : G.hooks == 1u ? "Retarget armed D, Run=save" : "Retarget armed I, Run=save");
}

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    struct context *c = ctx;
    char *p = c->text; put_s(&p, "AF retarget 9 ready"); *p = 0;
    api->set_text(c->text);
    return api->set_run_handler(module_run);
}
