/* Research-only helpers for Z30 C 1.20 SD modules. Not a stable ABI. */
#ifndef Z30_RESEARCH_H
#define Z30_RESEARCH_H
#include <stdint.h>
#include "z30_module_api.h"

/* Dormant e-VR hook cell (features/evr_hook.py). Zero at boot. */
#define EVR_HOOK_CELL ((volatile uint32_t *)0xC1832D44u)
/* Lens VR conversion divisors used for frame metadata +0x29EC/+0x29F0. */
#define LVR_K0 ((volatile int32_t *)0xC18C7298u)
#define LVR_K1 ((volatile int32_t *)0xC18C729Cu)

typedef void (*evr_frame_fn)(const uint8_t *input, uint8_t *result, const uint8_t *meta);

/* Stock microsecond clock: generic timer count / 48 (0x101D8300, 171 stock callers). */
typedef void (*z30_time_us_fn)(uint64_t *out);
#define Z30_TIME_US ((z30_time_us_fn)0x101D8301u)
static inline uint64_t time_us(void) { uint64_t t = 0; Z30_TIME_US(&t); return t; }

/* No CP15 cache maintenance here: modules may not run privileged. Publish
 * with a barrier only; a stale read on the menu side is harmless. */
static inline void barrier(void) { __asm__ volatile("dmb" ::: "memory"); }

static inline int32_t rd32(const uint8_t *p, uint32_t off) { return *(const volatile int32_t *)(p + off); }
static inline uint32_t rd8(const uint8_t *p, uint32_t off) { return *(const volatile uint8_t *)(p + off); }
#endif
