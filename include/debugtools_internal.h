#ifndef GUARD_DEBUGTOOLS_INTERNAL_H
#define GUARD_DEBUGTOOLS_INTERNAL_H

#include "proc.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action);
void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu);
int DebugTools_IsMenuTransitionScheduled(void);
void DebugTools_RunMenuTransition(ProcPtr proc);

enum DebugToolsLaunchTargetKind
{
    DEBUGTOOLS_LAUNCH_TARGET_CHAPTER = 1,
    DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH = 2,
};

enum DebugToolsLaunchRequestResult
{
    DEBUGTOOLS_LAUNCH_REQUEST_OK = 0,
    DEBUGTOOLS_LAUNCH_REQUEST_INVALID,
    DEBUGTOOLS_LAUNCH_REQUEST_UNAVAILABLE,
    DEBUGTOOLS_LAUNCH_REQUEST_BUSY,
};

enum DebugToolsLaunchRequestOrigin
{
    DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_DIRECT = 0,
    DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_CH4_PREP_COMPAT,
};

struct DebugToolsLaunchTarget
{
    u16 id;
    u8 kind;
    u8 chapterMode;
    u8 nodeId;
    u8 chapterId;
    u8 encounterChoice;
    u8 _pad;
};

struct DebugToolsLaunchRequest
{
    u16 targetId;
    u8 kind;
    u8 chapterMode;
    u8 nodeId;
    u8 chapterId;
    u8 encounterChoice;
    u8 origin;
};

void DebugTools_RegisterChapterSelectorAction(void);
int DebugTools_GetLaunchTargetCount(void);
int DebugTools_GetLaunchTarget(int index, struct DebugToolsLaunchTarget* out);
u16 DebugTools_GetSelectedTargetId(void);
enum DebugToolsLaunchRequestResult DebugTools_RequestTargetLaunch(u16 targetId);
enum DebugToolsLaunchRequestResult DebugTools_RequestTargetLaunchWithOrigin(
    u16 targetId,
    enum DebugToolsLaunchRequestOrigin origin);
int DebugTools_IsTargetLaunchPending(void);
int DebugTools_ConsumePendingTargetLaunch(struct DebugToolsLaunchRequest* out);
int DebugTools_QueueMapLaunchHandoff(void);
void DebugToolsSelector_RunMapHandoff(ProcPtr proc);
void DebugToolsSelector_MapHandoffOnEnd(ProcPtr proc);

extern struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[];

#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
/*
 * This private probe is compiled only by
 * expansion-modern-portrait-package-runtime-check. It deliberately stays
 * out of expansion_debugtools.h and every supported profile's EWRAM layout.
 */
struct PortraitPackageRuntimeProbe
{
    u32 faceId;
    u32 minimugRenderCount;
    u32 minimugVramWord;
    u32 minimugPaletteWord;
    u32 fullFaceRenderCount;
    u32 mouthDisplayBits;
    u32 eyeControl;
    u32 faceOam2;
    u32 mouthFrame0;
    u32 mouthFrame2;
};

extern struct PortraitPackageRuntimeProbe gPortraitPackageRuntimeProbe;
#endif

#endif

#endif
