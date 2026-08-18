#include "global.h"

#include <stdio.h>
#include <string.h>

#include "localized_game_text.h"
#include "scene.h"
#include "text_utf8.h"

void GetCgTextDimensions(const char *str, u8 *wOut, u8 *hOut);
void GetCgTextBoxDimensions(const char *str, int *wOut, int *hOut);
s8 DoesStringContainTact(const char *str);
int CgText_TestCopyName(char *buffer, u32 capacity, const char **source);
void GetBoxDialogueSize(const char *str, int *wOut, int *hOut);
void DialogBoxGetGlyphLen(const char *str, u8 *xOut);
extern struct TalkState *sTalkState;

static int sFailures;
static int sHelpBoxDrawCallCount;
static int sHelpBoxDrawCallX[6];
static int sHelpBoxDrawCallValue[6];
static const char *sHelpBoxDrawCallText[6];

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

static u32 GetTestTokenWidth(const char *str)
{
    struct TextUtf8Token token;

    TextUtf8_Next(str, &token);
    if (token.kind == TEXT_UTF8_TOKEN_INVALID)
        return 7;
    if (token.kind != TEXT_UTF8_TOKEN_SCALAR)
        return 0;
    if (token.scalar == TEXT_UTF8_LEGACY_SPACE_SCALAR)
        return 6;
    if (token.scalar == 0x3000)
        return 16;
    if (token.scalar < 0x80)
        return 8;
    return 12;
}

const char *GetCharTextLen(const char *str, u32 *width)
{
    struct TextUtf8Token token;
    const char *next;

    next = TextUtf8_Next(str, &token);
    *width = GetTestTokenWidth(str);
    return next;
}

int GetStringTextLen(const char *str)
{
    struct TextUtf8Token token;
    const char *next;
    int width;

    width = 0;
    for (;;)
    {
        next = TextUtf8_Next(str, &token);
        if (token.kind == TEXT_UTF8_TOKEN_END)
            return width;
        if (token.kind == TEXT_UTF8_TOKEN_CONTROL
            && token.control == CHFE_L_NL)
            return width;
        width += GetTestTokenWidth(str);
        str = next;
    }
}

void Text_InsertDrawString(struct Text *text, int x, int colorId, const char *str)
{
    (void)text;
    (void)colorId;
    sHelpBoxDrawCallX[sHelpBoxDrawCallCount] = x;
    sHelpBoxDrawCallValue[sHelpBoxDrawCallCount] = -1;
    sHelpBoxDrawCallText[sHelpBoxDrawCallCount] = str;
    sHelpBoxDrawCallCount++;
}

void Text_InsertDrawNumberOrBlank(struct Text *text, int x, int colorId, int number)
{
    (void)text;
    (void)colorId;
    sHelpBoxDrawCallX[sHelpBoxDrawCallCount] = x;
    sHelpBoxDrawCallValue[sHelpBoxDrawCallCount] = number;
    sHelpBoxDrawCallText[sHelpBoxDrawCallCount] = NULL;
    sHelpBoxDrawCallCount++;
}

char *GetItemDisplayRankString(int item)
{
    (void)item;
    return "D";
}

char *GetItemDisplayRangeString(int item)
{
    (void)item;
    return "\x81\x40\x81\x40\x81\x40" "1";
}

int GetItemWeight(int item)
{
    (void)item;
    return 10;
}

int GetItemMight(int item)
{
    (void)item;
    return 8;
}

int GetItemHit(int item)
{
    (void)item;
    return 75;
}

int GetItemCrit(int item)
{
    (void)item;
    return 0;
}

void SetTextFontGlyphs(int glyphSet)
{
    (void)glyphSet;
}

void NumberToStringAscii(int number, char *buffer)
{
    sprintf(buffer, "%d", number);
}

void Scene_TestGetTalkChoiceLabelLayout(
    int limit,
    int origin,
    int firstWidth,
    int secondWidth,
    int *firstX,
    int *secondX,
    int *step);
