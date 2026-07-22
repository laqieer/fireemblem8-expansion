# Standalone targets for scripts/generated_data (Issue #5 Slice 0 canary).
#
# Deliberately not wired into `all`/CI: this is a canary on a single table
# (SupportData) that does not yet replace src/data_supports.c. See
# docs/generated_data.md for scope and status.
#
# generated-data-generate : validate + write build/generated/data/*.c and
#                            the committed inventory/summary report.
# generated-data-check    : the CI-suitable gate -- fails on validation,
#                            round-trip, or committed-inventory drift.
#                            Only ever writes under build/ (self-heals the
#                            ephemeral generated C there); never rewrites
#                            anything committed.
# generated-data-validate : validate only, no output.
# generated-data-test     : run the stdlib unittest suite.

GENERATED_DATA_PY      := $(PYTHON) -m scripts.generated_data
GENERATED_DATA_OUT_DIR := build/generated/data
GENERATED_DATA_TABLES  := supports

.PHONY: generated-data-validate generated-data-generate generated-data-check generated-data-test

generated-data-validate:
	@for table in $(GENERATED_DATA_TABLES); do \
		$(GENERATED_DATA_PY) validate --table $$table || exit 1; \
	done

generated-data-generate:
	@for table in $(GENERATED_DATA_TABLES); do \
		$(GENERATED_DATA_PY) generate --table $$table --out-dir $(GENERATED_DATA_OUT_DIR) || exit 1; \
	done

generated-data-check:
	@for table in $(GENERATED_DATA_TABLES); do \
		$(GENERATED_DATA_PY) check --table $$table --out-dir $(GENERATED_DATA_OUT_DIR) || exit 1; \
	done

generated-data-test:
	$(PYTHON) -m unittest discover -s scripts/generated_data/tests -v
