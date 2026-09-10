MAKEDEP = mkdir -p $(DEPS_DIR)/$(dir $*) && $(CPP) $(CPPFLAGS) $< -MM -MG -MT $*.o > $(DEPS_DIR)/$*.d

# Pure host/default modern entrypoints must not eagerly remake unrelated
# archival dependency files or expand the explicit legacy object rules'
# scaninc prerequisites. Direct non-C object goals still need scaninc-based
# freshness, so they extend only the depfile-safe list, not the scan-safe one.
MAKECMDGOALS_NOSCANINC := all clean tag codeql-alerts-test codeql-fanalyzer-test \
	validation-ownership-check $(MODERN_GOALS) \
	assets-validate assets-generate assets-check assets-test \
	generated-data-validate generated-data-generate generated-data-check generated-data-test \
	localization-validate localization-generate localization-check localization-test localization-budget \
	game-localization-validate game-localization-generate \
	game-localization-check game-localization-test game-localization-budget \
	game-localization-width-check game-localization-text-edits-generate \
	game-localization-text-edits-check game-localization-eu-check \
	game-localization-leakage-audit game-localization-leakage-check \
	game-localization-final-authored-check \
	game-localization-final-mapping-check \
	game-localization-final-raw-closure-check \
	game-localization-final-leakage-audit \
	game-localization-final-font-check game-localization-final-check

MAKECMDGOALS_NODEP := $(MAKECMDGOALS_NOSCANINC) \
	$(filter-out $(C_OBJECTS) $(DATA_SRC_C_OBJECTS),$(ASM_OBJECTS) $(MID_OBJECTS) $(BANIM_OBJECT))

# Preserve explicit user NODEP settings. Otherwise, only all-safe pure
# modern/host requests inherit the implicit NODEP=1 suppression; mixed
# requests with direct legacy non-C objects keep real scaninc freshness.
ifeq ($(origin NODEP), undefined)
ifneq ($(strip $(MAKECMDGOALS)),)
ifeq (,$(filter-out $(MAKECMDGOALS_NOSCANINC),$(MAKECMDGOALS)))
NODEP := 1
endif
endif
endif

ARCHIVAL_SCANINC_NODEP := 1

ifneq ($(strip $(MAKECMDGOALS)),)
ifneq (,$(filter-out $(MAKECMDGOALS_NOSCANINC),$(MAKECMDGOALS)))
ARCHIVAL_SCANINC_NODEP :=
endif
ifneq (,$(filter-out $(MAKECMDGOALS_NODEP),$(MAKECMDGOALS)))
-include $(addprefix $(DEPS_DIR)/,$(patsubst %.c,%.d,$(filter-out $(CFILES_GENERATED),$(CFILES))))
endif
endif

$(DEPS_DIR)/%.d: %.c
	@$(MAKEDEP)
