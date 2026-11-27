#include "global.h"
#include "face.h"
#include "bmlib.h"
#include "ctc.h"
#include "rng.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

u16 gObject_32x16[1];
static u8 sMouth[1536];
static size_t sMouthSize;
static int sTransfers;
static int sExpectedByte;

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

void Register2dChrMove(const void *src, void *dst, int width, int height)
{
    const u8 *bytes = src;
    size_t offset = bytes - sMouth;
    size_t length = width * height * 32;
    size_t i;
    (void)dst;

    if (width != 4 || height != 2 || offset + length > sMouthSize)
    {
        fprintf(stderr, "mouth transfer outside generated component: %zu + %zu > %zu\n",
                offset, length, sMouthSize);
        exit(1);
    }

    for (i = 0; i < length; ++i)
        if (bytes[i] != sExpectedByte)
        {
            fprintf(stderr, "wrong mouth frame at %zu\n", offset + i);
            exit(1);
        }

    ++sTransfers;
}

int main(int argc, char **argv)
{
    struct FaceData data = {0};
    struct FaceProc face = {0};
    struct FaceBlinkProc mouth = {0};
    FILE *input;
    int smile;
    int talk;
    int phase;

    if (argc != 2 || !(input = fopen(argv[1], "rb")))
        return 2;
    sMouthSize = fread(sMouth, 1, sizeof(sMouth), input);
    if (ferror(input) || fgetc(input) != EOF)
        return 2;
    fclose(input);

    data.imgMouth = sMouth;
    face.pFaceInfo = &data;
    mouth.pFaceProc = &face;
    for (smile = 0; smile < 2; ++smile)
        for (talk = 0; talk < 3; ++talk)
            for (phase = 0; phase < 4; ++phase)
            {
                face.displayBits = (smile ? FACE_DISP_SMILE : 0)
                    | (talk == 1 ? FACE_DISP_TALK_1 : 0)
                    | (talk == 2 ? FACE_DISP_TALK_2 : 0);
                mouth.blinkControl = phase;
                mouth.unk_32 = 0;
                sExpectedByte = !talk || ((phase + 1) & 3) == 2 ? 0x11 : 0x22;
                sTransfers = 0;
                FaceMouth_Loop(&mouth);
                if (sTransfers != 1)
                    return 1;
            }

    return 0;
}
