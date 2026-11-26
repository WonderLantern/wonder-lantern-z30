// SPDX-License-Identifier: MIT
/* Compact htop-style live system monitor for Nikon Z30 fbcon (90x30, integer-only). */
#include <ctype.h>
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

struct cpu_stat {
	unsigned long long user, nice, sys, idle, iowait, irq, softirq, steal;
};

struct proc_info {
	int pid;
	char state;
	int prio, nice;
	unsigned long vsize_kb, rss_kb;
	unsigned long utime, stime;
	char comm[48];
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

static void draw_bar(int width, int permille, const char *col)
{
	int fill = (permille * width + 500) / 1000;
	if (fill < 0) fill = 0;
	if (fill > width) fill = width;
	printf("\033[1;37m[%s", col);
	for (int i = 0; i < fill; i++)
		putchar('|');
	printf("\033[0;37m");
	for (int i = fill; i < width; i++)
		putchar(' ');
	printf("\033[1;37m]\033[0m");
}

static int cmp_proc(const void *pa, const void *pb)
{
	const struct proc_info *a = pa, *b = pb;
	unsigned long ta = a->utime + a->stime, tb = b->utime + b->stime;
	if (tb != ta)
		return tb > ta ? 1 : -1;
	if (b->rss_kb != a->rss_kb)
		return b->rss_kb > a->rss_kb ? 1 : -1;
	return a->pid - b->pid;
}

static int read_procs(struct proc_info *list, int maxn, int *running)
{
	DIR *d = opendir("/proc");
	struct dirent *de;
	int n = 0;

	*running = 0;
	if (!d)
		return 0;
	while ((de = readdir(d)) != NULL && n < maxn) {
		if (!isdigit((unsigned char)de->d_name[0]))
			continue;
		int pid = atoi(de->d_name);
		char path[64], buf[512];
		snprintf(path, sizeof path, "/proc/%d/stat", pid);
		FILE *f = fopen(path, "r");
		if (!f)
			continue;
		size_t r = fread(buf, 1, sizeof buf - 1, f);
		fclose(f);
		buf[r] = 0;

		char *lp = strchr(buf, '(');
		char *rp = strrchr(buf, ')');
		if (!lp || !rp || rp <= lp)
			continue;
		struct proc_info *p = &list[n];
		memset(p, 0, sizeof *p);
		p->pid = pid;
		size_t clen = (size_t)(rp - lp - 1);
		if (clen >= sizeof p->comm)
			clen = sizeof p->comm - 1;
		memcpy(p->comm, lp + 1, clen);
		p->comm[clen] = 0;

		char st = '?';
		unsigned long ut = 0, stt = 0, vsize = 0;
		long prio = 0, ni = 0, rss = 0;
		/* fields after ') ': 3=state ... 14=utime 15=stime 18=prio 19=nice 23=vsize 24=rss */
		int flds = sscanf(rp + 2,
				  "%c %*d %*d %*d %*d %*d %*u %*u %*u %*u %*u %lu %lu %*d %*d %ld %ld %*d %*d %*u %lu %ld",
				  &st, &ut, &stt, &prio, &ni, &vsize, &rss);
		if (flds < 7)
			continue;
		p->state = st;
		if (st == 'R')
			(*running)++;
		p->utime = ut;
		p->stime = stt;
		p->prio = (int)prio;
		p->nice = (int)ni;
		p->vsize_kb = vsize / 1024;
		p->rss_kb = rss > 0 ? (unsigned long)rss * 4UL : 0;

		/* Prefer /proc/<pid>/cmdline if non-empty. */
		snprintf(path, sizeof path, "/proc/%d/cmdline", pid);
		f = fopen(path, "r");
		if (f) {
			size_t cr = fread(buf, 1, sizeof buf - 1, f);
			fclose(f);
			if (cr > 0) {
				for (size_t i = 0; i + 1 < cr; i++)
					if (buf[i] == 0)
						buf[i] = ' ';
				buf[cr] = 0;
				snprintf(p->comm, sizeof p->comm, "%.47s", buf);
			}
		}
		n++;
	}
	closedir(d);
	qsort(list, (size_t)n, sizeof(*list), cmp_proc);
	return n;
}

static void read_mem(unsigned long *tot_kb, unsigned long *avail_kb)
{
	FILE *f = fopen("/proc/meminfo", "r");
	char line[128];

	*tot_kb = 1;
	*avail_kb = 0;
	if (!f)
		return;
	while (fgets(line, sizeof line, f)) {
		unsigned long v;
		if (sscanf(line, "MemTotal: %lu kB", &v) == 1)
			*tot_kb = v;
		else if (sscanf(line, "MemAvailable: %lu kB", &v) == 1)
			*avail_kb = v;
	}
	fclose(f);
}

int main(int argc, char **argv)
{
	int secs = 0;
	for (int i = 1; i + 1 < argc; i++)
		if (!strcmp(argv[i], "-t"))
			secs = atoi(argv[i + 1]);

	struct timespec t0, now;
	clock_gettime(CLOCK_MONOTONIC, &t0);

	struct cpu_stat prev[3], cur[3];
	read_cpu(prev);

	printf("\033[?25l\033[2J");
	struct proc_info procs[128];

	for (;;) {
		usleep(500000);
		int ncpu = read_cpu(cur);
		int p0 = cpu_pct10(&prev[1], &cur[1]);
		int p1 = ncpu >= 2 ? cpu_pct10(&prev[2], &cur[2]) : 0;
		memcpy(prev, cur, sizeof prev);

		unsigned long tot_kb, avail_kb;
		read_mem(&tot_kb, &avail_kb);
		unsigned long used_kb = tot_kb > avail_kb ? tot_kb - avail_kb : 0;
		int mem_pm = (int)((used_kb * 1000ULL) / (tot_kb ? tot_kb : 1));

		char load[64] = "0.00 0.00 0.00";
		FILE *f = fopen("/proc/loadavg", "r");
		if (f) {
			char a[16], b[16], c[16];
			if (fscanf(f, "%15s %15s %15s", a, b, c) == 3)
				snprintf(load, sizeof load, "%s %s %s", a, b, c);
			fclose(f);
		}
		long up_s = 0;
		f = fopen("/proc/uptime", "r");
		if (f) {
			if (fscanf(f, "%ld", &up_s) != 1)
				up_s = 0;
			fclose(f);
		}

		int running = 0;
		int nproc = read_procs(procs, 128, &running);

		printf("\033[H");
		printf(" \033[1;36m0\033[0m ");
		draw_bar(28, p0, "\033[1;32m");
		printf(" %3d.%d%%   \033[1;36mTasks:\033[0m \033[1;37m%d\033[0m, \033[1;32m%d running\033[0m     \033[K\n",
		       p0 / 10, p0 % 10, nproc, running);
		printf(" \033[1;36m1\033[0m ");
		draw_bar(28, p1, "\033[1;32m");
		printf(" %3d.%d%%   \033[1;36mLoad average:\033[0m %s\033[K\n",
		       p1 / 10, p1 % 10, load);
		printf(" \033[1;36mMem\033[0m");
		draw_bar(27, mem_pm, "\033[1;33m");
		printf(" %luM/%luM  \033[1;36mUptime:\033[0m %02ld:%02ld:%02ld          \033[K\n",
		       used_kb / 1024, tot_kb / 1024, up_s / 3600, (up_s / 60) % 60, up_s % 60);
		printf(" \033[1;36mSwp\033[0m");
		draw_bar(27, 0, "\033[1;31m");
		printf("   0M/0M   \033[1;36mHost:\033[0m Nikon Z30 (ARMv7)   \033[K\n\033[K\n");

		printf("\033[30;42m  PID USER     PRI  NI  VIRT   RES S MEM%%   TIME+   Command                             \033[0m\033[K\n");
		int rows = 21;
		for (int i = 0; i < rows; i++) {
			if (i < nproc) {
				struct proc_info *p = &procs[i];
				int mp = (int)((p->rss_kb * 1000ULL) / (tot_kb ? tot_kb : 1));
				unsigned long cs = p->utime + p->stime;
				const char *stcol = p->state == 'R' ? "\033[1;32m" : "\033[0;37m";
				printf("%5d root    %4d %3d %5luK %4luK %s%c\033[0m %2d.%d%% %2lu:%02lu.%02lu %-30.30s\033[K\n",
				       p->pid, p->prio, p->nice, p->vsize_kb, p->rss_kb,
				       stcol, p->state, mp / 10, mp % 10,
				       cs / 6000, (cs / 100) % 60, cs % 100, p->comm);
			} else {
				printf("\033[K\n");
			}
		}
		printf("\033[30;46mF1\033[0mHelp \033[30;46mF2\033[0mSetup \033[30;46mF3\033[0mSearch \033[30;46mF4\033[0mFilter \033[30;46mF5\033[0mTree \033[30;46mF6\033[0mSort \033[30;46mF9\033[0mKill \033[30;46mF10\033[0mQuit\033[K");
		fflush(stdout);

		if (secs > 0) {
			clock_gettime(CLOCK_MONOTONIC, &now);
			if (now.tv_sec - t0.tv_sec >= secs)
				break;
		}
	}
	printf("\033[0m\033[?25h");
	fflush(stdout);
	return 0;
}
