CC1 := tools/agbcc/bin/old_agbcc
ALL_OBJECTS := src/sample.o asm/helper.o
OBJECTS_LST := objects.lst

compile:
	iconv -f UTF-8 -t CP932 < input.c | $(CC1)
	arm_compressing_linker.py -t linker_script_banim.txt -b 0x8c02000
