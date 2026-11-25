"""Replay the af_handover rule on a camera log (AFLOG3 or AFHAND1-7) without the camera.

Usage: replay_handover.py LOG.BIN
replay() mirrors the v7 rule of research/af/modules/af_handover/af_handover.c (one log
tick stands for one detection update); replay_v5() the old distance rule
(SCALE, HOLD, MISS, REARM, the reach and the detection test) (an AFHAND log holds only the tracking
ticks: gaps are replayed as 50 ms ticks without tracking) (SCALE, HOLD, MISS, REARM, the reach
and the detection test) and prints when OK would be pressed.
"""
import struct, sys

SCALE, HOLD, MISS, REARM = 106, 5, 10, 20


def ticks_from(path):
    d = open(path, 'rb').read()
    if d[:6] == b'AFHAND':
        count, _, _, rb = struct.unpack_from('<4I', d, 8)
        last = None
        for i in range(count):
            t, tr, n, st, pr, b0, b1, fx, fy, fw, fh, dx, dy = struct.unpack_from('<IBBBB2I6i', d, 32 + rb * i)
            # only tracking ticks are logged: a gap stands for ticks (50 ms) without tracking
            # (the camera's IMU tick is 50-90 ms, so only a gap over 0.5 s counts)
            if last is not None and t - last > 500:
                for _ in range(round((t - last) / 50) - 1):
                    yield last / 1000, False, 0, 0, 0, 0, (0, 0, 0, 0)
            last = t
            st = 0x101 if (d[6:7] == b'1' and st == 255) else ((st & 0x7F) | (0x100 if st & 0x80 and d[6:7] in (b'2', b'3', b'4', b'5', b'6', b'7') else 0))
            yield t / 1000, tr, n, st, b0, b1, (fx, fy, fw, fh)
    else:
        sys.path.insert(0, __file__.rsplit('/', 1)[0])
        from parse_aflog import load, word
        W, recs, _, _ = load(path)
        for (t, _), r in recs:
            yield (t / 1000, word(W, r, 0xC237F688) != 0, word(W, r, 0xC237E3E4), word(W, r, 0xC237E2C0) & 0xFFFFFFFF,
                   word(W, r, 0xC237D59C) & 0xFFFFFFFF, word(W, r, 0xC237D5A0) & 0xFFFFFFFF,
                   tuple(word(W, r, 0xC237F624 + 4 * i) for i in range(4)))


def replay(path, rule=7):
    """rule 7 (current): tracking first, subject later; rule 5: the old distance rule."""
    if rule == 5:
        return replay_v5(path)
    hold, miss_max, clear, rearm = 3, 10, 10, 10
    seen_run = miss = 0; off = rearm; done = False; session = blocked = False; clear_run = 0; presses = []
    for t, tracking, n, st, b0, b1, face in ticks_from(path):
        if not tracking:
            off = min(off + 1, rearm)
            if off >= rearm: done = False
            session = False; seen_run = miss = 0
            continue
        off = 0
        seen = 0 < n <= 5
        if not session:
            session, blocked, clear_run, seen_run, miss = True, seen, 0, 0, 0
        if blocked:
            clear_run = 0 if seen else clear_run + 1
            if clear_run >= clear: blocked = False
        elif seen: seen_run += 1; miss = 0
        else:
            miss += 1
            if miss >= miss_max: seen_run = miss = 0
        if not done and not blocked and seen_run >= hold:
            presses.append(round(t, 2)); done = True
    return presses


def replay_v5(path):
    score = miss = 0; off = REARM; done = False; presses = []
    for t, tracking, n, st, b0, b1, (fx, fy, fw, fh) in ticks_from(path):
        if not tracking:
            off = min(off + 1, REARM)
            if off >= REARM: done = False
            score = miss = 0
            continue
        off = 0
        x0, x1, y0 = b0 >> 16, b1 >> 16, b1 & 0xFFFF
        size = x1 - x0
        dx, dy = fx * SCALE - (x0 + size // 2), fy * SCALE - (y0 + size // 2)
        reach = size + size // 2 + fw * SCALE // 2
        detected = 0 < n <= 5 and (st & 0xFF) <= 2 and 0 < fw < 64 and 0 < size < 4000
        near = detected and abs(dx) <= reach and abs(dy) <= reach
        if near: score += 1; miss = 0
        elif detected: score = miss = 0
        else:
            miss += 1
            if miss >= MISS: score = miss = 0
        if not done and score >= HOLD:
            presses.append(round(t, 2)); done = True
    return presses


if __name__ == '__main__':
    print('OK would be pressed at', replay(sys.argv[1]), '(v7 rule; v5 distance rule:', replay(sys.argv[1], 5), ')')
