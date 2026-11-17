#!/bin/sh
# Build the AF retarget module payload (position-independent Thumb, no relocations).
# Usage: build.sh OUT.bin [extra cflags]   e.g. build.sh af_retarget.bin
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=$1; shift
tmp=$(mktemp -d)
flags="-mthumb -mcpu=cortex-a7 -mfloat-abi=soft -Os -std=c11 -Wall -Wextra -Werror $* \
  -ffreestanding -fno-builtin -fPIE -fno-common -fno-jump-tables -ffunction-sections \
  -nostdlib -nostartfiles -static -Wl,--no-dynamic-linker -I$here"
arm-none-eabi-gcc $flags -Wl,-T,"$here/module.ld" -o "$tmp/m.elf" "$here/af_retarget.c" -lgcc
if arm-none-eabi-readelf -r "$tmp/m.elf" | grep -q 'R_ARM'; then echo "relocations present" >&2; exit 1; fi
if arm-none-eabi-readelf -S "$tmp/m.elf" | grep -E '\.got|\.plt|\.dynamic'; then echo "GOT/PLT present" >&2; exit 1; fi
arm-none-eabi-objcopy -O binary -j .text "$tmp/m.elf" "$tmp/m.bin"
# Position independence: linked at another origin, the payload must be byte-identical.
sed 's/^  \. = 0;/  . = 0x10000;/' "$here/module.ld" > "$tmp/shift.ld"
arm-none-eabi-gcc $flags -Wl,-T,"$tmp/shift.ld" -o "$tmp/s.elf" "$here/af_retarget.c" -lgcc
arm-none-eabi-objcopy -O binary -j .text "$tmp/s.elf" "$tmp/s.bin"
cmp -s "$tmp/m.bin" "$tmp/s.bin" || { echo "payload is not position independent" >&2; exit 1; }
cp "$tmp/m.bin" "$out"
rm -rf "$tmp"
