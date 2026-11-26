// SPDX-License-Identifier: MIT
/* Real-time system dashboard for Nikon Z30 Linux fbcon (90x30, integer-only). */
#include <arpa/inet.h>
#include <ctype.h>
#include <dirent.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/klog.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

struct cpu_stat {
	unsigned long long user, nice, sys, idle, iowait, irq, softirq, steal;
};

struct proc_row {
	int pid;
	char state;
	unsigned long rss_kb;
	unsigned long ticks;
	char comm[36];
};

static int read_cpu(struct cpu_stat out[3])
{
	FILE *f = fopen("/proc/stat", "r");
	char line[256];
	int n = 0;

	if (!f)
		return 0;
	memset(out, 0, sizeof(*out) * 3);
	while (fgets(line, sizeof line, f)) {
		int idx = -1;
		if (!strncmp(line, "cpu ", 4))
			idx = 0;
		else if (!strncmp(line, "cpu0 ", 5))
			idx = 1;
		else if (!strncmp(line, "cpu1 ", 5))
			idx = 2;
		else
			continue;
		sscanf(line + (idx == 0 ? 4 : 5), "%llu %llu %llu %llu %llu %llu %llu %llu",
		       &out[idx].user, &out[idx].nice, &out[idx].sys, &out[idx].idle,
		       &out[idx].iowait, &out[idx].irq, &out[idx].softirq, &out[idx].steal);
		if (idx > n)
			n = idx;
	}
	fclose(f);
	return n;
}

static int cpu_pct10(const struct cpu_stat *a, const struct cpu_stat *b)
{
	unsigned long long id1 = a->idle + a->iowait;
	unsigned long long id2 = b->idle + b->iowait;
	unsigned long long tot1 = id1 + a->user + a->nice + a->sys + a->irq + a->softirq + a->steal;
	unsigned long long tot2 = id2 + b->user + b->nice + b->sys + b->irq + b->softirq + b->steal;
	unsigned long long dt = tot2 > tot1 ? tot2 - tot1 : 0;
	unsigned long long di = id2 > id1 ? id2 - id1 : 0;

	if (!dt)
		return 0;
	return (int)(((dt - di) * 1000ULL) / dt);
}

static void read_line(const char *path, char *buf, size_t sz, const char *fallback)
{
	FILE *f = fopen(path, "r");

	if (!f || !fgets(buf, (int)sz, f)) {
		snprintf(buf, sz, "%s", fallback);
		if (f)
			fclose(f);
		return;
	}
	fclose(f);
	size_t n = strlen(buf);
	while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r'))
		buf[--n] = 0;
}

static void read_mem(unsigned long *total_mb, unsigned long *used_mb)
{
	FILE *f = fopen("/proc/meminfo", "r");
	char line[128];
	unsigned long total = 0, avail = 0, memfree = 0;

	if (f) {
		while (fgets(line, sizeof line, f)) {
			if (!strncmp(line, "MemTotal:", 9))
				sscanf(line + 9, "%lu", &total);
			else if (!strncmp(line, "MemAvailable:", 13))
				sscanf(line + 13, "%lu", &avail);
			else if (!strncmp(line, "MemFree:", 8))
				sscanf(line + 8, "%lu", &memfree);
		}
		fclose(f);
	}
	if (!avail)
		avail = memfree;
	*total_mb = total / 1024;
	*used_mb = (total > avail ? total - avail : 0) / 1024;
}

static void read_irqs(unsigned long *timer_irqs, unsigned long *usb_irqs)
{
	FILE *f = fopen("/proc/interrupts", "r");
	char line[256];

	*timer_irqs = 0;
	*usb_irqs = 0;
	if (!f)
		return;
	while (fgets(line, sizeof line, f)) {
		unsigned long c0 = 0, c1 = 0;
		char *colon = strchr(line, ':');
		if (!colon)
			continue;
		int got = sscanf(colon + 1, "%lu %lu", &c0, &c1);
		if (got <= 0)
			continue;
		unsigned long sum = got == 2 ? c0 + c1 : c0;
		if (strstr(line, "arch_timer"))
			*timer_irqs += sum;
		else if (strstr(line, "dwc3"))
			*usb_irqs += sum;
	}
	fclose(f);
}

