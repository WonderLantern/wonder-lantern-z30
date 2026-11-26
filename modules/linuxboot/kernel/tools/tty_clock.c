// SPDX-License-Identifier: MIT
/* Compact tty-clock big-digit clock for Nikon Z30 fbcon (90x30). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* 10 digits, each 5 rows x 3 cols (rendered as 2 chars per block = 6 cols wide). */
static const unsigned char FONT[10][5] = {
	{7, 5, 5, 5, 7}, /* 0 */
	{2, 2, 2, 2, 2}, /* 1 */
	{7, 1, 7, 4, 7}, /* 2 */
	{7, 1, 7, 1, 7}, /* 3 */
	{5, 5, 7, 1, 1}, /* 4 */
	{7, 4, 7, 1, 7}, /* 5 */
	{7, 4, 7, 5, 7}, /* 6 */
	{7, 1, 1, 1, 1}, /* 7 */
	{7, 5, 7, 5, 7}, /* 8 */
	{7, 5, 7, 1, 7}, /* 9 */
};

static void print_row(int r, int h, int m, int s, int blink)
{
	int d[6] = {h / 10, h % 10, m / 10, m % 10, s / 10, s % 10};

	printf("                ");
	for (int i = 0; i < 6; i++) {
		unsigned char bits = FONT[d[i]][r];
		for (int b = 2; b >= 0; b--) {
			if (bits & (1u << b))
				printf("\033[42m  \033[0m");
			else
				printf("  ");
		}
		printf("  ");
		if (i == 1 || i == 3) {
			if (blink && (r == 1 || r == 3))
				printf("\033[42m  \033[0m  ");
			else
				printf("    ");
		}
	}
	printf("\033[K\n");
}

int main(int argc, char **argv)
{
	int secs = 0;
	for (int i = 1; i + 1 < argc; i++)
		if (!strcmp(argv[i], "-t"))
			secs = atoi(argv[i + 1]);

	struct timespec t0, now;
	clock_gettime(CLOCK_MONOTONIC, &t0);

	printf("\033[?25l\033[2J");
	for (;;) {
		clock_gettime(CLOCK_MONOTONIC, &now);
		long up = now.tv_sec;
		int hh = (int)((up / 3600) % 24);
		int mm = (int)((up / 60) % 60);
		int ss = (int)(up % 60);

		printf("\033[10;1H");
		for (int r = 0; r < 5; r++)
			print_row(r, hh, mm, ss, 1);

		printf("\n                         \033[1;32mNikon Z30 Linux  |  UPTIME %02d:%02d:%02d\033[0m\033[K\n",
		       hh, mm, ss);
		printf("                         \033[0;36mWonder Lantern  |  tty-clock (2x Cortex-A7)\033[0m\033[K\n");
		fflush(stdout);

		if (secs > 0 && now.tv_sec - t0.tv_sec >= secs)
			break;
		usleep(500000);
	}
	printf("\033[0m\033[?25h");
	fflush(stdout);
	return 0;
}
