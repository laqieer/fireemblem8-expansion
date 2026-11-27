#include "global.h"
#include "face.h"
#include "bmlib.h"
#include "hardware.h"
#include "ctc.h"
#include "rng.h"

extern u8 portrait_Proof_mouth[];
extern u8 portrait_Proof_mouth_end[];

u16 gObject_32x16[1];
u8 portrait_Mystery_1_tileset[1];
u8 portrait_Mystery_1_chibi[1];
u8 portrait_Mystery_1_mouth[1];
u16 portrait_Mystery_1_palette[1];
volatile u32 gMouthRuntimeResults[4];
static struct FaceProc sFace;
static struct FaceBlinkProc sMouth;

unsigned AdvanceGetLCGRNValue(void)
{
    return 0;
}

void PutSpriteExt(int layer, int xOam1, int yOam0, const u16 *object, int oam2)
{
    (void)layer;
    (void)xOam1;
    (void)yOam0;
    (void)object;
    (void)oam2;
}

void RegisterDataMove(const void *src, void *dst, int size)
{
    const u16 *from = src;
    volatile u16 *to = dst;
    int i;

    if ((u32)src < (u32)portrait_Proof_mouth
        || (u32)src + size > (u32)portrait_Proof_mouth_end)
    {
        ++gMouthRuntimeResults[1];
        return;
    }

    for (i = 0; i < size / 2; ++i)
        to[i] = from[i];
}

void PlannerRuntime_Main(void)
{
    volatile u16 *vram = (volatile u16 *)0x06010380;
    int smile;
    int talk;
    int phase;
    int row;
    int i;

    sFace.pFaceInfo = GetPortraitData(1);
    sMouth.pFaceProc = &sFace;
    for (smile = 0; smile < 2; ++smile)
        for (talk = 0; talk < 3; ++talk)
            for (phase = 0; phase < 4; ++phase)
            {
                u16 expected = !talk || ((phase + 1) & 3) == 2 ? 0x1111 : 0x2222;
                sFace.displayBits = (smile ? FACE_DISP_SMILE : 0)
                    | (talk == 1 ? FACE_DISP_TALK_1 : 0)
                    | (talk == 2 ? FACE_DISP_TALK_2 : 0);
                sMouth.blinkControl = phase;
                sMouth.unk_32 = 0;
                FaceMouth_Loop(&sMouth);
                for (row = 0; row < 2; ++row)
                    for (i = 0; i < 64; ++i)
                        if (vram[row * 512 + i] != expected)
                            ++gMouthRuntimeResults[2];
                ++gMouthRuntimeResults[0];
            }
    gMouthRuntimeResults[3] = 1;
}
