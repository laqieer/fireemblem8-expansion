. = ALIGN(4); *src/rng.o(.bss);

/* hardware.c*/
. = 0x00000C; gKeyComboResetEN = .;
. = 0x00000D; sModifiedBGs = .;
. = 0x00000E; sModifiedPalette = .;
. = 0x000010; gKeyStatusIgnoredSt = .;

. = 0x000014; gGameClock = .;
. = 0x000018; gUnk_34 = .;
. = 0x000019; gUnk_35 = .;
. = 0x00001A; gSoftwareResetFlag = .;
. = 0x000020; sOamHi = .;
. = 0x000030; sOamLo = .;
. = 0x000040;
. = ALIGN(4); *src/soundwrapper.o(.bss);
. = ALIGN(4); *src/scene.o(.bss);
. = 0x000108; *src/ap.o(.bss); /* sAPArray = .; */
. = 0x000428; gUnk_36 = .;
. = 0x000430; gUnk_37 = .;
. = 0x000434; gLoadedUnitBuffer = .;
. = 0x000438; gEventCallQueue = .;
. = 0x0004B8; gEventSlots = .;
. = 0x0004F0; gEventSlotQueue = .;
. = 0x000568; gEventSlotCounter = .;
. = 0x000570; gEventActiveQueue = .;
. = 0x0005B0; gGenericProc = .;
. = 0x0005D0;
. = ALIGN(4); *src/popup.o(.bss);
. = ALIGN(4); *src/eventscr3.o(.bss);
. = 0x000600; gUnk_38 = .;
. = 0x000700;
. = ALIGN(4); *src/eventfx-fade.o(.bss);
. = ALIGN(4); *src/bmlib.o(.bss);
. = ALIGN(4); *src/bmmap.o(.bss);
. = 0x001780; gDebugChapterModeIndex = .;
. = 0x001788; gUnk_39 = .;
. = 0x0017A0; gDungeonState = .;
. = 0x0017AC; gUnk_40 = .;
. = 0x0017C8;
. = ALIGN(4); *src/cp_script.o(.bss);
. = ALIGN(4); *src/cp_battle.o(.bss);
. = ALIGN(4); *src/sio_core.o(.bss);
. = ALIGN(4); gCurrentKeyInSeqIndex = .;
. = 0x00180C; gTargetKeyInSeqIndex = .;
. = 0x001810; gUnk_41 = .;
. = 0x001818; gUnk_42 = .;
. = 0x00182C; gUnk_43 = .;
. = 0x001830; gUnk_44 = .;
. = 0x001834; gUnk_45 = .;
. = 0x001838; gUnk_46 = .;
. = 0x001840; gUnk_47 = .;
. = 0x001850; gUnk_48 = .;
. = 0x001860; gUnk_49 = .;
. = 0x001864; gUnk_50 = .;
. = 0x001870;
. = ALIGN(4); *src/uimenu.o(.bss);
. = 0x0018F0; gUnk_51 = .;
. = 0x0018F8;
. = ALIGN(4); *src/mu.o(.bss);
. = 0x001A30; gEventREDAs = .;
. = 0x001C34; gUnk_52 = .;
. = 0x001C35; gUnk_53 = .;
. = 0x001C36; gUnk_54 = .;
. = 0x001C38; gUnitDef1 = .;
. = 0x001C48; gUnk_55 = .;
. = 0x001C50; gUnitDef2 = .;
. = 0x001C68; gUnitDefEggHatching = .;
. = 0x001C74; gUnk_56 = .;
. = 0x001C7C; gUnk_57 = .;
. = 0x001C80; gActiveEventRegistry = .;
. = 0x001CC0; gUdefCnt = .;
. = 0x001CC8; gUdefREDAs = .;
. = 0x001CE8; gUdefs = .;
. = 0x001D50; gUnk_58 = .;
. = 0x001DA0;
        . = ALIGN(4); *src/bmshop.o(.bss.gText_GoldBox);
. = 0x001DA8; gUnk_59 = .;
. = 0x001DE8; gUnk_60 = .;
. = 0x001E30; gUnk_61 = .;

. = 0x001E38;
. = ALIGN(4); sound/music_player_table.o(.bss);

. = 0x002A68;
. = ALIGN(4); *src/agb_sram.o(.bss.readSramFast_Work);
        . = ALIGN(4); *src/agb_sram.o(.bss.verifySramFast_Work);
        . = ALIGN(4); *src/agb_sram.o(.bss.verifySramValueFast_Work);

/*
 * The modern VerifySramValueFast workspace reserves 0x94 bytes for the
 * current 0x90-byte routine, leaving exactly 0x44 bytes before the fixed
 * SoundMainRAM buffer. The HQ profile uses that persistent gap for Issue #85's
 * telemetry and controller instead of placing them above the mixer buffer at
 * the user-stack boundary.
 */
