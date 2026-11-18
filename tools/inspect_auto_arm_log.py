"""Read an auto-arm log (Z30/ARnnnnnn.BIN) written by modules/auto_arm (SD module builds).

Usage: python tools/inspect_auto_arm_log.py ARnnnnnn.BIN

Each entry is a moment the module pressed OK, or in a dry-run build would
have. The check for a dry run: entries only while in photo live view in
Auto-area AF. A DIAG build also writes a sample about once a second whatever
the state, with the three idle-check words, so a log shows which check kept
the camera from counting as idle. The log holds the camera state values the
module read; no firmware content.
"""

from dataclasses import dataclass
import struct
import sys

MAGIC = b"AUTOARM1"
HEADER = struct.Struct("<8sIIIII4x")
RECORD = struct.Struct("<IBBBBI")
DIAG_RECORDS = (struct.Struct("<IBBBBIIIII"), struct.Struct("<IBBBBIIII"))  # 28 bytes; 24 before photo_sel
AUTO_AREA = 6
SAMPLE_FLAG = 0x2
BOX_EVENT_FLAG = 0x10
DIRECT_CALL_FLAG = 0x20


@dataclass(frozen=True)
class Entry:
    t_ms: int
    phase: int
    area: int
    flags: int
    ui_state: int
    backend: int
    af_busy: int = None
    shown: int = None
    tracking: int = None
    photo_sel: int = None

    @property
    def is_sample(self):
        return self.af_busy is not None and bool(self.flags & SAMPLE_FLAG)

    @property
    def is_box_event(self):
        return self.af_busy is not None and bool(self.flags & BOX_EVENT_FLAG)

    @property
    def in_photo_live_view(self):
        return self.phase in (1, 2) and self.ui_state == 0 and self.backend == 1

    @property
    def expected(self):
        # photo_sel: 0 in photo, 1 in movie on a Z30; None in older logs
        return self.in_photo_live_view and self.area == AUTO_AREA and not self.photo_sel

    def failed_checks(self):
        """The module's idle checks this sample fails (DIAG samples only)."""
        checks = [
            ("phase", self.phase, self.phase not in (1, 2)),
            ("ui_state", self.ui_state, self.ui_state != 0),
            ("backend", self.backend, self.backend != 1),
            ("photo_sel", self.photo_sel, bool(self.photo_sel)),
            ("area", self.area, self.area != AUTO_AREA),
            ("af_busy", self.af_busy, bool(self.af_busy)),
            ("shown", self.shown, bool(self.shown)),
            ("tracking", self.tracking, bool(self.tracking)),
        ]
        return [f"{name}=0x{value:X}" for name, value, failed in checks if failed]


@dataclass(frozen=True)
class Log:
    pressed_ok: bool
    diag: bool
    direct: bool
    presses: int
    ticks: int
    entries: list

    @property
    def presses_logged(self):
        return [e for e in self.entries if not e.is_sample and not e.is_box_event]

    @property
    def samples(self):
        return [e for e in self.entries if e.is_sample]

    @property
    def box_events(self):
        return [e for e in self.entries if e.is_box_event]


def parse(data):
    if len(data) < HEADER.size:
        raise ValueError("file is shorter than the header")
    magic, count, presses, ticks, rec_bytes, flags = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError("not an auto-arm log")
    diag = bool(flags & 2)
    sizes = {r.size: r for r in DIAG_RECORDS} if diag else {RECORD.size: RECORD}
    if rec_bytes not in sizes:
        raise ValueError(f"unexpected record size {rec_bytes}")
    record = sizes[rec_bytes]
    if len(data) != HEADER.size + count * record.size:
        raise ValueError("file length does not match the entry count")
    entries = [Entry(*record.unpack_from(data, HEADER.size + i * record.size)) for i in range(count)]
    return Log(bool(flags & 1), diag, bool(flags & 4), presses, ticks, entries)


def report(log):
    kind = "pressed OK" if log.pressed_ok else "dry run, nothing pressed"
    kind = "called the tracking mode directly" if log.direct else kind
    kind += ", diagnostic" if log.diag else ""
    presses = log.presses_logged
    lines = [f"{kind}; {len(presses)} entries; {log.ticks} ticks (~{log.ticks * 50 / 1000:.0f} s armed)"]
    lines.append("  time s  phase  ui_state  backend  area  result")
    for e in presses:
        sent = ""
        if log.pressed_ok and e.flags & DIRECT_CALL_FLAG:
            sent = " tracking mode called"
        elif log.pressed_ok:
            sent = " OK sent" if e.flags & 1 else ""
            sent += "" if e.flags & 0xC == 0xC else " (writer refused)"
        verdict = "expected" if e.expected else "UNEXPECTED"
        lines.append(f"  {e.t_ms / 1000:6.1f}  {e.phase:5}  {e.ui_state:8}  {e.backend:7}  {e.area:4}  {verdict}{sent}")
    bad = sum(not e.expected for e in presses)
    if bad:
        lines.append(f"{bad} unexpected entries")
    elif presses:
        lines.append("All entries were in photo live view, Auto-area AF.")
    else:
        lines.append("No entries: the module never found the camera idle in photo live view, Auto-area AF.")
    if log.box_events:
        lines.append("\nTracking box changes (with the press before each one that appeared):")
        for e in log.box_events:
            if not e.shown:
                lines.append(f"  {e.t_ms / 1000:6.1f}  box gone")
                continue
            before = [p for p in presses if p.t_ms <= e.t_ms]
            if before:
                last_up = max((b.t_ms for b in log.box_events if b.shown and b.t_ms < e.t_ms), default=-1)
                since = sum(p.t_ms > last_up for p in before)
                lines.append(f"  {e.t_ms / 1000:6.1f}  box up, {(e.t_ms - before[-1].t_ms) / 1000:.2f} s after a press"
                             f" ({since} presses since the last box)")
            else:
                lines.append(f"  {e.t_ms / 1000:6.1f}  box up, no press before it")
    if log.diag:
        lines.append(f"\n{len(log.samples)} samples (about one a second, whatever the state):")
        lines.append("  time s  phase  ui_state  backend  photo  area     af_busy       shown    tracking  failed checks")
        for e in log.samples:
            failed = ", ".join(e.failed_checks()) or "none (idle)"
            photo = "-" if e.photo_sel is None else f"{e.photo_sel}"
            lines.append(
                f"  {e.t_ms / 1000:6.1f}  {e.phase:5}  {e.ui_state:8}  {e.backend:7}  {photo:>5}  {e.area:4}"
                f"  0x{e.af_busy:08X}  0x{e.shown:08X}  0x{e.tracking:08X}  {failed}"
            )
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    with open(argv[0], "rb") as handle:
        log = parse(handle.read())
    print(report(log))
    return 1 if any(not e.expected for e in log.presses_logged) else 0


if __name__ == "__main__":
    sys.exit(main())