void CgText_TestGetYesNoChoiceLabelLayout(
    int limit,
    int firstWidth,
    int secondWidth,
    int *firstX,
    int *secondX,
    int *step);
void DrawHelpBoxWeaponStats(int item);

char *GetTacticianName(void)
{
    return "\xE8\xBB\x8D\xE5\xB8\xAB";
}

static void TestCgDimensionsAndControlCollision(void)
{
    static const char text[] = {
        (char)0xE5, (char)0x80, (char)0x99,
        (char)0x80, (char)0x21,
        (char)0xE8, (char)0xA8, (char)0xBA,
        CHFE_L_NL,
        (char)0xE3, (char)0x80, (char)0x80,
        CHFE_L_A,
        CHFE_L_X
    };
    u8 width;
    u8 height;
    int boxWidth;
    int boxHeight;

    width = 0;
    height = 0;
    GetCgTextDimensions(text, &width, &height);
    CHECK(width == 16);
    CHECK(height == 16);

    GetCgTextBoxDimensions(text, &boxWidth, &boxHeight);
    CHECK(boxWidth == 24);
    CHECK(boxHeight == 32);

    CHECK(DoesStringContainTact(text) == FALSE);
    CHECK(DoesStringContainTact("\xE5\x80\x99\x80\x20") == TRUE);
}

static void TestHelpDimensionsAndGlyphAdvance(void)
{
    static const char text[] = {
        (char)0xE7, (char)0x8C, (char)0xAB,
        (char)0xE3, (char)0x80, (char)0x80,
        CHFE_L_Pause8,
        CHFE_L_NL,
        (char)0xE8, (char)0xA8, (char)0xBA,
        CHFE_L_A,
        CHFE_L_X
    };
    int width;
    int height;
    u8 glyphLen;

    GetBoxDialogueSize(text, &width, &height);
    CHECK(width == 28);
    CHECK(height == 32);

    DialogBoxGetGlyphLen(text, &glyphLen);
    CHECK(glyphLen == 14);
}

static void TestTalkLengthControlsAndSpacing(void)
{
    static const char text[] = {
        (char)0xE7, (char)0x8C, (char)0xAB,
        (char)0xE3, (char)0x80, (char)0x80,
        (char)0x80, (char)0x21,
        CHFE_L_NL,
        (char)0xE8, (char)0xA8, (char)0xBA,
        CHFE_L_A,
        CHFE_L_X
    };
    static const char faceText[] = {
        CHFE_L_OpenFarLeft,
        CHFE_L_LoadFace, (char)0x93, (char)0x94,
        (char)0xE5, (char)0x80, (char)0x99,
        CHFE_L_X
    };
    static const char tactText[] = {
        (char)0x80, (char)0x20,
        CHFE_L_X
    };

    sTalkState->speakingFaceSlot = 0xFF;
    sTalkState->activeFaceSlot = 0xFF;
    CHECK(GetStrTalkLen(text, FALSE) == 28);
    CHECK(GetStrTalkLen(faceText, FALSE) == 24);
    CHECK(GetStrTalkLen(tactText, FALSE) == 24);
}

static void TestVisibleContentAndWidthAwareLayouts(void)
{
    static const char whitespace[] = {
        (char)0xE3, (char)0x80, (char)0x80,
        CHFE_L_Pause8, CHFE_L_X
    };
    static const char visible[] = {
        (char)0xE7, (char)0x8C, (char)0xAB, CHFE_L_X
    };
    int firstX;
    int secondX;
    int step;

    CHECK(TextUtf8_HasVisibleContent(whitespace) == FALSE);
    CHECK(TextUtf8_HasVisibleContent(visible) == TRUE);

    Scene_TestGetTalkChoiceLabelLayout(
        80, 0, 16, 32, &firstX, &secondX, &step);
    CHECK(firstX == 8);
    CHECK(secondX == 48);
    CHECK(secondX + 32 <= 80);
    CHECK(step == 40);

    CgText_TestGetYesNoChoiceLabelLayout(
        72, 16, 32, &firstX, &secondX, &step);
    CHECK(firstX == 0);
    CHECK(secondX == 40);
    CHECK(secondX + 32 <= 72);
}

