#ifndef GUARD_DEBUGTOOLS_INTERNAL_H
#define GUARD_DEBUGTOOLS_INTERNAL_H

#include "proc.h"
#include "expansion_debugtools.h"

struct DebugToolsAction;
struct MenuItemProc;
struct MenuProc;

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action);
void DebugToolsActions_ForceCleanup(void);
void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu);
int DebugTools_IsMenuTransitionScheduled(void);
void DebugTools_RunMenuTransition(ProcPtr proc);
u8 DebugTools_CancelMenu(struct MenuProc* menu, struct MenuItemProc* item);
void DebugToolsSaveState_OnHubReturn(void);

enum DebugToolsDiagnosticsRestoreMismatch
{
    DEBUGTOOLS_DIAG_RESTORE_BG0 = (1 << 0),
    DEBUGTOOLS_DIAG_RESTORE_BG1 = (1 << 1),
    DEBUGTOOLS_DIAG_RESTORE_FONT = (1 << 2),
    DEBUGTOOLS_DIAG_RESTORE_LCD = (1 << 3),
    DEBUGTOOLS_DIAG_RESTORE_PALETTE = (1 << 4),
    DEBUGTOOLS_DIAG_RESTORE_BG2 = (1 << 5),
    DEBUGTOOLS_DIAG_RESTORE_LOCK = (1 << 6),
};

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
struct DebugToolsDiagnosticsProbe
{
    u32 captureCount;
    u32 lastSequence;
    u32 lastResult;
    u32 lastContext;
    u32 lastValidMask;
    u32 lastCursorUnitId;
    u32 ownerActive;
    u32 ownerStartCount;
    u32 restorationCount;
    u32 forcedTeardownCount;
    u32 restorationMismatchMask;
    u32 statePageOpenCount;
    u32 enginePageOpenCount;
    u32 titleCaptureCount;
    u32 emptyUnitCaptureCount;
    u32 battleRejectCount;
    u32 runtimeTestComplete;
    u32 mapUnitCaptureCount;
    u32 mapRuntimeComplete;
    u32 prepCaptureCount;
    u32 prepRuntimeComplete;
    u32 viewRuntimeComplete;
    u32 lastLockBaseline;
    u32 lastLockAfterRestore;
    u32 postViewMapIdleCount;
};

extern struct DebugToolsDiagnosticsProbe gDebugToolsDiagnosticsProbe;
#endif

void DebugToolsDiagnostics_SetSessionContext(
    enum DebugToolsDiagnosticsContext context);
void DebugToolsDiagnostics_ClearSessionContext(void);
enum DebugToolsDiagnosticsContext DebugToolsDiagnostics_GetSessionContext(void);
int DebugToolsDiagnostics_IsContextAvailable(void);
enum DebugToolsResult DebugToolsDiagnostics_BeginSession(void);
void DebugToolsDiagnostics_EndSession(int forced);
void DebugToolsDiagnostics_ForceCloseSession(void);
int DebugToolsDiagnostics_IsRestoring(void);
void DebugToolsDiagnostics_SetActiveMenu(struct MenuProc* menu);
void DebugToolsDiagnostics_ClearActiveMenu(struct MenuProc* menu);
struct MenuProc* DebugToolsDiagnostics_GetActiveMenu(void);
struct MenuProc* DebugToolsDiagnostics_StartOwnedMenu(
    const struct MenuDef* menuDef);
const struct DebugToolsDiagnosticsSnapshot* DebugToolsDiagnostics_GetSnapshot(void);
char* DebugToolsDiagnostics_GetStatusBuffer(void);
void DebugToolsDiagnostics_DrawStatusText(
    struct MenuProc* menu,
    const char* text);
enum DebugToolsResult DebugToolsDiagnostics_RefreshSnapshot(void);
void DebugToolsDiagnostics_RecordViewOpen(int engineView);
void DebugToolsDiagnostics_OnSessionRestored(void);
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
void DebugToolsDiagnostics_RuntimeTestBoot(void);
void DebugToolsDiagnostics_RuntimeTestMap(void);
void DebugToolsDiagnostics_RuntimeTestPrep(void);
#endif

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

#else

#define DebugToolsDiagnostics_ForceCloseSession() ((void)0)

#endif

#endif