static int cmp_proc(const void *pa, const void *pb)
{
	const struct proc_row *a = pa, *b = pb;

	if (b->ticks != a->ticks)
		return b->ticks > a->ticks ? 1 : -1;
	if (b->rss_kb != a->rss_kb)
		return b->rss_kb > a->rss_kb ? 1 : -1;
	return a->pid - b->pid;
}

static int scan_procs(struct proc_row *rows, int max_rows, int *total_procs, int *sh_pid)
{
	DIR *d = opendir("/proc");
	struct dirent *de;
	int n = 0;

	*total_procs = 0;
	*sh_pid = 0;
	if (!d)
		return 0;
	while ((de = readdir(d)) != NULL) {
		if (!isdigit((unsigned char)de->d_name[0]))
			continue;
		(*total_procs)++;
		int pid = atoi(de->d_name);
		char path[64], buf[512];
		snprintf(path, sizeof path, "/proc/%d/stat", pid);
		FILE *f = fopen(path, "r");
		if (!f)
			continue;
		size_t r = fread(buf, 1, sizeof buf - 1, f);
		fclose(f);
		buf[r] = 0;
		char *lp = strchr(buf, '('), *rp = strrchr(buf, ')');
		if (!lp || !rp || rp <= lp)
			continue;
		char comm[36];
		size_t clen = (size_t)(rp - lp - 1);
		if (clen >= sizeof comm)
			clen = sizeof comm - 1;
		memcpy(comm, lp + 1, clen);
		comm[clen] = 0;

		char state = '?';
		unsigned long ut = 0, st = 0;
		long rss = 0;
		if (sscanf(rp + 2, "%c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %lu %lu %*d %*d %*d %*d %*d %*d %*u %*u %ld",
			   &state, &ut, &st, &rss) < 1)
			continue;
		if (!strcmp(comm, "sh") && pid > 1)
			*sh_pid = pid;
		if (n < max_rows) {
			rows[n].pid = pid;
			rows[n].state = state;
			rows[n].ticks = ut + st;
			rows[n].rss_kb = rss > 0 ? (unsigned long)rss * 4UL : 0;
			snprintf(rows[n].comm, sizeof rows[n].comm, "%s", comm);
			n++;
		}
	}
	closedir(d);
	qsort(rows, (size_t)n, sizeof rows[0], cmp_proc);
	return n;
}

static void draw_bar(int width, int permille, const char *col)
{
	int fill = (permille * width + 500) / 1000;
	if (fill < 0)
		fill = 0;
	if (fill > width)
		fill = width;
	printf("\033[1;37m[%s", col);
	for (int i = 0; i < fill; i++)
		putchar('#');
	printf("\033[0;37m");
	for (int i = fill; i < width; i++)
		putchar('-');
	printf("\033[1;37m]\033[0m");
}

static void draw_rule(int row)
{
	printf("\033[%d;1H\033[0;36m", row);
	for (int i = 0; i < 90; i++)
		putchar('-');
	printf("\033[0m");
}

static int read_wlan0_ip(char *buf, size_t sz)
{
	int fd = socket(AF_INET, SOCK_DGRAM, 0);
	struct ifreq ifr;

	buf[0] = 0;
	if (fd < 0)
		return 0;
	memset(&ifr, 0, sizeof ifr);
	snprintf(ifr.ifr_name, sizeof ifr.ifr_name, "wlan0");
	int ok = (ioctl(fd, SIOCGIFADDR, &ifr) == 0);
	close(fd);
	if (!ok)
		return 0;
	struct sockaddr_in *sin = (struct sockaddr_in *)&ifr.ifr_addr;
	return inet_ntop(AF_INET, &sin->sin_addr, buf, (socklen_t)sz) != NULL;
}

static int sd_mounted(char *fstype, size_t sz)
{
	FILE *f = fopen("/proc/mounts", "r");
	char dev[64], mp[64], fs[32];

	if (!f)
		return 0;
	while (fscanf(f, "%63s %63s %31s %*[^\n]\n", dev, mp, fs) == 3) {
		if (!strcmp(mp, "/mnt/sd")) {
			snprintf(fstype, sz, "%s", fs);
			fclose(f);
			return 1;
		}
	}
	fclose(f);
	return 0;
}

