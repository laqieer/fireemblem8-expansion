    .section .data

	.global gGfx_PlayerInterfaceFontTiles
gGfx_PlayerInterfaceFontTiles:  @ 0x08A167C8
	.incbin "graphics/player_interface/player_interface_font_tiles.4bpp.lz"

	.global gGfx_PlayerInterfaceNumbers
gGfx_PlayerInterfaceNumbers:  @ 0x08A16D6C
	.incbin "graphics/player_interface/player_interface_numbers.4bpp.lz"

	.global gGfx_StatusText
gGfx_StatusText:  @ 0x08A16DEC
	.incbin "graphics/player_interface/status_text.4bpp"

	.global gPal_PlayerInterface_Blue
gPal_PlayerInterface_Blue:  @ 0x08A1738C
	.incbin "graphics/player_interface/player_interface_blue.gbapal"

	.global gPal_PlayerInterface_Red
gPal_PlayerInterface_Red:  @ 0x08A173AC
	.incbin "graphics/player_interface/player_interface_red.gbapal"

	.global gPal_PlayerInterface_Green
gPal_PlayerInterface_Green:  @ 0x08A173CC
	.incbin "graphics/player_interface/player_interface_green.gbapal"

	.global gUnknown_08A173EC
gUnknown_08A173EC:  @ 0x08A173EC
	.incbin "graphics/player_interface/gUnknown_08A173EC.gbapal"

	.global gUnknown_08A1740C
gUnknown_08A1740C:  @ 0x08A1740C
	.incbin "graphics/player_interface/gUnknown_08A1740C.gbapal"

	.global gTSA_TerrainBox
gTSA_TerrainBox:  @ 0x08A1742C
	.incbin "graphics/misc/gTSA_TerrainBox.tsa.bin"

	.global gTSA_MinimugBox
gTSA_MinimugBox:  @ 0x08A17484
	.incbin "graphics/misc/gTSA_MinimugBox.tsa.bin"

	.global gUnknown_08A175B4
gUnknown_08A175B4:  @ 0x08A175B4
	.incbin "graphics/misc/gUnknown_08A175B4.tsa.bin"

	.global gUnknown_08A175C8
gUnknown_08A175C8:  @ 0x08A175C8
	.incbin "graphics/misc/gUnknown_08A175C8.tsa.bin"

	.global gUnknown_08A175DC
gUnknown_08A175DC:  @ 0x08A175DC
	.incbin "graphics/misc/gUnknown_08A175DC.tsa.bin"

	.global gUnknown_08A175F0
gUnknown_08A175F0:  @ 0x08A175F0
	.incbin "graphics/misc/gUnknown_08A175F0.tsa.bin"

	.global gUnknown_08A17604
gUnknown_08A17604:  @ 0x08A17604
	.incbin "graphics/misc/gUnknown_08A17604.tsa.bin"

	.global gUnknown_08A1763C
gUnknown_08A1763C:  @ 0x08A1763C
	.incbin "graphics/misc/gUnknown_08A1763C.tsa.bin"

	.global gUnknown_08A17650
gUnknown_08A17650:  @ 0x08A17650
	.incbin "graphics/misc/gUnknown_08A17650.tsa.bin"

	.global gUnknown_08A17664
gUnknown_08A17664:  @ 0x08A17664
	.incbin "graphics/misc/gUnknown_08A17664.tsa.bin"

	.global gUnknown_08A17678
gUnknown_08A17678:  @ 0x08A17678
	.incbin "graphics/misc/gUnknown_08A17678.tsa.bin"

	.global Tsa_TerrainMapUi_Labels
Tsa_TerrainMapUi_Labels:  @ 0x08A1768C
	.incbin "graphics/misc/Tsa_TerrainMapUi_Labels.tsa.bin"

	.global gTSA_TerrainBox_Ballistae
gTSA_TerrainBox_Ballistae:  @ 0x08A1769C
	.incbin "graphics/misc/gTSA_TerrainBox_Ballistae.tsa.bin"

	.global Tsa_TerrainMapUi_ObstacleLabels
Tsa_TerrainMapUi_ObstacleLabels:  @ 0x08A176A4
	.incbin "graphics/misc/Tsa_TerrainMapUi_ObstacleLabels.tsa.bin"

	.global Tsa_TerrainMapUi_ObstacleFullHp
Tsa_TerrainMapUi_ObstacleFullHp:  @ 0x08A176B4
	.incbin "graphics/misc/Tsa_TerrainMapUi_ObstacleFullHp.tsa.bin"

	.global gTSA_GoalBox_TwoLines
gTSA_GoalBox_TwoLines:  @ 0x08A176BC
	.incbin "graphics/misc/gTSA_GoalBox_TwoLines.tsa.bin"

	.global gTSA_GoalBox_OneLine
gTSA_GoalBox_OneLine:  @ 0x08A17744
	.incbin "graphics/misc/gTSA_GoalBox_OneLine.tsa.bin"
