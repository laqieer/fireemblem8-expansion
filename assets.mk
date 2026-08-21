# Versioned asset-manifest framework (issue #60).
#
# The generated include is intentionally an ordinary Make dependency fragment:
# it attaches committed asset sources to their existing owning object rather
# than creating another runtime table, linker list, or opaque build product.

ASSET_MANIFEST ?= assets/manifest.json
ASSET_OUTPUT_DIR ?= build/generated/assets
ifneq ($(ASSET_OUTPUT_DIR),build/generated/assets)
$(error assets.mk: ASSET_OUTPUT_DIR must be build/generated/assets while TMX map layouts are INCBIN consumers)
endif

ASSET_OUTPUT_MK := $(ASSET_OUTPUT_DIR)/asset_manifest.mk
ASSET_TOOL := $(PYTHON) -m scripts.assets
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
	$(ASSET_TOOL) --out-dir "$(ASSET_OUTPUT_DIR)" clean

assets-test:
	$(PYTHON) -m unittest discover -s scripts/assets/tests -v

# Remake this included Makefile before resolving object prerequisites. The
# emitted fragment lists every declared source directly on the existing
# chapter-table objects, including the configured modern output path.
$(ASSET_OUTPUT_MK): $(ASSET_MANIFEST) $(ASSET_MANIFEST_SOURCES) $(ASSET_TOOL_INPUTS)
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
