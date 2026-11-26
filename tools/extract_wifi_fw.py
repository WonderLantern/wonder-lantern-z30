"""Extract BCM43455 Wi-Fi firmware and Nikon Type 1MW NVRAM from stock Z_30_0120.bin.

Writes:
  <outdir>/brcm/brcmfmac43455-sdio.bin  (STA firmware 7.45.244 from ARM926 fw_slot_1)
  <outdir>/brcm/brcmfmac43455-sdio.txt  (Nikon Type 1MW SAR NVRAM)
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE

ARM926_ROM = 0x11C9E69C
ARM926_LEN = 0x204A00
FW1_OFF = 0xE7974
FW1_END = 0x16C608
NVRAM_OFF = 0x1F1990


def extract(source: Path, outdir: Path):
    eg = extract_eg(source.read_bytes())
    arm926 = eg[ARM926_ROM - BASE : ARM926_ROM - BASE + ARM926_LEN]

    fw = arm926[FW1_OFF:FW1_END]
    assert fw[:4] == b"\x98\xf1\x3e\xb8", "Unexpected BCM43455 firmware header"

    # Parse NUL-separated NVRAM entries
    lines = []
    i = NVRAM_OFF
    while i < len(arm926):
        end = arm926.find(b"\x00", i)
        if end <= i:
            break
        raw = arm926[i:end]
        if not all(32 <= b < 127 for b in raw):
            break
        text = raw.decode("ascii").strip()
        if not lines and "=" not in text:
            lines.append(f"# {text}")
        else:
            lines.append(text)
        i = end + 1
    assert len(lines) >= 100 and any(l.startswith("manfid=0x2d0") for l in lines), "Unexpected NVRAM"
    nvram = ("\n".join(lines) + "\n").encode("ascii")

    brcm = outdir / "brcm"
    brcm.mkdir(parents=True, exist_ok=True)
    fw_path = brcm / "brcmfmac43455-sdio.bin"
    nv_path = brcm / "brcmfmac43455-sdio.txt"
    fw_path.write_bytes(fw)
    nv_path.write_bytes(nvram)
    print(f"Wrote {fw_path} ({len(fw)} bytes, sha256={hashlib.sha256(fw).hexdigest()[:12]})")
    print(f"Wrote {nv_path} ({len(nvram)} bytes, {len(lines)} lines, sha256={hashlib.sha256(nvram).hexdigest()[:12]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=ROOT / "Z_30_0120.bin")
    ap.add_argument("--output", type=Path, default=ROOT / "build" / "linux" / "firmware")
    args = ap.parse_args()
    extract(args.source, args.output)


if __name__ == "__main__":
    main()
