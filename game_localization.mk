# game_localization.mk -- focused developer/CI targets for the
# deterministic full-game localized catalog generator. Every selected CJK
# profile also emits one shared modern English source bundle.
#
# Included by the top-level Makefile, but standalone on purpose: none of these
# targets is a prerequisite of `all`, so default English builds never generate
# full-game locale payloads unless a caller opts in explicitly.

GAME_LOCALIZATION_OUT_DIR ?= $(if $(filter-out default,$(FE8_PROJECT)),$(FE8_PROJECT_OUTPUT_DIR)/generated/game-localization,build/game-localization/generated)
GAME_LOCALIZATION_DEFAULT_LOCALES := ja,zh-Hans
GAME_LOCALIZATION_ENABLED_LOCALES ?= $(if $(filter-out default,$(FE8_PROJECT)),$(MODERN_GAME_LOCALIZATION_CATALOG_LOCALES),$(GAME_LOCALIZATION_DEFAULT_LOCALES))
GAME_LOCALIZATION_AUDIT_ARGS = $(if $(filter-out default,$(FE8_PROJECT)),--report "$(GAME_LOCALIZATION_OUT_DIR)/game_localization_latin_span_audit.json")
PYTHON3 ?= python3
PYTEST_ENV := PYTHONDONTWRITEBYTECODE=1

.PHONY: game-localization-validate game-localization-generate \
	game-localization-check game-localization-test game-localization-budget \
	game-localization-width-check \
	game-localization-leakage-audit game-localization-leakage-check \
	game-localization-final-authored-check \
	game-localization-final-mapping-check \
	game-localization-final-raw-closure-check \
	game-localization-final-leakage-audit \
	game-localization-final-font-check \
	game-localization-text-edits-generate \
	game-localization-text-edits-check \
	game-localization-eu-check \
	game-localization-final-check

game-localization-validate:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog validate \
		$(FE8_PROJECT_GAME_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-generate:
	@mkdir -p $(GAME_LOCALIZATION_OUT_DIR)
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog generate \
		$(FE8_PROJECT_GAME_ARGS) \
		--out-dir $(GAME_LOCALIZATION_OUT_DIR) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-check: game-localization-generate
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog check-leakage \
		$(FE8_PROJECT_GAME_ARGS) $(GAME_LOCALIZATION_AUDIT_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog check-width \
		$(FE8_PROJECT_GAME_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-width-check:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog check-width \
		$(FE8_PROJECT_GAME_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-text-edits-generate:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_locales.text_edit_ledger generate

game-localization-text-edits-check:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_locales.text_edit_ledger check

game-localization-leakage-audit:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog audit-leakage \
		$(FE8_PROJECT_GAME_ARGS) $(GAME_LOCALIZATION_AUDIT_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-leakage-check:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog check-leakage \
		$(FE8_PROJECT_GAME_ARGS) $(GAME_LOCALIZATION_AUDIT_ARGS) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-test:
	$(PYTEST_ENV) $(PYTHON3) -m unittest discover \
		-s scripts/localization/game_catalog/tests -p 'test_*.py' -v
	$(PYTEST_ENV) $(PYTHON3) -m unittest discover \
		-s scripts/localization/game_locales/tests -p 'test_*.py' -v
	$(PYTEST_ENV) $(PYTHON3) scripts/texttools/tests/test_text_renderer_native.py
	$(PYTEST_ENV) $(PYTHON3) scripts/texttools/tests/test_text_consumers_native.py
	$(PYTEST_ENV) $(PYTHON3) scripts/texttools/tests/test_text_consumer_audit.py
	$(MAKE) --no-print-directory game-localization-width-check
	$(MAKE) --no-print-directory game-localization-text-edits-check

game-localization-budget:
	@mkdir -p $(GAME_LOCALIZATION_OUT_DIR)
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog budget \
		$(FE8_PROJECT_GAME_ARGS) \
		--out-dir $(GAME_LOCALIZATION_OUT_DIR) \
		--enabled-locales "$(GAME_LOCALIZATION_ENABLED_LOCALES)"

game-localization-eu-check:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog validate \
		--enabled-locales "fr,de,es,it"

game-localization-final-authored-check:
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_locales \
		check-authored-catalogs

game-localization-final-mapping-check: game-localization-final-authored-check
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_locales \
		check-final-mapping --require-no-fallback --require-live-origin

game-localization-final-raw-closure-check: game-localization-final-mapping-check
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_locales \
		check-raw-closure

game-localization-final-leakage-audit: game-localization-final-raw-closure-check
	$(PYTEST_ENV) $(PYTHON3) -m scripts.localization.game_catalog audit-leakage

game-localization-final-font-check: game-localization-final-leakage-audit
	$(MAKE) --no-print-directory -f cjk_fonts.mk cjk-fonts-check

game-localization-final-check: game-localization-final-font-check
