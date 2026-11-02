.syntax unified
.arm
.section .text.boot, "ax", %progbits
.global crt0
.type crt0, %function
crt0:
    mov r0, #0xd2
    msr cpsr_c, r0
    ldr sp, =0x03007fa0
    mov r0, #0xdf
    msr cpsr_c, r0
    ldr sp, =0x03007f00
    mov r2, #0
    ldr r0, =0x02000000
    ldr r1, =0x02040000
1:
    str r2, [r0], #4
    cmp r0, r1
    blo 1b
    ldr r0, =0x03000000
    ldr r1, =0x03007800
2:
    str r2, [r0], #4
    cmp r0, r1
    blo 2b
    ldr r0, =__data_load
    ldr r1, =__data_start
    ldr r2, =__data_end
3:
    cmp r1, r2
    bhs 4f
    ldr r3, [r0], #4
    str r3, [r1], #4
    b 3b
4:
    ldr r0, =AgbMain
    bx r0
.size crt0, . - crt0
.ltorg
