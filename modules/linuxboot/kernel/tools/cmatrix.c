// SPDX-License-Identifier: MIT
/* Matrix digital rain that resolves into "WONDER LANTERN" on Nikon Z30 fbcon (90x30). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define COLS 89
#define ROWS 29

static uint32_t rng_state = 0x13579bdfu;
static uint32_t xrand(void)
{
	uint32_t x = rng_state;
	x ^= x << 13;
	x ^= x >> 17;
	x ^= x << 5;
	rng_state = x;
	return x;
}

static char glyph(void)
{
	static const char tbl[] =
		"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz@#$%&*+=<>/?:;~";
	return tbl[xrand() % (sizeof(tbl) - 1)];
}

/* 5x5 bitmap font for the letters in WONDER LANTERN. */
struct letter_bmp {
	char ch;
	const char *rows[5];
};

static const struct letter_bmp LETTERS[] = {
	{'W', {"#...#", "#...#", "#.#.#", "##.##", "#...#"}},
	{'O', {".###.", "#...#", "#...#", "#...#", ".###."}},
	{'N', {"#...#", "##..#", "#.#.#", "#..##", "#...#"}},
	{'D', {"####.", "#...#", "#...#", "#...#", "####."}},
	{'E', {"#####", "#....", "####.", "#....", "#####"}},
	{'R', {"####.", "#...#", "####.", "#..#.", "#...#"}},
	{'L', {"#....", "#....", "#....", "#....", "#####"}},
	{'A', {".###.", "#...#", "#####", "#...#", "#...#"}},
	{'T', {"#####", "..#..", "..#..", "..#..", "..#.."}},
};

static const char *const *find_letter(char ch)
{
	for (size_t i = 0; i < sizeof(LETTERS) / sizeof(LETTERS[0]); i++)
		if (LETTERS[i].ch == ch)
			return LETTERS[i].rows;
	return NULL;
}

/* target[r][c]: 0 = background, 1 = big logo block, 2 = subtitle char (stored in subch) */
static uint8_t target[ROWS][COLS];
static char subch[ROWS][COLS];

static void stamp_word(const char *word, int top_row)
{
	int n = (int)strlen(word);
	/* Each letter is 5 bitmap cols * 2 chars = 10 chars wide, with 1-char gap. */
	int total_w = n * 10 + (n - 1);
	int left = (COLS - total_w) / 2;

	for (int i = 0; i < n; i++) {
		const char *const *rows = find_letter(word[i]);
		if (!rows)
			continue;
		int x0 = left + i * 11;
		for (int r = 0; r < 5; r++) {
			for (int c = 0; c < 5; c++) {
				if (rows[r][c] == '#') {
					int y = top_row + r;
					int x = x0 + c * 2;
					if (y >= 0 && y < ROWS && x >= 0 && x + 1 < COLS) {
						target[y][x] = 1;
						target[y][x + 1] = 1;
					}
				}
			}
		}
	}
}

static void stamp_subtitle(const char *text, int row)
{
	int n = (int)strlen(text);
	int left = (COLS - n) / 2;

	for (int i = 0; i < n; i++) {
		if (text[i] == ' ')
			continue;
		int x = left + i;
		if (row >= 0 && row < ROWS && x >= 0 && x < COLS) {
			target[row][x] = 2;
			subch[row][x] = text[i];
		}
	}
}

static long elapsed_ms(const struct timespec *t0)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	return (now.tv_sec - t0->tv_sec) * 1000L + (now.tv_nsec - t0->tv_nsec) / 1000000L;
}

