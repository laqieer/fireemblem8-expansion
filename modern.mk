# Opt-in modern GCC object cohort and transitional modern ELF target.

MODERN_GOALS := expansion-modern-toolchain-check expansion-modern-cohort expansion-modern-all expansion-modern-elf expansion-modern-clean
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

MODERN_ALL_C_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_C_SOURCES:.c=.o))
MODERN_ALL_DATA_PRE := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_DATA_C_SOURCES:.c=.pre.c))
MODERN_ALL_DATA_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_DATA_C_SOURCES:.c=.o))
MODERN_ALL_ASM_OBJECTS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_ASM_SOURCES:.s=.o))
MODERN_ALL_OBJECTS := $(MODERN_ALL_C_OBJECTS) $(MODERN_ALL_DATA_OBJECTS) $(MODERN_ALL_ASM_OBJECTS)
MODERN_ALL_DEPS := $(MODERN_ALL_C_OBJECTS:.o=.d) $(MODERN_ALL_DATA_OBJECTS:.o=.d) $(MODERN_ALL_ASM_OBJECTS:.o=.d)

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
$(MODERN_OUTPUT_DIR)/src/agb_sram.o: MODERN_CFLAGS += -fdata-sections
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
# Asset dependency tracking deliberately does not gate on NODEP: every modern
# goal above already forces NODEP=1, so an "ifeq ($(NODEP),1) skip scaninc"
# guard here would silently disable INCBIN rebuild tracking for the modern
# build itself. Instead, each object's single .d file (generated by modern
# GCC's own -MMD/-MP for the .pre.c/header chain) has a second dependency
# rule appended after compilation, sourced from scaninc's scan of the
# original, un-preprocessed .c file. That appended rule takes effect on the
# next make invocation (once the .d is generated and -included below),
# exactly like GCC's own header tracking already works one build later.
$(MODERN_ALL_DATA_PRE): $(MODERN_OUTPUT_DIR)/%.pre.c: %.c $(MODERN_PREPROC)
	@mkdir -p "$(@D)"
	@if [ ! -x "$(MODERN_PREPROC)" ]; then \
		printf '%s\n' "error: modern preprocessor not found or not executable: $(MODERN_PREPROC)" >&2; \
		exit 1; \
	fi
	"$(MODERN_PREPROC)" "$<" > "$@" || { rm -f "$@"; exit 1; }

$(MODERN_ALL_DATA_OBJECTS): $(MODERN_OUTPUT_DIR)/%.o: $(MODERN_OUTPUT_DIR)/%.pre.c
	@mkdir -p "$(@D)"
	@if [ ! -x "$(MODERN_SCANINC)" ]; then \
		printf '%s\n' "error: modern scaninc not found or not executable: $(MODERN_SCANINC)" >&2; \
		exit 1; \
	fi
	"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" -MQ "$@" -c "$<" -o "$@"
	@assets=$$("$(MODERN_SCANINC)" -I include -I "" "$*.c") || { \
		printf '%s\n' "error: scaninc failed while scanning $*.c for INCBIN assets" >&2; \
		exit 1; \
	}; \
	assets=$$(printf '%s' "$$assets" | tr '\n' ' '); \
	printf '%s: %s %s\n' "$(MODERN_OUTPUT_DIR)/$*.pre.c" "$*.c" "$$assets" >> "$(@:.o=.d)"

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
# asm/fe6sio.o is a pre-existing transitional object.  The FE6 SIO
# object requires the legacy mgfembp preparation path; see
# docs/quickstart.md.  This target never silently invokes that build.
# ---------------------------------------------------------------------------

# Enforce AAPCS for linked outputs.
ifneq (,$(filter expansion-modern-elf,$(MAKECMDGOALS)))
  ifneq ($(MODERN_ABI),aapcs)
    $(error expansion-modern-elf requires MODERN_ABI=aapcs; \
      apcs-gnu objects are incompatible with EABI5 newlib/libgcc)
  endif
endif

# Link-only assembly objects not in expansion-modern-all.
MODERN_ELF_EXTRA_ASM_SOURCES := src/rom_header.s src/crt0.s src/m4a_1.s
MODERN_ELF_EXTRA_ASM_OBJECTS := \
	$(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ELF_EXTRA_ASM_SOURCES:.s=.o))

# All assembly sources replaced by modern objects (source-relative names).
MODERN_ELF_REPLACED_ASM := \
	$(MODERN_ALL_ASM_SOURCES:.s=.o) $(MODERN_ELF_EXTRA_ASM_SOURCES:.s=.o)

# Pre-existing transitional FE6 SIO object (not a scheduler target).
MODERN_ELF_FE6SIO := asm/fe6sio.o

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

# Manifest: source-relative paths of every modern-built object.
$(MODERN_ELF_MANIFEST): $(MODERN_ALL_OBJECTS) $(MODERN_ELF_EXTRA_ASM_OBJECTS)
	@mkdir -p "$(@D)"
	@printf '%s\n' $(sort \
		$(patsubst $(MODERN_OUTPUT_DIR)/%,%, \
			$(MODERN_ALL_OBJECTS) \
			$(MODERN_ELF_EXTRA_ASM_OBJECTS))) > "$@"

# Lightweight FE6 SIO preflight (no toolchain/manifest deps).
.PHONY: expansion-modern-fe6sio-check
expansion-modern-fe6sio-check:
	@if [ ! -f "$(MODERN_ELF_FE6SIO)" ]; then \
		printf '%s\n' \
			"error: pre-existing object missing: $(MODERN_ELF_FE6SIO)" >&2; \
		printf '%s\n' \
			"FE6 SIO object requires the legacy mgfembp" \
			"preparation path; see docs/quickstart.md" >&2; \
		exit 1; \
	fi

# Ensure legacy non-C objects are fresh with full scaninc tracking.
# The outer expansion-modern-elf runs under NODEP=1 which disables
# scaninc for legacy rules.  This phony step invokes a single recursive
# $(MAKE) with NODEP=0 to rebuild only the non-C assembly and MIDI
# objects with proper asset dependency tracking, using jobserver
# propagation (+).  No legacy C objects or mgfembp are invoked.
.PHONY: expansion-modern-legacy-ready
expansion-modern-legacy-ready:
	+$(MAKE) NODEP=0 $(MODERN_ELF_LEGACY_ASM) $(MODERN_ELF_LEGACY_MIDI)

# Link preparation: FE6 preflight, banim via scheduler, legacy
# freshness, sidecar recovery, then generator.  $(BANIM_OBJECT) is a
# normal prerequisite so the main scheduler builds it once with no
# recursive-make race.
.PHONY: expansion-modern-link-prepare
expansion-modern-link-prepare: expansion-modern-fe6sio-check \
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

expansion-modern-elf: expansion-modern-all \
		$(MODERN_ELF_EXTRA_ASM_OBJECTS) $(MODERN_ELF)
	@printf 'Modern ELF ready: %s\n' "$(MODERN_ELF)"

-include $(wildcard $(sort $(MODERN_COHORT_DEPS) $(MODERN_ALL_DEPS)))
