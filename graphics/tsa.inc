@ Tile-screen arrangement (TSA) entry macro.
@
@ A TSA is an array of u16 GBA BG screen entries. Each entry packs:
@   bits  0-9   tile index
@   bit   10    horizontal flip
@   bit   11    vertical flip
@   bits 12-15  palette bank
	.macro tsa_entry tile, pal=0, hflip=0, vflip=0
	.2byte ((\pal) << 12) | ((\vflip) << 11) | ((\hflip) << 10) | (\tile)
	.endm
