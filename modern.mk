# Opt-in modern GCC object cohort and transitional modern ELF target.

MODERN_GOALS := expansion-modern-toolchain-check expansion-modern-cohort expansion-modern-all expansion-modern-elf expansion-modern-rom expansion-modern-boot-check expansion-modern-clean
ifneq (,$(filter $(MODERN_GOALS),$(MAKECMDGOALS)))
  NODEP := 1
endif

MODERN_CONFIG ?= debug
MODERN_ABI ?= aapcs

MODERN_CONFIGS := debug release
MODERN_ABIS := aapcs apcs-gnu

ifeq (,$(filter $(MODERN_CONFIG),$(MODERN_CONFIGS)))
  $(error modern.mk: unsupported MODERN_CONFIG '$(MODERN_CONFIG)'; expected debug or release)
endif
ifeq (,$(filter $(MODERN_ABI),$(MODERN_ABIS)))
  $(error modern.mk: unsupported MODERN_ABI '$(MODERN_ABI)'; expected aapcs or apcs-gnu)
endif

MODERN_TOOLCHAIN_ROOT ?=
MODERN_BINUTILS_DIR ?=

# Debian/Ubuntu package newlib's target headers here rather than in GCC's
# built-in search path. An explicitly set (even empty) value always wins.
ifeq ($(origin MODERN_NEWLIB_INCLUDE),undefined)
  ifneq ($(wildcard /usr/include/newlib/stdlib.h),)
    MODERN_NEWLIB_INCLUDE := /usr/include/newlib
  else
    MODERN_NEWLIB_INCLUDE :=
  endif
endif

ifeq ($(MODERN_TOOLCHAIN_ROOT),)
  MODERN_CC ?= $(PREFIX)gcc$(EXE)
  MODERN_OBJDUMP ?= $(PREFIX)objdump$(EXE)
else
  MODERN_CC ?= $(MODERN_TOOLCHAIN_ROOT)/bin/$(PREFIX)gcc$(EXE)
  MODERN_OBJDUMP ?= $(MODERN_TOOLCHAIN_ROOT)/bin/$(PREFIX)objdump$(EXE)
endif

# Quotes are part of the recipe text after make expansion, preserving spaces.
# Do not treat user-supplied paths as make word lists.
MODERN_BINUTILS_FLAG = $(if $(MODERN_BINUTILS_DIR),"-B$(MODERN_BINUTILS_DIR)/")
MODERN_NEWLIB_FLAG = $(if $(MODERN_NEWLIB_INCLUDE),-isystem "$(MODERN_NEWLIB_INCLUDE)")
MODERN_DRIVER_FLAGS := $(MODERN_BINUTILS_FLAG) $(MODERN_NEWLIB_FLAG)

MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork
# -fgnu89-inline restores legacy-compatible emission for plain (non-static,
# non-extern) `inline` definitions: under -std=gnu11 alone, GCC may elide the
# external definition of such a function once every internal call site is
# inlined, silently breaking any other translation unit that still calls it.
# agbcc always emitted these externally; this flag matches that behavior.
MODERN_LANGUAGE_FLAGS := -std=gnu11 -fgnu89-inline
MODERN_RUNTIME_FLAGS := \
	-ffreestanding -fno-builtin -fno-common \
	-fno-pic -fno-pie \
	-fno-unwind-tables -fno-asynchronous-unwind-tables
MODERN_LAYOUT_FLAGS := \
	-fno-function-sections -fno-data-sections \
	-fno-merge-constants -fno-merge-all-constants
MODERN_DEFINE_FLAGS := -DMODERN=1 -DNONMATCHING=1
MODERN_INCLUDE_FLAGS := -Iinclude -I.
MODERN_WARNING_FLAGS := \
	-Wall -Wextra \
	-Werror=strict-prototypes \
	-Werror=implicit-function-declaration \
	-Werror=incompatible-pointer-types

ifeq ($(MODERN_CONFIG),debug)
  MODERN_CONFIG_FLAGS := -Og -g3
else
  MODERN_CONFIG_FLAGS := -O2 -g0 -DNDEBUG
endif

# aapcs uses GCC's default ABI and is the supported choice for linked
# outputs.  apcs-gnu remains available for compile-only layout comparison.
ifeq ($(MODERN_ABI),aapcs)
  MODERN_ABI_FLAGS :=
else
  MODERN_ABI_FLAGS := -mabi=apcs-gnu
endif

MODERN_CFLAGS := \
	$(MODERN_DRIVER_FLAGS) \
	$(MODERN_ARCH_FLAGS) \
	$(MODERN_LANGUAGE_FLAGS) \
	$(MODERN_RUNTIME_FLAGS) \
	$(MODERN_LAYOUT_FLAGS) \
	$(MODERN_DEFINE_FLAGS) \
	$(MODERN_INCLUDE_FLAGS) \
	$(MODERN_WARNING_FLAGS) \
	$(MODERN_CONFIG_FLAGS) \
	$(MODERN_ABI_FLAGS)

MODERN_BUILD_ROOT := build/expansion-modern
MODERN_OUTPUT_DIR := $(MODERN_BUILD_ROOT)/$(MODERN_CONFIG)/$(MODERN_ABI)

MODERN_COHORT_SOURCES ?= \
	src/fe3_dummy.c \
	src/unit_facing.c \
	src/time.c \
	src/ramfunc.c \
	src/agb_sram.c \
	src/bmlib-math.c \
	src/rng.c \
	src/irq.c \
	src/main.c \
	src/ap.c \
	src/bmsave-misc.c \
	src/bmsave-gmap.c \
	src/bmsave-lib.c \
	src/bmsave.c \
	src/bmsave-xmap.c \
	src/bmcontainer.c \
	src/proc.c \
	src/hardware.c

# Handwritten assembly that must not be decompiled (see CONTRIBUTING.md).
# libagbsyscall.s is a self-contained set of BIOS SWI trampolines; arm.s and
# arm_call.s are a coupled pair (arm_call.s's Thumb trampolines branch into
# arm.s's ARM-mode functions) and must stay together in this cohort.
MODERN_COHORT_ASM_SOURCES ?= \
	src/libagbsyscall.s \
	asm/arm.s \
	asm/arm_call.s

