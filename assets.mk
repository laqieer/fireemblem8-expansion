# Versioned asset-manifest framework (issue #60).
#
# The generated include is intentionally an ordinary Make dependency fragment:
# it attaches committed asset sources to their existing owning object rather
# than creating another runtime table, linker list, or opaque build product.

ASSET_MANIFEST ?= assets/manifest.json
EXPANSION_CUSTOM_SPELL_EFFECTS ?= 0
MODERN_BUILD_ROOT ?= build/expansion-modern
# Resolve the cap from Make's selected configuration rather than allowing the
# asset CLI to read an ambient environment/default value. Standalone assets.mk
# invocations use the same explicit, safely quoted resolver as generated data.
ifeq ($(strip $(GENERATED_DATA_ITEM_CAP)),)
ASSET__SQ := '
ASSET_ITEM_CAP_SHELL_ARG := $(ASSET__SQ)$(subst $(ASSET__SQ),$(ASSET__SQ)\$(ASSET__SQ)$(ASSET__SQ),$(FE8_ITEM_ID_CAP))$(ASSET__SQ)
ASSET_RESOLVED_ITEM_ID_CAP := $(shell FE8_ITEM_ID_CAP=$(ASSET_ITEM_CAP_SHELL_ARG) $(PYTHON) -c "import scripts.generated_data.idspace as i; print('0x%02X' % i.resolve_item_id_cap())" 2>/dev/null)
else
ASSET_RESOLVED_ITEM_ID_CAP := $(GENERATED_DATA_ITEM_CAP)
endif
ifeq ($(ASSET_RESOLVED_ITEM_ID_CAP),)
$(error FE8_ITEM_ID_CAP='$(FE8_ITEM_ID_CAP)' is not a valid item ID cap; see scripts/generated_data/idspace.py resolve_item_id_cap)
endif
ASSET_MANIFEST_KEY := $(subst /,_,$(subst .,_,$(abspath $(ASSET_MANIFEST))))
ASSET_PROFILE_KEY := $(ASSET_MANIFEST_KEY)-custom$(EXPANSION_CUSTOM_SPELL_EFFECTS)-cap$(ASSET_RESOLVED_ITEM_ID_CAP)
ifeq ($(strip $(MODERN_OUTPUT_DIR)),)
ASSET_PROFILE_ROOT := build/generated/assets
ASSET_DISCOVERY_ROOT := build/generated/asset-discovery
ASSET_OUTPUT_DIR ?= $(ASSET_PROFILE_ROOT)
else
ASSET_PROFILE_ROOT := $(MODERN_BUILD_ROOT)/generated/assets
ASSET_DISCOVERY_ROOT := $(MODERN_BUILD_ROOT)/generated/asset-discovery
ASSET_OUTPUT_DIR ?= $(ASSET_PROFILE_ROOT)/$(ASSET_PROFILE_KEY)
endif
ASSET_TOOL := $(PYTHON) -m scripts.assets --custom-spell-effects "$(EXPANSION_CUSTOM_SPELL_EFFECTS)" --item-id-cap "$(ASSET_RESOLVED_ITEM_ID_CAP)"
ASSET_OUTPUT_MK := $(ASSET_OUTPUT_DIR)/asset_manifest.mk
ASSET_DISCOVERY_KEY := $(subst /,_,$(subst .,_,$(ASSET_OUTPUT_DIR)))
ASSET_DISCOVERY_MK := $(ASSET_DISCOVERY_ROOT)/$(ASSET_DISCOVERY_KEY).mk
ASSET_MANIFEST_SOURCE_STAMP := $(ASSET_DISCOVERY_MK)
ifneq ($(MAKECMDGOALS),validation-ownership-check)
-include $(ASSET_DISCOVERY_MK)
endif

