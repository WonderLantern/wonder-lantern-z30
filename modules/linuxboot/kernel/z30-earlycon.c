// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nikon Z30 early console on the LCD overlay plane, plus the machine
 * descriptor that makes it possible (Wonder Lantern, experimental branch).
 *
 * "earlycon=z30ovl keep_bootcon" prints the kernel log straight into the
 * overlay plane the Wonder Lantern handoff described in the DTB node
 * overlay-fb, from the first parsed parameter until the overlay framebuffer
 * driver takes the screen over (it calls z30_earlycon_stop()). There is no
 * UART on the camera, so this is the only window into an early hang.
 *
 * ARM has no late fixmap (early_ioremap after early_ioremap_reset() BUGs), so
 * the console works in three phases, switched by this machine's hooks:
 *   early   from setup until .reserve: one early_ioremap per scanline;
 *   hold    .reserve until .map_io: text is kept in a small buffer;
 *   static  from .map_io (inside paging_init): a permanent mapping of the
 *           planes at Z30_VIRT; the held text is drawn first.
 * The screen wraps to the top instead of scrolling (no read-back of the plane).
 */
#include <linux/console.h>
#include <linux/delay.h>
#include <linux/font.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/mm.h>
#include <linux/of.h>
#include <linux/of_fdt.h>
#include <linux/pm.h>
#include <linux/reboot.h>
#include <linux/serial_core.h>
#include <linux/string.h>
#include <asm/early_ioremap.h>
#include <asm/mach/arch.h>
#include <asm/mach/map.h>

#include "z30-overlay.h"

#define Z30_VIRT 0xf8000000UL        /* static window: colour plane, then alpha at +2 MiB */
#define Z30_ALPHA_VIRT (Z30_VIRT + SZ_2M)
#define Z30_SP805_PHYS 0x0426c000UL  /* ARM SP805 watchdog (stock ISR 0x10054870) */
#define SP805_LOAD     0x000
#define SP805_CONTROL  0x008
#define SP805_INTCLR   0x00c
#define SP805_LOCK     0xc00
#define SP805_ITCR     0xf00
#define SP805_ITOP     0xf04
#define SP805_UNLOCK   0x1acce551u

static u32 color, alpha, width, height, cstride, astride;
static enum { P_OFF, P_EARLY, P_HOLD, P_STATIC } phase;
static u8 __iomem *vcolor, *valpha;
static const struct font_desc *font;
static u32 col, row;                  /* text cursor, in characters */
static char hold[8192];
static u32 hold_len;
static struct console *bootcon;

static bool __init z30_read_dt(void)
{
	int node = of_get_flat_dt_subnode_by_name(of_get_flat_dt_root(), "overlay-fb");
	const char *names[] = { "z30,overlay-color", "z30,overlay-alpha", "z30,width",
				"z30,height", "z30,color-stride", "z30,alpha-stride" };
	u32 *out[] = { &color, &alpha, &width, &height, &cstride, &astride };
	int i, len;

	if (node < 0)
		return false;
	for (i = 0; i < ARRAY_SIZE(names); i++) {
		const __be32 *p = of_get_flat_dt_prop(node, names[i], &len);

		if (!p || len < 4)
			return false;
		*out[i] = be32_to_cpup(p);
	}
	return width >= 320 && width <= 2048 && !(width & 1) && height >= 240 && height <= 2048 &&
	       cstride >= width * 2 && astride >= width && color && alpha;
}

/* One scanline of both planes: map, fill [x0, x1) from fn, unmap. */
/* __ref: early_ioremap() is only reached in the early phase, during init. */
static void __ref z30_line(u32 y, u32 x0, u32 x1, const char *s, u32 h)
{
	u8 __iomem *c, *a;
	u32 x;

	if (y >= height)
		return;
	if (phase == P_EARLY) {
		c = early_ioremap(color + y * cstride, width * 2);
		a = early_ioremap(alpha + y * astride, width);
		if (!c || !a) {
			if (c)
				early_iounmap(c, width * 2);
			if (a)
				early_iounmap(a, width);
			return;
		}
	} else {
		c = vcolor + y * cstride;
		a = valpha + y * astride;
	}
	for (x = x0; x < x1; x += 2) {
		u32 word = 0x80801010;                    /* black pair: Y 16, U/V 128 */

		if (s) {
			u32 i = (x - x0) / font->width, bit = (x - x0) % font->width;
			const u8 *g = font->data + (u8)s[i] * font->height * DIV_ROUND_UP(font->width, 8) +
				      h * DIV_ROUND_UP(font->width, 8);
			u8 bits = g[bit / 8];

			word = 0x80800000 | ((bits >> (7 - bit % 8)) & 1 ? 0xeb : 0x10) |
			       ((bits >> (6 - bit % 8)) & 1 ? 0xeb00 : 0x1000);
		}
		writel_relaxed(word, c + x * 2);
		writew_relaxed(0xffff, a + x);
	}
	if (phase == P_EARLY) {
		early_iounmap(a, width);
		early_iounmap(c, width * 2);
	}
}

static void z30_clear_row(u32 r)
{
	u32 h;

	for (h = 0; h < font->height; h++)
		z30_line(r * font->height + h, 0, width, NULL, 0);
}

