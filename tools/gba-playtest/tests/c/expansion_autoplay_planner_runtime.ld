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

    .ewram 0x02000000 (NOLOAD) :
    {
        *(ewram_data*)
        *(.bss*)
        *(COMMON)
    }
}
