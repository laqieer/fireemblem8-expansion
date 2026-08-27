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

    /DISCARD/ :
    {
        /* The fixture links three production functions, not their unrelated
         * event-dispatch and map-effect data tables. */
        *planner-production-event.o(.data* .rodata* .bss*)
        *planner-production-eventscr.o(.data* .rodata* .bss*)
        *planner-production-bmio.o(.data* .rodata* .bss*)
    }
}