. = ALIGN(4);
__expansion_autoplay_hq_iwram_start = .;
*src/expansion_autoplay.o(iwram_data.expansion_autoplay_hq)
__expansion_autoplay_hq_iwram_end = .;
. = ALIGN(4);

/* COMMON */
__hq_mixer_layout_shift = __hq_mixer_enabled * 0x6C0;
__hq_mixer_layout_anchor = 0x006A80;
. = 0x002C60;
        . = ALIGN(4); *src/m4a.o(.bss.code);
PROVIDE(gUnk_62 = __iwram_start + 0x002C61);
. = 0x003060 + __hq_mixer_layout_shift; gUnk_63 = .;
. = 0x003070 + __hq_mixer_layout_shift; gOamLoPutIt = .;
. = 0x003080 + __hq_mixer_layout_shift; gLCDControlBuffer = .;
. = 0x0030D8 + __hq_mixer_layout_shift; gUnk_64 = .;
. = 0x0030F0 + __hq_mixer_layout_shift; gIRQHandlers = .;
. = 0x003128 + __hq_mixer_layout_shift; gUnk_65 = .;
. = 0x00312C + __hq_mixer_layout_shift; gOamAffinePutId = .;
. = 0x003130 + __hq_mixer_layout_shift; gUnk_66 = .;
. = 0x003134 + __hq_mixer_layout_shift; sHBlankHandler1 = .;
. = 0x003140 + __hq_mixer_layout_shift; gOam = .;
. = 0x003740 + __hq_mixer_layout_shift; gUnk_67 = .;
. = 0x003744 + __hq_mixer_layout_shift; gOamHiPutIt = .;
. = 0x003748 + __hq_mixer_layout_shift; sHBlankHandler2 = .;
. = 0x003750 + __hq_mixer_layout_shift; gUnk_68 = .;
. = 0x004150 + __hq_mixer_layout_shift; DecodeStringRAMFunc = .;
. = 0x004154 + __hq_mixer_layout_shift; gUnk_69 = .;
. = 0x004158 + __hq_mixer_layout_shift; gOamAffinePutIt = .;
. = 0x004160 + __hq_mixer_layout_shift; IntrMain_Buffer = .;
. = 0x004960 + __hq_mixer_layout_shift; gUnk_70 = .;
. = 0x004970 + __hq_mixer_layout_shift; gSpriteAllocIt = .;
. = 0x004980 + __hq_mixer_layout_shift; gFaces = .;
. = 0x004990 + __hq_mixer_layout_shift; gpBgVerticalScrollSt = .;
. = 0x0049A0 + __hq_mixer_layout_shift; gWorkingBmMap = .;
. = 0x0049B0 + __hq_mixer_layout_shift; gMovMapFillStPool1 = .;
. = 0x004BB0 + __hq_mixer_layout_shift; gWorkingTerrainMoveCosts = .;
. = 0x004C00 + __hq_mixer_layout_shift; gStatGainSimUnit = .;
. = 0x004C50 + __hq_mixer_layout_shift; gMovMapFillStPool2 = .;
. = 0x004E50 + __hq_mixer_layout_shift; gActiveUnit = .;
. = 0x004E60 + __hq_mixer_layout_shift; gMovMapFillState = .;
. = 0x004E70 + __hq_mixer_layout_shift; gUnk_71 = .;
. = 0x004E74 + __hq_mixer_layout_shift; gUnk_72 = .;
. = 0x004E80 + __hq_mixer_layout_shift; gUnk_73 = .;
. = 0x004E86 + __hq_mixer_layout_shift; gUnk_74 = .;
. = 0x004F08 + __hq_mixer_layout_shift; gSioStateId = .;
. = 0x004F0C + __hq_mixer_layout_shift; gSioMsgBuf = .;
. = 0x004F10 + __hq_mixer_layout_shift; AiDecideMainFunc = .;
. = 0x004F20 + __hq_mixer_layout_shift; gUnk_75 = .;
. = 0x004FA0 + __hq_mixer_layout_shift; gUnk_76 = .;
. = 0x004FA4 + __hq_mixer_layout_shift; gUnk_77 = .;
. = 0x004FA8 + __hq_mixer_layout_shift; gEkrXQuakeOff = .;
. = 0x004FAC + __hq_mixer_layout_shift; gBanimDebugCurrentIndex = .;
. = 0x004FB0 + __hq_mixer_layout_shift; gEkrYQuakeOff = .;
. = 0x004FC0 + __hq_mixer_layout_shift;
. = ALIGN(4); *src/mapanim_debug.o(.bss);
. = ALIGN(4); *src/mapanim_spellassocfx.o(.bss);
. = 0x005250 + __hq_mixer_layout_shift; gPermanentFlagBits = .;
. = 0x005270 + __hq_mixer_layout_shift; gChapterFlagBits = .;
. = 0x005280 + __hq_mixer_layout_shift; gGMData = .;
. = 0x005324 + __hq_mixer_layout_shift; gUnk_78 = .;
. = 0x005349 + __hq_mixer_layout_shift; gUnk_79 = .;
. = 0x00534E + __hq_mixer_layout_shift; gGmMonsterRnState = .;
. = 0x005360 + __hq_mixer_layout_shift; gFontClassChgMenu = .;
. = 0x005380 + __hq_mixer_layout_shift; gFontClassChg = .;
. = 0x005398 + __hq_mixer_layout_shift; gUnk_80 = .;
. = 0x0053A0 + __hq_mixer_layout_shift; gUnk_81 = .;
. = 0x0053E0 + __hq_mixer_layout_shift; gUnk_82 = .;
. = 0x005408 + __hq_mixer_layout_shift; gUnk_83 = .;
. = 0x005410 + __hq_mixer_layout_shift;
        . = ALIGN(4); *src/m4a.o(.bss.gSoundInfo);