static void TestWeaponHelpBoxFixedValueSlots(void)
{
    sHelpBoxDrawCallCount = 0;

    DrawHelpBoxWeaponStats(0);

    CHECK(sHelpBoxDrawCallCount == 6);
    CHECK(sHelpBoxDrawCallX[0] == 32);
    CHECK(strcmp(sHelpBoxDrawCallText[0], "D") == 0);
    CHECK(sHelpBoxDrawCallX[1] == 67);
    CHECK(strcmp(sHelpBoxDrawCallText[1], "\x81\x40\x81\x40\x81\x40" "1") == 0);
    CHECK(sHelpBoxDrawCallX[2] == 129);
    CHECK(sHelpBoxDrawCallValue[2] == 10);
    CHECK(sHelpBoxDrawCallX[3] == 32);
    CHECK(sHelpBoxDrawCallValue[3] == 8);
    CHECK(sHelpBoxDrawCallX[4] == 81);
    CHECK(sHelpBoxDrawCallValue[4] == 75);
    CHECK(sHelpBoxDrawCallX[5] == 129);
    CHECK(sHelpBoxDrawCallValue[5] == 0);
}

static void TestNameCopyBounds(void)
{
    static const char name[] = {
        (char)0xE5, (char)0x80, (char)0x99,
        (char)0x80, (char)0x21,
        (char)0xE7, (char)0x8C, (char)0xAB,
        CHFE_L_NL,
        'Z',
        CHFE_L_X
    };
    static const char expected[] = {
        (char)0xE5, (char)0x80, (char)0x99,
        (char)0x80, (char)0x21,
        (char)0xE7, (char)0x8C, (char)0xAB,
        0
    };
    const char *cursor;
    u8 guarded[14];
    static const char malformed[] = {(char)0xE8, 0};

    memset(guarded, 0xA5, sizeof(guarded));
    cursor = name;
    CHECK(CgText_TestCopyName(
        (char *)(guarded + 1), 9, &cursor) == TRUE);
    CHECK(memcmp(guarded + 1, expected, sizeof(expected)) == 0);
    CHECK(*cursor == 'Z');
    CHECK(guarded[0] == 0xA5);
    CHECK(guarded[10] == 0xA5);

    memset(guarded, 0xA5, sizeof(guarded));
    cursor = name;
    CHECK(CgText_TestCopyName(
        (char *)(guarded + 1), 8, &cursor) == FALSE);
    CHECK(strcmp((char *)(guarded + 1), "<!LOC_O") == 0);
    CHECK(*cursor == 'Z');
    CHECK(guarded[0] == 0xA5);
    CHECK(guarded[9] == 0xA5);

    memset(guarded, 0xA5, sizeof(guarded));
    cursor = malformed;
    CHECK(CgText_TestCopyName(
        (char *)(guarded + 1), 12, &cursor) == FALSE);
    CHECK(strcmp(
        (char *)(guarded + 1), LOCALIZED_GAME_TEXT_MARKER_CORRUPT) == 0);
    CHECK(guarded[0] == 0xA5);
    CHECK(guarded[13] == 0xA5);
}

int main(void)
{
    TestCgDimensionsAndControlCollision();
    TestHelpDimensionsAndGlyphAdvance();
    TestTalkLengthControlsAndSpacing();
    TestVisibleContentAndWidthAwareLayouts();
    TestWeaponHelpBoxFixedValueSlots();
    TestNameCopyBounds();

    if (sFailures == 0)
    {
        puts("text_consumer_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
