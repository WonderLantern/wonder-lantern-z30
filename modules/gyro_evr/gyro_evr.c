/* Gyro e-VR stabilizer for Nikon Z30 C 1.20 (SD module and built-in firmware copy).
 *
 * EVR_PRODUCT (built into full, see docs/gyro-evr.md): diagnostics left out; the
 * Gyro e-VR menu choice (via api->get_property) and one owner of the IMU
 * (stabilize / idle) are added. The notes
 * below describe the research SD module.
 *
 * Needs the stage-4 EG (e-VR hooks on both paths + IMU-task hook).
 *
 * The IMU task drains the gyro FIFO and integrates yaw/pitch/roll angles with
 * a slow bias estimate. The VrMx path-1 callback rewrites the result corners
 * (A-D and A0-D0) to add a bounded translation (+ small roll) that stabilizes
 * the frame. Each correction is scaled down until the sampling quad stays
 * SAFETY px inside the warp-input frame, so it adapts to 1080 and 2160 alike.
 * On any doubt (path 2, stale gyro, invalid focal length) stock is kept.
 *
 * Run: the first Run arms (gyro + hooks) in OFF. Further Runs step
 * OFF -> SINE -> GYRO -> SAVE (writes A:\Z30\STnnnnnn.BIN, back to OFF).
 * SINE ignores the gyro: a slow horizontal sway proving the corner write
 * reaches the hardware, its direction, and the clamp. Power off disarms.
 */
#include "z30_research.h"

typedef void *(*pool_alloc_fn)(uint32_t size, uint32_t flags);
#define POOL_ALLOC ((pool_alloc_fn)0x10211F4Du)
typedef int32_t (*bus_fn)(uint32_t bus);
#define BUS_OPEN  ((bus_fn)0x101DCFE5u)
#define BUS_CLOSE ((bus_fn)0x101DD027u)
typedef int32_t (*bus_xfer_fn)(uint32_t bus, void *xfer);
#define BUS_XFER  ((bus_xfer_fn)0x101DD061u)
#define IMU_ROOT  ((volatile uint8_t *)0xC1807DDCu)
typedef int32_t (*fs_open_fn)(const char *path, uint32_t flags);
#define FS_OPEN   ((fs_open_fn)0x102C8AFDu)
typedef int32_t (*fs_write_fn)(int32_t fd, const void *buf, uint32_t len, uint32_t *done);
#define FS_WRITE  ((fs_write_fn)0x102C8A6Bu)
typedef int32_t (*fs_close_fn)(int32_t fd);
#define FS_CLOSE  ((fs_close_fn)0x102C89EFu)
typedef int32_t (*range_fn)(uint32_t addr, uint32_t len, uint32_t op, uint32_t scope);
#define RANGE_SERVICE ((range_fn)0x10035AACu)      /* ARM mode: even address */
#define IMU_HOOK_CELL ((volatile uint32_t *)0xC1832D48u)

#define IMU_ADDR 0x68
enum { R_FIFO_CFG = 0x16, R_FIFO_COUNT = 0x2E, R_FIFO_DATA = 0x30, R_SPR = 0x4B,
       R_INTF0 = 0x4C, R_PWR = 0x4E, R_GCFG = 0x4F, R_FIFO_CFG1 = 0x5F, R_WHOAMI = 0x75 };
#define GYRO_CFG 0x4F          /* +-500 dps, 500 Hz */
#define GYRO_CFG_200 0x47      /* +-500 dps, 200 Hz */
#define GYRO_CFG_100 0x48      /* +-500 dps, 100 Hz */
#define GYRO_LSB 65.5
#define PI 3.14159265358979

/* Measured: stock shifts the corner centre ~61 px/deg at 50 mm in the
 * 1866-wide warp-input space, i.e. ~70 px per mm of focal length there.
 * focal_px(geom) = focal_mm * 70 * geom0 / 1866. */
/* Stage 13 calibration against stock optical flow: the gain is physical,
 * focal_px = focal_mm * geom0 / 23.5 mm sensor width (71 px/deg at 50 mm, 1866). */
#define SENSOR_MM 23.5
#define PX_PER_MM_REF (1866.0 / SENSOR_MM)   /* logged only */
#define SAFETY 32.0           /* px kept clear of the frame edge (stage 21: 12 -> 32, warp fetch overshoot) */
#define HP_TAU_US 800000.0    /* intended-path low-pass time constant (stage 26: 0.8 s) */
#define HP_CUTOFF (16667.0 / HP_TAU_US)   /* logged only */
#define ZOOM_STD 0.93         /* standard: ~7.5% zoom for margin */
#define ZOOM_ENH 0.916        /* enhanced: 1.2x the standard extra crop (0.902 = 1.4x tore the image on hardware) */
#define ZOOM_NATIVE 1.0       /* stock crop only */
/* Standard (RS) (product menu row 1): Standard + rolling-shutter (jelly)
 * correction. The top and bottom rows were exposed half a readout before / after
 * the frame centre; the quad's top and bottom corners are sheared by the gyro
 * angle change over half a readout (RS-A direction, stage 20 on camera). The
 * warp hardware tears once a quad deforms ~20 px away from a rigid shape (Gyro H
 * v5; 6-10 px never tore, stages 22-23), and the uncapped shear of stages 20-21
 * tore all the time, so each corner's shear is capped. v6/v7 capped each axis at
 * 6 px (no tearing on camera, jelly only slightly reduced); v8 caps the length of
 * the shear vector at RS_CAP = 10 px, the largest per-corner deformation that
 * never tore (stage 23, vector length 10 px). */
#define RS_CAP 10.0
/* The RS modes read the gyro at 200 Hz (the ICM-42605 has no 250 Hz; at 100 Hz the
 * 9 ms readout falls between two 10 ms samples). Every other mode keeps 100 Hz,
 * the rate the 38 / 15 ms lead times were fitted with (camera, PURE200 vs
 * PURE100: no visible difference for plain stabilization). */
#define RS_GYRO_CFG GYRO_CFG_200
#define READOUT_1080_US 9000.0    /* Gyroflow's Z30 profile: 1080 any fps 9 ms, 2160 21 ms */
#define READOUT_4K_US 21000.0
#ifdef EVR_PRODUCT
#define SHEAR(e, t) ((e) + (t))
#else
#define SHEAR(e, t) ((void)(t), (e))   /* research SD module: no shear (16 KB limit) */
#endif
#define LENS_HOLD_FRAMES 20u  /* lens words frozen this long -> lens VR off */
#define LENS_BLEND 0.05       /* per frame (~0.6 s at 30p, 0.3 s at 60p) */
/* stage 27 gain model, fitted against stock optical flow at fixed, sharp focus
 * (ST000219 50 mm, ST000298 ~220 mm): image scale / physical(reported focal) =
 * base(f) + slope(f) * (focus word 0x25AE - far reference), extra capped at +10%. */
#define GAIN_BASE_W 1.035     /* 50 mm, far focus */
#define GAIN_BASE_T 1.036     /* >= 211 mm, far focus */
#define FOCUS_REF_W 100.0     /* 0x25AE at far focus, 50 mm */
#define FOCUS_REF_T 248.0     /* 0x25AE at far focus, ~220 mm */
#define FOCUS_SLOPE_W 3.2e-5  /* per 0x25AE unit at 50 mm  (2148 -> +6.5%) */
#define FOCUS_SLOPE_T 1.09e-3 /* per 0x25AE unit at ~220 mm (323 -> +8.2%) */
#define FOCUS_EXTRA_MAX 0.10
#define SOFT_LIMIT 0.90       /* correction kept within this share of the margin (stage 26: 0.90) */
#define OUT_SMOOTH 0.75       /* light output smoothing (0.35 lagged ~2 frames -> overshoot) */
#define SGN_X (+1.0)
#define SGN_Y (-1.0)
/* Roll (gyro Z = camera back axis). The lens VR cannot correct roll, so roll is
 * applied in every gyro state, also while the lens stabilizes yaw/pitch. Sign
 * derived in the same frame as the camera-confirmed yaw/pitch signs: a +Z turn
 * rotates the content clockwise on screen (y down), and the sampling quad
 * follows the content like it does for yaw/pitch -> rotate the quad by +roll. */
#define SGN_ROLL (+1.0)
#define ROLL_LIMIT 0.0436     /* rad, 2.5 deg (stage 26); further limited by the margin */
#define STALE_US 250000u      /* 50 ms IMU period must never read as stale */
#define LEAD_30P_US 38000u    /* measured vs optical flow: 1080/30 40 ms, 4K/30 35 ms */
#define LEAD_60P_US 15000u    /* 1080/60: fits 10-20 ms across logs (r 0.98-0.99) */
#define LEAD_US LEAD_30P_US   /* logged only */
#define EXTRAP_TAU 40000.0    /* damped extrapolation beyond the newest sample */
#define N_ASAMP 1024u         /* ~2 s of timestamped angle (in the pool) */

/* Run cycles OFF -> QUIET -> PURE 20ms -> PURE 10ms -> SAVE (stock IMU task period). QUIET: no gyro I2C at all
 * (baseline for stock's own bus stalls), image untouched. PURE*: stock's own
 * translation removed, gyro only, 100 Hz gyro ODR. */