static void show_klog(int start_row, int max_lines)
{
	static char kbuf[32768];
	const char *lines[16];
	int lens[16];
	int count = 0;

	int n = klogctl(3, kbuf, sizeof kbuf - 1);
	if (n < 0)
		n = 0;
	kbuf[n] = 0;

	for (char *p = kbuf; *p; ) {
		char *nl = strchr(p, '\n');
		int len = nl ? (int)(nl - p) : (int)strlen(p);
		/* Skip noisy SDHCI register dump lines so real events stay visible. */
		if (len > 0 && !strstr(p, "mmc0: sdhci:") && !strstr(p, "Reset 0x4 never completed")) {
			char *s = p;
			if (*s == '<') {
				char *gt = strchr(s, '>');
				if (gt && gt < p + len) {
					len -= (int)(gt + 1 - s);
					s = gt + 1;
				}
			}
			lines[count % max_lines] = s;
			lens[count % max_lines] = len;
			count++;
		}
		if (!nl)
			break;
		p = nl + 1;
	}

	int shown = count < max_lines ? count : max_lines;
	int first = count < max_lines ? 0 : (count % max_lines);
	for (int i = 0; i < max_lines; i++) {
		printf("\033[%d;1H", start_row + i);
		if (i < shown) {
			int idx = (first + i) % max_lines;
			int w = lens[idx] > 88 ? 88 : lens[idx];
			printf(" \033[0;37m%.*s\033[0m\033[K", w, lines[idx]);
		} else {
			printf("\033[K");
		}
	}
}

