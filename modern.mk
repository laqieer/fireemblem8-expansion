# Opt-in modern GCC object cohort. This intentionally stops at relocatable
# objects; the matching ROM build and linker remain on the legacy pipeline.

MODERN_GOALS := expansion-modern-toolchain-check expansion-modern-cohort expansion-modern-clean
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
MODERN_LANGUAGE_FLAGS := -std=gnu11
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

# aapcs deliberately uses GCC's default ABI. It is provisional, not a final
# ABI selection. apcs-gnu exists for side-by-side migration experiments.
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
	printf 'Modern flags: ARM7TDMI Thumb/interwork; config=%s; provisional ABI=%s\n' \
		'$(MODERN_CONFIG)' '$(MODERN_ABI)'

expansion-modern-cohort: expansion-modern-toolchain-check $(MODERN_COHORT_OBJECTS)
	@printf 'Built %s modern relocatable objects in %s\n' \
		'$(words $(MODERN_COHORT_OBJECTS))' '$(MODERN_OUTPUT_DIR)'

$(MODERN_COHORT_OBJECTS): | expansion-modern-toolchain-check

$(MODERN_OUTPUT_DIR)/%.o: %.c
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD -MP -MF "$(@:.o=.d)" -MQ "$@" -c "$<" -o "$@"

# GAS's own --MD tracks uppercase .INCLUDE directives (e.g. macro.inc,
# gba.inc), so no cpp preprocessing or scaninc invocation is needed here.
$(MODERN_OUTPUT_DIR)/%.o: %.s
	@mkdir -p "$(@D)"
	"$(MODERN_CC)" $(MODERN_ASFLAGS) -Wa,--MD,"$(@:.o=.d)" -c "$<" -o "$@"

expansion-modern-clean:
	$(RM) -r "$(MODERN_BUILD_ROOT)"

-include $(wildcard $(MODERN_COHORT_DEPS))
