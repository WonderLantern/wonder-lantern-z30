/* Test kernel: one flat block, entry at 0, 16 KiB stack at the end. */
ENTRY(_start)
SECTIONS
{
  . = 0;
  .text : {
    KEEP(*(.text.entry))
    *(.text .text.*)
    *(.rodata .rodata.*)
    *(.data .data.*)
    . = ALIGN(8);
    *(.bss .bss.* COMMON)
    . = ALIGN(8);
    . = . + 16384;
    tk_stack_top = .;
  }
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) *(.note*) }
}
