/* Research ZMOD layout: one flat position-independent blob, entry at offset 0. */
ENTRY(module_init)
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
    . = ALIGN(4);
  }
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) *(.note*) }
}