int main(int argc, char **argv)
{
	int secs = 12;
	for (int i = 1; i + 1 < argc; i++)
		if (!strcmp(argv[i], "-t"))
			secs = atoi(argv[i + 1]);
	if (secs <= 0)
		secs = 12;

	memset(target, 0, sizeof target);
	memset(subch, 0, sizeof subch);
	stamp_word("WONDER", 7);
	stamp_word("LANTERN", 14);
	stamp_subtitle("N I K O N   Z 3 0   //   L I N U X   6 . 1 8", 22);

	struct timespec t0;
	clock_gettime(CLOCK_MONOTONIC, &t0);
	rng_state ^= (uint32_t)t0.tv_nsec ^ (uint32_t)t0.tv_sec;

	int head[COLS], len[COLS], speed[COLS], tick[COLS];
	char grid[ROWS][COLS], locked_ch[ROWS][COLS];
	uint8_t locked[ROWS][COLS]; /* 0 = unlocked, 1..4 = flash countdown -> settled */
	memset(grid, ' ', sizeof grid);
	memset(locked, 0, sizeof locked);
	memset(locked_ch, ' ', sizeof locked_ch);

	for (int c = 0; c < COLS; c++) {
		head[c] = -(int)(xrand() % (ROWS * 2));
		len[c] = 5 + (int)(xrand() % 13);
		speed[c] = 1 + (int)(xrand() % 2);
		tick[c] = 0;
	}

	/* Timeline scaled to secs (default 12s):
	 *   0%..30%   : pure Matrix rain
	 *   30%..68%  : raindrops lock onto WONDER LANTERN cells
	 *   68%..100% : background rain drains away, leaving glowing WONDER LANTERN */
	long total_ms = (long)secs * 1000L;
	long lock_start_ms = (total_ms * 30) / 100;
	long drain_start_ms = (total_ms * 65) / 100;
	long force_done_ms = (total_ms * 75) / 100;

	printf("\033[?25l\033[2J");
	for (;;) {
		long ms = elapsed_ms(&t0);
		int lock_phase = ms >= lock_start_ms;
		int drain_phase = ms >= drain_start_ms;
		int force_phase = ms >= force_done_ms;

		for (int c = 0; c < COLS; c++) {
			if (++tick[c] < speed[c])
				continue;
			tick[c] = 0;
			head[c]++;
			if (head[c] >= 0 && head[c] < ROWS) {
				char g = glyph();
				grid[head[c]][c] = g;
				if (lock_phase && target[head[c]][c] && !locked[head[c]][c]) {
					locked[head[c]][c] = 4; /* white flash then settle */
					locked_ch[head[c]][c] = target[head[c]][c] == 2 ? subch[head[c]][c] : g;
				}
			}
			int tail = head[c] - len[c];
			if (tail >= 0 && tail < ROWS)
				grid[tail][c] = ' ';
			if (tail >= ROWS) {
				if (!drain_phase) {
					head[c] = -(int)(xrand() % 14);
					len[c] = 5 + (int)(xrand() % 13);
					speed[c] = 1 + (int)(xrand() % 2);
				} else {
					head[c] = ROWS + len[c] + 5;
				}
			}
		}

		/* Ensure every remaining target pixel locks smoothly before the final hold. */
		if (drain_phase) {
			for (int r = 0; r < ROWS; r++) {
				for (int c = 0; c < COLS; c++) {
					if (target[r][c] && !locked[r][c] &&
					    (force_phase || (xrand() % 6) == 0)) {
						locked[r][c] = 4;
						locked_ch[r][c] = target[r][c] == 2 ? subch[r][c] : glyph();
					}
				}
			}
		}

		printf("\033[H");
		for (int r = 0; r < ROWS; r++) {
			int last_col = -1;
			for (int c = 0; c < COLS; c++) {
				if (locked[r][c]) {
					uint8_t st = locked[r][c];
					if (st > 1)
						locked[r][c] = (uint8_t)(st - 1);
					if (target[r][c] == 2) {
						/* Subtitle: flash white, settle bold cyan */
						int col = st > 1 ? 1 : 5;
						if (last_col != col) {
							printf(col == 1 ? "\033[0;1;37m" : "\033[0;1;36m");
							last_col = col;
						}
						putchar(locked_ch[r][c]);
					} else {
						/* Main logo: flash white-on-green, settle bright green block with glyph */
						int col = st > 1 ? 6 : 4;
						if (last_col != col) {
							printf(col == 6 ? "\033[0;1;37;42m" : "\033[0;30;42m");
							last_col = col;
						}
						putchar(locked_ch[r][c]);
					}
					continue;
				}

				char ch = grid[r][c];
				if (ch == ' ') {
					if (last_col != 0) {
						printf("\033[0m");
						last_col = 0;
					}
					putchar(' ');
				} else if (r == head[c]) {
					if (last_col != 1) {
						printf("\033[0;1;37m");
						last_col = 1;
					}
					putchar(ch);
				} else if (head[c] - r < 4) {
					if (last_col != 2) {
						printf("\033[0;1;32m");
						last_col = 2;
					}
					putchar(ch);
				} else {
					if (last_col != 3) {
						printf("\033[0;32m");
						last_col = 3;
					}
					putchar(ch);
				}
			}
			if (r + 1 < ROWS)
				putchar('\n');
		}
		fflush(stdout);
		usleep(65000);

		if (ms >= total_ms)
			break;
	}
	printf("\033[0m\033[?25h");
	fflush(stdout);
	return 0;
}