/* Run cycles OFF -> STD -> ENH -> NATIVE -> SAVE. All gyro modes are pure gyro
 * (stock optical-flow translation removed), 10 ms stock IMU period, 100 Hz gyro. */
/* stage 20: OFF -> STD -> RSA -> RSB -> ENH -> NATIVE -> SAVE.
 * RSA / RSB: standard + rolling-shutter shear, readout top-first / bottom-first. */
enum { M_OFF, M_STD, M_ENH, M_SAVE, M_NATIVE, M_RS, M_NMODES };
/* Removed after camera tests: rolling-shutter shear (stage 20-21: stalled the warp
 * hardware), perspective / homography terms (stage 22-23: looked wrong at 16 mm),
 * ENH zoom 0.902/0.916 (tore the image). Final: translation + roll, STD or NATIVE. */
#define M_COUNT 4               /* header layout (NATIVE frames are in G only) */
#ifdef EVR_PRODUCT
#define STAT(e) ((void)0)     /* diagnostics counters: research build only */
#else
#define STAT(e) ((void)(e))
#endif
#define IS_GYRO(m) ((m) == M_STD || (m) == M_ENH || (m) == M_NATIVE || IS_RS(m))
#define IS_RS(m) ((m) == M_RS)

struct xfer { uint8_t op, pad; uint16_t addr; const uint8_t *tx; uint8_t *rx; uint32_t tx_len, rx_len; };

struct log_rec {              /* 56 bytes */
    uint64_t t_us;
    uint32_t frame;
    uint8_t mode, clamped, path, flags;  /* flags bit0 stale */
    int32_t focal_mm, geom0;
    int32_t dx_q8, dy_q8;                /* applied corner translation, Q8 px */
    int32_t roll_mdeg, yaw_dev_mdeg, pitch_dev_mdeg;
    int16_t stock_cx_q2, stock_cy_q2;    /* stock A-D centroid offset from geom centre, 1/4 px */
    int32_t alpha, beta;                 /* stock optical-flow motion of this frame (ground truth) */
};
_Static_assert(sizeof(struct log_rec) == 56, "log record");

struct header {
    char magic[8];                       /* "EVRSTB1" */
    uint32_t version, hdr_bytes, rec_size, count, total;
    uint32_t whoami, pwr_after, gcfg_after, intf0, backoffs;
    uint32_t mode_frames[M_COUNT], stale_frames, path2_frames, clamp_frames;
    int32_t px_per_mm_x100, safety_px, hp_x1000, sine_amp_px;
    uint64_t t_armed, t_saved;
    uint32_t reserved[8];
    uint32_t n_alog, alog_bytes;           /* v6: raw angle samples appended after the IMU events */
    uint32_t cfg_ptr, per_orig, per_set, per_writes;   /* v7: stock IMU task period control */
    uint32_t n_lvr, lvr_bytes;                         /* v9: lens-VR blocks after the angle log */
};
#define HDR_BYTES 256u
_Static_assert(sizeof(struct header) <= HDR_BYTES, "header fits");

/* EVR_PRODUCT (the built-in firmware copy): diagnostics shrunk to one entry so the
 * permanent pool block is ~37 KB instead of ~300 KB; stabilization is identical. */
#ifdef EVR_PRODUCT
#define N_LOG 1u
#else
#define N_LOG 2048u
#endif
struct imu_ev { uint32_t t_ms; uint8_t stock, nvalid, pwr, fcfg; uint16_t count, flags; uint8_t mode, ntrans; uint16_t io_us100; };  /* 16 bytes */
_Static_assert(sizeof(struct imu_ev) == 16, "imu_ev");
#ifdef EVR_PRODUCT
#define N_EV 1u
#else
#define N_EV 1024u              /* ~50 s of 50 ms IMU ticks */
#endif
enum { EV_PAUSED = 1, EV_BACKOFF = 2, EV_CNTFAIL = 4, EV_DATAFAIL = 8, EV_EMPTY = 16, EV_FIXED = 32, EV_SLOW = 64, EV_FLUSH = 128, EV_QUIET = 256, EV_ODR = 512, EV_DESYNC = 1024 };
struct alog { uint32_t t_us; int32_t yaw_urad, pitch_urad; };   /* 12 bytes (v13: integer micro-radians) */
#ifdef EVR_PRODUCT
#define N_ALOG 1u
#else
#define N_ALOG 4096u            /* 41 s at 100 Hz */
#endif
#define LVR_BYTES 96u
/* stage 25: focus-breathing hunt. Every SNAP_EVERY gyro frames, copy the whole
 * frame metadata (0x2C00) and the e-VR input (0x70) into a ring of N_SNAP. */
#ifdef EVR_PRODUCT
#define SNAP_META 0u
#define SNAP_IN 0u
#else
#define SNAP_META 0x2C00u
#define SNAP_IN 0x70u
#endif
#define SNAP_REC (8u + SNAP_META + SNAP_IN)
#ifdef EVR_PRODUCT
#define N_SNAP 1u
#else
#define N_SNAP 8u
#endif
#define SNAP_EVERY 90u
#define TMP_BYTES 2048u        /* whole FIFO: at 200 Hz a ~1.3 s stall is recovered */
#define CHUNK 128u
struct asample { uint64_t t_us; double yaw, pitch, roll; };  /* 32 bytes (double: no soft-float single lib) */
_Static_assert(sizeof(struct asample) == 32, "asample");
#define POOL_BYTES (HDR_BYTES + N_ASAMP * sizeof(struct asample) + N_LOG * sizeof(struct log_rec) + N_EV * sizeof(struct imu_ev) + TMP_BYTES + N_ALOG * sizeof(struct alog) + N_SNAP * SNAP_REC)

static struct {
    struct alog *alog; uint32_t al_head, al_count;
    uint32_t per_cfg, per_orig, per_set, per_writes, cfg_seen;
    uint32_t lv_x, lv_y, lv_quiet, lv_frames; double lv_w;
    uint8_t *snap; uint32_t sn_head, sn_count, sn_tick;
    uint32_t distress, distress_events, rs_capped; double last_rollv, dscale;     /* lens-VR detection and blend */
    uint8_t *pool, *tmp; uint32_t ntrans, io_us; volatile uint32_t req_cfg; uint32_t gcfg, odr_us; struct log_rec *log; struct imu_ev *ev; uint32_t ev_head, ev_count, empty_run, fixes;
    uint32_t nf_imu_old, nf_nsamp, nf_focal, nf_angle, was_stale;
    struct asample *ring;                /* timestamped cumulative angle (in pool) */
    volatile uint32_t ring_head;         /* published after each IMU push */
    uint32_t head, count, total;
    volatile uint32_t mode;              /* read by the VrMx callback */
    volatile uint32_t busy_imu, busy_frame, paused;
    double cyaw, cpitch, croll;          /* cumulative angle, IMU task only */
    double bias[3], bsq[3]; uint32_t recal;
    volatile uint64_t imu_t;
    double lp_yaw, lp_pitch, lp_roll;    /* intended path (VrMx task) */
    uint64_t t_last, t_frame; double frame_us;
    double tgt_dx, tgt_dy, out_dx, out_dy, tgt_roll, out_roll, fpx_q8; uint32_t gyro_ok;
    double tgt_sx, tgt_sy, out_sx, out_sy;   /* rolling-shutter shear, Q8 px (Gyro RS) */
    uint32_t nsamp, imu_ticks, slow_reads;
    uint32_t biased, cal_n, cal_us, cal_tries, cal_var_x100; double best_var, best_bias[3];
    uint32_t backoffs, desyncs; uint64_t backoff_until;
    uint32_t intf0, whoami, pwr_after, gcfg_after;
    uint32_t mode_frames[M_NMODES], stale_frames, path2_frames, clamp_frames;
    uint64_t t_armed, t0_sine;
    uint32_t file_no;
} G;

struct context { uint32_t armed, pad; char text[40]; };

void *memcpy(void *d, const void *s, uint32_t n)
{ uint8_t *a = d; const uint8_t *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int v, uint32_t n)
{ uint8_t *a = d; while (n--) *a++ = (uint8_t)v; return d; }


/* ---- I2C ------------------------------------------------------------- */
static uint32_t bus(void) { return *(volatile uint32_t *)(IMU_ROOT + 0x2C); }
static int imu_ok(void) { return IMU_ROOT[0] != 1 && bus() != 0; }
static int imu_read(uint8_t reg, uint8_t *dst, uint32_t n)
{
    uint32_t b = bus(); uint8_t r = reg;
    struct xfer x = { 1, 0, IMU_ADDR, &r, dst, 1, n };
    if (BUS_OPEN(b) != 0) return 0;
    int32_t e = BUS_XFER(b, &x); BUS_CLOSE(b); return e == 0;
}
static int imu_write(uint8_t reg, uint8_t v)
{
    uint32_t b = bus(); uint8_t d[2] = { reg, v };
    struct xfer x = { 1, 0, IMU_ADDR, d, 0, 2, 0 };
    if (BUS_OPEN(b) != 0) return 0;
    int32_t e = BUS_XFER(b, &x); BUS_CLOSE(b); return e == 0;
}
static uint32_t imu_reg(uint8_t reg) { uint8_t v = 0; return imu_read(reg, &v, 1) ? v : 0x100; }

