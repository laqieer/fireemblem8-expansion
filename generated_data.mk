# Standalone targets for scripts/generated_data (Issue #5 Chapter 2 slice).
#
# Not wired into `all` (this work never links generated C or replaces any
# hand-written src/ file), but `generated-data-check` *is* wired into
# .github/workflows/build.yml (Batch C) as an actionable pre-flight gate
# ahead of the modern debug/release linker checks -- see docs/generated_data.md
# for full scope/status, including what remains open for Issue #5 itself.
#
# generated-data-generate  : validate + write build/generated/data/*.c (skipped
#                            for metadata-only tables) and the committed
#                            inventory/summary report, for every registered
#                            table.
# generated-data-check     : the CI-suitable gate -- fails on validation,
#                            round-trip, or committed-inventory drift, for
#                            every registered table. Only ever writes under
#                            build/ (self-heals the ephemeral generated C
#                            there); never rewrites anything committed.
# generated-data-validate  : validate only (all tables), no output.
# generated-data-test      : run the stdlib unittest suite.
# generated-data-ch2-check : Batch C alias scoped to the Chapter 2 whole-bundle
#                            manifest and every table it composes (currently
#                            identical to generated-data-check, since that's
#                            every registered table today) -- kept as its own
#                            name so a future non-Ch2 table addition doesn't
#                            silently change what "the Ch2 bundle is clean"
#                            means.

GENERATED_DATA_PY       := $(PYTHON) -m scripts.generated_data
GENERATED_DATA_OUT_DIR  := build/generated/data
GENERATED_DATA_TABLES   := supports units shops traps items classes characters eventscripts eventlists chapterbundle
GENERATED_DATA_CH2_TABLES := units shops traps eventscripts eventlists chapterbundle

.PHONY: generated-data-validate generated-data-generate generated-data-check generated-data-test \
        generated-data-ch2-check generated-data-bundle-validate generated-data-bundle-check

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

# Batch C: validate/check just the chapterbundle table on its own (fast
# path for iterating on src/data/ch2_bundle.json without re-running the
# supports table's 33-record round trip too).
generated-data-bundle-validate:
	$(GENERATED_DATA_PY) validate --table chapterbundle

generated-data-bundle-check:
	$(GENERATED_DATA_PY) check --table chapterbundle --out-dir $(GENERATED_DATA_OUT_DIR)

# Batch C: the Chapter 2 whole-bundle gate -- every table the bundle
# composes plus the bundle itself.
generated-data-ch2-check:
	@for table in $(GENERATED_DATA_CH2_TABLES); do \
		$(GENERATED_DATA_PY) check --table $$table --out-dir $(GENERATED_DATA_OUT_DIR) || exit 1; \
	done
