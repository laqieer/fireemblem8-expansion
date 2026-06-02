.section .data

	.global ProcScr_ManimShiftingSineWaveScanlineBuf
ProcScr_ManimShiftingSineWaveScanlineBuf:  @ 0x089A52FC
        @ PROC_CALL
        .short 0x2, 0x0
        .word sub_80825B0
        @ PROC_REPEAT
        .short 0x3, 0x0
        .word sub_80825B8
        @ PROC_END
        .short 0x0, 0x0
        .word 0x0