/* ---- IMU task: drain FIFO, integrate, push timestamped angle ---------- */
static void push_sample(uint64_t t)
{
    uint32_t h = G.ring_head;
    struct asample *s = &G.ring[h];
    s->t_us = t; s->yaw = G.cyaw; s->pitch = G.cpitch; s->roll = G.croll;
    barrier();
    G.ring_head = (h + 1) % N_ASAMP;
#ifndef EVR_PRODUCT
    if (IS_GYRO(G.mode)) {
        struct alog *a = &G.alog[G.al_head];
        a->t_us = (uint32_t)t; a->yaw_urad = (int32_t)(G.cyaw * 1e6); a->pitch_urad = (int32_t)(G.cpitch * 1e6);
        G.al_head = (G.al_head + 1) % N_ALOG; if (G.al_count < N_ALOG) G.al_count++;
    }
#endif
}

/* Bias: 0.5 s windows (time-based, any ODR). A window with < 2 dps spread is
 * accepted at once; otherwise the stillest of 3 windows (1.5 s) is taken, so a
 * hand-held start never waits. Afterwards a ~3 s EMA tracks it while still. */
#define CAL_US 500000u
#define CAL_TRIES 3u
static void integrate(int16_t gx, int16_t gy, int16_t gz)
{
    double r[3] = { gx / GYRO_LSB, gy / GYRO_LSB, gz / GYRO_LSB };
    if (!G.biased) {
        for (int i = 0; i < 3; i++) { G.bias[i] += r[i]; G.bsq[i] += r[i] * r[i]; }
        G.cal_n++; G.cal_us += G.odr_us;
        if (G.cal_us >= CAL_US) {
            double m[3], vmax = 0;
            for (int i = 0; i < 3; i++) {
                m[i] = G.bias[i] / G.cal_n;
                double var = G.bsq[i] / G.cal_n - m[i] * m[i];
                if (var > vmax) vmax = var;
            }
            if (G.cal_tries == 0 || vmax < G.best_var) {
                G.best_var = vmax; for (int i = 0; i < 3; i++) G.best_bias[i] = m[i];
            }
            G.cal_tries++;
            if (vmax <= 4.0 || G.cal_tries >= CAL_TRIES) {
                for (int i = 0; i < 3; i++) G.bias[i] = G.best_bias[i];
                G.biased = 1; G.cal_var_x100 = (uint32_t)(G.best_var * 100.0);
            } else {
                STAT(G.recal++);
                for (int i = 0; i < 3; i++) G.bias[i] = G.bsq[i] = 0;
            }
            G.cal_n = 0; G.cal_us = 0;
        }
        return;                              /* no integration until biased */
    }
    G.nsamp++;
    /* track bias (~3 s) only while still: a pan must never leak into it */
    double d0 = r[0] - G.bias[0], d1 = r[1] - G.bias[1], d2 = r[2] - G.bias[2];
    double kb = (double)G.odr_us / 3000000.0;
    if (d0 * d0 < 9.0 && d1 * d1 < 9.0 && d2 * d2 < 9.0)
        for (int i = 0; i < 3; i++) G.bias[i] += (r[i] - G.bias[i]) * kb;
    double k = (double)G.odr_us * 1e-6 * PI / 180.0;
    G.cyaw += (r[0] - G.bias[0]) * k;
    G.cpitch += (r[1] - G.bias[1]) * k;
    G.croll += (r[2] - G.bias[2]) * k;
}

static void ev_push(uint64_t now, uint32_t stock, uint32_t nvalid, uint32_t count, uint32_t flags)
{
#ifdef EVR_PRODUCT
    (void)now; (void)stock; (void)nvalid; (void)count; (void)flags;
#else
    struct imu_ev *e = &G.ev[G.ev_head];
    e->t_ms = (uint32_t)(now / 1000u); e->stock = (uint8_t)stock; e->nvalid = (uint8_t)(nvalid > 255 ? 255 : nvalid);
    e->pwr = 0; e->fcfg = 0; e->count = (uint16_t)(count > 65535 ? 65535 : count); e->flags = (uint16_t)flags;
    e->mode = (uint8_t)G.mode; e->ntrans = (uint8_t)G.ntrans;
    e->io_us100 = (uint16_t)(G.io_us / 100u > 65535u ? 65535u : G.io_us / 100u);
    G.ev_head = (G.ev_head + 1) % N_EV; if (G.ev_count < N_EV) G.ev_count++;
#endif
}


#ifdef EVR_PRODUCT
/* ---- built-in product: menu choice and IMU ownership -------------------------
 * The Gyro e-VR menu choice (0 Standard, 1 Enhanced, 2 Native crop, 3 OFF) is
 * read every 25 IMU ticks through the private get_property key of evr_builtin.
 * One owner of the IMU, two set-ups (P.cfg):
 *   0 stabilize: gyro 100 Hz (200 Hz in the RS modes), 8-byte FIFO packets, while e-VR frames arrive
 *   2 idle:      stock IMU registers, no FIFO reads (menu OFF, or no frames) */
#define GYRO_KEY 0x47595230u
#define EVR_GONE_US 400000u
#define CHOICE_TICKS 25u
static struct {
    const struct z30_module_api *api;
    uint32_t tick, cfg;
    volatile uint64_t t_evr;
    uint32_t pwr_stock, fcfg_stock, fcfg1_stock, gcfg_stock;
    int32_t saved[3]; uint32_t have_saved, loaded;
} P;

/* ---- zero drift (gyro bias) kept on the card, v51 ----------------------------
 * A:\WL-GYROZ.CFG holds the last tracked bias: magic 'WGZ1', three int32 in
 * milli-deg/s (yaw, pitch, roll), and ~(magic ^ b0 ^ b1 ^ b2). arm() loads it, so
 * the first stabilization after power-on starts at once from the saved bias
 * instead of waiting for a still 0.5-1.5 s calibration; the ~3 s EMA still tracks
 * it while the camera is still. It is written again (IMU task) whenever the
 * stabilizer goes idle (no e-VR frames for 0.4 s, or menu OFF) and the tracked
 * bias moved by more than 0.01 deg/s since the last write. The file goes through
 * the stock wrappers the menu settings use (init / open / read / close); a
 * missing or damaged file just means the normal calibration. */
#define GZ_MAGIC 0x315A4757u
#define GZ_SUCCESS 0x04370080u
#define GZ_MAX 50000             /* mdps: sanity limit only */
typedef void (*gz_init_fn)(void *st, const char *path);
typedef uint32_t (*gz_open_fn)(void *st, uint32_t mode, uint32_t zero);
typedef uint32_t (*gz_read_fn)(void *st, void *buf, uint32_t len, uint32_t *done);
typedef uint32_t (*gz_close_fn)(void *st);
#define GZ_INIT  ((gz_init_fn)0x1033829Fu)
#define GZ_OPEN  ((gz_open_fn)0x103382A3u)
#define GZ_READ  ((gz_read_fn)0x10338415u)
#define GZ_CLOSE ((gz_close_fn)0x1033832Fu)
#define GZ_WRITE_RAW ((fs_write_fn)0x102C8A6Bu)
static const char GZ_PATH[] = "A:\\WL-GYROZ.CFG";

static void gz_start(uint32_t *st)
{
    for (int i = 0; i < 12; i++) st[i] = 0;
    st[2] = 0xFFFFFFFFu;
    GZ_INIT(st, GZ_PATH);
}

static int gz_load(int32_t b[3])
{
    uint32_t st[12], rec[6] = {0}, n = 0;
    gz_start(st);
    if (GZ_OPEN(st, 0, 0) != GZ_SUCCESS) return 0;
    uint32_t r = GZ_READ(st, rec, 21, &n);
    GZ_CLOSE(st);
    if (r != GZ_SUCCESS || n != 20 || rec[0] != GZ_MAGIC) return 0;
    if (rec[4] != ~(rec[0] ^ rec[1] ^ rec[2] ^ rec[3])) return 0;
    for (int i = 0; i < 3; i++) {
        b[i] = (int32_t)rec[1 + i];
        if (b[i] > GZ_MAX || b[i] < -GZ_MAX) return 0;
    }
    return 1;
}

static int gz_save(const int32_t b[3])
{
    uint32_t st[12], rec[5], done = 0;
    rec[0] = GZ_MAGIC; rec[1] = (uint32_t)b[0]; rec[2] = (uint32_t)b[1]; rec[3] = (uint32_t)b[2];
    rec[4] = ~(rec[0] ^ rec[1] ^ rec[2] ^ rec[3]);
    gz_start(st);
    if (GZ_OPEN(st, 0x301, 0) != GZ_SUCCESS) return 0;
    int32_t w = GZ_WRITE_RAW((int32_t)st[2], rec, 20, &done);
    uint32_t c = GZ_CLOSE(st);
    return w == 0 && done == 20 && c == GZ_SUCCESS;
}

static int32_t mdps(double v) { return (int32_t)(v * 1000.0 + (v < 0 ? -0.5 : 0.5)); }

