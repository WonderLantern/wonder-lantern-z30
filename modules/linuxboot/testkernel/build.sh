#!/bin/sh
# Build the handoff test kernel: a flat position-independent ARM/Thumb image
# entered like a zImage (entry at offset 0, ARM state). Usage: build.sh OUT.bin
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=$1
tmp=$(mktemp -d)
python "$here/make_font.py" >/dev/null 2>&1 || python3 "$here/make_font.py" >/dev/null
flags="-mthumb -march=armv7-a -mfloat-abi=soft -O2 -std=c11 -Wall -Wextra -Werror \
  -ffreestanding -fno-builtin -fPIE -fno-common -fno-jump-tables -ffunction-sections \
  -nostdlib -nostartfiles -static -Wl,--no-dynamic-linker -I$here"
arm-none-eabi-gcc $flags -Wl,-T,"$here/link.ld" -o "$tmp/k.elf" "$here/entry.S" "$here/main.c" -lgcc
if arm-none-eabi-readelf -r "$tmp/k.elf" | grep -q 'R_ARM'; then echo "relocations present" >&2; exit 1; fi
if arm-none-eabi-readelf -S "$tmp/k.elf" | grep -E '\.got|\.plt|\.dynamic'; then echo "GOT/PLT present" >&2; exit 1; fi
arm-none-eabi-objcopy -O binary -j .text "$tmp/k.elf" "$out"
rm -rf "$tmp"