. = 0x0063C0 + __hq_mixer_layout_shift;
PROVIDE(gMPlayInfo_SE4_BMP2 = .);
. = (0x006400 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE5_BMP3 = .);
. = (0x006440 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_BGM1 = .);
. = (0x006480 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
        . = ALIGN(4); *src/m4a.o(.bss.gMPlayJumpTable);
PROVIDE(gUnk_84 = __iwram_start + 0x006484 + __hq_mixer_layout_shift);
PROVIDE(gUnk_85 = __iwram_start + 0x006508 + __hq_mixer_layout_shift);
PROVIDE(gUnk_86 = __iwram_start + 0x00650C + __hq_mixer_layout_shift);
. = (0x006510 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
        . = ALIGN(4); *src/m4a.o(.bss.gCgbChans);
. = (0x006610 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE6_BMP4 = .);
. = (0x006650 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_BGM2 = .);
. = (0x006690 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE1_SYS1 = .);
. = (0x0066D0 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE3_BMP1 = .);
. = (0x006710 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
        . = ALIGN(4); *src/m4a.o(.bss.gMPlayMemAccArea);
. = (0x006720 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE7_EVT = .);
. = (0x006760 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
PROVIDE(gMPlayInfo_SE2_SYS2 = .);

/* *src/agb_sram.o(COMMON); */
. = (0x0067A0 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
        . = ALIGN(4); *src/agb_sram.o(.bss.ReadSramFast);
. = (0x0067A4 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
        . = ALIGN(4); *src/agb_sram.o(.bss.VerifySramFast);

. = (0x0067A8 + __hq_mixer_layout_shift) * (1 - __hq_mixer_enabled)
    + __hq_mixer_layout_anchor * __hq_mixer_enabled;
. = ALIGN(4); *libc.a:libc_a-sbrkr.o(COMMON);

. = __hq_mixer_layout_anchor * __hq_mixer_enabled + . * (1 - __hq_mixer_enabled);
        . = ALIGN(4); *src/m4a.o(.bss.hq_mix_buffer);

/*
 * Modern CJK message transforms need their decoded source and derived
 * output at the same time, so this workspace cannot alias sMsgString or
 * gGenericBuffer. It is transient, private to msg.c, and fits below the
 * reserved user/IRQ stacks; English and legacy objects emit no such input
 * section and therefore retain the historical IWRAM layout exactly.
 */
. = ALIGN(4);
__localized_msg_transform_scratch_start = .;
*src/msg.o(iwram_data.localized_msg_transform)
__localized_msg_transform_scratch_end = .;

/*
 * This section always contains sSelectedPolicyState for the selected policy
 * used by regular builds. Dedicated debug test profiles additionally append
 * Issue #58's runtime probe fields.
 */
*src/banim_presentation.o(iwram_data)

/*
 * Issue #85's transient controller and pointer-free telemetry must survive
 * map, UI, and battle-animation overlay switches, but are never serialized.
 * Keep both together in persistent IWRAM rather than consuming the final
 * all-locale EWRAM headroom. The HQ profile consumes its dedicated section
 * in the reclaimed pre-mixer gap above, so this input is empty there.
 */
. = ALIGN(4);
__expansion_autoplay_regular_iwram_start = .;
*src/expansion_autoplay.o(iwram_data)
__expansion_autoplay_regular_iwram_end = .;

/*
 * Issue #89's objective telemetry is likewise transient and reconstructible
 * from chapter/event state. Keep it adjacent to autoplay's probe surfaces.
 */
. = ALIGN(4);
__expansion_chapter_objectives_iwram_start = .;
*src/expansion_chapter_objectives.o(iwram_data)
__expansion_chapter_objectives_iwram_end = .;
