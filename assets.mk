# Versioned asset-manifest framework (issue #60).
#
# The generated include is intentionally an ordinary Make dependency fragment:
# it attaches committed asset sources to their existing owning object rather
# than creating another runtime table, linker list, or opaque build product.

ASSET_MANIFEST ?= assets/manifest.json
ASSET_OUTPUT_DIR ?= build/generated/assets
ASSET_OUTPUT_MK := $(ASSET_OUTPUT_DIR)/asset_manifest.mk
ASSET_BANIM_DATA_ENTRIES := $(ASSET_OUTPUT_DIR)/banim/banim_data_entries.inc
ASSET_BANIM_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_defs.inc
ASSET_BANIM_DEFS_HEADER := $(ASSET_OUTPUT_DIR)/banim/banim_defs.h
ASSET_BANIM_RUNTIME_TEST_DEFS := $(ASSET_OUTPUT_DIR)/banim/banim_runtime_test_defs.h
ASSET_TOOL := $(PYTHON) -m scripts.assets
ASSET_TOOL_INPUTS := $(filter-out scripts/assets/tests/%,$(sort $(shell find scripts/assets -type f -name '*.py' -print)))

.PHONY: assets-validate assets-generate assets-check assets-clean assets-test

assets-validate:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" validate

assets-generate:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

assets-check:
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" check

assets-clean:
	$(ASSET_TOOL) --out-dir "$(ASSET_OUTPUT_DIR)" clean

assets-test:
	$(PYTHON) -m unittest discover -s scripts/assets/tests -v

# Remake this included Makefile before resolving object prerequisites. The
# emitted fragment lists every declared source directly on the existing
# chapter-table objects, including the configured modern output path.
$(ASSET_OUTPUT_MK): $(ASSET_MANIFEST) $(ASSET_TOOL_INPUTS)
	$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate

$(ASSET_BANIM_DATA_ENTRIES) $(ASSET_BANIM_DEFS) $(ASSET_BANIM_DEFS_HEADER) \
$(ASSET_BANIM_RUNTIME_TEST_DEFS): $(ASSET_OUTPUT_MK)
	@test -f $@

src/banim_data.o $(MODERN_OUTPUT_DIR)/src/banim_data.o: $(ASSET_BANIM_DATA_ENTRIES)

src/data_banimconf.o $(MODERN_OUTPUT_DIR)/src/data_banimconf.o: $(ASSET_BANIM_DEFS)

$(MODERN_OUTPUT_DIR)/src/banim_package_runtime_test.o: $(ASSET_BANIM_RUNTIME_TEST_DEFS)

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
