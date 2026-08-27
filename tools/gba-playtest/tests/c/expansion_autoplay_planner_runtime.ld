ENTRY(_start)

SECTIONS
{
    . = 0x08000000;
    .text :
    {
        KEEP(*(.header))
        *(.text.startup)
        *(.text*)
        *(.rodata*)
    }

    .ewram 0x02020000 (NOLOAD) :
    {
        __ewram_start = .;
        *(ewram_data*)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __ewram_end = .;
    }
}