# Every modern build root/profile owns an independent generated asset tree.
# Compile-time path definitions select that tree for the existing source-owned
# consumers, so a concurrent profile cannot prune or overwrite another
# compiler's include/data files after generation releases its owner lock.
ASSET_SELECTION_STAMP := $(ASSET_OUTPUT_DIR).manifest-selection
ASSET_GENERATE_TOOL := $(ASSET_TOOL) --selection-stamp "$(ASSET_SELECTION_STAMP)"
ASSET_BANIM_DATA_ENTRIES := $(ASSET_OUTPUT_DIR)/banim/banim_data_entries.inc
ASSET_BANIM_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_defs.inc
ASSET_BANIM_DEFS_HEADER := $(ASSET_OUTPUT_DIR)/banim/banim_defs.h
ASSET_BANIM_RUNTIME_TEST_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_runtime_test_defs.h
ASSET_BANIM_RUNTIME_SYMBOLS := $(ASSET_OUTPUT_DIR)/banim/banim_runtime_symbols.h
ASSET_BANIM_COMBINED_LINKER_SCRIPT := $(ASSET_OUTPUT_DIR)/banim/linker_script_banim.txt
ASSET_TOOL_INPUTS := $(filter-out scripts/assets/tests/%,$(sort $(shell find scripts/assets -type f -name '*.py' -print)))
ASSET_PREPROC_FLAGS := -Rbuild/generated/assets/tmx/CH2_MAIN_MAP.bin.lz=$(ASSET_OUTPUT_DIR)/tmx/CH2_MAIN_MAP.bin.lz
ASSET_INCLUDE_FLAGS := -I$(ASSET_OUTPUT_DIR) -I$(ASSET_OUTPUT_DIR)/banim -I$(ASSET_OUTPUT_DIR)/custom_spell
MODERN_CFLAGS += $(ASSET_INCLUDE_FLAGS)
MODERN_PREPROC_FLAGS += $(ASSET_PREPROC_FLAGS)
CPPFLAGS += $(ASSET_INCLUDE_FLAGS)
PREPROC_FLAGS += $(ASSET_PREPROC_FLAGS)