/* IMU task, stabilizer just went idle: keep the tracked bias for the next start */
static void gz_keep(void)
{
    if (!G.biased) return;
    int32_t b[3] = { mdps(G.bias[0]), mdps(G.bias[1]), mdps(G.bias[2]) };
    for (int i = 0; i < 3; i++) if (b[i] > GZ_MAX || b[i] < -GZ_MAX) return;
    /* only a still calibration (spread <= 2 deg/s, the calibration's own test) or a
     * loaded bias that the still-only EMA tracked: a hand-held start is never kept */
    if (!P.loaded && G.cal_var_x100 > 400) return;
    if (P.have_saved) {
        int32_t d = 0;
        for (int i = 0; i < 3; i++) { int32_t e = b[i] - P.saved[i]; if (e < 0) e = -e; if (e > d) d = e; }
        if (d <= 10) return;
    }
    if (gz_save(b)) { for (int i = 0; i < 3; i++) P.saved[i] = b[i]; P.have_saved = 1; }
}
static void enter_gyro(void);
static uint32_t gyro_rate(uint32_t mode) { return IS_RS(mode) ? RS_GYRO_CFG : GYRO_CFG_100; }

static int imu_setup(uint32_t cfg)
{
    int ok;
    if (cfg == 2) {
        ok = imu_write(R_FIFO_CFG1, (uint8_t)P.fcfg1_stock) & imu_write(R_FIFO_CFG, (uint8_t)P.fcfg_stock) &
             imu_write(R_GCFG, (uint8_t)P.gcfg_stock) & imu_write(R_PWR, (uint8_t)P.pwr_stock);
    } else {
        uint32_t g = gyro_rate(G.mode);
        ok = imu_write(R_GCFG, (uint8_t)g) & imu_write(R_FIFO_CFG1, 0x02) & imu_write(R_FIFO_CFG, 0x40) &
             imu_write(R_SPR, 0x02) & imu_write(R_PWR, (uint8_t)(P.pwr_stock | 0x0C));
        G.gcfg = g; G.odr_us = g == GYRO_CFG_200 ? 5000u : 10000u;
    }
    G.t_last = 0; P.cfg = cfg;
    return ok;
}

static void apply_choice(uint32_t ch)
{
    /* menu rows: 0 Standard, 1 Enhanced, 2 Native crop, 3 OFF (Standard (RS) removed 2026-11-14;
     * M_RS stays reachable only from the research SD build's mode cycle) */
    uint32_t m = ch == 0 ? M_STD : ch == 1 ? M_ENH : ch == 2 ? M_NATIVE : M_OFF;
    if (m == G.mode) return;
    if (G.mode == M_OFF) enter_gyro();
    barrier(); G.mode = m;
    /* already stabilizing: switch the gyro rate in the next IMU tick (flush + resync) */
    if (P.cfg == 0 && m != M_OFF && gyro_rate(m) != G.gcfg) G.req_cfg = gyro_rate(m);
}

/* Runs in the IMU task before the stabilizer's FIFO read; 1 = tick handled. */
static int product_tick(uint64_t now)
{
    if (++P.tick >= CHOICE_TICKS) { P.tick = 0; apply_choice(P.api->get_property(GYRO_KEY)); }
    uint32_t want = G.mode != M_OFF && now - P.t_evr < EVR_GONE_US ? 0 : 2;
    if (want != P.cfg) {
        int stopping = P.cfg == 0 && want == 2;
        imu_setup(want);
        if (stopping) gz_keep();
        return 1;
    }
    return want != 0;
}
#endif

/* Stock IMU task sleeps cfg->period (cfg = [[IMU_ROOT+4]+0x1A8], period at +0x20)
 * right before calling our hook. PURE modes shorten it (fresher gyro, less latency);
 * any other mode restores the original. Only touched when cfg is in RAM and the
 * original value is sane (30..100). */
static void period_ctl(void)
{
    uint32_t b = *(volatile uint32_t *)(IMU_ROOT + 4);
    if (b < 0xC0000000u || b >= 0xD0000000u || (b & 3)) return;
    uint32_t cfg = *(volatile uint32_t *)(b + 0x1A8u);
    G.cfg_seen = cfg;
    /* stage 14 log: cfg lives at 0xC875xxxx, so accept the whole 0xC... DRAM window;
     * stock itself reads cfg->period every loop, so the read is safe */
    if (cfg < 0xC0000000u || cfg >= 0xD0000000u || (cfg & 3)) return;
    volatile uint32_t *per = (volatile uint32_t *)(cfg + 0x20u);
    if (G.per_cfg != cfg) {
        if (G.per_cfg && G.per_orig) *(volatile uint32_t *)(G.per_cfg + 0x20u) = G.per_orig;
        G.per_cfg = cfg; G.per_orig = *per;
    }
    if (G.per_orig < 30u || G.per_orig > 100u) return;
#ifdef EVR_PRODUCT
    uint32_t want = IS_GYRO(G.mode) && P.cfg != 2 ? 10u : G.per_orig;
#else
    uint32_t want = IS_GYRO(G.mode) ? 10u : G.per_orig;
#endif
    if (*per != want) {
        *per = want; barrier();
        if (*per != want) { G.per_orig = 0; return; }   /* not writable: leave it */
        STAT(G.per_writes++);
    }
    G.per_set = want;
}

static void imu_tick(uint32_t stock_result)
{
    G.busy_imu = 1; barrier();
    uint64_t now = time_us();
    uint32_t evf = 0, nvalid = 0, count = 0;
    if (G.paused) { evf = EV_PAUSED; goto out; }
    if (stock_result != 1) goto out;     /* stock's own read failed: leave the bus alone */
    if (now < G.backoff_until) { evf = EV_BACKOFF; goto out; }
    uint8_t *tmp = G.tmp;
    uint64_t tcount; uint32_t got = 0;
    G.ntrans = 0; G.io_us = 0;
    uint32_t rq = G.req_cfg;
    if (rq) {
        /* ODR change / resync requested by the menu: apply in this task, flush, resync */
        G.ntrans = 2;
        imu_write(R_GCFG, (uint8_t)rq); imu_write(R_SPR, 0x02);
        G.gcfg = rq; G.odr_us = rq == GYRO_CFG_100 ? 10000u : rq == GYRO_CFG_200 ? 5000u : 2000u;
        G.t_last = 0; G.req_cfg = 0; evf = EV_ODR | EV_FLUSH;
        G.io_us = (uint32_t)(time_us() - now); goto out;
    }
    period_ctl();
#ifdef EVR_PRODUCT
    if (product_tick(now)) goto out;
#endif
    {
        uint8_t c[2] = { 0, 0 };
        G.ntrans++;
        if (!imu_read(R_FIFO_COUNT, c, 2)) { G.backoff_until = now + 1000000u; STAT(G.backoffs++); evf = EV_CNTFAIL; goto out; }
        tcount = time_us();                  /* newest FIFO sample is about here */
        count = (G.intf0 & 0x20) ? ((uint32_t)c[0] << 8 | c[1]) : ((uint32_t)c[1] << 8 | c[0]);
        if (G.intf0 & 0x40) count *= 8;
        uint32_t want = count & ~7u;
        if (want >= 2000u || want > TMP_BYTES) {
            /* FIFO (2 KB) overflowed during a stall: samples cannot be dated, flush and resync */
            G.ntrans++; imu_write(R_SPR, 0x02); evf |= EV_FLUSH; G.t_last = 0;
            G.io_us = (uint32_t)(time_us() - now); goto out;
        }
        while (got < want) {             /* exact length only: never read past the FIFO end */
            uint32_t n = want - got > CHUNK ? CHUNK : want - got;
            G.ntrans++;
            if (!imu_read(R_FIFO_DATA, tmp + got, n)) { G.backoff_until = time_us() + 1000000u; STAT(G.backoffs++); evf |= EV_DATAFAIL; break; }
            got += n;
        }
    }
    G.io_us = (uint32_t)(time_us() - now);
    if (G.io_us > 100000u) { STAT(G.slow_reads++); evf |= EV_SLOW; }
    /* v51: a gyro-only packet header (FIFO_CONFIG1 0x02, no time stamp) is 0x2X:
     * gyro bit 5 set, empty 7 / accel 6 / 20-bit 4 / time stamp 3-2 clear (bits
     * 1-0 flag an ODR change). Anything else in a non-empty slot means the stream is
     * out of step with the 8-byte packets, so the axes would be read shifted (camera:
     * pitch showing up as roll until a power cycle). Flush and resync instead. */
    for (uint32_t i = 0; i + 8 <= got; i += 8) {
        if (tmp[i] & 0x80) continue;
        if ((tmp[i] & 0xFC) != 0x20) {
            G.ntrans++; imu_write(R_SPR, 0x02); evf |= EV_FLUSH | EV_DESYNC; G.t_last = 0;
            STAT(G.desyncs++); nvalid = 0;
            goto out;
        }
        nvalid++;
    }
    if (!nvalid) {
        evf |= EV_EMPTY;
        /* stage 20: heal() removed (never triggered; the dry FIFO was the bus stall) */
        goto out;
    }
    G.empty_run = 0;
    /* monotonic sample clock: advance 2 ms per sample, steer gently to tcount */
    uint64_t first = G.t_last + G.odr_us;
    uint64_t ideal_first = tcount - (uint64_t)(nvalid - 1) * G.odr_us;
    int64_t err = (int64_t)(ideal_first - first);
    if (G.t_last == 0 || err > 20000 || err < -20000) first = ideal_first;   /* resync after a gap */
    else first = (uint64_t)((int64_t)first + err / 16);
    if (first <= G.t_last) first = G.t_last + 1;
    uint32_t idx = 0;
    for (uint32_t i = 0; i + 8 <= got; i += 8) {
        const uint8_t *p = tmp + i;
        if ((p[0] & 0x80) || !(p[0] & 0x20)) continue;
        int16_t g[3];
        for (int k = 0; k < 3; k++) {
            uint8_t a = p[1 + 2 * k], b = p[2 + 2 * k];
            g[k] = (int16_t)((G.intf0 & 0x10) ? (a << 8 | b) : (b << 8 | a));
        }
        integrate(g[0], g[1], g[2]);
        G.t_last = first + (uint64_t)idx * G.odr_us;
        push_sample(G.t_last);
        idx++;
    }
    G.imu_t = tcount; STAT(G.imu_ticks++);
out:
    if (evf != 0xFFFF && G.ev) ev_push(now, stock_result, nvalid, count, evf);
    barrier(); G.busy_imu = 0;
}

