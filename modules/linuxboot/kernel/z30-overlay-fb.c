// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nikon Z30 LCD overlay framebuffer (Wonder Lantern, experimental branch).
 *
 * The camera's own firmware leaves the LCD controller scanning a GUI overlay
 * plane when the Wonder Lantern handoff starts Linux. That plane is YUV 4:2:2
 * with two pixels in four bytes "Y0 Y1 U V", plus a separate one-byte-per-pixel
 * alpha plane. The handoff writes their addresses, geometry and strides into
 * this node (docs/linux.md in the Wonder Lantern Z30 repository).
 *
 * Linux sees an ordinary RGB565 framebuffer in system memory. fbcon drawing and
 * mmap writes go through deferred I/O; the damaged rectangle is converted to
 * the overlay format by a work item. The display controller itself is left
 * exactly as the stock firmware programmed it.
 *
 *   overlay-fb {
 *       compatible = "wonderlantern,z30-overlay";
 *       z30,overlay-color = <addr>;  z30,overlay-alpha = <addr>;
 *       z30,width = <720>;  z30,height = <480>;
 *       z30,color-stride = <1440>;  z30,alpha-stride = <720>;
 *   };
 */
#include <linux/fb.h>
#include <linux/io.h>
#include <linux/minmax.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/spinlock.h>

#include "z30-overlay.h"

struct z30fb {
	struct fb_info *info;
	u8 *color, *alpha;            /* overlay planes (memremap, write-combined) */
	u32 width, height, cstride, astride;
	spinlock_t lock;
	bool pending;
	u32 x1, y1, x2, y2;           /* damage, exclusive x2 / y2 */
	u32 pseudo_palette[16];
};

/* RGB565 -> studio-range BT.601 Y, Cb, Cr. Integer only: this CPU has no FPU. */
static inline void z30fb_yuv(u16 p, int *y, int *u, int *v)
{
	int r = (p >> 11) << 3, g = ((p >> 5) & 63) << 2, b = (p & 31) << 3;

	r |= r >> 5; g |= g >> 6; b |= b >> 5;
	*y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16;
	*u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128;
	*v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128;
}

static void z30fb_convert(struct z30fb *par, u32 x1, u32 y1, u32 x2, u32 y2)
{
	const u16 *src = (const u16 *)par->info->screen_buffer;
	u32 x, y;

	x1 &= ~1u;
	x2 = min(ALIGN(x2, 2), par->width);
	for (y = y1; y < y2; y++) {
		const u16 *s = src + y * par->width;
		u8 *d = par->color + y * par->cstride;

		for (x = x1; x < x2; x += 2) {     /* width is even (probe) */
			int y0, u0, v0, y1_, u1, v1;
			u32 word;

			z30fb_yuv(s[x], &y0, &u0, &v0);
			z30fb_yuv(s[x + 1], &y1_, &u1, &v1);
			word = (u32)y0 | (u32)y1_ << 8 | (u32)((u0 + u1 + 1) >> 1) << 16 |
			       (u32)((v0 + v1 + 1) >> 1) << 24;
			*(u32 *)(d + x * 2) = word;
		}
	}
	wmb();
}

static void z30fb_damage(struct fb_info *info, u32 x, u32 y, u32 w, u32 h)
{
	struct z30fb *par = info->par;
	unsigned long flags;
	u32 x2, y2;

	if (!w || !h || x >= par->width || y >= par->height)
		return;
	x2 = x + min(w, par->width - x);
	y2 = y + min(h, par->height - y);
	spin_lock_irqsave(&par->lock, flags);
	if (par->pending) {
		par->x1 = min(par->x1, x); par->y1 = min(par->y1, y);
		par->x2 = max(par->x2, x2); par->y2 = max(par->y2, y2);
	} else {
		par->x1 = x; par->y1 = y; par->x2 = x2; par->y2 = y2;
		par->pending = true;
	}
	spin_unlock_irqrestore(&par->lock, flags);
	/* Throttle (schedule_delayed_work), not debounce (mod_delayed_work):
	 * continuous fbcon output (htop, cmatrix) would otherwise keep pushing
	 * the 40 ms timer back and starve overlay flushes indefinitely. */
	schedule_delayed_work(&info->deferred_work, info->fbdefio->delay);
}

static void z30fb_damage_range(struct fb_info *info, off_t off, size_t len)
{
	struct z30fb *par = info->par;
	u32 line = par->width * 2;
	u32 y1 = off / line, y2 = DIV_ROUND_UP(off + len, line);

	z30fb_damage(info, 0, y1, par->width, y2 - y1);
}

static void z30fb_damage_area(struct fb_info *info, u32 x, u32 y, u32 w, u32 h)
{
	z30fb_damage(info, x, y, w, h);
}

FB_GEN_DEFAULT_DEFERRED_SYSMEM_OPS(z30fb, z30fb_damage_range, z30fb_damage_area)

static int z30fb_setcolreg(unsigned int regno, unsigned int red, unsigned int green,
			   unsigned int blue, unsigned int transp, struct fb_info *info)
{
	struct z30fb *par = info->par;

	if (regno >= 16)
		return -EINVAL;
	par->pseudo_palette[regno] = (red >> 11) << 11 | (green >> 10) << 5 | blue >> 11;
	return 0;
}

static const struct fb_ops z30fb_ops = {
	.owner = THIS_MODULE,
	FB_DEFAULT_DEFERRED_OPS(z30fb),
	.fb_setcolreg = z30fb_setcolreg,
};