MODERN_ASFLAGS := \
	$(MODERN_DRIVER_FLAGS) \
	$(MODERN_ARCH_FLAGS) \
	$(MODERN_INCLUDE_FLAGS) \
	$(MODERN_ABI_FLAGS)

MODERN_COHORT_C_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_COHORT_SOURCES:.c=.o))
MODERN_COHORT_ASM_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_COHORT_ASM_SOURCES:.s=.o))
MODERN_COHORT_OBJECTS := $(MODERN_COHORT_C_OBJECTS) $(MODERN_COHORT_ASM_OBJECTS)
MODERN_COHORT_DEPS := $(MODERN_COHORT_OBJECTS:.o=.d)

# Full-source cohort: every currently supported translation unit, not just the
# fast/default 18-file cohort above. Self-contained $(wildcard ...) defaults so
# this works when modern.mk is included standalone by test fixtures (which
# override these variables with small synthetic lists rather than relying on
# the top-level Makefile's own CFILES/DATA_SRC_C_FILES).
MODERN_ALL_C_SOURCES ?= $(wildcard src/*.c)
ifeq (,$(findstring src/msg_data.c,$(MODERN_ALL_C_SOURCES)))
MODERN_ALL_C_SOURCES += src/msg_data.c
endif

MODERN_ALL_DATA_C_SOURCES ?= $(wildcard src/data/*.c src/data/mapanim/*.c src/data/menu/*.c src/data/ending/*.c src/data/worldmap/*.c src/data/ui/*.c)

# A separate default list from MODERN_COHORT_ASM_SOURCES (not derived from
# it), so a caller overriding one does not silently blank out the other.
MODERN_ALL_ASM_SOURCES ?= \
	src/libagbsyscall.s \
	asm/arm.s \
	asm/arm_call.s

# tools/preproc expands INCBIN_U8/INCBIN_U16 macros into inline literal byte
# arrays but leaves #include directives untouched; tools/scaninc reports both
# the header includes and the INCBIN binary/graphics asset paths of a raw,
# un-preprocessed data source file.
MODERN_PREPROC ?= tools/preproc/preproc$(EXE)
MODERN_SCANINC ?= tools/scaninc/scaninc$(EXE)

# Explicit host build rule for the default tools/scaninc/scaninc$(EXE),
# parallel to the top-level Makefile's own $(PREPROC) rule. Without this,
# the .assets.d rule below's own "$(MODERN_SCANINC)" prerequisite would be
# reachable, when missing, only through GNU Make's built-in implicit rule
# chain (%: %.o, %.o: %.cpp): that chain compiles just scaninc.cpp (missing
# c_file.cpp/asm_file.cpp/source_file.cpp) with default host flags plus any
# CPPFLAGS already exported for the legacy agbcc pipeline (e.g. -nostdinc),
# producing a broken link or a "cstdio: No such file or directory" failure
# instead of a correct scaninc binary. This exact-path explicit rule always
# takes precedence over that implicit chain and builds scaninc the correct
# way, using its own Makefile's host C++ flags. An overridden MODERN_SCANINC
# pointing elsewhere never matches this literal target path, so it is
# unaffected. The prerequisite list is $(wildcard)-based (evaluated once at
# parse time) rather than literal filenames, so a prebuilt/stubbed scaninc
# binary sitting next to no (or different) sources -- e.g. test fixtures --
# is still treated as already up to date instead of failing with "No rule
# to make target tools/scaninc/scaninc.cpp".
tools/scaninc/scaninc$(EXE): $(wildcard tools/scaninc/*.cpp tools/scaninc/*.h tools/scaninc/Makefile)
	$(MAKE) -C tools/scaninc

MODERN_ALL_C_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_C_SOURCES:.c=.o))
MODERN_ALL_DATA_PRE := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_DATA_C_SOURCES:.c=.pre.c))
MODERN_ALL_DATA_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_DATA_C_SOURCES:.c=.o))
MODERN_ALL_ASM_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_ASM_SOURCES:.s=.o))
MODERN_ALL_OBJECTS := $(MODERN_ALL_C_OBJECTS) $(MODERN_ALL_DATA_OBJECTS) $(MODERN_ALL_ASM_OBJECTS)
MODERN_ALL_DEPS := $(MODERN_ALL_C_OBJECTS:.o=.d) $(MODERN_ALL_DATA_OBJECTS:.o=.d) $(MODERN_ALL_ASM_OBJECTS:.o=.d)

# One deterministic, scaninc-derived asset dependency file per data C
# source (mirroring the .pre.c/.o naming), generated and `include`d (see
# the asset dependency bootstrap section below the .pre.c/.o rules) before
# any .pre.c/.o recipe is ever considered, so missing INCBIN graphics/
# binary assets become ordinary prerequisites on the very first
# invocation instead of surfacing only after a failed build.
MODERN_ALL_DATA_ASSET_DEPS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_DATA_C_SOURCES:.c=.assets.d))

# A related but distinct first-invocation gap: ordinary (non-data)
# MODERN_ALL_C_SOURCES files can #include a *generated* header that is not
# an INCBIN asset (e.g. src/chapterdata.c includes the JSON-generated
# src/data/chapter_settings.h from json_data_rules.mk). scaninc cannot
# discover this: it silently drops any #include it cannot fopen() on disk
# (see tools/scaninc/scaninc.cpp), so it never reports a not-yet-generated
# header at all. GCC/cpp's own "-MM -MG" ("assume missing headers are
# generated") is the correct tool here instead -- it is what the legacy
# Makefile's own $(MAKEDEP)/-MG step already relies on for exactly this
# case. One dependency file per normal C source, targeting its .o directly
# (there is no .pre.c stage for these sources).
MODERN_ALL_C_HEADER_DEPS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_C_SOURCES:.c=.headers.d))

# Only these goals ever touch the full data/normal C source lists;
# expansion-modern-cohort, expansion-modern-toolchain-check, and
# expansion-modern-clean must stay untouched by (and not pay the cost of)
# scanning sources they do not build.
MODERN_ALL_SOURCE_GOALS := expansion-modern-all expansion-modern-elf expansion-modern-rom expansion-modern-boot-check

CLEAN_DIRS += $(MODERN_BUILD_ROOT)

.PHONY: $(MODERN_GOALS)

expansion-modern-toolchain-check:
	@set -eu; \
	cc='$(MODERN_CC)'; \
	cc_path=$$(command -v "$$cc" 2>/dev/null || true); \
	if [ -z "$$cc_path" ] || [ ! -x "$$cc_path" ]; then \
		printf '%s\n' "error: modern compiler not found: $$cc" >&2; \
		printf '%s\n' "Install gcc-arm-none-eabi or set MODERN_TOOLCHAIN_ROOT/MODERN_CC." >&2; \
		exit 1; \
	fi; \
	version=$$("$$cc" $(MODERN_BINUTILS_FLAG) --version 2>&1) || { \
		printf '%s\n' "error: failed to run modern compiler: $$cc" >&2; \
		exit 1; \
	}; \
	printf 'Modern compiler: %s\n' "$$(printf '%s\n' "$$version" | sed -n '1p')"; \
	target=$$("$$cc" $(MODERN_BINUTILS_FLAG) -dumpmachine 2>&1) || { \
		printf '%s\n' "error: could not query modern compiler target: $$cc" >&2; \
		exit 1; \
	}; \
	if [ "$$target" != arm-none-eabi ]; then \
		printf "error: modern compiler targets '%s'; expected 'arm-none-eabi'\n" "$$target" >&2; \
		exit 1; \
	fi; \
	printf 'Modern target: %s\n' "$$target"; \
	assembler=$$("$$cc" $(MODERN_BINUTILS_FLAG) -print-prog-name=as 2>&1) || { \
		printf '%s\n' "error: could not resolve the assembler used by modern GCC" >&2; \
		exit 1; \
	}; \
	case "$$assembler" in \
		/*) resolved_as="$$assembler" ;; \
		*) resolved_as=$$(command -v "$$assembler" 2>/dev/null || true) ;; \
	esac; \
	if [ -z "$$resolved_as" ] || [ ! -x "$$resolved_as" ]; then \
		printf "error: modern GCC resolved assembler '%s', but it is not executable\n" "$$assembler" >&2; \
		printf '%s\n' "Install binutils-arm-none-eabi or set MODERN_BINUTILS_DIR." >&2; \
		exit 1; \
	fi; \
	printf 'Modern assembler: %s\n' "$$resolved_as"; \
	if ! printf '%s\n' '#include "global.h"' | \
		"$$cc" $(MODERN_DRIVER_FLAGS) $(MODERN_ARCH_FLAGS) $(MODERN_LANGUAGE_FLAGS) \
		$(MODERN_DEFINE_FLAGS) $(MODERN_INCLUDE_FLAGS) -fsyntax-only -x c -; then \
		printf '%s\n' "error: modern GCC cannot parse include/global.h and its standard headers." >&2; \
		printf '%s\n' "Install newlib headers (Ubuntu: libnewlib-arm-none-eabi) or set MODERN_NEWLIB_INCLUDE." >&2; \
		exit 1; \
	fi; \
	if ! printf '%s\n' 'void modern_arm7tdmi_thumb_probe(void) {}' | \
		"$$cc" $(MODERN_DRIVER_FLAGS) $(MODERN_ARCH_FLAGS) $(MODERN_ABI_FLAGS) \
		-ffreestanding -fno-pic -fno-pie -c -x c -o /dev/null -; then \
		printf '%s\n' "error: ARM7TDMI Thumb/interwork compile probe failed." >&2; \
		printf '%s\n' "Check the GCC/binutils pairing and MODERN_BINUTILS_DIR." >&2; \
		exit 1; \
	fi; \
	printf 'Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\n' \
		'$(MODERN_CONFIG)' '$(MODERN_ABI)'

expansion-modern-cohort: expansion-modern-toolchain-check $(MODERN_COHORT_OBJECTS)
	@printf 'Built %s modern relocatable objects in %s\n' \
		'$(words $(MODERN_COHORT_OBJECTS))' '$(MODERN_OUTPUT_DIR)'

$(MODERN_COHORT_OBJECTS): | expansion-modern-toolchain-check

# Full-source cohort target. Builds every currently supported C translation
# unit (normal + preprocessed data) and the three supported handwritten
# assembly files to relocatable objects only. This does not link, boot, or
# claim runtime readiness, and it does not replace expansion-modern-cohort,
# which remains the fast/default migration target.
expansion-modern-all: expansion-modern-toolchain-check $(MODERN_ALL_OBJECTS)
	@printf 'Built %s modern relocatable objects in %s\n' \
		'$(words $(MODERN_ALL_OBJECTS))' '$(MODERN_OUTPUT_DIR)'

$(MODERN_ALL_OBJECTS): | expansion-modern-toolchain-check

$(MODERN_OUTPUT_DIR)/%.o: %.c
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" -MQ "$@" -c "$<" -o "$@"

# IWRAM-placed symbols need per-symbol BSS sections.
$(MODERN_OUTPUT_DIR)/src/agb_sram.o: MODERN_CFLAGS += -fdata-sections -fno-toplevel-reorder -fno-reorder-functions
$(MODERN_OUTPUT_DIR)/src/m4a.o: MODERN_CFLAGS += -fdata-sections
$(MODERN_OUTPUT_DIR)/src/bmshop.o: MODERN_CFLAGS += -fdata-sections

# GAS's own --MD tracks uppercase .INCLUDE directives (e.g. macro.inc,
# gba.inc), so no cpp preprocessing or scaninc invocation is needed here.
$(MODERN_OUTPUT_DIR)/%.o: %.s
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_ASFLAGS) -Wa,--MD,"$(@:.o=.d)" -c "$<" -o "$@"

# Data C sources embed INCBIN_U8/INCBIN_U16 binary/graphics assets and cannot
# be handed to modern GCC directly. This explicit static pattern rule (bound
# to the exact MODERN_ALL_DATA_PRE/MODERN_ALL_DATA_OBJECTS target lists) takes
# precedence over the generic $(MODERN_OUTPUT_DIR)/%.o: %.c rule above for
# these same target names, so data files never reach the modern C compiler
# unpreprocessed.
#
# Every data source's INCBIN/graphics asset prerequisites are declared as
# real prerequisites of its .pre.c target by the generated
# $(MODERN_OUTPUT_DIR)/%.assets.d file `include`d below (see the asset
# dependency bootstrap section), so a missing asset is resolved by the
# top-level Makefile's own generation rules (e.g. %.4bpp: %.png) before
# preproc is ever invoked, on the very first invocation -- rather than
# preproc failing hard on the first unresolved INCBIN it hits.
$(MODERN_ALL_DATA_PRE): $(MODERN_OUTPUT_DIR)/%.pre.c: %.c $(MODERN_PREPROC)
	@mkdir -p "$(@D)"
	@if [ ! -x "$(MODERN_PREPROC)" ]; then \
		printf '%s\n' "error: modern preprocessor not found or not executable: $(MODERN_PREPROC)" >&2; \
		exit 1; \
	fi
	"$(MODERN_PREPROC)" "$<" > "$@" || { rm -f "$@"; exit 1; }

$(MODERN_ALL_DATA_OBJECTS): $(MODERN_OUTPUT_DIR)/%.o: $(MODERN_OUTPUT_DIR)/%.pre.c
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" -MQ "$@" -c "$<" -o "$@"

# ---------------------------------------------------------------------------
# Asset dependency bootstrap (issue #3): first-invocation INCBIN readiness
#
# Generates one deterministic dependency file per data C source (mirroring
# the .pre.c/.o naming), scanning the raw, un-preprocessed source with
# scaninc for every INCBIN_* asset path (and #include) it references, and
# declaring them as prerequisites of that source's .pre.c target above.
#
# These files are `include`d directly below with no $(wildcard ...)
# filtering (unlike the plain "-include $(wildcard ...)" of MODERN_ALL_DEPS
# at the bottom of this file), so a missing or stale .assets.d is itself a
# real prerequisite that GNU Make remakes using the rule below, then
# restarts and re-reads all makefiles -- the standard "remake included
# makefiles" behavior described in the GNU Make manual's "Generating
# Prerequisites Automatically" section. The practical effect: the very
# first `make expansion-modern-all/-elf/-rom/-boot-check` invocation
# already resolves missing graphics/binary assets as ordinary
# prerequisites (built via the top-level Makefile's existing asset rules)
# before any .pre.c/.o recipe is attempted, instead of only self-healing
# one build later.
#
# Deliberately not gated on NODEP, for the same reason this scaninc-based
# tracking never was: every modern goal above forces NODEP=1, so gating
# this on NODEP would permanently disable INCBIN readiness tracking for
# the modern build. A data source whose INCBIN references no derivable
# source (no matching .png/.bin and no rule to build one) fails with GNU
# Make's own actionable "No rule to make target '<asset>', needed by
# '<pre.c>'" error, naming both the missing asset and the .pre.c that
# needed it.
#
# Old-style FETSATOOL rules in graphics_file_rules.mk produce a raw
# "<name>.feimgN.bin"/"<name>.fetsaN.bin" (N=1..4) pair from a single
# recipe invocation, using a multi-target explicit rule (not GNU Make 4.3+
# grouped "&:" targets). GNU Make treats each target of such a rule as an
# independent goal with its own copy of the recipe: if two different data
# sources' .assets.d files each list only one sibling of the pair (one
# lists the feimg, another the fetsa) as an independent prerequisite
# elsewhere in the graph, Make can decide both need building and invoke
# the same recipe twice in one run, instead of once. Since neither
# graphics_file_rules.mk nor GNU Make's grouped-target syntax may be
# touched here, every raw/.lz "fetsaN.bin" asset scaninc reports below is
# also given an explicit "<raw fetsaN.bin>: <sibling raw feimgN.bin>"
# ordering edge (deduplicated and sorted for determinism), so a sequential
# (-j1) build always resolves the feimg half of the pair first.
#
# That ordering edge alone is *not* sufficient once a parallel (-j>1)
# build is in play, confirmed with an isolated GNU Make 4.3 reproduction:
# when both siblings are still missing, Make's scheduler evaluates the
# fetsa target's own staleness (file absent) and can dispatch its copy of
# the shared recipe as soon as the feimg build job is merely "already
# being made" (pruned from re-traversal), not once it has actually
# finished writing output -- so both copies of the recipe can still run
# concurrently even with the edge present. Since the shared recipe itself
# (in graphics_file_rules.mk) can't be touched, FETSATOOL is redefined
# below -- only for modern goals, so the legacy agbcc build is untouched
# -- as a small idempotent wrapper: it takes a portable mkdir-based
# advisory lock keyed by the pair's own feimg output path (with a stale
# lock timeout in case a prior invocation was killed), so whichever
# invocation loses the race blocks until the winner's real generation
# finishes, then finds both outputs already newer than the source and
# skips re-running the real tool. The ordering edge above still forces
# sequential builds to resolve deterministically without ever taking the
# lock, and remains useful documentation of the intended feimg-first
# generation order; the wrapper is what actually makes -j builds safe.
#
# NOTE: a clean build already logs "make: Circular X <- X dependency
# dropped" lines for some of these old-style multi-target rules' outputs.
# This is pre-existing GNU Make behavior from graphics_file_rules.mk's/
# Makefile's own multi-target recipes (confirmed unchanged in count and
# content on baseline commit 9e938633, i.e. with neither the ordering
# edge above nor the FETSATOOL wrapper present) and is not introduced or
# affected by either mechanism here; Make drops the spurious self-edge
# and proceeds, so it does not affect build correctness.
ifneq (,$(filter $(MODERN_GOALS),$(MAKECMDGOALS)))
  MODERN_FETSATOOL_REAL := $(FETSATOOL)
  # $0 is "_"; $1 is the real tool path; $2.. are the tool's own args
  # (src, feimg-out, fetsa-out, then any recipe-specific extra flags).
  MODERN_FETSATOOL_LOCK := sh -c '\
		real="$$1"; shift; \
		src="$$1"; out1="$$2"; out2="$$3"; \
		lockdir="$$out1.fetsalock.d"; \
		tries=0; \
		while ! mkdir "$$lockdir" 2>/dev/null; do \
			tries=$$((tries + 1)); \
			if [ "$$tries" -gt 5 ]; then \
				stale=$$(find "$$lockdir" -maxdepth 0 -mmin +2 2>/dev/null); \
				if [ -n "$$stale" ]; then rmdir "$$lockdir" 2>/dev/null; fi; \
			fi; \
			sleep 1; \
		done; \
		if [ -e "$$out1" ] && [ -e "$$out2" ] && [ "$$out1" -nt "$$src" ] && [ "$$out2" -nt "$$src" ]; then \
			rmdir "$$lockdir" 2>/dev/null; \
			exit 0; \
		fi; \
		"$$real" "$$@"; \
		status=$$?; \
		rmdir "$$lockdir" 2>/dev/null; \
		exit $$status \
	' _
  FETSATOOL = $(MODERN_FETSATOOL_LOCK) $(MODERN_FETSATOOL_REAL)
endif
$(MODERN_ALL_DATA_ASSET_DEPS): $(MODERN_OUTPUT_DIR)/%.assets.d: %.c $(MODERN_SCANINC)
	@mkdir -p "$(@D)"
	@if [ ! -x "$(MODERN_SCANINC)" ]; then \
		printf '%s\n' "error: modern scaninc not found or not executable: $(MODERN_SCANINC)" >&2; \
		exit 1; \
	fi
	@assets=$$("$(MODERN_SCANINC)" -I include -I "" "$<") || { \
		printf '%s\n' "error: scaninc failed while scanning $< for INCBIN assets" >&2; \
		exit 1; \
	}; \
	fetsa_pairs=$$(printf '%s\n' "$$assets" | while IFS= read -r asset; do \
	    [ -n "$$asset" ] || continue; \
	    case "$$asset" in \
	      *.fetsa[1-9]*.bin|*.fetsa[1-9]*.bin.lz) ;; \
	      *) continue ;; \
	    esac; \
	    match=$$(printf '%s\n' "$$asset" | sed -n -E 's/^(.*)\.fetsa([1-4])\.bin(\.lz)?$$/\1|\2/p'); \
	    if [ -z "$$match" ]; then \
	      printf '%s\n' "error: malformed FETSA asset pair name: $$asset" >&2; \
	      exit 1; \
	    fi; \
	    prefix=$${match%|*}; \
	    n=$${match#*|}; \
	    printf '%s:%s\n' "$${prefix}.fetsa$${n}.bin" "$${prefix}.feimg$${n}.bin"; \
	  done) || exit 1; \
	{ printf '%s: %s' "$(MODERN_OUTPUT_DIR)/$*.pre.c" "$<"; \
	  printf '%s\n' "$$assets" | while IFS= read -r asset; do \
	    [ -n "$$asset" ] || continue; \
	    escaped=$$(printf '%s' "$$asset" | sed -e 's/\\/\\\\/g' -e 's/ /\\ /g'); \
	    printf ' %s' "$$escaped"; \
	  done; \
	  printf '\n'; \
	  printf '%s\n' "$$fetsa_pairs" | sort -u | while IFS=: read -r fetsa_raw feimg_raw; do \
	    [ -n "$$fetsa_raw" ] || continue; \
	    escaped_fetsa=$$(printf '%s' "$$fetsa_raw" | sed -e 's/\\/\\\\/g' -e 's/ /\\ /g'); \
	    escaped_feimg=$$(printf '%s' "$$feimg_raw" | sed -e 's/\\/\\\\/g' -e 's/ /\\ /g'); \
	    printf '%s: %s\n' "$$escaped_fetsa" "$$escaped_feimg"; \
	  done; \
	} > "$@.tmp"
	@mv -f "$@.tmp" "$@"

ifneq (,$(filter $(MODERN_ALL_SOURCE_GOALS),$(MAKECMDGOALS)))
include $(MODERN_ALL_DATA_ASSET_DEPS)
endif

# Generated-header bootstrap for ordinary (non-data) C sources: see the
# MODERN_ALL_C_HEADER_DEPS comment above for why GCC's own "-MM -MG" is
# used here instead of scaninc. Same `include`d-for-real-remake-restart
# wiring, gated by the same goal set, and equally exempt from NODEP for
# the same first-invocation reason.
#
# GNU Make remakes stale/missing `include`d makefiles (this rule's own
# targets) before it ever evaluates expansion-modern-all's own
# "expansion-modern-toolchain-check" prerequisite -- that check runs far
# too late to help here. Without this order-only prerequisite, a missing
# or misconfigured MODERN_CC would instead surface as a raw
# "$(MODERN_CC): not found"-style shell error from this recipe. Adding
# expansion-modern-toolchain-check as an order-only prerequisite makes
# its own actionable diagnostic run first every time any .headers.d needs
# remaking; being .PHONY, it always runs but only once per invocation (no
# remake-restart loop, since a successfully generated .headers.d is no
# longer stale on the following pass).
$(MODERN_ALL_C_HEADER_DEPS): | expansion-modern-toolchain-check

$(MODERN_ALL_C_HEADER_DEPS): $(MODERN_OUTPUT_DIR)/%.headers.d: %.c
	@mkdir -p "$(@D)"
	@"$(MODERN_CC)" $(MODERN_CFLAGS) -MM -MG -MT "$(MODERN_OUTPUT_DIR)/$*.o" "$<" > "$@.tmp" || { \
		rm -f "$@.tmp"; \
		printf '%s\n' "error: failed to pre-scan $< for generated header dependencies" >&2; \
		exit 1; \
	}
	@mv -f "$@.tmp" "$@"

ifneq (,$(filter $(MODERN_ALL_SOURCE_GOALS),$(MAKECMDGOALS)))
include $(MODERN_ALL_C_HEADER_DEPS)
endif

expansion-modern-clean:
	$(RM) -r "$(MODERN_BUILD_ROOT)"

# ---------------------------------------------------------------------------
# Transitional modern ELF target (issue #3)
#
# Links a full modern ELF using the reviewed prepare_modern_link.py
# generator, modern runtime libraries (-lc -lnosys -lgcc), and no
# tools/agbcc libraries.  AAPCS is the selected supported ABI for
# linked outputs; APCS-GNU remains available for compile-only layout
# comparison via expansion-modern-cohort/all but is incompatible with
# the EABI5 runtime libraries.
#
# asm/fe6sio.o is assembled by the modern build from a generated
# FE6 SIO payload built from the mgfembp submodule with modern GCC.
# ---------------------------------------------------------------------------

# Enforce AAPCS for linked outputs. expansion-modern-elf is the only target
# that actually links, but expansion-modern-rom/-boot-check both transitively
# depend on it (via $(MODERN_ELF)) without naming "expansion-modern-elf"
# literally in MAKECMDGOALS themselves, so the guard must recognize the
# whole linked-output goal set, not just the elf target, or a direct
# `make expansion-modern-rom MODERN_ABI=apcs-gnu` would silently slip past
# this check and proceed toward an incompatible EABI link.
MODERN_LINKED_GOALS := expansion-modern-elf expansion-modern-rom expansion-modern-boot-check
MODERN_REQUESTED_LINKED_GOALS := $(filter $(MODERN_LINKED_GOALS),$(MAKECMDGOALS))
ifneq (,$(MODERN_REQUESTED_LINKED_GOALS))
  ifneq ($(MODERN_ABI),aapcs)
    $(error $(MODERN_REQUESTED_LINKED_GOALS) requires MODERN_ABI=aapcs; \
      all modern linked outputs (expansion-modern-elf, expansion-modern-rom, \
      expansion-modern-boot-check) require AAPCS -- apcs-gnu objects are \
      incompatible with EABI5 newlib/libgcc)
  endif
endif

# Link-only assembly objects not in expansion-modern-all.
MODERN_ELF_EXTRA_ASM_SOURCES := src/rom_header.s src/crt0.s src/m4a_1.s
MODERN_ELF_EXTRA_ASM_OBJECTS := \
	$(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ELF_EXTRA_ASM_SOURCES:.s=.o))

# Modern FE6 SIO payload build.
MODERN_MGFEMBP_DIR := $(MODERN_OUTPUT_DIR)/mgfembp
MODERN_MGFEMBP_PAYLOAD := $(MODERN_MGFEMBP_DIR)/fe6sio_payload.bin.lz
MODERN_FE6SIO_OBJ := $(MODERN_OUTPUT_DIR)/asm/fe6sio.o
MODERN_MGFEMBP_BUILDER := $(PYTHON) scripts/modernize/build_mgfembp.py

# All assembly sources replaced by modern objects (source-relative names).
MODERN_ELF_REPLACED_ASM := \
	$(MODERN_ALL_ASM_SOURCES:.s=.o) $(MODERN_ELF_EXTRA_ASM_SOURCES:.s=.o) \
	$(MODERN_FE6SIO_OBJ:$(MODERN_OUTPUT_DIR)/%=%)

MODERN_ELF_FE6SIO := $(MODERN_FE6SIO_OBJ)

# Non-C assembled objects from the legacy pipeline (sound, data asm, midi).
# Filter out every C object, every modern-replaced assembly, and prebuilts.
MODERN_ELF_LEGACY_ASM := $(filter-out \
	$(C_OBJECTS) $(DATA_SRC_C_OBJECTS) \
	$(MODERN_ELF_FE6SIO) $(BANIM_OBJECT) \
	$(MODERN_ELF_REPLACED_ASM), \
	$(ASM_OBJECTS))
MODERN_ELF_LEGACY_MIDI := $(MID_OBJECTS)

MODERN_ELF_LINK_DIR := $(MODERN_OUTPUT_DIR)/link
MODERN_ELF_MANIFEST := $(MODERN_ELF_LINK_DIR)/manifest.txt
MODERN_ELF_OBJECTS_LST := $(MODERN_ELF_LINK_DIR)/objects.lst
MODERN_ELF_LDSCRIPT := $(MODERN_ELF_LINK_DIR)/ldscript.ld
MODERN_ELF := $(MODERN_OUTPUT_DIR)/fireemblem8.elf
MODERN_MAP := $(MODERN_OUTPUT_DIR)/fireemblem8.map
MODERN_ELF_BANIM_SYM := $(BANIM_OBJECT).sym.o

MODERN_LINK_GENERATOR := $(PYTHON) scripts/modernize/prepare_modern_link.py

# Resolve modern LD consistently with the toolchain root.
ifeq ($(MODERN_TOOLCHAIN_ROOT),)
  MODERN_LD ?= $(PREFIX)ld$(EXE)
else
  MODERN_LD ?= $(MODERN_TOOLCHAIN_ROOT)/bin/$(PREFIX)ld$(EXE)
endif

# Resolve modern OBJCOPY consistently with the toolchain root, parallel to
# MODERN_OBJDUMP/MODERN_LD above.
ifeq ($(MODERN_TOOLCHAIN_ROOT),)
  MODERN_OBJCOPY ?= $(PREFIX)objcopy$(EXE)
else
  MODERN_OBJCOPY ?= $(MODERN_TOOLCHAIN_ROOT)/bin/$(PREFIX)objcopy$(EXE)
endif

# Library discovery at recipe time (space-safe, no xargs).
MODERN_LIBGCC_DIR = $(shell p=$$("$(MODERN_CC)" $(MODERN_BINUTILS_FLAG) \
	$(MODERN_ARCH_FLAGS) -print-libgcc-file-name 2>/dev/null) \
	&& dirname "$$p")
MODERN_NEWLIB_LIB ?=
ifeq ($(MODERN_NEWLIB_LIB),)
  MODERN_LIBC_DIR = $(shell p=$$("$(MODERN_CC)" $(MODERN_BINUTILS_FLAG) \
	$(MODERN_ARCH_FLAGS) -print-file-name=libc.a 2>/dev/null) \
	&& dirname "$$p")
else
  MODERN_LIBC_DIR = $(MODERN_NEWLIB_LIB)
endif

# Modern FE6 SIO payload: compile mgfembp submodule with modern GCC.
# Prerequisites are explicit file targets for correct incrementality.
MODERN_MGFEMBP_C_SRCS := $(sort $(wildcard mgfembp/src/*.c))
MODERN_MGFEMBP_S_SRCS := $(sort $(wildcard mgfembp/src/*.s))
MODERN_MGFEMBP_HEADERS := $(sort $(wildcard mgfembp/include/*.h))
MODERN_MGFEMBP_LDS := mgfembp/mgfembp.lds
MODERN_MGFEMBP_EMBED_ASSETS := \
	mgfembp/data/debug_font.png \
	mgfembp/data/message_gfx.png \
	mgfembp/data/message_tm_1.bin \
	mgfembp/data/message_tm_2.bin \
	mgfembp/data/message_tm_3.bin

$(MODERN_MGFEMBP_PAYLOAD): scripts/modernize/build_mgfembp.py \
		$(MODERN_MGFEMBP_LDS) $(MODERN_MGFEMBP_C_SRCS) \
		$(MODERN_MGFEMBP_S_SRCS) $(MODERN_MGFEMBP_HEADERS) \
		$(MODERN_MGFEMBP_EMBED_ASSETS) \
		$(GBAGFX) | expansion-modern-toolchain-check
	$(MODERN_MGFEMBP_BUILDER) \
		--submodule-root mgfembp \
		--output-dir "$(MODERN_MGFEMBP_DIR)" \
		--cc "$(MODERN_CC)" \
		--ld "$(MODERN_LD)" \
		--objcopy "$(MODERN_OBJCOPY)" \
		--gbagfx "$(GBAGFX)" \
		$(if $(MODERN_BINUTILS_DIR),--binutils-dir "$(MODERN_BINUTILS_DIR)") \
		$(if $(MODERN_NEWLIB_LIB),--newlib-lib "$(MODERN_NEWLIB_LIB)") \
		$(foreach a,$(MODERN_MGFEMBP_EMBED_ASSETS),--embed-asset "$a")

.PHONY: expansion-modern-mgfembp
expansion-modern-mgfembp: $(MODERN_MGFEMBP_PAYLOAD)

# Assemble fe6sio.s with -I pointing to modern payload directory
# so .incbin "fe6sio_payload.bin.lz" resolves to modern output.
$(MODERN_FE6SIO_OBJ): asm/fe6sio.s $(MODERN_MGFEMBP_PAYLOAD)
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_ASFLAGS) \
		-Wa,-I,"$(MODERN_MGFEMBP_DIR)" \
		-Wa,--MD,"$(@:.o=.d)" -c "$<" -o "$@"

# Manifest: source-relative paths of every modern-built object.
$(MODERN_ELF_MANIFEST): $(MODERN_ALL_OBJECTS) $(MODERN_ELF_EXTRA_ASM_OBJECTS) $(MODERN_ELF_FE6SIO)
	@mkdir -p "$(@D)"
	@printf '%s\n' $(sort \
		$(patsubst $(MODERN_OUTPUT_DIR)/%,%, \
			$(MODERN_ALL_OBJECTS) \
			$(MODERN_ELF_EXTRA_ASM_OBJECTS) \
			$(MODERN_ELF_FE6SIO))) > "$@"

# Ensure legacy non-C objects are fresh with full scaninc tracking.
# The outer expansion-modern-elf runs under NODEP=1 which disables
# scaninc for legacy rules.  This phony step invokes a single recursive
# $(MAKE) with NODEP=0 to rebuild only the non-C assembly and MIDI
# objects with proper asset dependency tracking, using jobserver
# propagation (+).  No legacy C objects or mgfembp are invoked.
.PHONY: expansion-modern-legacy-ready
expansion-modern-legacy-ready:
	+$(MAKE) NODEP=0 $(MODERN_ELF_LEGACY_ASM) $(MODERN_ELF_LEGACY_MIDI)

# Link preparation: FE6 SIO build output, banim via scheduler, legacy
# freshness, sidecar recovery, then generator.  $(BANIM_OBJECT) is a
# normal prerequisite so the main scheduler builds it once with no
# recursive-make race.
.PHONY: expansion-modern-link-prepare
expansion-modern-link-prepare: $(MODERN_ELF_FE6SIO) \
		expansion-modern-legacy-ready \
		$(MODERN_ELF_MANIFEST) $(BANIM_OBJECT) \
		$(LDSCRIPT) $(SYM_FILES) linker_script_sound.txt
	@if [ ! -f "$(MODERN_ELF_BANIM_SYM)" ]; then \
		printf '%s\n' \
			"Sidecar missing; forcing banim rebuild..." >&2; \
		rm -f "$(BANIM_OBJECT)" "$(MODERN_ELF_BANIM_SYM)"; \
	fi
	+@if [ ! -f "$(MODERN_ELF_BANIM_SYM)" ]; then \
		$(MAKE) "$(BANIM_OBJECT)"; \
	fi
	@if [ ! -f "$(MODERN_ELF_BANIM_SYM)" ]; then \
		printf '%s\n' \
			"error: $(MODERN_ELF_BANIM_SYM) not produced" >&2; \
		exit 1; \
	fi
	$(MODERN_LINK_GENERATOR) \
		--root . \
		--modern-root "$(MODERN_OUTPUT_DIR)" \
		--manifest "$(MODERN_ELF_MANIFEST)" \
		--output-dir "$(MODERN_ELF_LINK_DIR)" \
		--legacy-objects \
			$(MODERN_ELF_FE6SIO) $(BANIM_OBJECT) \
			$(MODERN_ELF_LEGACY_ASM) \
			$(MODERN_ELF_LEGACY_MIDI)

# Link the transitional modern ELF.
# Legacy objects and banim are owned by expansion-modern-link-prepare.
$(MODERN_ELF): expansion-modern-link-prepare
	@set -eu; \
	libgcc_dir="$(MODERN_LIBGCC_DIR)"; \
	libc_dir="$(MODERN_LIBC_DIR)"; \
	if [ -z "$$libgcc_dir" ] || [ ! -d "$$libgcc_dir" ]; then \
		printf '%s\n' \
			"error: cannot discover modern libgcc directory" >&2; \
		printf '%s\n' \
			"Check MODERN_CC or set library paths explicitly." >&2; \
		exit 1; \
	fi; \
	if [ -z "$$libc_dir" ] || [ ! -d "$$libc_dir" ]; then \
		printf '%s\n' \
			"error: cannot discover modern libc directory" >&2; \
		printf '%s\n' \
			"Set MODERN_NEWLIB_LIB to the directory containing" \
			"libc.a." >&2; \
		exit 1; \
	fi; \
	"$(MODERN_LD)" \
		-T "$(MODERN_ELF_LDSCRIPT)" \
		-Map "$(MODERN_MAP)" \
		@"$(MODERN_ELF_OBJECTS_LST)" \
		-R "$(MODERN_ELF_BANIM_SYM)" \
		-L "$$libgcc_dir" \
		-L "$$libc_dir" \
		-o "$@" \
		-lc -lnosys -lgcc
	@printf 'Linked modern ELF: %s\n' "$@"

expansion-modern-elf: expansion-modern-mgfembp expansion-modern-all \
		$(MODERN_ELF_EXTRA_ASM_OBJECTS) $(MODERN_ELF)
	@printf 'Modern ELF ready: %s\n' "$(MODERN_ELF)"

# ---------------------------------------------------------------------------
# Modern ROM and deterministic boot-check targets (issue #3)
#
# expansion-modern-rom converts the transitional modern ELF to an isolated,
# header-verified 16 MiB GBA ROM using the same objcopy flags as the legacy
# %.gba rule (see Makefile). expansion-modern-boot-check runs the existing
# gba-playtest behavior-policy verifier against that ROM and the repository's
# boot scenario/fingerprint, proving deterministic runtime progress (not just
# a successful link) at frames 0/60/120. Neither target claims ROM byte
# identity with the legacy build; both remain part of issue-#3 transitional
# infrastructure alongside expansion-modern-elf's linker debt.
# ---------------------------------------------------------------------------

MODERN_ROM := $(MODERN_OUTPUT_DIR)/fireemblem8.gba
MODERN_ROM_HEADER_VERIFIER := scripts/modernize/verify_rom_header.py
MODERN_BOOT_SCENARIO := tools/gba-playtest/scenarios/boot.json
MODERN_BOOT_FINGERPRINT := tools/gba-playtest/fingerprints/boot.json
MODERN_PLAYTEST := tools/gba-playtest/gba_playtest.py

# Convert the linked ELF to a flat, padded ROM image, then verify the GBA
# header in-place: exact 16 MiB size, title/game code/maker code/fixed byte,
# and the checksum byte at offset 0xBD recomputed over 0xA0..0xBC. A failed
# verification deletes the ROM so a stale, invalid image is never left behind
# for expansion-modern-boot-check (or a caller) to pick up silently.
$(MODERN_ROM): $(MODERN_ELF)
	@mkdir -p "$(@D)"
	"$(MODERN_OBJCOPY)" --strip-debug -O binary --pad-to 0x9000000 --gap-fill=0xff "$<" "$@"
	@if ! "$(PYTHON)" "$(MODERN_ROM_HEADER_VERIFIER)" "$@"; then \
		rm -f "$@"; \
		exit 1; \
	fi

expansion-modern-rom: expansion-modern-elf $(MODERN_ROM)
	@printf 'Modern ROM ready: %s (config=%s abi=%s)\n' \
		"$(MODERN_ROM)" '$(MODERN_CONFIG)' '$(MODERN_ABI)'

# Preflight the libmGBA-backed playtest backend before spending time building
# the ROM, with an actionable error pointing at the same backend-check
# subcommand used by tools/gba-playtest's own tests.
.PHONY: expansion-modern-boot-preflight
expansion-modern-boot-preflight:
	@if [ ! -f "$(MODERN_BOOT_SCENARIO)" ] || [ ! -f "$(MODERN_BOOT_FINGERPRINT)" ]; then \
		printf '%s\n' \
			"error: missing boot scenario or fingerprint" >&2; \
		printf '  scenario:    %s\n' "$(MODERN_BOOT_SCENARIO)" >&2; \
		printf '  fingerprint: %s\n' "$(MODERN_BOOT_FINGERPRINT)" >&2; \
		exit 1; \
	fi
	@if ! "$(PYTHON)" "$(MODERN_PLAYTEST)" backend-check; then \
		printf '%s\n' \
			"error: libmGBA playtest backend is unavailable" >&2; \
		printf '%s\n' \
			"Install a C compiler and libmgba-dev (or equivalent)," \
			"then rerun: $(PYTHON) $(MODERN_PLAYTEST) backend-check" >&2; \
		exit 1; \
	fi

# Full 3-checkpoint behavior-policy boot gate: proves the modern ROM reaches
# the same deterministic runtime state as the legacy ROM at frames 0/60/120
# (not merely that it links). --policy behavior is required here because the
# modern ROM is not byte-identical to the legacy ROM referenced by the
# checked-in fingerprint's own "rom" stanza; only --policy exact-rom would
# additionally require ROM identity, which this target does not claim.
expansion-modern-boot-check: expansion-modern-boot-preflight expansion-modern-rom
	"$(PYTHON)" "$(MODERN_PLAYTEST)" verify \
		--rom "$(MODERN_ROM)" \
		--scenario "$(MODERN_BOOT_SCENARIO)" \
		--expected "$(MODERN_BOOT_FINGERPRINT)" \
		--policy behavior
	@printf 'Modern ROM boot-check passed: %s (config=%s abi=%s)\n' \
		"$(MODERN_ROM)" '$(MODERN_CONFIG)' '$(MODERN_ABI)'

-include $(wildcard $(sort $(MODERN_COHORT_DEPS) $(MODERN_ALL_DEPS) $(MODERN_FE6SIO_OBJ:.o=.d)))