/* Interpolate cumulative angle at target. 1 = ok (age_us = how far target is
 * beyond the newest sample, 0 when bracketed); 0 = no usable data. */
static int angle_at3(uint64_t target, double *y, double *p, double *rl, uint32_t *age_us)
{
    uint32_t head = G.ring_head;
    struct asample *nw = &G.ring[(head + N_ASAMP - 1) % N_ASAMP];
    if (nw->t_us == 0) return 0;
    if (target >= nw->t_us) {
        *y = nw->yaw; *p = nw->pitch; *rl = nw->roll; *age_us = (uint32_t)(target - nw->t_us);
        double age = (double)(target - nw->t_us);
        if (age < 300000.0) {
            /* damped extrapolation with the rate over the newest ~20 ms */
            for (uint32_t k = 1; k < 64; k++) {
                struct asample *o = &G.ring[(head + N_ASAMP - 1 - k) % N_ASAMP];
                if (o->t_us == 0 || o->t_us >= nw->t_us) break;
                if (nw->t_us - o->t_us >= 18000u) {
                    double dt = (double)(nw->t_us - o->t_us), w = EXTRAP_TAU * age / (EXTRAP_TAU + age);
                    *y += (nw->yaw - o->yaw) / dt * w; *p += (nw->pitch - o->pitch) / dt * w;
                    *rl += (nw->roll - o->roll) / dt * w;
                    break;
                }
            }
        }
        return 1;
    }
    *age_us = 0;
    for (uint32_t k = 1; k < N_ASAMP; k++) {
        struct asample *a = &G.ring[(head + N_ASAMP - 1 - k) % N_ASAMP];
        struct asample *b = &G.ring[(head + N_ASAMP - k) % N_ASAMP];
        if (a->t_us == 0 || a->t_us >= b->t_us) return 0;
        if (a->t_us <= target) {
            double f = (double)(target - a->t_us) / (double)(b->t_us - a->t_us);
            *y = a->yaw + f * (b->yaw - a->yaw);
            *p = a->pitch + f * (b->pitch - a->pitch);
            *rl = a->roll + f * (b->roll - a->roll);
            return 1;
        }
    }
    return 0;
}


/* ---- VrMx path-1 callback: rewrite corners ---------------------------- */
static int32_t rdi(const uint8_t *p, uint32_t o) { return *(const volatile int32_t *)(p + o); }
static void wri(uint8_t *p, uint32_t o, int32_t v) { *(volatile int32_t *)(p + o) = v; }
/* x offsets: A-D at +0x28..+0x40, A0-D0 at +0x58..+0x70; y at x+4. */
static uint32_t corner_off(int i) { return (i < 4) ? 0x28u + 8u * (uint32_t)i : 0x58u + 8u * (uint32_t)(i - 4); }


