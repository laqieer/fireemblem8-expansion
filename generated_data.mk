# Standalone targets for scripts/generated_data (Issue #5 Chapter 2 slice).
#
# Deliberately not wired into `all`/CI: Slice 0 proved the framework on a
# single table (SupportData); this Batch A adds the Chapter 2 vertical
# slice's pure-data tables (units/shops/traps) plus a metadata-only
# eventscripts table, none of which replace any hand-written src/ file or
# link into the ROM build. See docs/generated_data.md for scope and status,
# including what Batch B/C still need to add before Issue #5 can close.
#
# generated-data-generate : validate + write build/generated/data/*.c (skipped
#                            for metadata-only tables) and the committed
#                            inventory/summary report, for every registered
#                            table.
# generated-data-check    : the CI-suitable gate -- fails on validation,
#                            round-trip, or committed-inventory drift, for
#                            every registered table. Only ever writes under
#                            build/ (self-heals the ephemeral generated C
#                            there); never rewrites anything committed.
# generated-data-validate : validate only (all tables), no output.
# generated-data-test     : run the stdlib unittest suite.

GENERATED_DATA_PY      := $(PYTHON) -m scripts.generated_data
GENERATED_DATA_OUT_DIR := build/generated/data
GENERATED_DATA_TABLES  := supports units shops traps eventscripts eventlists

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
