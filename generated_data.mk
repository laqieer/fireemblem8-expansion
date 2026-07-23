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

# ---------------------------------------------------------------------------
# Linked generated-data tables (Issue #5 Batch 2c-1)
# ---------------------------------------------------------------------------
# Everything above never links generated C in place of any hand-written
# src/ table -- see docs/generated_data.md's "Remaining Issue #5 scope"
# section. Batch 2c starts closing that gap, one table at a time.
#
# GENERATED_DATA_LINKED_HAND_SOURCES is the single source of truth: every
# hand-written src/ C file listed here has its object filtered out of both
# the legacy (top-level Makefile) and modern (modern.mk) object lists and
# replaced by its generated build/generated/data/ equivalent -- the real
# ROM (and the modern object cohort) links the generated table, not the
# hand-written one. The hand source itself is never edited or deleted: it
# stays in the repo purely as the schema/round-trip reference (see this
# table's own "schema" section in docs/generated_data.md) that
# `generated-data-check` keeps proving byte-for-byte identical against.
#
# Batch 2c-1 links only `classes`. Extending this list to another table
# also requires defining that table's own
# GENERATED_DATA_CONFIG_INPUTS_<table> (below) since the generator's
# non-JSON, non-script "config" inputs (live enum/struct-layout headers,
# hand data-source tables read for live counts, etc.) are wildly
# table-specific and cannot be derived generically.
GENERATED_DATA_LINKED_HAND_SOURCES := src/data_classes.c

# Table name for each entry above, same order. Derived from the
# `src/data_<table>.c` naming convention shared by every currently-linked
# table (classes/items/characters/supports all follow it); a future linked
# table whose hand source doesn't follow that convention would need an
# explicit override here instead of this patsubst. Also used by modern.mk
# to build each linked table's explicit compile-rule override (see
# GENERATED_DATA_MODERN_OVERRIDE_RULES there).
GENERATED_DATA_LINKED_TABLES := $(patsubst src/data_%.c,%,$(GENERATED_DATA_LINKED_HAND_SOURCES))

# The generated equivalents, under the same ephemeral, gitignored
# build/generated/data/ output directory every generated-data-* target
# above already writes to (never committed).
GENERATED_DATA_LINKED_C       := $(addprefix $(GENERATED_DATA_OUT_DIR)/,$(notdir $(GENERATED_DATA_LINKED_HAND_SOURCES)))
GENERATED_DATA_LINKED_OBJECTS := $(GENERATED_DATA_LINKED_C:.c=.o)

# `classes`' own generator "config" inputs: headers/hand C sources the
# generator reads live constants from (the CLASS_*/CA_*/ITYPE_* enums, the
# struct ClassData field capacities, live MSG_COUNT, and live portrait/SMS
# counts -- see scripts/generated_data/classes/schema.py's own *_HEADER/
# *_SOURCE constants), beyond the table's own JSON source and the shared/
# per-table generator scripts (tracked generically below for every linked
# table). A future linked table would define its own
# GENERATED_DATA_CONFIG_INPUTS_<table> the same way.
GENERATED_DATA_CONFIG_INPUTS_classes := \
	include/constants/classes.h \
	include/bmunit.h \
	include/bmitem.h \
	include/ekrbattle.h \
	include/variables.h \
	include/constants/msg.h \
	src/portrait_data.c \
	src/unit_icon_wait_data.c