static void frame(const uint8_t *input, uint8_t *result, const uint8_t *meta, uint32_t path)
{
    G.busy_frame = 1; barrier();
    uint32_t mode = G.mode;
    if (G.paused || !IS_GYRO(mode)) { STAT(G.mode_frames[M_OFF]++); goto out; }
    if (path != 1) { STAT(G.path2_frames++); goto out; }
    int32_t geom0 = rdi(input, 0x34), geom1 = rdi(input, 0x38);
    if (geom0 <= 64 || geom1 <= 64 || geom0 > 8192 || geom1 > 8192) goto out;
    uint64_t now = time_us();
    /* frame period EMA -> lead time (exposure centre is ~0.9 frame before the callback) */
    if (G.t_frame && now > G.t_frame && now - G.t_frame < 100000u) {
        double dt = (double)(now - G.t_frame);
        G.frame_us = G.frame_us > 0 ? G.frame_us + (dt - G.frame_us) * 0.1 : dt;
    }
    if (G.t_frame && G.frame_us > 5000.0 && now - G.t_frame > (uint64_t)(1.8 * G.frame_us) && now - G.t_frame < 1000000u) {
        if (!G.distress) STAT(G.distress_events++);
        G.distress = 60;                      /* pipeline skipped a frame: back off for ~1-2 s */
    }
    G.t_frame = now;

    int32_t cx[8], cy[8];
    for (int i = 0; i < 8; i++) { cx[i] = rdi(result, corner_off(i)); cy[i] = rdi(result, corner_off(i) + 4); }
#ifdef EVR_PRODUCT
    P.t_evr = now;
#endif
    double scx = 0, scy = 0, s0x = 0, s0y = 0;
    for (int i = 0; i < 4; i++) { scx += cx[i]; scy += cy[i]; s0x += cx[i + 4]; s0y += cy[i + 4]; }
    scx /= 4; scy /= 4; s0x /= 4; s0y /= 4;
    /* zoom: shrink each quad about its own centroid (stays inside the stock quad) */
    double zoom = mode == M_NATIVE ? ZOOM_NATIVE : mode == M_ENH ? ZOOM_ENH : ZOOM_STD;
    for (int i = 0; i < 8; i++) {
        double ox = i < 4 ? scx : s0x, oy = i < 4 ? scy : s0y;
        cx[i] = (int32_t)(ox + (cx[i] - ox) * zoom);
        cy[i] = (int32_t)(oy + (cy[i] - oy) * zoom);
    }
    /* Lens VR: when the lens is stabilizing (its position words 0x29B8/0x29BC move
     * every frame; with VR off they are frozen), the lens removes the shake
     * optically (stage 17 logs: 70-100%). Then hand the residual back to stock
     * e-VR (Nikon's own lens+e-VR pairing) and fade the gyro correction out;
     * with VR off, pure gyro. w blends over ~0.3 s so the framing never jumps. */
    if (meta) {
        uint32_t lx = *(const volatile uint16_t *)(meta + 0x29B8), ly = *(const volatile uint16_t *)(meta + 0x29BC);
        if (lx != G.lv_x || ly != G.lv_y) G.lv_quiet = 0; else if (G.lv_quiet < 1000) G.lv_quiet++;
        G.lv_x = lx; G.lv_y = ly;
    }
    double lens_t = G.lv_quiet < LENS_HOLD_FRAMES ? 1.0 : 0.0;
    G.lv_w += (lens_t - G.lv_w) * LENS_BLEND;
    if (G.lv_w > 0.5) STAT(G.lv_frames++);
    double gw = 1.0 - G.lv_w;                  /* gyro share */
    {
        /* PURE: remove stock's own (optical-flow) translation (scaled by the gyro share) */
        int32_t ox = (int32_t)((scx - geom0 * 128.0) * gw), oy = (int32_t)((scy - geom1 * 128.0) * gw);
        for (int i = 0; i < 8; i++) { cx[i] -= ox; cy[i] -= oy; }
    }
    double lo = SAFETY * 256.0, hix = (geom0 - SAFETY) * 256.0, hiy = (geom1 - SAFETY) * 256.0;
    int32_t focal_mm = meta ? (int32_t)(*(const volatile uint16_t *)(meta + 0x258A)) : 0;
    double dx = 0, dy = 0;
    int32_t ydev = 0, pdev = 0; uint32_t age = 0;
    /* how far the stock quad may move each way before a corner hits the margin */
    double bx = hix, byv = hiy;
    for (int i = 0; i < 8; i++) {
        double rl = cx[i] - lo, rr = hix - cx[i], ru = cy[i] - lo, rd = hiy - cy[i];
        if (rl < bx) bx = rl;
        if (rr < bx) bx = rr;
        if (ru < byv) byv = ru;
        if (rd < byv) byv = rd;
    }
    if (bx < 0 || byv < 0) goto out;          /* no room at all: keep stock */

    {
        /* Add a gyro translation ON TOP of the stock corners (do not discard
         * stock's own correction). GYRO_P and GYRO_N use opposite signs so the
         * correct one can be found on camera. Hold the last target when gyro
         * is not fresh (never flicker back to stock). */
        double sgn = 1.0;                     /* sign A (+) confirmed on camera */
        double tgt_dx = G.tgt_dx, tgt_dy = G.tgt_dy;
        double yaw, pitch, rollv = 0;
        uint64_t it = G.imu_t;
        int f_imu = it != 0 && now + 5000u >= it && (now > it ? now - it : 0) <= STALE_US;
        int f_ns = G.biased && G.nsamp >= 10, f_fo = focal_mm > 0 && focal_mm <= 1000;
        int fresh = f_imu && f_ns && f_fo && angle_at3(now - (uint64_t)(G.frame_us > 25000.0 ? LEAD_30P_US : LEAD_60P_US), &yaw, &pitch, &rollv, &age);
        if (!fresh) {
            if (!f_imu) STAT(G.nf_imu_old++); else if (!f_ns) STAT(G.nf_nsamp++); else if (!f_fo) STAT(G.nf_focal++); else STAT(G.nf_angle++);
        }
        if (fresh) {
            /* stage 17: with VR off at 211 mm (reported) the image moved 1.22x the
             * physical model (reported focal reads short at the long end); interpolate */
            double tele = focal_mm <= 50 ? 0.0 : focal_mm >= 211 ? 1.0 : (focal_mm - 50) / 161.0;
            double fpos = meta ? (double)*(const volatile uint16_t *)(meta + 0x25AEu) : 0.0;
            double ref = FOCUS_REF_W + (FOCUS_REF_T - FOCUS_REF_W) * tele;
            /* focus sensitivity grows steeply with focal length: interpolate on tele^2 */
            double sl = FOCUS_SLOPE_W + (FOCUS_SLOPE_T - FOCUS_SLOPE_W) * tele * tele;
            double extra = (fpos - ref) * sl;
            if (extra < 0) extra = 0; else if (extra > FOCUS_EXTRA_MAX) extra = FOCUS_EXTRA_MAX;
            double fpx = focal_mm * geom0 / SENSOR_MM * (GAIN_BASE_W + (GAIN_BASE_T - GAIN_BASE_W) * tele + extra);
            double dt = G.frame_us > 0 ? G.frame_us : 33333.0;
            double k = dt / HP_TAU_US; if (k > 0.2) k = 0.2;
            G.lp_yaw += (yaw - G.lp_yaw) * k;      /* smooth path = low-pass of the angle */
            G.lp_pitch += (pitch - G.lp_pitch) * k;
            G.lp_roll += (rollv - G.lp_roll) * k; G.last_rollv = rollv;
            if (rollv - G.lp_roll > ROLL_LIMIT) G.lp_roll = rollv - ROLL_LIMIT;
            else if (rollv - G.lp_roll < -ROLL_LIMIT) G.lp_roll = rollv + ROLL_LIMIT;
            if (G.was_stale && G.gyro_ok) G.lp_roll = rollv - G.tgt_roll;
            G.tgt_roll = SGN_ROLL * (rollv - G.lp_roll);
            /* anti-windup: never ask for more than SOFT_LIMIT of the margin, so a pan
             * is followed smoothly instead of pinning at the edge and snapping back */
            double gx = sgn * fpx * 256.0, lim_y = gx > 0 ? SOFT_LIMIT * bx / gx : 0;
            double lim_p = gx > 0 ? SOFT_LIMIT * byv / gx : 0;
            if (yaw - G.lp_yaw > lim_y) G.lp_yaw = yaw - lim_y; else if (yaw - G.lp_yaw < -lim_y) G.lp_yaw = yaw + lim_y;
            if (pitch - G.lp_pitch > lim_p) G.lp_pitch = pitch - lim_p; else if (pitch - G.lp_pitch < -lim_p) G.lp_pitch = pitch + lim_p;
            if (G.was_stale && G.gyro_ok && gx > 0) {
                /* resume after a gap: continue from the held correction, no jump */
                G.lp_yaw = yaw - G.tgt_dx / gx;
                G.lp_pitch = pitch + G.tgt_dy / gx;
            }
            G.was_stale = 0;
            double corr_y = yaw - G.lp_yaw, corr_p = pitch - G.lp_pitch;
            tgt_dx = sgn * fpx * corr_y * 256.0;
            tgt_dy = sgn * -fpx * corr_p * 256.0;  /* pitch axis is inverted vs yaw */
            ydev = (int32_t)(corr_y * 180000.0 / PI);
            pdev = (int32_t)(corr_p * 180000.0 / PI);
            G.tgt_dx = tgt_dx; G.tgt_dy = tgt_dy; G.gyro_ok = 1;
            G.tgt_sx = G.tgt_sy = 0;
#ifdef EVR_PRODUCT
            if (IS_RS(mode)) {
                double ro = geom0 >= 3000 ? READOUT_4K_US : READOUT_1080_US, y0, p0, r0, y1, p1, r1;
                uint64_t tc = now - (uint64_t)(G.frame_us > 25000.0 ? LEAD_30P_US : LEAD_60P_US);
                uint32_t ag;
                if (angle_at3(tc - (uint64_t)(ro / 2), &y0, &p0, &r0, &ag) && angle_at3(tc + (uint64_t)(ro / 2), &y1, &p1, &r1, &ag)) {
                    double cap = RS_CAP * 256.0;
                    G.tgt_sx = sgn * fpx * 256.0 * (y1 - y0) / 2.0;
                    G.tgt_sy = sgn * -fpx * 256.0 * (p1 - p0) / 2.0;
                    double m2 = G.tgt_sx * G.tgt_sx + G.tgt_sy * G.tgt_sy;
                    if (m2 > cap * cap) {
                        /* scale the vector down to length cap (Newton square root) */
                        double m = cap * 2.0;
                        for (int k = 0; k < 8; k++) m = 0.5 * (m + m2 / m);
                        G.tgt_sx *= cap / m; G.tgt_sy *= cap / m;
                        STAT(G.rs_capped++);
                    }
                }
            }
#endif
            G.fpx_q8 = fpx * 256.0;
        } else {
            STAT(G.stale_frames++); G.was_stale = 1;
            if (!G.gyro_ok) goto out;
            G.tgt_dx *= 0.98; G.tgt_dy *= 0.98; G.tgt_roll *= 0.98; G.tgt_sx *= 0.9; G.tgt_sy *= 0.9;
            tgt_dx = G.tgt_dx; tgt_dy = G.tgt_dy;
        }
        G.out_dx += (tgt_dx - G.out_dx) * OUT_SMOOTH;
        G.out_dy += (tgt_dy - G.out_dy) * OUT_SMOOTH;
        dx = G.out_dx * gw; dy = G.out_dy * gw;
        G.out_roll += (G.tgt_roll - G.out_roll) * OUT_SMOOTH;
#ifdef EVR_PRODUCT
        G.out_sx += (G.tgt_sx - G.out_sx) * OUT_SMOOTH;
        G.out_sy += (G.tgt_sy - G.out_sy) * OUT_SMOOTH;
#endif
        if (dx > bx) dx = bx; else if (dx < -bx) dx = -bx;
        if (dy > byv) dy = byv; else if (dy < -byv) dy = -byv;
    }
    /* rotate each quad about its own centroid by the roll correction; halve the
     * roll until every corner (with the translation) stays inside the margin */
    double rot = G.out_roll, shx = G.out_sx * gw, shy = G.out_sy * gw, qcx[2], qcy[2];
    /* pipeline distress: ease the correction down to half (and back), never a jump */
    G.dscale += ((G.distress ? 0.5 : 1.0) - G.dscale) * 0.1;
    if (G.distress) G.distress--;
    dx *= G.dscale; dy *= G.dscale; rot *= G.dscale;
#ifdef EVR_PRODUCT
    shx *= G.dscale; shy *= G.dscale;
#endif
    qcx[0] = qcx[1] = qcy[0] = qcy[1] = 0;
    for (int i = 0; i < 8; i++) { qcx[i / 4] += cx[i] / 4.0; qcy[i / 4] += cy[i] / 4.0; }
    {
        /* smooth roll limit: the largest rotation each way that keeps every corner
         * (after translation and shear) inside; clamp to 90% of it and feed it back
         * to the roll filter so a fast roll is followed, not dropped and re-taken */
        double tp = ROLL_LIMIT, tn = ROLL_LIMIT;
        for (int i = 0; i < 8; i++) {
            double ux = cx[i] - qcx[i / 4], uy = cy[i] - qcy[i / 4], sh = (i & 2) ? 1.0 : -1.0;
            double nx = SHEAR(cx[i] + dx, sh * shx), ny = SHEAR(cy[i] + dy, sh * shy);
            double l = nx - lo, r = hix - nx, u = ny - lo, d = hiy - ny;
            if (l < 0) l = 0;
            if (r < 0) r = 0;
            if (u < 0) u = 0;
            if (d < 0) d = 0;
            if (uy > 1) { if (l / uy < tp) tp = l / uy; if (r / uy < tn) tn = r / uy; }
            if (uy < -1) { if (r / -uy < tp) tp = r / -uy; if (l / -uy < tn) tn = l / -uy; }
            if (ux > 1) { if (d / ux < tp) tp = d / ux; if (u / ux < tn) tn = u / ux; }
            if (ux < -1) { if (u / -ux < tp) tp = u / -ux; if (d / -ux < tn) tn = d / -ux; }
        }
        tp *= 0.9; tn *= 0.9;
        if (rot > tp || rot < -tn) {
            rot = rot > tp ? tp : -tn;
            if (G.dscale > 0.99) { G.out_roll = G.tgt_roll = rot; G.lp_roll = G.last_rollv - rot; }
        }
    }
    for (int tries = 0; tries < 6; tries++) {
        double sn = rot - rot * rot * rot / 6.0, cs = 1.0 - rot * rot / 2.0;
        int fits = 1;
        for (int i = 0; i < 8 && fits; i++) {
            double ux = cx[i] - qcx[i / 4], uy = cy[i] - qcy[i / 4];
            double sh = (i & 2) ? 1.0 : -1.0;          /* A,B top corners; C,D bottom corners */
            double nx = SHEAR(qcx[i / 4] + ux * cs - uy * sn + dx, sh * shx), ny = SHEAR(qcy[i / 4] + ux * sn + uy * cs + dy, sh * shy);
            if (nx < lo || nx > hix || ny < lo || ny > hiy) fits = 0;
        }
        if (fits) break;
#ifdef EVR_PRODUCT
        if (tries < 4) { rot *= 0.5; shx *= 0.5; shy *= 0.5; } else rot = shx = shy = 0.0;
#else
        if (tries < 4) rot *= 0.5; else rot = 0.0;
#endif
    }
    {
        double sn = rot - rot * rot * rot / 6.0, cs = 1.0 - rot * rot / 2.0;
        for (int i = 0; i < 8; i++) {
            double ux = cx[i] - qcx[i / 4], uy = cy[i] - qcy[i / 4];
            double sh = (i & 2) ? 1.0 : -1.0;
            cx[i] = (int32_t)SHEAR(qcx[i / 4] + ux * cs - uy * sn, sh * shx);
            cy[i] = (int32_t)SHEAR(qcy[i / 4] + ux * sn + uy * cs, sh * shy);
        }
    }
    int clamped = 0;
    for (int i = 0; i < 8; i++) {
        double nx = cx[i] + dx, ny = cy[i] + dy;
        if (nx < lo) { nx = lo; clamped = 1; } else if (nx > hix) { nx = hix; clamped = 1; }
        if (ny < lo) { ny = lo; clamped = 1; } else if (ny > hiy) { ny = hiy; clamped = 1; }
        wri(result, corner_off(i), (int32_t)nx);
        wri(result, corner_off(i) + 4, (int32_t)ny);
    }
    STAT(G.mode_frames[mode]++);
    if (clamped) STAT(G.clamp_frames++);
#ifndef EVR_PRODUCT
    struct log_rec *r = &G.log[G.head];
    r->t_us = now; r->frame = (uint32_t)rdi(input, 0x64);
    r->mode = (uint8_t)mode; r->clamped = (uint8_t)(clamped | (G.lv_w > 0.5 ? 2 : 0)); r->path = (uint8_t)path;
    r->flags = (uint8_t)(age / 1000u > 255 ? 255 : age / 1000u);     /* gyro age beyond newest, ms */
    r->focal_mm = focal_mm; r->geom0 = geom0;
    r->dx_q8 = (int32_t)dx; r->dy_q8 = (int32_t)dy;
    r->roll_mdeg = (int32_t)(((uint32_t)(int16_t)(rot * 180000.0 / PI) << 16) | ((uint32_t)(G.frame_us / 4.0) & 0xFFFFu)); r->yaw_dev_mdeg = ydev; r->pitch_dev_mdeg = pdev;
    r->alpha = rdi(result, 0); r->beta = rdi(result, 4);
    if (meta && G.snap && ++G.sn_tick >= SNAP_EVERY) {
        G.sn_tick = 0;
        uint8_t *d = G.snap + G.sn_head * SNAP_REC;
        *(uint32_t *)d = (uint32_t)now; *(uint32_t *)(d + 4) = r->frame;
        for (uint32_t i = 0; i < SNAP_META; i++) d[8 + i] = *(const volatile uint8_t *)(meta + i);
        for (uint32_t i = 0; i < SNAP_IN; i++) d[8 + SNAP_META + i] = *(const volatile uint8_t *)(input + i);
        G.sn_head = (G.sn_head + 1) % N_SNAP; if (G.sn_count < N_SNAP) G.sn_count++;
    }

    /* v18: focus-breathing candidates from the lens block next to the focal length */
    if (meta) {
        const volatile uint16_t *lm = (const volatile uint16_t *)(meta + 0x258Eu);
        r->stock_cx_q2 = (int16_t)lm[(0x25AE - 0x258E) / 2]; r->stock_cy_q2 = (int16_t)lm[(0x2598 - 0x258E) / 2];
        r->pitch_dev_mdeg = (int32_t)((uint32_t)lm[(0x2596 - 0x258E) / 2] << 16 | lm[0]);
        r->flags = (uint8_t)lm[(0x259A - 0x258E) / 2];
    }
    G.head = (G.head + 1) % N_LOG; if (G.count < N_LOG) G.count++; G.total++;
#else
    (void)ydev; (void)pdev; (void)age;
#endif
out:
    barrier(); G.busy_frame = 0;
}

