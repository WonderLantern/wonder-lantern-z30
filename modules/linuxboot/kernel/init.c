// SPDX-License-Identifier: MIT
/*
 * /init for the Z30 Linux demo (Wonder Lantern, experimental branch).
 * Static musl, soft float (this CPU has no FPU): no floating point anywhere.
 *
 * Prints what Linux sees on the camera, draws a fixed-point Mandelbrot into
 * /dev/fb0 (the LCD overlay driver) and then shows the uptime every second.
 */
#define _GNU_SOURCE                                   /* memmem */
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/klog.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static void show(const char *path, const char *const *keys)
{
	char line[256];
	FILE *f = fopen(path, "r");

	if (!f) {
		printf("  (no %s)\n", path);
		return;
	}
	while (fgets(line, sizeof line, f)) {
		if (!keys) {
			fputs("  ", stdout);
			fputs(line, stdout);
			continue;
		}
		for (const char *const *k = keys; *k; k++)
			if (!strncmp(line, *k, strlen(*k))) {
				fputs("  ", stdout);
				fputs(line, stdout);
			}
	}
	fclose(f);
}

static long ms_now(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* Mandelbrot in Q4.12 into the lower part of an RGB565 framebuffer: rows first, first + step, ... */
static void mandelbrot(uint16_t *fb, int w, int h, int y0, int rows, int first, int step)
{
	for (int y = first; y < rows; y += step) {
		int ci = -(1 << 12) + (y * (2 << 12)) / rows;
		for (int x = 0; x < w; x++) {
			int cr = -(5 << 11) + (x * (3 << 12)) / w;
			int zr = 0, zi = 0, i = 0;
			for (; i < 48; i++) {
				int rr = (zr * zr) >> 12, ii = (zi * zi) >> 12;
				if (rr + ii > (4 << 12))
					break;
				zi = ((zr * zi) >> 11) + ci;
				zr = rr - ii + cr;
			}
			uint16_t c = i == 48 ? 0 : (uint16_t)(((i * 5) & 31) << 11 | ((i * 3) & 63) << 5 | (31 - (i & 31)));
			if (y0 + y < h)
				fb[(y0 + y) * w + x] = c;
		}
	}
}

int main(void)
{
	static const char *const cpu[] = {"processor", "CPU part", "Hardware", NULL};
	static const char *const mem[] = {"MemTotal", NULL};
	static const char *const smp[] = {"smp:", "SMP:", "CPU1", "z30-smp", NULL};
	static char klog[1 << 17];
	struct utsname u;

	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	setvbuf(stdout, NULL, _IONBF, 0);

	/* One screen (90 x 30 characters on 720 x 480): text in rows 1-19, the
	 * Mandelbrot in the bottom third, its timing on row 20. */
	printf("\033[2J\033[H*** Wonder Lantern Z30: Linux userspace is running ***\n");
	if (!uname(&u))
		printf("  %s %s %s %s\n", u.sysname, u.release, u.version, u.machine);
	show("/proc/cpuinfo", cpu);
	show("/proc/meminfo", mem);
	printf("  CPUs online: %ld\n", sysconf(_SC_NPROCESSORS_ONLN));
	/* The kernel's own words about the second core. */
	int n = klogctl(3, klog, sizeof klog - 1);           /* SYSLOG_ACTION_READ_ALL */
	if (n > 0) {
		klog[n] = 0;
		int shown = 0;
		for (char *line = klog, *end; *line && shown < 6; line = end + 1) {
			end = strchr(line, '\n');
			if (!end)
				end = line + strlen(line);
			for (const char *const *k = smp; *k; k++)
				if (memmem(line, end - line, *k, strlen(*k))) {
					printf("  %.*s\n", (int)(end - line > 86 ? 86 : end - line), line);
					shown++;
					break;
				}
			if (!*end)
				break;
		}
	}

	int fd = open("/dev/fb0", O_RDWR);
	if (fd >= 0) {
		struct fb_var_screeninfo var;
		if (!ioctl(fd, FBIOGET_VSCREENINFO, &var) && var.bits_per_pixel == 16) {
			size_t size = (size_t)var.xres * var.yres * 2;
			uint16_t *fb = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
			if (fb != MAP_FAILED) {
				int rows = var.yres / 3, y0 = var.yres - rows;
				long cpus = sysconf(_SC_NPROCESSORS_ONLN);
				long t0 = ms_now();
				printf("\033[%d;1H  Mandelbrot %ux%d...", y0 / 16, var.xres, rows);
				mandelbrot(fb, var.xres, var.yres, y0, rows, 0, 1);
				long one = ms_now() - t0;
				printf("\r  Mandelbrot %ux%d: 1 process %ld ms", var.xres, rows, one);
				if (cpus > 1) {
					/* The same picture again, even and odd rows in two processes. */
					t0 = ms_now();
					pid_t child = fork();
					if (child == 0) {
						mandelbrot(fb, var.xres, var.yres, y0, rows, 1, 2);
						_exit(0);
					}
					mandelbrot(fb, var.xres, var.yres, y0, rows, 0, 2);
					if (child > 0)
						waitpid(child, NULL, 0);
					long two = ms_now() - t0;
					if (!two)
						two = 1;
					printf(", 2 processes %ld ms (%ld CPUs, x%ld.%02ld)", two, cpus,
					       one / two, one * 100 / two % 100);
				}
			}
		}
	}
	for (;;) {
		struct timespec t;
		clock_gettime(CLOCK_MONOTONIC, &t);
		printf("\0337\033[1;72Huptime %5ld s\0338", (long)t.tv_sec);   /* top right, cursor kept */
		sleep(1);
	}
}
