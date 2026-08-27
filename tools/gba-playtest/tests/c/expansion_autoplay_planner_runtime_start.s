    .syntax unified
    .arm

    .section .header, "ax", %progbits
    .global _start
_start:
    b PlannerRuntime_Entry
    .space 0x9C
    .ascii "GPTPLANR"
    .space 4
    .ascii "GPR2"
    .ascii "00"
    .byte 0x96
    .space 0x0D

    .section .text.startup, "ax", %progbits
    .global PlannerRuntime_Entry
PlannerRuntime_Entry:
    ldr sp, =0x03007F00
    bl PlannerRuntime_Main
1:
    b 1b
