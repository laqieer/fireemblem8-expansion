#include "global.h"
#include "event.h"
#include "eventinfo.h"
#include "eventscript.h"
#include "EAstdlib.h"
#include "chapterdata.h"
#include "constants/event-flags.h"
#include "bmunit.h"
#include "bmtrap.h"

#include "events/prologue-eventinfo.h"
#include "events/ch1-eventinfo.h"

/* Issue #5 Batch 3d: the 7 EventListScr_Ch2_* event-list arrays, the
 * EventListScr_Ch2_Tutorial pointer list, and the struct
 * ChapterEventGroup Ch2Events manifest -- all of "events/ch2-eventinfo.h"
 * -- are canonically generated from src/data/ch2_eventlists.json -- see
 * scripts/generated_data/eventlists/ and docs/generated_data.md's
 * "eventlists" schema section. build/generated/data/data_ch2_eventlists.o
 * is linked in its place (ldscript.txt places it immediately after this
 * file's own (.data), so this excluded include leaves zero address gap).
 * Unlike the units/traps/shops tables (Batch 3a/3b/3c), whose Chapter 2
 * content lives inline in a shared hand .c file, Chapter 2's event-list
 * composition lives entirely in its own header, "events/ch2-eventinfo.h",
 * so the include itself is guarded here rather than any content inside
 * it. The header is deliberately left in place, verbatim, rather than
 * deleted: generated-data-check's round-trip parser
 * (scripts/generated_data/eventlists/parser.py) reads that exact source
 * text (plain regex/brace-depth parsing, never the compiler) to keep
 * proving the generated table byte-for-byte identical in meaning to it.
 * Never hand-edit "events/ch2-eventinfo.h" -- edit
 * src/data/ch2_eventlists.json and regenerate instead. */
#define GENERATED_DATA_EVENTLISTS_CH2_LINKED 1

#if !GENERATED_DATA_EVENTLISTS_CH2_LINKED
#include "events/ch2-eventinfo.h"
#endif /* !GENERATED_DATA_EVENTLISTS_CH2_LINKED */

/* The excluded header above is not actually a binary-layout prefix of
 * this translation unit's .data -- the "events/prologue-eventinfo.h" and
 * "events/ch1-eventinfo.h" includes above emit Prologue/Chapter-1
 * event-list data first, ahead of Chapter 2. To let
 * build/generated/data/data_ch2_eventlists.o(.data) slot in at the exact
 * original Chapter-2 address (between the still-hand Prologue/Ch1 data
 * and this file's own Chapter 3+ data) without shifting either side,
 * everything from here to end-of-file is redirected into a distinctly
 * named section so ldscript.txt can place the generated object between
 * the two pieces of this same object file's data. See ldscript.txt and
 * docs/generated_data.md's "eventlists" section. */
#undef CONST_DATA
#define CONST_DATA SECTION(".data.ch2eventtail")

#include "events/ch3-eventinfo.h"
#include "events/ch4-eventinfo.h"
#include "events/ch5-eventinfo.h"
#include "events/ch5x-eventinfo.h"
#include "events/ch6-eventinfo.h"
#include "events/ch7-eventinfo.h"
#include "events/ch8-eventinfo.h"

#include "events/ch9a-eventinfo.h"
#include "events/ch10a-eventinfo.h"
#include "events/ch11a-eventinfo.h"
#include "events/ch12a-eventinfo.h"
#include "events/ch13a-eventinfo.h"
#include "events/ch14a-eventinfo.h"
#include "events/ch15a-eventinfo.h"
#include "events/ch16a-eventinfo.h"
#include "events/ch17a-eventinfo.h"
#include "events/ch18a-eventinfo.h"
#include "events/ch19a-eventinfo.h"
#include "events/ch20a-eventinfo.h"
#include "events/ch21a-eventinfo.h"
#include "events/ch21xa-eventinfo.h"

#include "events/ch9b-eventinfo.h"
#include "events/ch10b-eventinfo.h"
#include "events/ch11b-eventinfo.h"
#include "events/ch12b-eventinfo.h"
#include "events/ch13b-eventinfo.h"
#include "events/ch14b-eventinfo.h"
#include "events/ch15b-eventinfo.h"
#include "events/ch16b-eventinfo.h"
#include "events/ch17b-eventinfo.h"
#include "events/ch18b-eventinfo.h"
#include "events/ch19b-eventinfo.h"
#include "events/ch20b-eventinfo.h"
#include "events/ch21b-eventinfo.h"
#include "events/ch21xb-eventinfo.h"

#include "events/tower1-eventinfo.h"
#include "events/tower2-eventinfo.h"
#include "events/tower3-eventinfo.h"
#include "events/tower4-eventinfo.h"
#include "events/tower5-eventinfo.h"
#include "events/tower6-eventinfo.h"
#include "events/tower7-eventinfo.h"
#include "events/tower8-eventinfo.h"

#include "events/ruin1-eventinfo.h"
#include "events/ruin2-eventinfo.h"
#include "events/ruin3-eventinfo.h"
#include "events/ruin4-eventinfo.h"
#include "events/ruin5-eventinfo.h"
#include "events/ruin6-eventinfo.h"
#include "events/ruin7-eventinfo.h"
#include "events/ruin8-eventinfo.h"
#include "events/ruin9-eventinfo.h"
#include "events/ruin10-eventinfo.h"

#include "events/lordsplit-eventinfo.h"
#include "events/MelkaenCoast-eventinfo.h"
#include "events/chunk3B-eventinfo.h"
#include "events/debugmap-eventinfo.h"
