#!/bin/sh
# Build the Linux handoff payload (position-independent Thumb, no relocations).
# Usage: build.sh OUT.bin [extra cflags]
# C code is plain ARMv7-A without an FPU; the MMU-off and exception-context
# parts are ARM code in lowlevel.S.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=$1; shift
tmp=$(mktemp -d)
flags="-mthumb -march=armv7-a -mtune=cortex-a7 -mfloat-abi=soft -O2 -std=c11 -Wall -Wextra -Werror -fno-use-linker-plugin $* \
  -ffreestanding -fno-builtin -fPIE -fno-common -fno-jump-tables -ffunction-sections \
  -nostdlib -nostartfiles -static -Wl,--no-dynamic-linker -I$here"
src="$here/linuxboot.c $here/lowlevel.S"
arm-none-eabi-gcc $flags -Wl,-T,"$here/module.ld" -o "$tmp/m.elf" $src -lgcc
if arm-none-eabi-readelf -r "$tmp/m.elf" | grep -q 'R_ARM'; then echo "relocations present" >&2; exit 1; fi
if arm-none-eabi-readelf -S "$tmp/m.elf" | grep -E '\.got|\.plt|\.dynamic'; then echo "GOT/PLT present" >&2; exit 1; fi
arm-none-eabi-objcopy -O binary -j .text "$tmp/m.elf" "$tmp/m.bin"
# Position independence: linked at another origin, the payload must be byte-identical.
sed 's/^  \. = 0;/  . = 0x10000;/' "$here/module.ld" > "$tmp/shift.ld"
arm-none-eabi-gcc $flags -Wl,-T,"$tmp/shift.ld" -o "$tmp/s.elf" $src -lgcc
arm-none-eabi-objcopy -O binary -j .text "$tmp/s.elf" "$tmp/s.bin"
cmp -s "$tmp/m.bin" "$tmp/s.bin" || { echo "payload is not position independent" >&2; exit 1; }
cp "$tmp/m.bin" "$out"
[ -n "${KEEP_ELF:-}" ] && cp "$tmp/m.elf" "$KEEP_ELF"
rm -rf "$tmp"