/* ---- menu ------------------------------------------------------------ */
struct out { char *p, *end; };
#ifdef EVR_PRODUCT
/* The built-in copy has no status line (its set_text is a no-op): no texts. */
#define put_c(o, ch) ((void)(o))
#define put_s(o, str) ((void)(o))
#define put_x2(o, v) ((void)(o), (void)(v))
#define say(api, c, o) ((void)(api), (void)(c), (void)(o), 1u)
#else
static void put_c(struct out *o, char c) { if (o->p < o->end) *o->p++ = c; }
static void put_s(struct out *o, const char *s) { while (*s) put_c(o, *s++); }
static void put_u(struct out *o, uint32_t v)
{
    char b[10]; int n = 0;
    do { b[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10);
    while (n) put_c(o, b[--n]);
}
static void put_x2(struct out *o, uint32_t v)
{ const char *h = "0123456789ABCDEF"; put_c(o, h[(v >> 4) & 15]); put_c(o, h[v & 15]); }
static uint32_t say(const struct z30_module_api *api, struct context *c, struct out *o)
{ *o->p = 0; return api->set_text(c->text); }
#endif

#ifndef EVR_PRODUCT
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
#endif

static uint32_t arm(const struct z30_module_api *api, struct context *c)
{
    struct out o = { c->text, c->text + 24 };
    if (*(volatile uint32_t *)0x1021C360u == 0xFC6AF7FFu ||
        *(volatile uint32_t *)0x100B97B8u == 0xF2406B61u ||
        *(volatile uint32_t *)0x100B9738u == 0x46686829u) {
        put_s(&o, "Need stage4 EG"); return say(api, c, &o);
    }
    if (!G.pool) {
        uint8_t *p = POOL_ALLOC(POOL_BYTES, 0);
        if (!p) { put_s(&o, "No memory"); return say(api, c, &o); }
        G.pool = p; G.ring = (struct asample *)(p + HDR_BYTES);
        G.log = (struct log_rec *)(p + HDR_BYTES + N_ASAMP * sizeof(struct asample));
        G.ev = (struct imu_ev *)((uint8_t *)G.log + N_LOG * sizeof(struct log_rec));
        G.tmp = (uint8_t *)G.ev + N_EV * sizeof(struct imu_ev);
        G.alog = (struct alog *)(G.tmp + TMP_BYTES);
        G.snap = (uint8_t *)(G.alog + N_ALOG);
    }
    if (!imu_ok()) { put_s(&o, "IMU not found"); return say(api, c, &o); }
    G.whoami = imu_reg(R_WHOAMI);
    if (G.whoami != 0x42) { put_s(&o, "IMU id "); put_x2(&o, G.whoami); return say(api, c, &o); }
    G.intf0 = imu_reg(R_INTF0);
    uint32_t pwr = imu_reg(R_PWR);
    if (G.intf0 > 0xFF || pwr > 0xFF) { put_s(&o, "IMU read failed"); return say(api, c, &o); }
#ifdef EVR_PRODUCT
    P.pwr_stock = pwr; P.gcfg_stock = imu_reg(R_GCFG);
    P.fcfg_stock = imu_reg(R_FIFO_CFG); P.fcfg1_stock = imu_reg(R_FIFO_CFG1);
    if ((P.gcfg_stock | P.fcfg_stock | P.fcfg1_stock) > 0xFF) { put_s(&o, "IMU read failed"); return say(api, c, &o); }
    int ok = imu_setup(0);
#else
    int ok = imu_write(R_GCFG, GYRO_CFG) && imu_write(R_FIFO_CFG1, 0x02) &&
             imu_write(R_FIFO_CFG, 0x40) && imu_write(R_SPR, 0x02) &&
             imu_write(R_PWR, (uint8_t)(pwr | 0x0C));
#endif
    G.pwr_after = imu_reg(R_PWR); G.gcfg_after = imu_reg(R_GCFG);
    if (!ok) { put_s(&o, "IMU setup failed"); return say(api, c, &o); }
    G.bias[0] = G.bias[1] = G.bias[2] = 0; G.bsq[0] = G.bsq[1] = G.bsq[2] = 0; G.recal = 0;
    G.cyaw = G.cpitch = G.croll = 0; G.lp_yaw = G.lp_pitch = G.lp_roll = 0;
#ifndef EVR_PRODUCT
    G.gcfg = GYRO_CFG; G.odr_us = 2000u;
#endif
    G.req_cfg = 0;
    G.ring_head = 0; G.imu_t = 0; G.mode = M_OFF; G.paused = 0; G.t_last = 0; G.nsamp = 0; G.biased = 0; G.cal_n = G.cal_us = G.cal_tries = 0; G.best_var = 0; G.t_frame = 0; G.frame_us = 0;
    G.tgt_dx = G.tgt_dy = G.out_dx = G.out_dy = 0; G.gyro_ok = 0; G.was_stale = 0; G.empty_run = 0;
    for (uint32_t i = 0; i < N_ASAMP; i++) G.ring[i].t_us = 0;
    G.t_armed = time_us(); G.t0_sine = G.t_armed;
#ifdef EVR_PRODUCT
    P.api = api;
    P.t_evr = G.t_armed;
    {   /* start from the bias saved last time (WL-GYROZ.CFG), if any */
        int32_t b[3];
        if (gz_load(b)) {
            for (int i = 0; i < 3; i++) { G.bias[i] = b[i] / 1000.0; P.saved[i] = b[i]; }
            P.have_saved = 1; P.loaded = 1; G.biased = 1;
        }
    }
#endif
    barrier();
    *EVR_HOOK_CELL = (uint32_t)frame;
    *IMU_HOOK_CELL = (uint32_t)imu_tick;
    barrier();
    c->armed = 1;
    put_s(&o, "Armed OFF "); put_x2(&o, pwr); put_c(&o, '>'); put_x2(&o, G.pwr_after & 0xFF);
    return say(api, c, &o);
}

#ifndef EVR_PRODUCT
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

static uint32_t save(const struct z30_module_api *api, struct context *c)
{
    struct out o = { c->text, c->text + 24 };
    G.paused = 1; barrier();
    for (uint32_t spin = 0; (G.busy_frame || G.busy_imu) && spin < 50000000u; spin++) barrier();
    if (G.busy_frame || G.busy_imu) { G.paused = 0; put_s(&o, "Busy, try again"); return say(api, c, &o); }
    struct header *h = (struct header *)G.pool;
    memset(h, 0, sizeof *h);
    memcpy(h->magic, "EVRSTB1", 8);
    h->version = 19; h->hdr_bytes = sizeof *h; h->rec_size = sizeof(struct log_rec);
    h->count = G.count; h->total = G.total;
    h->whoami = G.whoami; h->pwr_after = G.pwr_after; h->gcfg_after = G.gcfg_after;
    h->intf0 = G.intf0; h->backoffs = G.backoffs;
    for (int i = 0; i < M_COUNT; i++) h->mode_frames[i] = G.mode_frames[i];
    h->stale_frames = G.stale_frames; h->path2_frames = G.path2_frames; h->clamp_frames = G.clamp_frames;
    h->px_per_mm_x100 = (int32_t)(PX_PER_MM_REF * 100); h->safety_px = (int32_t)SAFETY;
    h->hp_x1000 = (int32_t)(HP_CUTOFF * 1000); h->sine_amp_px = (int32_t)G.lv_frames;   /* v10: frames with lens VR active */
    h->t_armed = G.t_armed; h->t_saved = time_us();
    h->n_alog = G.al_count; h->alog_bytes = sizeof(struct alog);
    h->n_lvr = G.sn_count; h->lvr_bytes = SNAP_REC;   /* v17: metadata snapshots */
    h->cfg_ptr = G.cfg_seen; h->per_orig = G.per_orig; h->per_set = G.per_set; h->per_writes = G.per_writes; h->reserved[0] = G.recal | (G.cal_var_x100 << 8); h->reserved[1] = G.fixes; h->reserved[2] = G.nf_imu_old;
    h->reserved[3] = G.nf_nsamp; h->reserved[4] = G.distress_events; h->reserved[5] = G.nf_angle; h->reserved[6] = G.ev_count;
    h->reserved[7] = G.imu_ticks;
    char path[24] = "A:\\Z30\\ST000000.BIN";
    uint32_t sec = (uint32_t)(h->t_saved / 1000000u) + G.file_no++;
    for (int i = 14; i >= 9; i--) { path[i] = (char)('0' + sec % 10u); sec /= 10u; }
    shared_clean(G.pool, POOL_BYTES);
    int32_t fd = FS_OPEN(path, 0x20);
    int ok = 0;
    if (fd >= 0) {
        uint32_t first = G.count < N_LOG ? 0 : G.head;
        uint32_t n1 = G.count < N_LOG ? G.count : N_LOG - first;
        ok = write_all(fd, (const uint8_t *)h, sizeof *h) &&
             write_all(fd, (const uint8_t *)(G.log + first), n1 * sizeof(struct log_rec)) &&
             (first == 0 || write_all(fd, (const uint8_t *)G.log, first * sizeof(struct log_rec)));
        uint32_t e1 = G.ev_count < N_EV ? 0 : G.ev_head, en = G.ev_count < N_EV ? G.ev_count : N_EV - e1;
        ok = ok && write_all(fd, (const uint8_t *)(G.ev + e1), en * sizeof(struct imu_ev)) &&
             (e1 == 0 || write_all(fd, (const uint8_t *)G.ev, e1 * sizeof(struct imu_ev)));
        uint32_t a1 = G.al_count < N_ALOG ? 0 : G.al_head, an = G.al_count < N_ALOG ? G.al_count : N_ALOG - a1;
        ok = ok && write_all(fd, (const uint8_t *)(G.alog + a1), an * sizeof(struct alog)) &&
             (a1 == 0 || write_all(fd, (const uint8_t *)G.alog, a1 * sizeof(struct alog)));
        uint32_t s1 = G.sn_count < N_SNAP ? 0 : G.sn_head, sn = G.sn_count < N_SNAP ? G.sn_count : N_SNAP - s1;
        ok = ok && write_all(fd, G.snap + s1 * SNAP_REC, sn * SNAP_REC) &&
             (s1 == 0 || write_all(fd, G.snap, s1 * SNAP_REC));

        FS_CLOSE(fd);
    }
    uint32_t n = G.count;
    if (ok) {
        G.head = G.count = 0; G.ev_head = G.ev_count = 0; G.al_head = G.al_count = 0; G.sn_head = G.sn_count = 0; G.fixes = 0;
        G.nf_imu_old = G.nf_nsamp = G.nf_focal = G.nf_angle = 0;
        for (int i = 0; i < M_NMODES; i++) G.mode_frames[i] = 0;
        G.stale_frames = G.path2_frames = G.clamp_frames = G.backoffs = 0;
    }
    G.mode = M_OFF; barrier(); G.paused = 0;
    if (fd < 0) { put_s(&o, "Open failed"); }
    else if (!ok) put_s(&o, "Write failed");
    else { for (int i = 7; i < 15; i++) put_c(&o, path[i]); put_c(&o, ' '); put_u(&o, n); }
    return say(api, c, &o);
}
#endif

/* entering gyro from OFF: start from the current angle, no stale correction */
static void enter_gyro(void)
{
    G.lv_w = 0; G.lv_quiet = 1000; G.distress = 0; G.dscale = 1.0;
    double y = 0, p = 0, rl0 = 0; uint32_t age = 0;
    if (angle_at3(time_us(), &y, &p, &rl0, &age)) { G.lp_yaw = y; G.lp_pitch = p; G.lp_roll = rl0; }
    G.tgt_roll = G.out_roll = 0; G.tgt_sx = G.tgt_sy = G.out_sx = G.out_sy = 0;
    G.tgt_dx = G.tgt_dy = G.out_dx = G.out_dy = 0; G.gyro_ok = 0; G.was_stale = 0; G.empty_run = 0;
}

#ifdef EVR_PRODUCT
/* Built-in copy: the first call arms (stabilize set-up, mode OFF); every later
 * call (autostart's second call, the research SD host's Run row) applies the
 * menu choice at once. */
static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    if (!c->armed) return arm(api, c);
    apply_choice(P.api->get_property(GYRO_KEY));
    return 1;
}
#else
static uint32_t module_run(const struct z30_module_api *api, void *vctx)
{
    struct context *c = vctx;
    if (!c->armed) return arm(api, c);
    uint32_t cur = G.mode, next;
    switch (cur) {
    case M_OFF: next = M_STD; break;
    case M_STD: next = M_ENH; break;
    case M_ENH: next = M_NATIVE; break;
    default: next = M_SAVE; break;
    }
    if (next == M_SAVE) {
        G.req_cfg = GYRO_CFG;                 /* back to 500 Hz for OFF */
        return save(api, c);
    }
    if (cur == M_OFF) G.req_cfg = GYRO_CFG_100;
    if (cur == M_OFF) enter_gyro();
    barrier();
    G.mode = next;
    struct out o = { c->text, c->text + 24 };
    put_s(&o, next == M_STD ? "GYRO Standard" : next == M_ENH ? "GYRO Enhanced" : "GYRO Native crop");
    return say(api, c, &o);
}
#endif

__attribute__((section(".text.entry"), used))
uint32_t module_init(const struct z30_module_api *api, void *ctx)
{
    (void)ctx;
    if (api->version < 2 || api->size < 20) return 0;
    return api->set_run_handler(module_run);
}
