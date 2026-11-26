// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nikon Z30 second core (Wonder Lantern, experimental branch).
 *
 * The camera firmware ran both Cortex-A7 cores. Before starting Linux the
 * Wonder Lantern handoff parks core 1 in a loop outside Linux memory
 * (modules/linuxboot/lowlevel.S lb_park_loop): interrupts off, its stock SGI
 * ended, waiting in WFE for a word to become non-zero. When it does, it turns
 * its MMU and caches off and jumps there, like a cold secondary.
 *
 * The cpu node carries that word's physical address:
 *   enable-method = "wonderlantern,z30-smp";  cpu-release-addr = <...>;
 */
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/of.h>
#include <linux/smp.h>
#include <asm/cacheflush.h>
#include <asm/smp.h>
#include <asm/smp_plat.h>

static int z30_boot_secondary(unsigned int cpu, struct task_struct *idle)
{
	struct device_node *np = of_get_cpu_node(cpu, NULL);
	void __iomem *release;
	u32 addr;
	int ret;

	if (!np)
		return -ENODEV;
	ret = of_property_read_u32(np, "cpu-release-addr", &addr);
	of_node_put(np);
	if (ret)
		return ret;
	/* The word sits in the stock firmware's heap, outside Linux memory. */
	release = ioremap(addr & PAGE_MASK, PAGE_SIZE);
	if (!release)
		return -ENOMEM;
	{
		void __iomem *word = release + (addr & ~PAGE_MASK);
		/* The parking state is {parked, release address}: parked becomes 2 once core 1
		 * has seen the address and left the loop (lowlevel.S). */
		u32 before = readl(word - 4), after = before;
		int ms;

		writel(__pa_symbol(secondary_startup_arm), word);
		dsb_sev();
		for (ms = 0; ms < 100 && after != 2; ms++) {
			mdelay(1);
			after = readl(word - 4);
		}
		pr_info("z30-smp: core %u parking word %u -> %u after %d ms (release word at %08x)\n",
			cpu, before, after, ms, addr);
	}
	iounmap(release);
	return 0;
}

static const struct smp_operations z30_smp_ops __initconst = {
	.smp_boot_secondary = z30_boot_secondary,
};
CPU_METHOD_OF_DECLARE(z30_smp, "wonderlantern,z30-smp", &z30_smp_ops);
