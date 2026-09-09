MAKEDEP = mkdir -p $(DEPS_DIR)/$(dir $*) && $(CPP) $(CPPFLAGS) $< -MM -MG -MT $*.o > $(DEPS_DIR)/$*.d

MAKECMDGOALS_NODEP := all clean tag codeql-alerts-test codeql-fanalyzer-test $(MODERN_GOALS) \
	assets-validate assets-generate assets-check assets-test \
	generated-data-validate generated-data-generate generated-data-check generated-data-test \
	localization-validate localization-generate localization-check localization-test localization-budget \
	game-localization-validate game-localization-generate \
	game-localization-check game-localization-test game-localization-budget \
	game-localization-leakage-audit game-localization-leakage-check \
	game-localization-final-authored-check \
	game-localization-final-mapping-check \
	game-localization-final-raw-closure-check \
	game-localization-final-leakage-audit \
	game-localization-final-font-check game-localization-final-check

ifneq ($(strip $(MAKECMDGOALS)),)
ifneq (,$(filter-out $(MAKECMDGOALS_NODEP),$(MAKECMDGOALS)))
-include $(addprefix $(DEPS_DIR)/,$(patsubst %.c,%.d,$(filter-out $(CFILES_GENERATED),$(CFILES))))
endif
endif

$(DEPS_DIR)/%.d: %.c
	@$(MAKEDEP)