int main(void)
{
	struct cpu_stat prev[3], cur[3];
	struct proc_row procs[64];
	struct utsname u;
	char rel[32] = "Linux";

	if (!uname(&u))
		snprintf(rel, sizeof rel, "%s", u.release);
	read_cpu(prev);
	setvbuf(stdout, NULL, _IONBF, 0);
	printf("\033[2J\033[?25l");

	for (;;) {
		struct timespec ts;
		clock_gettime(CLOCK_MONOTONIC, &ts);
		long up = (long)ts.tv_sec;
		read_cpu(cur);
		int p0 = cpu_pct10(&prev[1], &cur[1]);
		int p1 = cpu_pct10(&prev[2], &cur[2]);
		memcpy(prev, cur, sizeof prev);

		unsigned long mem_tot = 0, mem_used = 0;
		read_mem(&mem_tot, &mem_used);
		int mem_pm = mem_tot ? (int)((mem_used * 1000UL) / mem_tot) : 0;

		char load[64], udc_state[32], udc_speed[32], fs[32];
		read_line("/proc/loadavg", load, sizeof load, "0.00 0.00 0.00");
		char *sp = load;
		for (int k = 0; k < 3 && sp; k++) {
			sp = strchr(sp, ' ');
			if (sp && k < 2)
				sp++;
		}
		if (sp)
			*sp = 0;

		read_line("/sys/class/udc/2aa00000.usb/state", udc_state, sizeof udc_state, "n/a");
		read_line("/sys/class/udc/2aa00000.usb/current_speed", udc_speed, sizeof udc_speed, "none");
		struct stat st_tty, st_wlan;
		int has_tty = (stat("/dev/ttyGS0", &st_tty) == 0);
		int has_wlan = (stat("/sys/class/net/wlan0", &st_wlan) == 0);
		char wlan_ip[32] = "";
		int has_ip = has_wlan && read_wlan0_ip(wlan_ip, sizeof wlan_ip);
		int has_sd = sd_mounted(fs, sizeof fs);

		unsigned long irq_tmr = 0, irq_usb = 0;
		read_irqs(&irq_tmr, &irq_usb);

		int total_procs = 0, sh_pid = 0;
		int nproc = scan_procs(procs, 64, &total_procs, &sh_pid);

		/* Row 1: Header */
		printf("\033[1;1H\033[1;37;44m  NIKON Z30  |  Linux %-12s (EXPEED 6 Dual-A7)           UPTIME %02ld:%02ld:%02ld  \033[0m\033[K",
		       rel, up / 3600, (up / 60) % 60, up % 60);
		draw_rule(2);

		/* Rows 3-10: Two-column status */
		printf("\033[3;1H \033[1;36m[ SYSTEM & COMPUTE ]\033[0m\033[45G\033[0;36m|\033[0m \033[1;32m[ INTERFACES & SHELL ]\033[0m\033[K");

		printf("\033[4;1H CPU0 ");
		draw_bar(24, p0, p0 > 700 ? "\033[1;31m" : "\033[1;32m");
		printf(" %3d.%d%%\033[45G\033[0;36m|\033[0m USB  2aa00000.usb (DWC3 v2.90a)\033[K", p0 / 10, p0 % 10);

		printf("\033[5;1H CPU1 ");
		draw_bar(24, p1, p1 > 700 ? "\033[1;31m" : "\033[1;32m");
		printf(" %3d.%d%%\033[45G\033[0;36m|\033[0m LINK %s%s\033[0m @ %s\033[K",
		       p1 / 10, p1 % 10,
		       !strcmp(udc_state, "configured") ? "\033[1;32m" : "\033[1;33m",
		       udc_state, udc_speed);

		printf("\033[6;1H MEM  ");
		draw_bar(24, mem_pm, "\033[1;36m");
		printf(" %3lu/%luM\033[45G\033[0;36m|\033[0m TTY  /dev/ttyGS0 %s\033[0m\033[K",
		       mem_used, mem_tot,
		       has_tty ? "\033[1;32m[ONLINE]" : "\033[1;33m[WAITING]");

		printf("\033[7;1H LOAD %-16s  PROCS %-4d\033[45G\033[0;36m|\033[0m SH   ", load, total_procs);
		if (sh_pid)
			printf("\033[1;32mactive (PID %d)\033[0m\033[K", sh_pid);
		else
			printf("\033[0;37mready (getty)\033[0m\033[K");

		printf("\033[8;1H ARCH armv7l  2x Cortex-A7 (soft-fp)\033[45G\033[0;36m|\033[0m SD0  ");
		if (has_sd)
			printf("\033[1;32m/mnt/sd (%s, ro)\033[0m\033[K", fs);
		else
			printf("\033[1;33mnot mounted\033[0m\033[K");

		printf("\033[9;1H DISP 720x480 YUV422 Overlay (fb0)\033[45G\033[0;36m|\033[0m WIFI ");
		if (has_ip)
			printf("\033[1;32m%s\033[0m (BCM43455)\033[K", wlan_ip);
		else if (has_wlan)
			printf("\033[1;33mconnecting...\033[0m (BCM43455)\033[K");
		else
			printf("BCM43455 (SD1) standby\033[K");

		printf("\033[10;1H IRQS Timer:%-8lu USB:%-8lu\033[45G\033[0;36m|\033[0m ", irq_tmr, irq_usb);
		if (has_ip)
			printf("TELN \033[1;32mtelnet %s\033[0m\033[K", wlan_ip);
		else
			printf("TOOL python tools/z30_usb_term.py\033[K");

		draw_rule(11);

		/* Rows 12-19: Top processes */
		printf("\033[12;1H \033[1;33m[ TOP PROCESSES ]\033[0m    PID   ST   RSS(KB)   TICKS   COMMAND\033[K");
		for (int i = 0; i < 6; i++) {
			printf("\033[%d;1H", 13 + i);
			if (i < nproc) {
				printf("                     %5d    %c   %7lu   %5lu   %-30s\033[K",
				       procs[i].pid, procs[i].state, procs[i].rss_kb, procs[i].ticks, procs[i].comm);
			} else {
				printf("\033[K");
			}
		}

		draw_rule(19);
		printf("\033[20;1H \033[1;35m[ KERNEL LOG ]\033[0m\033[K");
		show_klog(21, 8);

		/* Row 30: Footer */
		printf("\033[30;1H\033[1;37;40m  Z30-DASH  |  USB Serial 0525:A4A6  |  1s refresh  |  Pull battery to return to Nikon  \033[0m\033[K");

		sleep(1);
	}
	return 0;
}