static void z30fb_deferred_io(struct fb_info *info, struct list_head *pagereflist)
{
	struct z30fb *par = info->par;
	unsigned long flags;
	u32 x1, y1, x2, y2;

	spin_lock_irqsave(&par->lock, flags);
	if (!list_empty(pagereflist)) {
		x1 = 0; y1 = 0; x2 = par->width; y2 = par->height;
	} else if (par->pending) {
		x1 = par->x1; y1 = par->y1; x2 = par->x2; y2 = par->y2;
	} else {
		spin_unlock_irqrestore(&par->lock, flags);
		return;
	}
	par->pending = false;
	spin_unlock_irqrestore(&par->lock, flags);
	z30fb_convert(par, x1, y1, x2, y2);
}

static struct fb_deferred_io z30fb_defio = {
	.delay = HZ / 25,
	.deferred_io = z30fb_deferred_io,
};

static int z30fb_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *np = dev->of_node;
	u32 color, alpha, size;
	struct fb_info *info;
	struct z30fb *par;
	void *vmem;
	int ret, y;

	/* The early console writes the same planes: from here on the screen is ours. */
	z30_earlycon_stop();
	info = framebuffer_alloc(sizeof(*par), dev);
	if (!info)
		return -ENOMEM;
	par = info->par;
	par->info = info;
	spin_lock_init(&par->lock);
	if (of_property_read_u32(np, "z30,overlay-color", &color) ||
	    of_property_read_u32(np, "z30,overlay-alpha", &alpha) ||
	    of_property_read_u32(np, "z30,width", &par->width) ||
	    of_property_read_u32(np, "z30,height", &par->height) ||
	    of_property_read_u32(np, "z30,color-stride", &par->cstride) ||
	    of_property_read_u32(np, "z30,alpha-stride", &par->astride)) {
		ret = -EINVAL;
		goto fail;
	}
	if (par->width < 320 || par->width > 2048 || (par->width & 1) || par->height < 240 || par->height > 2048 ||
	    par->cstride < par->width * 2 || par->astride < par->width) {
		dev_err(dev, "implausible overlay %ux%u strides %u/%u\n",
			par->width, par->height, par->cstride, par->astride);
		ret = -EINVAL;
		goto fail;
	}
	par->color = devm_memremap(dev, color, par->cstride * par->height, MEMREMAP_WC);
	par->alpha = devm_memremap(dev, alpha, par->astride * par->height, MEMREMAP_WC);
	if (IS_ERR(par->color) || IS_ERR(par->alpha)) {
		ret = -ENOMEM;
		goto fail;
	}

	size = PAGE_ALIGN(par->width * par->height * 2);
	vmem = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, get_order(size));
	if (!vmem) {
		ret = -ENOMEM;
		goto fail;
	}
	info->fbops = &z30fb_ops;
	strscpy(info->fix.id, "z30-overlay", sizeof(info->fix.id));
	info->fix.type = FB_TYPE_PACKED_PIXELS;
	info->fix.visual = FB_VISUAL_TRUECOLOR;
	info->fix.line_length = par->width * 2;
	info->fix.smem_start = __pa(vmem);
	info->fix.smem_len = size;
	info->screen_buffer = vmem;
	info->var.xres = info->var.xres_virtual = par->width;
	info->var.yres = info->var.yres_virtual = par->height;
	info->var.bits_per_pixel = 16;
	info->var.red = (struct fb_bitfield){ 11, 5, 0 };
	info->var.green = (struct fb_bitfield){ 5, 6, 0 };
	info->var.blue = (struct fb_bitfield){ 0, 5, 0 };
	info->var.activate = FB_ACTIVATE_NOW;
	info->pseudo_palette = par->pseudo_palette;
	info->fbdefio = &z30fb_defio;
	info->flags |= FBINFO_VIRTFB;     /* system-memory framebuffer: sys_* drawing helpers */
	ret = fb_deferred_io_init(info);
	if (ret)
		goto fail_pages;

	/* Opaque overlay, black picture. */
	for (y = 0; y < par->height; y++)
		memset(par->alpha + y * par->astride, 0xff, par->width);
	z30fb_convert(par, 0, 0, par->width, par->height);

	ret = register_framebuffer(info);
	if (ret)
		goto fail_defio;
	platform_set_drvdata(pdev, info);
	dev_info(dev, "fb%d: %ux%u RGB565 -> overlay YUV422 at %08x, alpha %08x\n",
		 info->node, par->width, par->height, color, alpha);
	return 0;

fail_defio:
	fb_deferred_io_cleanup(info);
fail_pages:
	free_pages((unsigned long)vmem, get_order(size));
fail:
	framebuffer_release(info);
	return ret;
}

static const struct of_device_id z30fb_of_match[] = {
	{ .compatible = "wonderlantern,z30-overlay" },
	{ }
};
MODULE_DEVICE_TABLE(of, z30fb_of_match);

static struct platform_driver z30fb_driver = {
	.probe = z30fb_probe,
	.driver = {
		.name = "z30-overlay-fb",
		.of_match_table = z30fb_of_match,
	},
};
module_platform_driver(z30fb_driver);

MODULE_DESCRIPTION("Nikon Z30 LCD overlay framebuffer (Wonder Lantern)");
MODULE_LICENSE("GPL");