static void z30_draw(const char *s, u32 n)
{
	u32 cols = width / font->width, rows = height / font->height;

	while (n) {
		u32 count = strnchrnul(s, n, '\n') - s, h;

		count = min(count, cols - col);
		if (count) {
			for (h = 0; h < font->height; h++)
				z30_line(row * font->height + h, col * font->width,
					 (col + count) * font->width, s, h);
			col += count; s += count; n -= count;
		}
		if (n && *s == '\n') {
			s++; n--;
			col = cols;                               /* force a new line */
		}
		if (col >= cols) {
			col = 0;
			row = (row + 1) % rows;
			z30_clear_row(row);
			z30_clear_row((row + 1) % rows);          /* a blank line marks the newest text */
		}
	}
}

static void z30_write(struct console *con, const char *s, unsigned int n)
{
	if (phase == P_HOLD) {
		u32 room = sizeof(hold) - hold_len;

		n = min(n, room);
		memcpy(hold + hold_len, s, n);
		hold_len += n;
		return;
	}
	if (phase == P_EARLY || phase == P_STATIC)
		z30_draw(s, n);
}

static int __init z30_earlycon_setup(struct earlycon_device *device, const char *opt)
{
	if (!z30_read_dt())
		return -ENODEV;
	font = get_default_font(width, height, NULL, NULL);
	if (!font || font->width % 2)
		return -ENODEV;
	phase = P_EARLY;
	bootcon = device->con;
	device->con->write = z30_write;
	z30_clear_row(0);
	z30_clear_row(1);
	return 0;
}
EARLYCON_DECLARE(z30ovl, z30_earlycon_setup);

/* Called by the framebuffer driver before it draws: the screen is its from then on. */
void z30_earlycon_stop(void)
{
	if (bootcon && console_is_registered(bootcon))
		unregister_console(bootcon);
	phase = P_OFF;
}

/* ---- machine ------------------------------------------------------------ */

static void __init z30_reserve(void)
{
	if (phase == P_EARLY)
		phase = P_HOLD;               /* early_ioremap_reset() comes next */
}

static void __init z30_map_io(void)
{
	struct map_desc map[2];
	u32 cbase, abase;

	if (!color && !z30_read_dt())
		return;
	cbase = color & PAGE_MASK;
	abase = alpha & PAGE_MASK;
	map[0] = (struct map_desc){ Z30_VIRT, __phys_to_pfn(cbase),
				    PAGE_ALIGN(color - cbase + cstride * height), MT_DEVICE };
	map[1] = (struct map_desc){ Z30_ALPHA_VIRT, __phys_to_pfn(abase),
				    PAGE_ALIGN(alpha - abase + astride * height), MT_DEVICE };
	if (map[0].length > SZ_2M || map[1].length > SZ_2M)
		return;
	iotable_init(map, ARRAY_SIZE(map));
	vcolor = (u8 __iomem *)(Z30_VIRT + (color - cbase));
	valpha = (u8 __iomem *)(Z30_ALPHA_VIRT + (alpha - abase));
	if (phase == P_HOLD) {
		phase = P_STATIC;
		z30_draw(hold, hold_len);
		hold_len = 0;
	}
}

static void __iomem *z30_wdt;

/* Hardware reset via ARM SP805 watchdog (0x0426C000), matching stock EG ISR 0x10054870.
 * Never touches SP805 registers during boot so z30-qemu (which has no SP805 model) is unaffected. */
static void z30_restart(enum reboot_mode mode, const char *cmd)
{
	if (!z30_wdt) {
		pr_emerg("z30: SP805 not mapped, cannot reset\n");
		return;
	}
	pr_emerg("z30: triggering SP805 hardware reset...\n");
	writel(SP805_UNLOCK, z30_wdt + SP805_LOCK);
	writel(0, z30_wdt + SP805_INTCLR);
	/* Integration test mode: assert WDOGRES immediately (same as stock 0x100548d6). */
	writel(1, z30_wdt + SP805_ITCR);
	writel(1, z30_wdt + SP805_ITOP);
	dsb();
	mdelay(200);
	/* Fallback: arm counter with load=1 and INTEN|RESEN enabled. */
	writel(0, z30_wdt + SP805_ITCR);
	writel(1, z30_wdt + SP805_LOAD);
	writel(3, z30_wdt + SP805_CONTROL);
	dsb();
	mdelay(500);
}

static void z30_power_off(void)
{
	/* EX MCU owns the main battery power rail; resetting via SP805 returns to stock
	 * firmware, which immediately powers the camera off if the power switch is OFF. */
	pr_emerg("z30: poweroff -> SP805 reset (flip power switch OFF to shut down)\n");
	z30_restart(REBOOT_HARD, NULL);
}

static int __init z30_power_init(void)
{
	if (!of_machine_is_compatible("wonderlantern,z30"))
		return 0;
	z30_wdt = ioremap(Z30_SP805_PHYS, SZ_4K);
	if (z30_wdt)
		pm_power_off = z30_power_off;
	return 0;
}
arch_initcall(z30_power_init);

static const char *const z30_compat[] __initconst = { "wonderlantern,z30", NULL };

DT_MACHINE_START(Z30, "Nikon Z30 (Wonder Lantern)")
	.dt_compat = z30_compat,
	.reserve = z30_reserve,
	.map_io = z30_map_io,
	.restart = z30_restart,
MACHINE_END