# Shared (every table) generator scripts. Test files/fixtures are
# deliberately excluded -- they never affect generated output.
GENERATED_DATA_SHARED_PY_SOURCES := $(wildcard scripts/generated_data/*.py)

# One generation rule per linked table, instantiated below via
# GENERATED_DATA_LINK_TABLE_RULES. The .c target depends directly on its
# JSON source, the shared + per-table generator scripts, and its
# table-specific config inputs -- deterministic, ordinary make staleness:
# if any input is newer than the existing .c (or the .c is simply
# missing), the rule reruns.
#
# No separate "stamp" file: `generate`'s own write-if-changed contract
# (scripts/generated_data/cgen.py's write_if_changed) already deliberately
# leaves the .c file's mtime untouched whenever regeneration reproduces
# byte-identical content, so a plain input-driven rule gets exactly the
# desired incremental behavior for free -- the (idempotent, sub-second)
# Python generator may re-run when an input's mtime is merely touched
# without a real content change (e.g. right after a fresh checkout), but
# that only costs a fast no-op invocation; the .c file's mtime -- and
# therefore every downstream legacy/.o and modern build step -- only ever
# advances on a genuine content change. A stamp-file indirection was
# considered and rejected: it decouples "generation is current" from "the
# .c file exists with current content", which breaks the moment the two
# facts disagree (e.g. the .c is deleted, such as by `make clean`, while
# the stamp survives) -- the stamp would then be up to date and the .c
# rule would never regenerate the missing file. Depending on the real
# inputs directly avoids that failure mode entirely.
define GENERATED_DATA_LINK_TABLE_RULES
$(GENERATED_DATA_OUT_DIR)/data_$(1).c: src/data/$(1).json $(GENERATED_DATA_SHARED_PY_SOURCES) $(wildcard scripts/generated_data/$(1)/*.py) $(GENERATED_DATA_CONFIG_INPUTS_$(1))
	@mkdir -p $$(@D)
	$(GENERATED_DATA_PY) generate --table $(1) --out-dir $(GENERATED_DATA_OUT_DIR)
	@test -e $$@ || { echo "error: generated-data table '$(1)' did not produce $$@ (schema default_output_name mismatch?)" >&2; exit 1; }
endef

$(foreach t,$(GENERATED_DATA_LINKED_TABLES),$(eval $(call GENERATED_DATA_LINK_TABLE_RULES,$(t))))

# Legacy compile/assemble: the exact same cpp | iconv | agbcc | as pipeline
# and flags as the top-level Makefile's own $(C_OBJECTS) rule (CC1FLAGS/
# CPPFLAGS/ASFLAGS/CPP/CC1/AS/SED/UNAME are all already defined by the time
# this file is included -- see the "Issue #5 generated-data platform"
# include near the top of Makefile, right after the Tools section).
#
# Uses $(@:.o=.s) rather than $(C_OBJECTS)'s own $*.s: this is a static
# pattern rule scoped to the $(GENERATED_DATA_OUT_DIR)/ prefix, so its stem
# ($*) is just the base name (e.g. "data_classes") with the directory
# already stripped by the pattern match -- $*.s would land in the current
# working directory instead of alongside the generated .c/.o. $(@:.o=.s)
# keeps the intermediate .s file in $(GENERATED_DATA_OUT_DIR) (covered by
# CLEAN_DIRS) no matter where make is invoked from.
$(GENERATED_DATA_LINKED_OBJECTS): $(GENERATED_DATA_OUT_DIR)/%.o: $(GENERATED_DATA_OUT_DIR)/%.c
	$(CPP) $(CPPFLAGS) $< | iconv -f UTF-8 -t CP932 | $(CC1) $(CC1FLAGS) -o $(@:.o=.s)
	echo '.ALIGN 2, 0' >> $(@:.o=.s)
ifeq ($(UNAME),Darwin)
	$(SED) -f scripts/align_2_before_debug_section_for_osx.sed $(@:.o=.s)
else
	$(SED) '/.section	.debug_line/i\.align 2, 0' $(@:.o=.s)
endif
	$(AS) $(ASFLAGS) $(@:.o=.s) -o $@

.PHONY: generated-data-link-check

# Batch 2c-1 gate: proves the classes link-swap is wired correctly --
# exactly one generated object selected in place of the hand source, in
# both the legacy and modern object lists, no other table affected, the
# ldscript.txt swap is exact, `gClassData` links exactly once from the
# generated object, the hand source is preserved untouched, generated
# artifacts are covered by `clean`, a touched-but-content-unchanged input
# re-invokes the generator but never re-runs the legacy compile/assemble
# pipeline -- proven entirely by *behavior* evidence, deliberately not
# filesystem mtime comparisons (an earlier mtime-based version of this
# subtest was flaky: `.c`/`.o` mtimes can land in the same timestamp
# window, and even nanosecond-precision `stat` couldn't fully rule out
# races against `write_if_changed`'s own write-skip decision). Instead,
# after a deterministic rm+rebuild baseline, the object target's own
# `$(MAKE)` output is captured and grepped: `generate --table classes`
# must appear (the generator did re-run against the touched JSON), but
# none of the legacy compile/assemble markers (`agbcc`, `arm-none-eabi-
# as`) may appear (the object was never rebuilt); the object's MD5 is
# also asserted unchanged, and a final rebuild after restoring the
# JSON's original timestamp (via `touch -r`, from a saved reference,
# restored on every exit path via `trap`) must produce no generate/
# compile output at all (fully up to date). A from-scratch parallel (-j)
# build of both consumers of the shared generated .c is race-free, and
# -- critically, since this whole
# plumbing hinges on `include generated_data.mk` happening before `all:`
# is defined in Makefile -- that a bare `make`/`make -n` still builds
# the ROM by default, not generated-data-validate (generated_data.mk's
# own first target, which would otherwise silently steal GNU Make's
# implicit default-goal rule); that probe itself uses `-rR`
# (--no-builtin-rules --no-builtin-variables) against a nonexistent
# target so GNU Make's own implicit suffix-rule search can't accidentally
# match the bogus probe name and spawn a real (if harmlessly failing)
# assembler invocation -- our own `:=`-assigned AS/CC1/etc. variables are
# untouched by `-R`.
# Local/manual gate -- not CI wired, since CI's tool install
# (make_tools.mk) deliberately excludes agbcc (tools/agbcc/Makefile) and
# cannot run the legacy pipeline; the modern half of this same swap is
# already exercised by CI's existing expansion-modern-linker-check for
# both MODERN_CONFIG values instead.
generated-data-link-check: $(GENERATED_DATA_LINKED_OBJECTS)
	@echo '--- Batch 2c-1 scope: exactly classes ---'
	@if [ "$(strip $(GENERATED_DATA_LINKED_HAND_SOURCES))" != "src/data_classes.c" ]; then \
		echo "FAIL: GENERATED_DATA_LINKED_HAND_SOURCES changed unexpectedly ('$(GENERATED_DATA_LINKED_HAND_SOURCES)'); Batch 2c-1 scope is classes only" >&2; exit 1; \
	fi
	@echo '--- bare `make` default goal is still `all` (the ROM), not generated-data validation ---'
	@probe=$$($(MAKE) --no-print-directory -rR -p __generated_data_link_check_default_goal_probe__ 2>/dev/null); \
	default_goal=$$(printf '%s\n' "$$probe" | grep -m1 '^\.DEFAULT_GOAL '); \
	if [ "$$default_goal" != '.DEFAULT_GOAL := all' ]; then \
		echo "FAIL: bare make's default goal is '$$default_goal' (want '.DEFAULT_GOAL := all') -- 'include generated_data.mk' before 'all:' would otherwise let generated-data-validate (its own first target) silently become the default goal instead, so a bare 'make' would validate JSON instead of building the ROM" >&2; exit 1; \
	fi; \
	all_rule=$$(printf '%s\n' "$$probe" | grep -m1 '^all:'); \
	if ! printf '%s\n' "$$all_rule" | grep -q 'fireemblem8\.gba'; then \
		echo "FAIL: the 'all:' rule ('$$all_rule') no longer depends on fireemblem8.gba (\$$(ROM)) -- bare make would not build the ROM" >&2; exit 1; \
	fi
	@echo 'OK: bare make/make -n still target the ROM/ELF build (all), not just generated-data validation'
	@echo '--- legacy CFILES/ALL_OBJECTS ---'
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_HAND_SOURCES),$(CFILES)))" ]; then \
		echo "FAIL: $(GENERATED_DATA_LINKED_HAND_SOURCES) still present in legacy CFILES" >&2; exit 1; \
	fi
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_HAND_SOURCES:.c=.o),$(ALL_OBJECTS)))" ]; then \
		echo "FAIL: hand object still present in legacy ALL_OBJECTS" >&2; exit 1; \
	fi
	@if [ "$(words $(filter $(GENERATED_DATA_LINKED_OBJECTS),$(ALL_OBJECTS)))" != "$(words $(GENERATED_DATA_LINKED_TABLES))" ]; then \
		echo "FAIL: generated object(s) not present exactly once each in legacy ALL_OBJECTS" >&2; exit 1; \
	fi
	@for other in src/data_items.c src/data_characters.c src/data_supports.c; do \
		if ! printf '%s\n' $(CFILES) | grep -qx "$$other"; then \
			echo "FAIL: unrelated hand source $$other unexpectedly filtered out of legacy CFILES" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: exactly src/data_classes.c is filtered from the legacy build'
	@echo '--- modern MODERN_ALL_C_SOURCES/MODERN_ALL_C_OBJECTS ---'
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_HAND_SOURCES),$(MODERN_ALL_C_SOURCES)))" ]; then \
		echo "FAIL: hand source still present in modern MODERN_ALL_C_SOURCES" >&2; exit 1; \
	fi
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_C),$(MODERN_ALL_C_SOURCES)))" ]; then \
		echo "FAIL: generated source(s) unexpectedly present in modern MODERN_ALL_C_SOURCES (should only be reinstated as an object, at the original hand-object path, so \$(sort) in MODERN_ELF_OBJECTS_LST/MANIFEST keeps it in the hand object's original sorted slot)" >&2; exit 1; \
	fi
	@for other in src/data_items.c src/data_characters.c src/data_supports.c; do \
		if ! printf '%s\n' $(MODERN_ALL_C_SOURCES) | grep -qx "$$other"; then \
			echo "FAIL: unrelated hand source $$other unexpectedly filtered out of modern MODERN_ALL_C_SOURCES" >&2; exit 1; \
		fi; \
	done
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_C:.c=.o),$(MODERN_ALL_C_OBJECTS)))" ]; then \
		echo "FAIL: generated object unexpectedly present at its own (build/generated/data/...) path in MODERN_ALL_C_OBJECTS -- must be reinstated at the original hand-object path instead" >&2; exit 1; \
	fi
	@if [ "$(words $(filter $(addprefix $(MODERN_OUTPUT_DIR)/,$(GENERATED_DATA_LINKED_HAND_SOURCES:.c=.o)),$(MODERN_ALL_C_OBJECTS)))" != "$(words $(GENERATED_DATA_LINKED_TABLES))" ]; then \
		echo "FAIL: generated table's object not present exactly once at the original hand-object path in MODERN_ALL_C_OBJECTS" >&2; exit 1; \
	fi
	@echo 'OK: exactly src/data_classes.c is filtered from the modern cohort, and its generated object is reinstated at the original object path'
	@echo '--- ldscript.txt swap ---'
	@if grep -qx '        . = ALIGN(4); src/data_classes.o(.data);' ldscript.txt; then \
		echo "FAIL: ldscript.txt still references src/data_classes.o(.data)" >&2; exit 1; \
	fi
	@linked_count=$$(grep -Fc '$(GENERATED_DATA_OUT_DIR)/data_classes.o(.data)' ldscript.txt); \
	if [ "$$linked_count" != 1 ]; then \
		echo "FAIL: ldscript.txt references $(GENERATED_DATA_OUT_DIR)/data_classes.o(.data) $$linked_count time(s) (want exactly 1)" >&2; exit 1; \
	fi
	@echo 'OK: ldscript.txt links the generated object exactly once, in place of the hand object'
	@echo '--- gClassData symbol ---'
	@symcount=$$(arm-none-eabi-nm $(GENERATED_DATA_OUT_DIR)/data_classes.o | grep -c ' gClassData$$'); \
	if [ "$$symcount" != 1 ]; then \
		echo "FAIL: generated object defines gClassData $$symcount time(s) (want exactly 1)" >&2; exit 1; \
	fi
	@echo 'OK: exactly one gClassData definition in the generated object'
	@echo '--- hand source preserved ---'
	@test -f src/data_classes.c || { echo "FAIL: src/data_classes.c was deleted" >&2; exit 1; }
	@echo 'OK: src/data_classes.c preserved untouched'
	@echo '--- clean coverage ---'
	@if [ -z "$(strip $(filter $(GENERATED_DATA_OUT_DIR),$(CLEAN_DIRS)))" ]; then \
		echo "FAIL: $(GENERATED_DATA_OUT_DIR) missing from CLEAN_DIRS -- clean/clean_fast would not remove it" >&2; exit 1; \
	fi
	@echo 'OK: clean/clean_fast remove build/generated/data (.c/.s/.o)'
	@echo '--- touched-but-unchanged input: content-preserving no-op regenerate (behavior evidence, not mtime) ---'
	@rm -f $(GENERATED_DATA_LINKED_C) $(GENERATED_DATA_LINKED_OBJECTS) $(GENERATED_DATA_LINKED_C:.c=.s); \
	$(MAKE) --no-print-directory $(GENERATED_DATA_LINKED_OBJECTS) >/dev/null; \
	o_hash_before=$$(md5sum $(GENERATED_DATA_OUT_DIR)/data_classes.o | cut -d' ' -f1); \
	json_ref=generated-data-link-check.json_ref.tmp; \
	trap 'touch -r "$$json_ref" src/data/classes.json 2>/dev/null; rm -f "$$json_ref" generated-data-link-check.regen.log generated-data-link-check.uptodate.log' EXIT; \
	touch -r src/data/classes.json "$$json_ref"; \
	touch src/data/classes.json; \
	$(MAKE) --no-print-directory $(GENERATED_DATA_LINKED_OBJECTS) >generated-data-link-check.regen.log 2>&1; \
	if ! grep -q 'generate --table classes' generated-data-link-check.regen.log; then \
		echo "FAIL: touching the JSON source did not trigger a regenerate at all" >&2; exit 1; \
	fi; \
	if grep -qE 'arm-none-eabi-as|agbcc' generated-data-link-check.regen.log; then \
		echo "FAIL: unchanged-content regenerate still ran the legacy compile/assemble pipeline (unnecessary object recompile):" >&2; cat generated-data-link-check.regen.log >&2; exit 1; \
	fi; \
	o_hash_after=$$(md5sum $(GENERATED_DATA_OUT_DIR)/data_classes.o | cut -d' ' -f1); \
	if [ "$$o_hash_before" != "$$o_hash_after" ]; then \
		echo "FAIL: object content changed even though no recompile should have run ($$o_hash_before -> $$o_hash_after)" >&2; exit 1; \
	fi; \
	touch -r "$$json_ref" src/data/classes.json; \
	$(MAKE) --no-print-directory $(GENERATED_DATA_LINKED_OBJECTS) >generated-data-link-check.uptodate.log 2>&1; \
	if grep -qE 'generate --table classes|arm-none-eabi-as|agbcc' generated-data-link-check.uptodate.log; then \
		echo "FAIL: after restoring the JSON source's original timestamp, the object target is not fully up to date:" >&2; cat generated-data-link-check.uptodate.log >&2; exit 1; \
	fi
	@echo 'OK: a touched-but-unchanged JSON input re-invokes the generator but never re-runs the legacy compile/assemble pipeline (no unnecessary object recompile), proven by captured build-log evidence and stable object content, not filesystem mtimes'
	@echo '--- from-scratch parallel build (shared generated .c, two consumers) ---'
	@rm -f $(GENERATED_DATA_LINKED_C) $(GENERATED_DATA_LINKED_OBJECTS)
	@$(MAKE) --no-print-directory -j4 $(GENERATED_DATA_LINKED_OBJECTS) $(GENERATED_DATA_LINKED_C) >/dev/null
	@test -e $(GENERATED_DATA_OUT_DIR)/data_classes.c || { echo "FAIL: parallel build did not produce the generated .c" >&2; exit 1; }
	@test -e $(GENERATED_DATA_OUT_DIR)/data_classes.o || { echo "FAIL: parallel build did not produce the generated object" >&2; exit 1; }
	@echo 'OK: from-scratch parallel (-j4) build of both consumers of the shared generated .c succeeds, no race/duplicate generation'
	@echo 'PASS: generated-data-link-check'