# GCC dependency files record generated headers included through
# ASSET_INCLUDE_FLAGS by basename. These aliases never write shared files;
# they map each basename back to this invocation's profile-qualified producer.
custom_spell_effect_generated.h: $(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_generated.h ;
custom_spell_effect_runtime_test.h: $(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_runtime_test.h ;
custom_spell_effect_data.inc: $(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_data.inc ;
custom_spell_effect_spellassoc.inc: $(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_spellassoc.inc ;
banim_runtime_symbols.h: $(ASSET_BANIM_RUNTIME_SYMBOLS) ;
banim_data_entries.inc: $(ASSET_BANIM_DATA_ENTRIES) ;
banim_defs.h: $(ASSET_BANIM_DEFS_HEADER) ;
banim_runtime_test_defs.h: $(ASSET_BANIM_RUNTIME_TEST_DEFS) ;
banim_defs.inc: $(ASSET_BANIM_DEFS) ;
portrait_data.inc: $(ASSET_OUTPUT_DIR)/portrait_data.inc ;
portrait_components.inc: $(ASSET_OUTPUT_DIR)/portrait_components.inc ;
portrait_components.h: $(ASSET_OUTPUT_DIR)/portrait_components.h ;
build/generated/assets/tmx/CH2_MAIN_MAP.bin.lz: $(ASSET_OUTPUT_DIR)/tmx/CH2_MAIN_MAP.bin.lz ;

.PHONY: assets-validate assets-generate assets-check assets-clean assets-test print-ASSET_OUTPUT_DIR

print-ASSET_OUTPUT_DIR:
	@printf '%s\n' "$(ASSET_OUTPUT_DIR)"

assets-validate:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" validate

assets-generate:
	$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

assets-check:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" check

assets-clean:
	$(PYTHON) -m scripts.assets --out-dir "$(ASSET_OUTPUT_DIR)" clean
	$(RM) -f "$(ASSET_SELECTION_STAMP)" "$(ASSET_DISCOVERY_MK)" \
		"$(ASSET_OUTPUT_DIR).asset-manifest-generate.lock"

assets-test:
	env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES \
		-u ASSET_MANIFEST -u ASSET_OUTPUT_DIR \
		-u EXPANSION_CUSTOM_SPELL_EFFECTS \
		-u FE8_ITEM_ID_CAP \
		$(PYTHON) -m unittest discover -s scripts/assets/tests -v

# Remake this included Makefile before resolving object prerequisites. The
# emitted fragment lists every declared source directly on the existing
# chapter-table objects, including the configured modern output path.
.PHONY: FORCE_ASSET_SELECTION
FORCE_ASSET_SELECTION:
.PHONY: FORCE_ASSET_SOURCES
FORCE_ASSET_SOURCES:

$(ASSET_SELECTION_STAMP): FORCE_ASSET_SELECTION
	@mkdir -p "$(dir $@)"
	@tmp="$@.$$$$.tmp"; \
	trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	printf '%s\n' \
		'manifest=$(abspath $(ASSET_MANIFEST))' \
		'custom_spell_effects=$(EXPANSION_CUSTOM_SPELL_EFFECTS)' \
		'item_id_cap=$(ASSET_RESOLVED_ITEM_ID_CAP)' > "$$tmp"; \
	if test -f "$@" && cmp -s "$$tmp" "$@"; then \
		rm -f "$$tmp"; \
	else \
		mv -f "$$tmp" "$@"; \
	fi

ifeq ($(MAKE_RESTARTS),)
$(ASSET_DISCOVERY_MK): FORCE_ASSET_SOURCES
	@mkdir -p "$(dir $@)"
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --discovery-makefile "$@" discovery-makefile
else
$(ASSET_DISCOVERY_MK): ;
endif

$(ASSET_OUTPUT_MK): $(ASSET_SELECTION_STAMP) $(ASSET_MANIFEST_SOURCE_STAMP) $(ASSET_MANIFEST) $(ASSET_TOOL_INPUTS)
	$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

$(ASSET_BANIM_DATA_ENTRIES) $(ASSET_BANIM_DEFS) $(ASSET_BANIM_DEFS_HEADER) \
$(ASSET_BANIM_RUNTIME_TEST_DEFS) $(ASSET_BANIM_RUNTIME_SYMBOLS) &: $(ASSET_OUTPUT_MK)
	$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate
	@test -f $(ASSET_BANIM_DATA_ENTRIES)
	@test -f $(ASSET_BANIM_DEFS)
	@test -f $(ASSET_BANIM_DEFS_HEADER)
	@test -f $(ASSET_BANIM_RUNTIME_TEST_DEFS)
	@test -f $(ASSET_BANIM_RUNTIME_SYMBOLS)

src/banim_data.o $(MODERN_OUTPUT_DIR)/src/banim_data.o: $(ASSET_BANIM_DATA_ENTRIES) \
$(ASSET_BANIM_RUNTIME_SYMBOLS)

src/data_banimconf.o $(MODERN_OUTPUT_DIR)/src/data_banimconf.o: $(ASSET_BANIM_DEFS)

$(MODERN_OUTPUT_DIR)/src/banim_package_runtime_test.o: $(ASSET_BANIM_DEFS_HEADER) \
$(ASSET_BANIM_RUNTIME_TEST_DEFS)

# A normal build can request the derived TMX `.mar`/metadata pair after a
# clean or an interrupted asset generation. Regenerate both together before
# Make reaches the ordinary `.mar -> .bin -> .bin.lz` conversion chain.
$(ASSET_OUTPUT_DIR)/tmx/%.mar $(ASSET_OUTPUT_DIR)/tmx/%.json &: $(ASSET_OUTPUT_MK)
	$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

# A strict maintenance/check command must report a missing or stale output
# instead of Make remaking this include before the target runs. Any ordinary
# build goal (including bare `make`) still remakes and includes it before the
# dependency graph is resolved.
ASSET_MAINTENANCE_GOALS := assets-validate assets-generate assets-check assets-clean assets-test \
	validation-ownership-check
ifneq ($(MAKECMDGOALS),)
ifneq ($(filter-out $(ASSET_MAINTENANCE_GOALS),$(MAKECMDGOALS)),)
-include $(ASSET_OUTPUT_MK)
endif
else
-include $(ASSET_OUTPUT_MK)
endif
