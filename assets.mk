# Versioned asset-manifest framework (issue #60).
#
# The generated include is intentionally an ordinary Make dependency fragment:
# it attaches committed asset sources to their existing owning object rather
# than creating another runtime table, linker list, or opaque build product.

ASSET_MANIFEST ?= assets/manifest.json
ASSET_OUTPUT_DIR ?= build/generated/assets
EXPANSION_CUSTOM_SPELL_EFFECTS ?= 0
ASSET_TOOL := $(PYTHON) -m scripts.assets --custom-spell-effects "$(EXPANSION_CUSTOM_SPELL_EFFECTS)"
ASSET_PORTRAIT_INCBIN_CONSUMERS := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" portrait-incbin-consumers)
ifneq ($(strip $(ASSET_PORTRAIT_INCBIN_CONSUMERS)),)
ifneq ($(ASSET_OUTPUT_DIR),build/generated/assets)
$(error assets.mk: ASSET_OUTPUT_DIR must be build/generated/assets while portrait package INCBIN consumer(s) $(ASSET_PORTRAIT_INCBIN_CONSUMERS) are declared)
endif
endif

ASSET_TMX_INCBIN_CONSUMERS := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" tmx-incbin-consumers)
ifneq ($(strip $(ASSET_TMX_INCBIN_CONSUMERS)),)
ifneq ($(ASSET_OUTPUT_DIR),build/generated/assets)
$(error assets.mk: ASSET_OUTPUT_DIR must be build/generated/assets while TMX map-layout INCBIN consumer(s) $(ASSET_TMX_INCBIN_CONSUMERS) are declared)
endif
endif

ASSET_BANIM_INCBIN_CONSUMERS := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" banim-incbin-consumers)
ifneq ($(strip $(ASSET_BANIM_INCBIN_CONSUMERS)),)
ifneq ($(ASSET_OUTPUT_DIR),build/generated/assets)
$(error assets.mk: ASSET_OUTPUT_DIR must be build/generated/assets while battle-animation package INCBIN consumer(s) $(ASSET_BANIM_INCBIN_CONSUMERS) are declared)
endif
endif

ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" custom-spell-incbin-consumers)
ifneq ($(strip $(ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS)),)
ifneq ($(ASSET_OUTPUT_DIR),build/generated/assets)
$(error assets.mk: ASSET_OUTPUT_DIR must be build/generated/assets while custom-spell-effect INCBIN consumer(s) $(ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS) are declared)
endif
endif

ASSET_OUTPUT_MK := $(ASSET_OUTPUT_DIR)/asset_manifest.mk
# The generated fragment shares a stable path because consumers include it
# directly. Record the active manifest/profile outside the checked output
# tree so switching profiles rebuilds that fragment even when both manifests
# predate it.
ASSET_SELECTION_STAMP := $(ASSET_OUTPUT_DIR).manifest-selection
ASSET_BANIM_DATA_ENTRIES := $(ASSET_OUTPUT_DIR)/banim/banim_data_entries.inc
ASSET_BANIM_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_defs.inc
ASSET_BANIM_DEFS_HEADER := $(ASSET_OUTPUT_DIR)/banim/banim_defs.h
ASSET_BANIM_RUNTIME_TEST_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_runtime_test_defs.h
ASSET_BANIM_RUNTIME_SYMBOLS := $(ASSET_OUTPUT_DIR)/banim/banim_runtime_symbols.h
ASSET_BANIM_COMBINED_LINKER_SCRIPT := $(ASSET_OUTPUT_DIR)/banim/linker_script_banim.txt
ASSET_TOOL_INPUTS := $(filter-out scripts/assets/tests/%,$(sort $(shell find scripts/assets -type f -name '*.py' -print)))
ASSET_MANIFEST_SOURCES := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" sources)

.PHONY: assets-validate assets-generate assets-check assets-clean assets-test

assets-validate:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" validate

assets-generate:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

assets-check:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" check

assets-clean:
	$(PYTHON) -m scripts.assets --out-dir "$(ASSET_OUTPUT_DIR)" clean
	$(RM) -f "$(ASSET_SELECTION_STAMP)"

assets-test:
	env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES \
		-u ASSET_MANIFEST -u ASSET_OUTPUT_DIR \
		-u EXPANSION_CUSTOM_SPELL_EFFECTS \
		$(PYTHON) -m unittest discover -s scripts/assets/tests -v

# Remake this included Makefile before resolving object prerequisites. The
# emitted fragment lists every declared source directly on the existing
# chapter-table objects, including the configured modern output path.
.PHONY: FORCE_ASSET_SELECTION
FORCE_ASSET_SELECTION:

$(ASSET_SELECTION_STAMP): FORCE_ASSET_SELECTION
	@mkdir -p "$(dir $@)"
	@printf '%s\n' \
		'manifest=$(abspath $(ASSET_MANIFEST))' \
		'custom_spell_effects=$(EXPANSION_CUSTOM_SPELL_EFFECTS)' \
		'item_id_cap=$(FE8_ITEM_ID_CAP)' > "$@.tmp"
	@if test -f "$@" && cmp -s "$@.tmp" "$@"; then \
		rm -f "$@.tmp"; \
	else \
		mv -f "$@.tmp" "$@"; \
	fi

$(ASSET_OUTPUT_MK): $(ASSET_SELECTION_STAMP) $(ASSET_MANIFEST) $(ASSET_MANIFEST_SOURCES) $(ASSET_TOOL_INPUTS)
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

$(ASSET_BANIM_DATA_ENTRIES) $(ASSET_BANIM_DEFS) $(ASSET_BANIM_DEFS_HEADER) \
$(ASSET_BANIM_RUNTIME_TEST_DEFS) $(ASSET_BANIM_RUNTIME_SYMBOLS) &: $(ASSET_OUTPUT_MK)
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate
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
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

# A strict maintenance/check command must report a missing or stale output
# instead of Make remaking this include before the target runs. Any ordinary
# build goal (including bare `make`) still remakes and includes it before the
# dependency graph is resolved.
ASSET_MAINTENANCE_GOALS := assets-validate assets-generate assets-check assets-clean assets-test
ifneq ($(MAKECMDGOALS),)
ifneq ($(filter-out $(ASSET_MAINTENANCE_GOALS),$(MAKECMDGOALS)),)
-include $(ASSET_OUTPUT_MK)
endif
else
-include $(ASSET_OUTPUT_MK)
endif
