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
# Linked generated-data tables (Issue #5 Batch 2c-1 + 2c-2 + 2c-3 + 2c-4)
# ---------------------------------------------------------------------------
# Everything above never links generated C in place of any hand-written
# src/ table -- see docs/generated_data.md's "Remaining Issue #5 scope"
# section. Batch 2c is closing that gap, one table at a time.
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
# Batch 2c-1 linked `classes`; Batch 2c-2 added `items` (the 206-record
# global gItemData[] table); Batch 2c-3 added `supports` (the 33-record
# SupportData_* table -- see below, `supports` is a *multi-symbol* table,
# unlike `classes`/`items`' single top-level array symbol); Batch 2c-4
# (this update) adds `characters` (the 256-record global gCharacterData[]
# table -- back to a single top-level array symbol, like `classes`/
# `items`). Extending this list to another table also requires defining
# that table's own GENERATED_DATA_CONFIG_INPUTS_<table> and
# GENERATED_DATA_LINKED_SYMBOL_<table> (both below), since the
# generator's non-JSON, non-script "config" inputs (live enum/struct-
# layout headers, hand data-source tables read for live counts, etc.)
# and each table's top-level generated symbol name(s) are wildly
# table-specific and cannot be derived generically.
GENERATED_DATA_LINKED_HAND_SOURCES := src/data_classes.c src/data_items.c src/data_supports.c src/data_characters.c

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

# `items`' own generator "config" inputs: headers/hand C sources
# scripts/generated_data/items/schema.py reads live constants from -- the
# ITEM_*/IA_*/ITYPE_*/WPN_*-style enums and struct ItemData field layout
# (include/constants/items.h, include/bmitem.h), the live
# `effectiveness`/`statBonuses` C-symbol-reference validation against
# include/variables.h (`CSymbolRefField`, resolved against
# VARIABLES_HEADER), live MSG_COUNT (include/constants/msg.h), and the
# live item-icon graphics tile count derived from
# src/data/data_item_icon.c (see items/schema.py's ITEMS_HEADER/
# BMITEM_HEADER/VARIABLES_HEADER/MSG_HEADER/ITEM_ICON_SOURCE constants).
GENERATED_DATA_CONFIG_INPUTS_items := \
	include/constants/items.h \
	include/bmitem.h \
	include/variables.h \
	include/constants/msg.h \
	src/data/data_item_icon.c

# `supports`' own generator "config" inputs: headers
# scripts/generated_data/supports/schema.py reads live constants from --
# the CHARACTER_* designator set (include/constants/characters.h, via the
# shared character_refs.py helper) used to validate owner/partner
# references, and the live UNIT_SUPPORT_MAX_COUNT capacity
# (include/types.h) used for the fixed-capacity check (see
# supports/schema.py's own CHARACTERS_HEADER/TYPES_HEADER constants).
GENERATED_DATA_CONFIG_INPUTS_supports := \
	include/constants/characters.h \
	include/types.h

# `characters`' own generator "config" inputs: headers/hand C sources
# scripts/generated_data/characters/schema.py reads live constants from --
# the CHARACTER_* designator set (include/constants/characters.h, via the
# shared character_refs.py helper) used for the 256-slot symbolic/raw
# designator model, the CLASS_*-style default-class reference
# (include/constants/classes.h), struct CharacterData field capacities/
# CA_*/affinity constants (include/bmunit.h), item-rank references
# (include/bmitem.h), live MSG_COUNT (include/constants/msg.h), and the
# live portrait/mini-portrait counts derived from src/portrait_data.c/
# src/face.c (see characters/schema.py's own CHARACTERS_HEADER/
# CLASSES_HEADER/BMUNIT_HEADER/BMITEM_HEADER/MSG_HEADER/
# PORTRAIT_DATA_SOURCE/FACE_SOURCE constants).
GENERATED_DATA_CONFIG_INPUTS_characters := \
	include/constants/characters.h \
	include/constants/classes.h \
	include/bmunit.h \
	include/bmitem.h \
	include/constants/msg.h \
	src/portrait_data.c \
	src/face.c

# Shared (every table) generator scripts. Test files/fixtures are
# deliberately excluded -- they never affect generated output.
GENERATED_DATA_SHARED_PY_SOURCES := $(wildcard scripts/generated_data/*.py)

# Each linked table's top-level generated C symbol name(s) -- used by
# generated-data-link-check to prove exactly one definition of each links
# from the generated object. Cannot be derived generically from the table
# name (`classes` -> `gClassData`, `items` -> `gItemData`, both singular;
# `supports` -> 33 distinct `SupportData_*` per-owner symbols, since
# unlike `classes`/`items` there is no single top-level array wrapping
# the whole table), so each linked table defines its own entry here, the
# same way each defines its own GENERATED_DATA_CONFIG_INPUTS_<table>
# above. A table's entry may list more than one symbol (space-separated);
# generated-data-link-check checks every one of them individually.
GENERATED_DATA_LINKED_SYMBOL_classes  := gClassData
GENERATED_DATA_LINKED_SYMBOL_items    := gItemData
GENERATED_DATA_LINKED_SYMBOL_characters := gCharacterData

# `supports` has no single top-level symbol -- its generated object
# defines one `SupportData_<Owner>` per record instead. The expected
# symbol list is derived straight from the committed source of truth
# (`src/data/supports.json`, the same file the generator itself reads),
# not hardcoded here or re-derived from the generated inventory report,
# so this list can never silently drift from what the table actually
# authors -- see `GENERATED_DATA_LINKED_SYMBOL_PREFIX_supports` below for
# the accompanying "no extra/unexpected symbol" check.
GENERATED_DATA_LINKED_SYMBOL_supports := $(shell $(PYTHON) -c \
	"import json; d = json.load(open('src/data/supports.json')); print(' '.join(sorted(r['symbol'] for r in d['records'])))")

# Optional, per-table: a `SupportData_`-style symbol-family prefix used
# to additionally prove the generated object defines *no more and no
# fewer* than the expected symbol list above -- i.e. no leftover/rogue
# `SupportData_*` definition beyond the exact 33 the source declares.
# Tables with a single top-level symbol (`classes`/`items`) leave this
# unset: a lone array symbol has no "family" to over/under-count.
GENERATED_DATA_LINKED_SYMBOL_PREFIX_supports := SupportData_

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

# Batch 2c-1 + 2c-2 + 2c-3 + 2c-4 gate: proves every table in
# GENERATED_DATA_LINKED_TABLES (currently `classes`, `items`, `supports`,
# `characters`) has its link-swap wired correctly -- exactly one
# generated object selected in place of each hand source, in both the
# legacy and modern object lists, no other (unlinked) table affected,
# each table's ldscript.txt swap is exact, each table's own top-level
# generated symbol(s) (GENERATED_DATA_LINKED_SYMBOL_<table>:
# `gClassData`, `gItemData`, `gCharacterData`, or `supports`' 33
# `SupportData_*` per-owner symbols) each link exactly once from its
# generated object (and, for `supports`, no extra/unexpected
# `SupportData_*` symbol beyond those 33 -- see
# GENERATED_DATA_LINKED_SYMBOL_PREFIX_supports), every hand source is
# preserved untouched, generated artifacts are covered by `clean`, and --
# per table
# -- a touched-but-content-unchanged input re-invokes that table's
# generator but never re-runs the legacy compile/assemble pipeline --
# proven entirely by *behavior* evidence, deliberately not filesystem
# mtime comparisons (an earlier mtime-based version of this subtest was
# flaky: `.c`/`.o` mtimes can land in the same timestamp window, and even
# nanosecond-precision `stat` couldn't fully rule out races against
# `write_if_changed`'s own write-skip decision). Instead, after a
# deterministic rm+rebuild baseline, each table's own object-target
# `$(MAKE)` output is captured and grepped: `generate --table <table>`
# must appear (that table's generator did re-run against its own touched
# JSON), but none of the legacy compile/assemble markers (`agbcc`,
# `arm-none-eabi-as`) may appear (the object was never rebuilt); that
# table's object MD5 is also asserted unchanged, and a final rebuild
# after restoring that JSON's original timestamp (via `touch -r`, from a
# saved reference, restored on every exit path via `trap`) must produce
# no generate/compile output at all (fully up to date), for every linked
# table serially (never overlapping two tables' rm+touch+rebuild windows
# in the same `$(MAKE)` invocation, so each table's captured log/hash
# evidence stays unambiguous). A from-scratch parallel (-j) build of
# every linked table's shared generated .c/.o pair is race-free, and --
# critically, since this whole
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
	@echo '--- Batch 2c-1 + 2c-2 + 2c-3 + 2c-4 scope: exactly classes, items, supports, and characters ---'
	@if [ "$(strip $(GENERATED_DATA_LINKED_HAND_SOURCES))" != "src/data_classes.c src/data_items.c src/data_supports.c src/data_characters.c" ]; then \
		echo "FAIL: GENERATED_DATA_LINKED_HAND_SOURCES changed unexpectedly ('$(GENERATED_DATA_LINKED_HAND_SOURCES)'); Batch 2c-1 + 2c-2 + 2c-3 + 2c-4 scope is classes, items, supports, and characters only" >&2; exit 1; \
	fi
	@if [ "$(strip $(GENERATED_DATA_LINKED_TABLES))" != "classes items supports characters" ]; then \
		echo "FAIL: GENERATED_DATA_LINKED_TABLES changed unexpectedly ('$(GENERATED_DATA_LINKED_TABLES)'); Batch 2c-1 + 2c-2 + 2c-3 + 2c-4 scope is classes, items, supports, and characters only" >&2; exit 1; \
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
	@for other in src/data_terrains.c; do \
		if ! printf '%s\n' $(CFILES) | grep -qx "$$other"; then \
			echo "FAIL: unrelated hand source $$other unexpectedly filtered out of legacy CFILES" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: exactly $(GENERATED_DATA_LINKED_HAND_SOURCES) is filtered from the legacy build'
	@echo '--- $(OBJECTS_LST) self-heals a stale/corrupted manifest, even when its own mtime looks fully up to date (regression: incremental multiple-definition link error) ---'
	@backup=generated-data-link-check.objects_lst.backup.tmp; \
	had_objects_lst=0; \
	if [ -e $(OBJECTS_LST) ]; then had_objects_lst=1; cp -p $(OBJECTS_LST) "$$backup"; fi; \
	trap 'if [ "$$had_objects_lst" = 1 ]; then mv -f "$$backup" $(OBJECTS_LST); else rm -f $(OBJECTS_LST); fi; rm -f "$$backup"' EXIT; \
	stale="$(ALL_OBJECTS)"; \
	for table in $(GENERATED_DATA_LINKED_TABLES); do \
		hand=src/data_$$table.o; \
		gen=$(GENERATED_DATA_OUT_DIR)/data_$$table.o; \
		stale=$$(printf '%s' "$$stale" | sed "s#$$gen#$$hand#g"); \
	done; \
	printf '%s\n' "$$stale" > $(OBJECTS_LST); \
	touch -d '+1 day' $(OBJECTS_LST); \
	for table in $(GENERATED_DATA_LINKED_TABLES); do \
		hand=src/data_$$table.o; \
		gen=$(GENERATED_DATA_OUT_DIR)/data_$$table.o; \
		if ! grep -qF "$$hand" $(OBJECTS_LST) || grep -qF "$$gen" $(OBJECTS_LST); then \
			echo "FAIL: test setup did not actually stage a stale $(OBJECTS_LST) (still references $$gen, or missing $$hand)" >&2; exit 1; \
		fi; \
	done; \
	echo 'staged stale manifest (hand objects instead of generated, manifest mtime pushed 1 day into the future):'; \
	for table in $(GENERATED_DATA_LINKED_TABLES); do printf '  src/data_%s.o\n' "$$table"; done; \
	$(MAKE) --no-print-directory $(OBJECTS_LST); \
	for table in $(GENERATED_DATA_LINKED_TABLES); do \
		hand=src/data_$$table.o; \
		gen=$(GENERATED_DATA_OUT_DIR)/data_$$table.o; \
		gen_count=$$(grep -oF "$$gen" $(OBJECTS_LST) | wc -l); \
		hand_count=$$(grep -oF "$$hand" $(OBJECTS_LST) | wc -l); \
		if [ "$$gen_count" != 1 ]; then \
			echo "FAIL: after self-heal, $(OBJECTS_LST) references $$gen $$gen_count time(s) (want exactly 1)" >&2; exit 1; \
		fi; \
		if [ "$$hand_count" != 0 ]; then \
			echo "FAIL: after self-heal, $(OBJECTS_LST) still references stale hand object $$hand $$hand_count time(s) (want 0)" >&2; exit 1; \
		fi; \
	done; \
	if ! grep -qF "src/data_terrains.o" $(OBJECTS_LST); then \
		echo "FAIL: self-heal unexpectedly dropped an unrelated object (src/data_terrains.o) from $(OBJECTS_LST)" >&2; exit 1; \
	fi; \
	healed_word_count=$$(wc -w < $(OBJECTS_LST)); \
	expected_word_count=$(words $(ALL_OBJECTS)); \
	if [ "$$healed_word_count" != "$$expected_word_count" ]; then \
		echo "FAIL: healed $(OBJECTS_LST) has $$healed_word_count object(s), want exactly $$expected_word_count (\$$(words \$$(ALL_OBJECTS))) -- an unrelated entry was dropped or duplicated" >&2; exit 1; \
	fi; \
	echo 'OK: $(MAKE) $(OBJECTS_LST) self-healed the stale manifest (every generated object present exactly once, every stale hand object gone, unrelated entries preserved) despite the manifest'"'"'s own mtime being in the future'; \
	mtime_before=$$(stat -c %Y $(OBJECTS_LST)); \
	$(MAKE) --no-print-directory $(OBJECTS_LST); \
	mtime_after=$$(stat -c %Y $(OBJECTS_LST)); \
	if [ "$$mtime_before" != "$$mtime_after" ]; then \
		echo "FAIL: a second $(OBJECTS_LST) rebuild changed its mtime ($$mtime_before -> $$mtime_after) even though content was already correct -- write-if-changed (temp+cmp+mv) is not preserving mtime on a stable manifest" >&2; exit 1; \
	fi; \
	echo 'OK: a second, already-correct $(OBJECTS_LST) rebuild left its mtime unchanged (content-preserving write, not touch-on-every-invocation)'
	@echo '--- modern MODERN_ALL_C_SOURCES/MODERN_ALL_C_OBJECTS ---'
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_HAND_SOURCES),$(MODERN_ALL_C_SOURCES)))" ]; then \
		echo "FAIL: hand source still present in modern MODERN_ALL_C_SOURCES" >&2; exit 1; \
	fi
	@if [ -n "$(strip $(filter $(GENERATED_DATA_LINKED_C),$(MODERN_ALL_C_SOURCES)))" ]; then \
		echo "FAIL: generated source(s) unexpectedly present in modern MODERN_ALL_C_SOURCES (should only be reinstated as an object, at the original hand-object path, so \$(sort) in MODERN_ELF_OBJECTS_LST/MANIFEST keeps it in the hand object's original sorted slot)" >&2; exit 1; \
	fi
	@for other in src/data_terrains.c; do \
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
	@echo 'OK: exactly $(GENERATED_DATA_LINKED_HAND_SOURCES) is filtered from the modern cohort, and each generated object is reinstated at its original object path'
	@echo '--- ldscript.txt swap (per table) ---'
	@for table in $(GENERATED_DATA_LINKED_TABLES); do \
		if grep -qx "        . = ALIGN(4); src/data_$$table.o(.data);" ldscript.txt; then \
			echo "FAIL: ldscript.txt still references src/data_$$table.o(.data)" >&2; exit 1; \
		fi; \
		linked_count=$$(grep -Fc "$(GENERATED_DATA_OUT_DIR)/data_$$table.o(.data)" ldscript.txt); \
		if [ "$$linked_count" != 1 ]; then \
			echo "FAIL: ldscript.txt references $(GENERATED_DATA_OUT_DIR)/data_$$table.o(.data) $$linked_count time(s) (want exactly 1)" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: ldscript.txt links each generated object exactly once, in place of its hand object'
	@echo '--- generated table symbols (per table; a table may define more than one top-level symbol, e.g. supports'"'"' 33 SupportData_* records) ---'
	@for table in $(GENERATED_DATA_LINKED_TABLES); do \
		symbols=""; prefix=""; \
		$(foreach t,$(GENERATED_DATA_LINKED_TABLES),if [ "$$table" = "$(t)" ]; then symbols="$(GENERATED_DATA_LINKED_SYMBOL_$(t))"; prefix="$(GENERATED_DATA_LINKED_SYMBOL_PREFIX_$(t))"; fi;) \
		test -n "$$symbols" || { echo "FAIL: no GENERATED_DATA_LINKED_SYMBOL_ entry for table $$table" >&2; exit 1; }; \
		nm_out=$$(arm-none-eabi-nm $(GENERATED_DATA_OUT_DIR)/data_$$table.o); \
		expected_count=0; \
		for symbol in $$symbols; do \
			expected_count=$$((expected_count + 1)); \
			symcount=$$(printf '%s\n' "$$nm_out" | grep -c " $$symbol\$$"); \
			if [ "$$symcount" != 1 ]; then \
				echo "FAIL: generated object for $$table defines $$symbol $$symcount time(s) (want exactly 1)" >&2; exit 1; \
			fi; \
		done; \
		echo "OK: exactly one definition of each of $$table's $$expected_count expected top-level symbol(s) in its generated object"; \
		if [ -n "$$prefix" ]; then \
			actual_family=$$(printf '%s\n' "$$nm_out" | awk '{print $$3}' | grep "^$$prefix" | sort | tr '\n' ' '); \
			actual_family=$${actual_family% }; \
			expected_family=$$(printf '%s\n' $$symbols | tr ' ' '\n' | sort | tr '\n' ' '); \
			expected_family=$${expected_family% }; \
			if [ "$$actual_family" != "$$expected_family" ]; then \
				echo "FAIL: generated object for $$table's $${prefix}* symbol family does not exactly match the expected $$expected_count-symbol list derived from src/data/$$table.json (no hand object may remain, and no extra/unexpected $${prefix}* definition may exist)" >&2; \
				echo "  expected: $$expected_family" >&2; \
				echo "  actual:   $$actual_family" >&2; \
				exit 1; \
			fi; \
			echo "OK: generated object for $$table defines exactly the expected $$expected_count $${prefix}* symbols, no more, no fewer"; \
		fi; \
	done
	@echo '--- hand sources preserved ---'
	@for hand in $(GENERATED_DATA_LINKED_HAND_SOURCES); do \
		test -f "$$hand" || { echo "FAIL: $$hand was deleted" >&2; exit 1; }; \
	done
	@echo 'OK: $(GENERATED_DATA_LINKED_HAND_SOURCES) preserved untouched'
	@echo '--- clean coverage ---'
	@if [ -z "$(strip $(filter $(GENERATED_DATA_OUT_DIR),$(CLEAN_DIRS)))" ]; then \
		echo "FAIL: $(GENERATED_DATA_OUT_DIR) missing from CLEAN_DIRS -- clean/clean_fast would not remove it" >&2; exit 1; \
	fi
	@echo 'OK: clean/clean_fast remove build/generated/data (.c/.s/.o) for every linked table'
	@echo '--- touched-but-unchanged input: content-preserving no-op regenerate (behavior evidence, not mtime), serially per table ---'
	@rm -f $(GENERATED_DATA_LINKED_C) $(GENERATED_DATA_LINKED_OBJECTS) $(GENERATED_DATA_LINKED_C:.c=.s); \
	$(MAKE) --no-print-directory $(GENERATED_DATA_LINKED_OBJECTS) >/dev/null; \
	for table in $(GENERATED_DATA_LINKED_TABLES); do \
		obj=$(GENERATED_DATA_OUT_DIR)/data_$$table.o; \
		json=src/data/$$table.json; \
		o_hash_before=$$(md5sum "$$obj" | cut -d' ' -f1); \
		json_ref=generated-data-link-check.$$table.json_ref.tmp; \
		regen_log=generated-data-link-check.$$table.regen.log; \
		uptodate_log=generated-data-link-check.$$table.uptodate.log; \
		trap 'touch -r "$$json_ref" "$$json" 2>/dev/null; rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"' EXIT; \
		touch -r "$$json" "$$json_ref"; \
		touch "$$json"; \
		$(MAKE) --no-print-directory "$$obj" >"$$regen_log" 2>&1; \
		if ! grep -q "generate --table $$table" "$$regen_log"; then \
			echo "FAIL: touching $$json did not trigger a $$table regenerate at all" >&2; exit 1; \
		fi; \
		if grep -qE 'arm-none-eabi-as|agbcc' "$$regen_log"; then \
			echo "FAIL: $$table unchanged-content regenerate still ran the legacy compile/assemble pipeline (unnecessary object recompile):" >&2; cat "$$regen_log" >&2; exit 1; \
		fi; \
		o_hash_after=$$(md5sum "$$obj" | cut -d' ' -f1); \
		if [ "$$o_hash_before" != "$$o_hash_after" ]; then \
			echo "FAIL: $$table object content changed even though no recompile should have run ($$o_hash_before -> $$o_hash_after)" >&2; exit 1; \
		fi; \
		touch -r "$$json_ref" "$$json"; \
		$(MAKE) --no-print-directory "$$obj" >"$$uptodate_log" 2>&1; \
		if grep -qE "generate --table $$table|arm-none-eabi-as|agbcc" "$$uptodate_log"; then \
			echo "FAIL: after restoring $$json's original timestamp, the $$table object target is not fully up to date:" >&2; cat "$$uptodate_log" >&2; exit 1; \
		fi; \
		rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"; \
		trap - EXIT; \
		echo "OK: $$table touched-but-unchanged JSON input re-invokes the generator but never re-runs the legacy compile/assemble pipeline (no unnecessary object recompile), proven by captured build-log evidence and stable object content, not filesystem mtimes"; \
	done
	@echo '--- from-scratch parallel build (shared generated .c per table, two consumers each) ---'
	@rm -f $(GENERATED_DATA_LINKED_C) $(GENERATED_DATA_LINKED_OBJECTS)
	@$(MAKE) --no-print-directory -j4 $(GENERATED_DATA_LINKED_OBJECTS) $(GENERATED_DATA_LINKED_C) >/dev/null
	@for table in $(GENERATED_DATA_LINKED_TABLES); do \
		test -e $(GENERATED_DATA_OUT_DIR)/data_$$table.c || { echo "FAIL: parallel build did not produce the generated .c for $$table" >&2; exit 1; }; \
		test -e $(GENERATED_DATA_OUT_DIR)/data_$$table.o || { echo "FAIL: parallel build did not produce the generated object for $$table" >&2; exit 1; }; \
	done
	@echo 'OK: from-scratch parallel (-j4) build of every linked table'"'"'s shared generated .c succeeds, no race/duplicate generation'
	@echo 'PASS: generated-data-link-check'

# ---------------------------------------------------------------------------
# Linking a Chapter-2-owned partial-file table (Issue #5 Batch 3a)
# ---------------------------------------------------------------------------
# Batch 2c-1..2c-4 (above) each replaced one *entire* hand-written
# src/data_<table>.c file with its generated equivalent -- viable only
# because each of those hand files contains nothing but that one global
# table. `units` (Batch 3a, the first Chapter-2-*owned* table to link) is
# structurally different: its hand definitions
# (`UnitDef_Event_Ch2Ally`/`UnitDef_Ch2Enemy_0`/`UnitDef_LordSplitAlly`/
# `UnitDef_Ch2Ally`/`UnitDef_Ch2NPC`/`UnitDef_Ch2Enemy_1`/
# `UnitDef_Ch2Enemy_2`, plus their private REDA sub-arrays) are only a
# *prefix slice* of src/events_udefs.c -- lines immediately after that
# file's own includes, ending right before Chapter 3's own REDA/
# UnitDefinition data begins. The same translation unit also defines
# every other chapter's units, which must stay hand-linked untouched, so
# this slice can't be excluded from compilation by filtering a whole
# file out of CFILES/MODERN_ALL_C_SOURCES the way GENERATED_DATA_LINKED_*
# above does.
#
# Instead, src/events_udefs.c carries its own self-contained guard:
# `#define GENERATED_DATA_UNITS_CH2_LINKED 1` immediately above the Ch2
# prefix block, which is itself wrapped in `#if !GENERATED_DATA_UNITS_CH2_LINKED
# / #endif`. Since the macro is unconditionally defined to 1 right there
# in the source, that block is permanently excluded from compilation --
# but its source text is left completely untouched, because
# generated-data-check's own round-trip parser (units/parser.py) reads
# src/events_udefs.c's raw text directly (brace-depth-aware regex, never
# the compiler) to keep proving the generated table byte-for-byte
# identical in meaning to it; preprocessor directives are invisible to
# that text-based parser, so the guard cannot desync the two.
#
# The excluded Ch2 block is a prefix of src/events_udefs.c's *own*
# top-level definitions, but NOT a prefix of the translation unit's
# compiled .data layout: the file's own two #include's just above it
# (src/events/prologue-eventudefs.h, src/events/ch1-eventudefs.h) emit
# Prologue/Chapter-1 REDA/UnitDefinition data first, ahead of Chapter 2,
# all still within the same events_udefs.o. So the generated Ch2 object
# must land, address-wise, *between* that still-hand Prologue/Ch1 data
# and events_udefs.c's own Chapter 3+ data -- not merely before the
# whole object. Since a single input section is placed by the linker as
# one atomic unit, right after the guard's closing #endif
# src/events_udefs.c redirects everything from Chapter 3 onward into a
# second, distinctly-named section (`#undef CONST_DATA` /
# `#define CONST_DATA SECTION(".data.ch2tail")`), splitting
# events_udefs.o's .data into two independently-placeable pieces of the
# *same* object file:
#   * legacy (ldscript.txt): src/events_udefs.o(.data) (Prologue/Ch1,
#     unchanged), then build/generated/data/data_ch2_units.o(.data),
#     then src/events_udefs.o(.data.ch2tail) (Chapter 3+, unchanged) --
#     each piece lands at exactly its original address, so the ROM is
#     byte-identical overall (verified via `cmp` against a pre-change
#     ROM).
#   * modern (modern.mk): modern's own MODERN_ELF_OBJECTS_LST/MANIFEST
#     $(sort) the full object list to decide floating-.data placement
#     order (see the GENERATED_DATA_LINKED_C reinstatement comment in
#     modern.mk) -- there is no "original hand path" to reuse here since
#     this object is additive, not a replacement, so it is instead
#     reinstated at a synthetic slot path
#     ($(MODERN_OUTPUT_DIR)/src/events_u-ch2units.o) deliberately chosen
#     to sort immediately between src/events_trapdata.o and
#     src/events_udefs.o. Modern's per-object (not per-input-section)
#     sort keeps events_udefs.o's two sections (.data and .data.ch2tail)
#     adjacent to each other regardless, so the synthetic slot ends up
#     immediately before events_udefs.o as a whole rather than truly
#     between its two pieces -- an acceptable, already-documented
#     divergence for the modern build (see the "Batch 3a" docs section:
#     modern's requirement is a successful, shiftable build, not literal
#     re-derivation of legacy's byte layout).
GENERATED_DATA_CH2_UNITS_HAND_SOURCE := src/events_udefs.c
GENERATED_DATA_CH2_UNITS_GUARD_MACRO := GENERATED_DATA_UNITS_CH2_LINKED
GENERATED_DATA_CH2_UNITS_C      := $(GENERATED_DATA_OUT_DIR)/data_ch2_units.c
GENERATED_DATA_CH2_UNITS_OBJECT := $(GENERATED_DATA_CH2_UNITS_C:.c=.o)

# `units`' own generator "config" inputs: headers
# scripts/generated_data/units/schema.py reads live constants from -- the
# CHARACTER_*/CLASS_*/ITEM_* designator sets (via the shared
# character_refs.py helper plus include/constants/classes.h and
# include/constants/items.h) used to validate charIndex/classIndex/items
# references, and struct UnitDefinition's own field capacities/
# UNIT_DEFINITION_ITEM_COUNT/enum udef_ai_index (include/bmunit.h) (see
# units/schema.py's own CHARACTERS_HEADER/CLASSES_HEADER/ITEMS_HEADER/
# BMUNIT_HEADER constants).
GENERATED_DATA_CONFIG_INPUTS_units := \
	include/constants/characters.h \
	include/constants/classes.h \
	include/constants/items.h \
	include/bmunit.h

# The 7 UnitDefinition group symbols this table's generated object must
# define exactly once each -- derived live from src/data/ch2_units.json
# (the same file the generator itself reads), not hardcoded here, so
# this list can never silently drift from what the table actually
# authors (same technique as GENERATED_DATA_LINKED_SYMBOL_supports
# above). The generated object's own private REDA_UnitDef_*_<index>
# sub-array symbol names are a deliberate implementation detail (see
# scripts/generated_data/units/generate.py's own docstring) never
# referenced from outside build/generated/data/data_ch2_units.o, so --
# unlike the 7 group symbols below, which src/events/ch2-eventinfo.h,
# src/events/ch2-eventscript.h, and src/events/lordsplit-eventscript.h
# all reference by name from other translation units -- they are not
# tracked here.
GENERATED_DATA_CH2_UNITS_SYMBOLS := $(shell $(PYTHON) -c \
	"import json; d = json.load(open('src/data/ch2_units.json')); print(' '.join(g['symbol'] for g in d['groups']))")

$(GENERATED_DATA_CH2_UNITS_C): src/data/ch2_units.json $(GENERATED_DATA_SHARED_PY_SOURCES) $(wildcard scripts/generated_data/units/*.py) $(GENERATED_DATA_CONFIG_INPUTS_units)
	@mkdir -p $(@D)
	$(GENERATED_DATA_PY) generate --table units --out-dir $(GENERATED_DATA_OUT_DIR)
	@test -e $@ || { echo "error: generated-data table 'units' did not produce $@ (schema default_output_name mismatch?)" >&2; exit 1; }

# Same legacy compile/assemble pipeline as GENERATED_DATA_LINKED_OBJECTS
# above (see that rule's own comment for why $(@:.o=.s), not $*.s).
$(GENERATED_DATA_CH2_UNITS_OBJECT): $(GENERATED_DATA_CH2_UNITS_C)
	$(CPP) $(CPPFLAGS) $< | iconv -f UTF-8 -t CP932 | $(CC1) $(CC1FLAGS) -o $(@:.o=.s)
	echo '.ALIGN 2, 0' >> $(@:.o=.s)
ifeq ($(UNAME),Darwin)
	$(SED) -f scripts/align_2_before_debug_section_for_osx.sed $(@:.o=.s)
else
	$(SED) '/.section	.debug_line/i\.align 2, 0' $(@:.o=.s)
endif
	$(AS) $(ASFLAGS) $(@:.o=.s) -o $@

.PHONY: generated-data-ch2-units-link-check

# Batch 3a gate: proves the units link-swap is wired correctly -- the
# generated object linked exactly once, at the exact ldscript.txt
# position, in both legacy ALL_OBJECTS and the modern cohort (at the
# adjacency-preserving synthetic slot path); every one of the table's 7
# top-level group symbols defined exactly once by the generated object
# and, critically, *zero* times by a freshly rebuilt src/events_udefs.o
# (the guard actually excluded them, so no multiple-definition risk);
# the hand block's source text still present verbatim (never deleted);
# an unrelated chapter's symbol (Chapter 1's REDA_Ch10AAlly_0_EIRIKA,
# Chapter 3's REDA_Event_Ch3Ally_EIRIKA) still defined by
# src/events_udefs.o, proving the guard didn't overreach; and the same
# touched-but-unchanged-input / from-scratch-parallel-build behavior
# evidence as generated-data-link-check, scoped to this one table. Local/
# manual gate, same reasoning as generated-data-link-check for why it's
# not CI-wired (agbcc is unavailable in CI's tool install).
generated-data-ch2-units-link-check: $(GENERATED_DATA_CH2_UNITS_OBJECT)
	@echo '--- guard present in $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE), hand block preserved verbatim ---'
	@if ! grep -qF '#define $(GENERATED_DATA_CH2_UNITS_GUARD_MACRO) 1' $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE); then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE) is missing '#define $(GENERATED_DATA_CH2_UNITS_GUARD_MACRO) 1'" >&2; exit 1; \
	fi
	@if ! grep -qF '#if !$(GENERATED_DATA_CH2_UNITS_GUARD_MACRO)' $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE); then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE) is missing the '#if !$(GENERATED_DATA_CH2_UNITS_GUARD_MACRO)' guard" >&2; exit 1; \
	fi
	@if ! grep -qF '#define CONST_DATA SECTION(".data.ch2tail")' $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE); then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE) is missing the post-guard CONST_DATA redirect to .data.ch2tail -- without it, Chapter 3+ data would stay glued to Prologue/Ch1 data in the same .data section and the generated object could not slot in between at the exact original address" >&2; exit 1; \
	fi
	@for symbol in $(GENERATED_DATA_CH2_UNITS_SYMBOLS); do \
		if [ "$$(grep -c "struct UnitDefinition $$symbol\[\]" $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE))" != 1 ]; then \
			echo "FAIL: hand source text for group '$$symbol' missing or duplicated in $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE) -- must stay present verbatim as the round-trip reference" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: guard present, all 7 hand group definitions preserved verbatim in source text'
	@echo '--- ldscript.txt three-piece split (events_udefs.o(.data), generated object, events_udefs.o(.data.ch2tail)) ---'
	@if grep -qx "        . = ALIGN(4); src/data_units.o(.data);" ldscript.txt; then \
		echo "FAIL: ldscript.txt unexpectedly references a non-existent src/data_units.o(.data)" >&2; exit 1; \
	fi
	@linked_count=$$(grep -Fc "$(GENERATED_DATA_CH2_UNITS_OBJECT)(.data);" ldscript.txt); \
	if [ "$$linked_count" != 1 ]; then \
		echo "FAIL: ldscript.txt references $(GENERATED_DATA_CH2_UNITS_OBJECT)(.data) $$linked_count time(s) (want exactly 1)" >&2; exit 1; \
	fi
	@prologue_line=$$(grep -nx "        . = ALIGN(4); src/events_udefs.o(.data);" ldscript.txt | cut -d: -f1); \
	gen_line=$$(grep -nF "$(GENERATED_DATA_CH2_UNITS_OBJECT)(.data);" ldscript.txt | cut -d: -f1); \
	tail_line=$$(grep -nx "        . = ALIGN(4); src/events_udefs.o(.data.ch2tail);" ldscript.txt | cut -d: -f1); \
	if [ -z "$$prologue_line" ]; then echo "FAIL: ldscript.txt no longer links src/events_udefs.o(.data) (the Prologue/Ch1 piece) at all" >&2; exit 1; fi; \
	if [ -z "$$tail_line" ]; then echo "FAIL: ldscript.txt no longer links src/events_udefs.o(.data.ch2tail) (the Chapter 3+ piece) at all" >&2; exit 1; fi; \
	if [ "$$((gen_line - prologue_line))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_OBJECT)(.data) (line $$gen_line) is not immediately after src/events_udefs.o(.data) (line $$prologue_line)" >&2; exit 1; \
	fi; \
	if [ "$$((tail_line - gen_line))" != 1 ]; then \
		echo "FAIL: src/events_udefs.o(.data.ch2tail) (line $$tail_line) is not immediately after $(GENERATED_DATA_CH2_UNITS_OBJECT)(.data) (line $$gen_line)" >&2; exit 1; \
	fi
	@echo 'OK: ldscript.txt links, in order, src/events_udefs.o(.data) [Prologue/Ch1], the generated object exactly once, then src/events_udefs.o(.data.ch2tail) [Chapter 3+]'
	@echo '--- legacy ALL_OBJECTS ---'
	@if [ "$(words $(filter $(GENERATED_DATA_CH2_UNITS_OBJECT),$(ALL_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_OBJECT) not present exactly once in legacy ALL_OBJECTS" >&2; exit 1; \
	fi
	@if [ "$(words $(filter $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE:.c=.o),$(ALL_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_UNITS_HAND_SOURCE:.c=.o) unexpectedly missing from legacy ALL_OBJECTS -- it must stay linked (it still defines every other chapter's units)" >&2; exit 1; \
	fi
	@echo 'OK: both the generated object and the (still-required) src/events_udefs.o are present exactly once each in legacy ALL_OBJECTS'
	@echo '--- modern MODERN_ALL_C_OBJECTS (synthetic adjacency-preserving slot) ---'
	@if [ "$(words $(filter $(MODERN_OUTPUT_DIR)/src/events_u-ch2units.o,$(MODERN_ALL_C_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(MODERN_OUTPUT_DIR)/src/events_u-ch2units.o not present exactly once in modern MODERN_ALL_C_OBJECTS" >&2; exit 1; \
	fi
	@sorted_slot=$$(printf '%s\n' $(sort $(MODERN_ALL_C_OBJECTS)) | grep -n -x -e "$(MODERN_OUTPUT_DIR)/src/events_u-ch2units.o" -e "$(MODERN_OUTPUT_DIR)/src/events_udefs.o" | cut -d: -f1 | tr '\n' ' '); \
	first=$$(echo $$sorted_slot | cut -d' ' -f1); second=$$(echo $$sorted_slot | cut -d' ' -f2); \
	if [ -z "$$first" ] || [ -z "$$second" ] || [ "$$((second - first))" != 1 ]; then \
		echo "FAIL: in the sorted modern object list, the synthetic slot is not immediately adjacent (and before) src/events_udefs.o (positions: $$sorted_slot)" >&2; exit 1; \
	fi
	@echo 'OK: synthetic slot object sorts immediately before src/events_udefs.o in the modern object list, exactly like the legacy ldscript.txt adjacency'
	@echo '--- generated object symbols (all 7 group symbols, exactly once each) ---'
	@nm_out=$$(arm-none-eabi-nm $(GENERATED_DATA_CH2_UNITS_OBJECT)); \
	for symbol in $(GENERATED_DATA_CH2_UNITS_SYMBOLS); do \
		symcount=$$(printf '%s\n' "$$nm_out" | grep -c " $$symbol\$$"); \
		if [ "$$symcount" != 1 ]; then \
			echo "FAIL: generated object for units defines $$symbol $$symcount time(s) (want exactly 1)" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: exactly one definition of each of the 7 expected group symbols in the generated object'
	@echo '--- src/events_udefs.o no longer defines any Ch2 group symbol, but still defines other chapters'"'"' ---'
	@rm -f src/events_udefs.o src/events_udefs.s
	@$(MAKE) --no-print-directory src/events_udefs.o >/dev/null
	@udefs_nm=$$(arm-none-eabi-nm src/events_udefs.o); \
	for symbol in $(GENERATED_DATA_CH2_UNITS_SYMBOLS); do \
		symcount=$$(printf '%s\n' "$$udefs_nm" | grep -c " $$symbol\$$"); \
		if [ "$$symcount" != 0 ]; then \
			echo "FAIL: src/events_udefs.o still defines $$symbol $$symcount time(s) -- the guard did not exclude it (would be a multiple-definition link error against the generated object)" >&2; exit 1; \
		fi; \
	done; \
	for other in REDA_Ch10AAlly_0_EIRIKA REDA_Event_Ch3Ally_EIRIKA; do \
		if ! printf '%s\n' "$$udefs_nm" | grep -q " $$other\$$"; then \
			echo "FAIL: src/events_udefs.o unexpectedly lost unrelated-chapter symbol $$other -- the guard over-excluded" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: src/events_udefs.o defines zero Ch2 group symbols and still defines Chapter 1/3 symbols untouched'
	@echo '--- clean coverage ---'
	@if [ -z "$(strip $(filter $(GENERATED_DATA_OUT_DIR),$(CLEAN_DIRS)))" ]; then \
		echo "FAIL: $(GENERATED_DATA_OUT_DIR) missing from CLEAN_DIRS -- clean/clean_fast would not remove data_ch2_units.c/.s/.o" >&2; exit 1; \
	fi
	@echo 'OK: clean/clean_fast remove build/generated/data (covers data_ch2_units.c/.s/.o)'
	@echo '--- touched-but-unchanged input: content-preserving no-op regenerate (behavior evidence, not mtime) ---'
	@rm -f $(GENERATED_DATA_CH2_UNITS_C) $(GENERATED_DATA_CH2_UNITS_OBJECT) $(GENERATED_DATA_CH2_UNITS_C:.c=.s); \
	$(MAKE) --no-print-directory $(GENERATED_DATA_CH2_UNITS_OBJECT) >/dev/null; \
	o_hash_before=$$(md5sum "$(GENERATED_DATA_CH2_UNITS_OBJECT)" | cut -d' ' -f1); \
	json_ref=generated-data-ch2-units-link-check.json_ref.tmp; \
	regen_log=generated-data-ch2-units-link-check.regen.log; \
	uptodate_log=generated-data-ch2-units-link-check.uptodate.log; \
	trap 'touch -r "$$json_ref" src/data/ch2_units.json 2>/dev/null; rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"' EXIT; \
	touch -r src/data/ch2_units.json "$$json_ref"; \
	touch src/data/ch2_units.json; \
	$(MAKE) --no-print-directory "$(GENERATED_DATA_CH2_UNITS_OBJECT)" >"$$regen_log" 2>&1; \
	if ! grep -q "generate --table units" "$$regen_log"; then \
		echo "FAIL: touching ch2_units.json did not trigger a units regenerate at all" >&2; exit 1; \
	fi; \
	if grep -qE 'arm-none-eabi-as|agbcc' "$$regen_log"; then \
		echo "FAIL: units unchanged-content regenerate still ran the legacy compile/assemble pipeline (unnecessary object recompile):" >&2; cat "$$regen_log" >&2; exit 1; \
	fi; \
	o_hash_after=$$(md5sum "$(GENERATED_DATA_CH2_UNITS_OBJECT)" | cut -d' ' -f1); \
	if [ "$$o_hash_before" != "$$o_hash_after" ]; then \
		echo "FAIL: units object content changed even though no recompile should have run ($$o_hash_before -> $$o_hash_after)" >&2; exit 1; \
	fi; \
	touch -r "$$json_ref" src/data/ch2_units.json; \
	$(MAKE) --no-print-directory "$(GENERATED_DATA_CH2_UNITS_OBJECT)" >"$$uptodate_log" 2>&1; \
	if grep -qE "generate --table units|arm-none-eabi-as|agbcc" "$$uptodate_log"; then \
		echo "FAIL: after restoring ch2_units.json's original timestamp, the units object target is not fully up to date:" >&2; cat "$$uptodate_log" >&2; exit 1; \
	fi; \
	rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"; \
	trap - EXIT; \
	echo 'OK: units touched-but-unchanged JSON input re-invokes the generator but never re-runs the legacy compile/assemble pipeline, proven by captured build-log evidence and stable object content'
	@echo '--- from-scratch parallel build ---'
	@rm -f $(GENERATED_DATA_CH2_UNITS_C) $(GENERATED_DATA_CH2_UNITS_OBJECT)
	@$(MAKE) --no-print-directory -j4 $(GENERATED_DATA_CH2_UNITS_OBJECT) $(GENERATED_DATA_CH2_UNITS_C) >/dev/null
	@test -e $(GENERATED_DATA_CH2_UNITS_C) || { echo "FAIL: parallel build did not produce the generated .c for units" >&2; exit 1; }
	@test -e $(GENERATED_DATA_CH2_UNITS_OBJECT) || { echo "FAIL: parallel build did not produce the generated object for units" >&2; exit 1; }
	@echo 'OK: from-scratch parallel (-j4) build of units'"'"'s generated .c/.o succeeds, no race/duplicate generation'
	@echo 'PASS: generated-data-ch2-units-link-check'

# ---------------------------------------------------------------------------
# Linking a Chapter-2-owned partial-file table with two non-adjacent hand
# blocks (Issue #5 Batch 3b)
# ---------------------------------------------------------------------------
# `traps` is structurally like `units` (Batch 3a, above) in that its two
# Chapter 2 symbols (`TrapData_Event_Ch2`, `TrapData_Event_Ch2Hard`) are
# only a slice of src/events_trapdata.c, a translation unit that also
# defines every other chapter's (and every other difficulty's) trap
# arrays, which must stay hand-linked untouched.
#
# It differs from `units` in one important way: `TrapData_Event_Ch2` and
# `TrapData_Event_Ch2Hard` are *not* adjacent to each other in the file --
# the file is laid out as one normal-mode block (Prologue..Ch19B, which
# is where TrapData_Event_Ch2 lives, right after Ch1) followed by one
# hard-mode block (PrologueHard..DebugMap_22, which is where
# TrapData_Event_Ch2Hard lives, ~1850 lines later, right after
# Ch1Hard). Excluding both from compilation via a single `#if !GUARD /
# #endif` region (like units' single contiguous prefix) is not possible
# since they aren't contiguous; instead src/events_trapdata.c wraps each
# in its own `#if !GENERATED_DATA_TRAPS_CH2_LINKED / #endif` region,
# sharing one guard macro (defined once, immediately above the first
# region).
#
# Since a single input section is placed by the linker as one atomic
# unit, and the two symbols must land at two addresses roughly 1850
# hand-written trap-array lines apart, splicing in one generated object
# containing both symbols in the *same* default section (the units
# approach) would force one of the two symbols to move far from its
# original address -- an avoidable, unquantifiably-large single-symbol
# jump. Instead, this table's generator
# (scripts/generated_data/traps/generate.py) places
# `TrapData_Event_Ch2Hard` alone into its own dedicated section
# (`.data.trapch2hard`) distinct from `TrapData_Event_Ch2`'s ordinary
# `.data`, so the *same* generated object can be spliced into ldscript.txt
# at two independent points. Combined with two `#undef CONST_DATA` /
# `#define CONST_DATA SECTION(...)` redirects in src/events_trapdata.c
# (right after each guard's closing #endif -- first to `.data.trapch2mid`
# for Chapter 3 through Ch1Hard, then to `.data.traptail` for Ch3Hard
# through end-of-file), this produces a four-piece split with *zero*
# address shift anywhere:
#   * legacy (ldscript.txt): src/events_trapdata.o(.data) (Prologue..Ch1,
#     unchanged), build/generated/data/data_ch2_traps.o(.data) (the
#     generated TrapData_Event_Ch2), src/events_trapdata.o(.data.trapch2mid)
#     (Ch3..Ch1Hard, unchanged), build/generated/data/data_ch2_traps.o
#     (.data.trapch2hard) (the generated TrapData_Event_Ch2Hard), then
#     src/events_trapdata.o(.data.traptail) (Ch3Hard..EOF, unchanged) --
#     each piece lands at exactly its original address, so the ROM is
#     byte-identical overall (verified via `cmp` against a pre-change
#     ROM).
#   * modern (modern.mk): same reasoning as the units table's synthetic
#     slot -- modern links whole objects, not per-input-section, and this
#     object is additive (no "original hand path" to reuse), so it is
#     reinstated at a synthetic slot path
#     ($(MODERN_OUTPUT_DIR)/src/events_t-ch2traps.o) chosen to sort
#     immediately before src/events_trapdata.o -- an acceptable,
#     already-documented divergence for the modern build (see the
#     "Batch 3a" docs section: modern's requirement is a successful,
#     shiftable build, not literal re-derivation of legacy's byte
#     layout).
GENERATED_DATA_CH2_TRAPS_HAND_SOURCE := src/events_trapdata.c
GENERATED_DATA_CH2_TRAPS_GUARD_MACRO := GENERATED_DATA_TRAPS_CH2_LINKED
GENERATED_DATA_CH2_TRAPS_C      := $(GENERATED_DATA_OUT_DIR)/data_ch2_traps.c
GENERATED_DATA_CH2_TRAPS_OBJECT := $(GENERATED_DATA_CH2_TRAPS_C:.c=.o)

# `traps`' own generator "config" inputs: include/bmtrick.h (the TRAP_*
# trap-type enum and TRAP_MAX_COUNT, read live by
# scripts/generated_data/traps/schema.py's read_trap_types()/
# read_trap_max_count()) and include/constants/items.h (ITEM_* subtype
# references).
GENERATED_DATA_CONFIG_INPUTS_traps := \
	include/bmtrick.h \
	include/constants/items.h

# The 2 trap array symbols this table's generated object must define
# exactly once each -- derived live from src/data/ch2_traps.json (the
# same file the generator itself reads), not hardcoded here, so this
# list can never silently drift from what the table actually authors
# (same technique as GENERATED_DATA_CH2_UNITS_SYMBOLS above).
GENERATED_DATA_CH2_TRAPS_SYMBOLS := $(shell $(PYTHON) -c \
	"import json; d = json.load(open('src/data/ch2_traps.json')); print(' '.join(t['symbol'] for t in d['traps']))")

$(GENERATED_DATA_CH2_TRAPS_C): src/data/ch2_traps.json $(GENERATED_DATA_SHARED_PY_SOURCES) $(wildcard scripts/generated_data/traps/*.py) $(GENERATED_DATA_CONFIG_INPUTS_traps)
	@mkdir -p $(@D)
	$(GENERATED_DATA_PY) generate --table traps --out-dir $(GENERATED_DATA_OUT_DIR)
	@test -e $@ || { echo "error: generated-data table 'traps' did not produce $@ (schema default_output_name mismatch?)" >&2; exit 1; }

# Same legacy compile/assemble pipeline as GENERATED_DATA_CH2_UNITS_OBJECT
# above (see that rule's own comment for why $(@:.o=.s), not $*.s).
$(GENERATED_DATA_CH2_TRAPS_OBJECT): $(GENERATED_DATA_CH2_TRAPS_C)
	$(CPP) $(CPPFLAGS) $< | iconv -f UTF-8 -t CP932 | $(CC1) $(CC1FLAGS) -o $(@:.o=.s)
	echo '.ALIGN 2, 0' >> $(@:.o=.s)
ifeq ($(UNAME),Darwin)
	$(SED) -f scripts/align_2_before_debug_section_for_osx.sed $(@:.o=.s)
else
	$(SED) '/.section	.debug_line/i\.align 2, 0' $(@:.o=.s)
endif
	$(AS) $(ASFLAGS) $(@:.o=.s) -o $@

.PHONY: generated-data-ch2-traps-link-check

# Batch 3b gate: proves the traps link-swap is wired correctly -- the
# generated object linked exactly once at each of its two independent
# ldscript.txt positions (once per section), in both legacy ALL_OBJECTS
# and the modern cohort (at the adjacency-preserving synthetic slot
# path); both of the table's 2 trap symbols defined exactly once by the
# generated object (one per section) and, critically, *zero* times by a
# freshly rebuilt src/events_trapdata.o (both guards actually excluded
# them, so no multiple-definition risk); both hand blocks' source text
# still present verbatim (never deleted); unrelated chapters' symbols
# (TrapData_Event_Ch3, TrapData_Event_Ch1Hard, both inside the
# .data.trapch2mid redirect, plus TrapData_Event_Ch3Hard just outside the
# .data.traptail redirect) still defined by src/events_trapdata.o, proving
# neither guard over-reached; and the same touched-but-unchanged-input /
# from-scratch-parallel-build evidence as generated-data-ch2-units-link-check,
# scoped to this one table. Local/manual gate, same reasoning as the
# other ch2-*-link-check targets for why it's not CI-wired (agbcc is
# unavailable in CI's tool install).
generated-data-ch2-traps-link-check: $(GENERATED_DATA_CH2_TRAPS_OBJECT)
	@echo '--- guard present in $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE), both hand blocks preserved verbatim ---'
	@if [ "$$(grep -cF '#define $(GENERATED_DATA_CH2_TRAPS_GUARD_MACRO) 1' $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) does not define '#define $(GENERATED_DATA_CH2_TRAPS_GUARD_MACRO) 1' exactly once" >&2; exit 1; \
	fi
	@if [ "$$(grep -cF '#if !$(GENERATED_DATA_CH2_TRAPS_GUARD_MACRO)' $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE))" != 2 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) does not have exactly 2 '#if !$(GENERATED_DATA_CH2_TRAPS_GUARD_MACRO)' guards (want one per non-adjacent Ch2 block)" >&2; exit 1; \
	fi
	@if [ "$$(grep -cF '#endif /* !$(GENERATED_DATA_CH2_TRAPS_GUARD_MACRO) */' $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE))" != 2 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) does not have exactly 2 matching '#endif' guard closes" >&2; exit 1; \
	fi
	@if ! grep -qF '#define CONST_DATA SECTION(".data.trapch2mid")' $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE); then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) is missing the post-Ch2-guard CONST_DATA redirect to .data.trapch2mid" >&2; exit 1; \
	fi
	@if ! grep -qF '#define CONST_DATA SECTION(".data.traptail")' $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE); then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) is missing the post-Ch2Hard-guard CONST_DATA redirect to .data.traptail" >&2; exit 1; \
	fi
	@for symbol in $(GENERATED_DATA_CH2_TRAPS_SYMBOLS); do \
		if [ "$$(grep -c "CONST_DATA u8 $$symbol\[\]" $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE))" != 1 ]; then \
			echo "FAIL: hand source text for trap array '$$symbol' missing or duplicated in $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE) -- must stay present verbatim as the round-trip reference" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: guard present exactly twice, both hand trap array definitions preserved verbatim in source text'
	@echo '--- ldscript.txt four-piece split (events_trapdata.o(.data), generated Ch2, events_trapdata.o(.data.trapch2mid), generated Ch2Hard, events_trapdata.o(.data.traptail)) ---'
	@normal_count=$$(grep -Fc "$(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data);" ldscript.txt); \
	if [ "$$normal_count" != 1 ]; then \
		echo "FAIL: ldscript.txt references $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data) $$normal_count time(s) (want exactly 1)" >&2; exit 1; \
	fi
	@hard_count=$$(grep -Fc "$(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data.trapch2hard);" ldscript.txt); \
	if [ "$$hard_count" != 1 ]; then \
		echo "FAIL: ldscript.txt references $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data.trapch2hard) $$hard_count time(s) (want exactly 1)" >&2; exit 1; \
	fi
	@prefix_line=$$(grep -nx "        . = ALIGN(4); src/events_trapdata.o(.data);" ldscript.txt | cut -d: -f1); \
	gen1_line=$$(grep -nx "        build/generated/data/data_ch2_traps.o(.data);" ldscript.txt | cut -d: -f1); \
	mid_line=$$(grep -nx "        src/events_trapdata.o(.data.trapch2mid);" ldscript.txt | cut -d: -f1); \
	gen2_line=$$(grep -nx "        build/generated/data/data_ch2_traps.o(.data.trapch2hard);" ldscript.txt | cut -d: -f1); \
	tail_line=$$(grep -nx "        src/events_trapdata.o(.data.traptail);" ldscript.txt | cut -d: -f1); \
	if [ -z "$$prefix_line" ]; then echo "FAIL: ldscript.txt no longer links src/events_trapdata.o(.data) (the Prologue/Ch1 piece) at all" >&2; exit 1; fi; \
	if [ -z "$$mid_line" ]; then echo "FAIL: ldscript.txt no longer links src/events_trapdata.o(.data.trapch2mid) (the Ch3..Ch1Hard piece) at all" >&2; exit 1; fi; \
	if [ -z "$$tail_line" ]; then echo "FAIL: ldscript.txt no longer links src/events_trapdata.o(.data.traptail) (the Ch3Hard..EOF piece) at all" >&2; exit 1; fi; \
	if [ "$$((gen1_line - prefix_line))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data) (line $$gen1_line) is not immediately after src/events_trapdata.o(.data) (line $$prefix_line)" >&2; exit 1; \
	fi; \
	if [ "$$((mid_line - gen1_line))" != 1 ]; then \
		echo "FAIL: src/events_trapdata.o(.data.trapch2mid) (line $$mid_line) is not immediately after $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data) (line $$gen1_line)" >&2; exit 1; \
	fi; \
	if [ "$$((gen2_line - mid_line))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data.trapch2hard) (line $$gen2_line) is not immediately after src/events_trapdata.o(.data.trapch2mid) (line $$mid_line)" >&2; exit 1; \
	fi; \
	if [ "$$((tail_line - gen2_line))" != 1 ]; then \
		echo "FAIL: src/events_trapdata.o(.data.traptail) (line $$tail_line) is not immediately after $(GENERATED_DATA_CH2_TRAPS_OBJECT)(.data.trapch2hard) (line $$gen2_line)" >&2; exit 1; \
	fi
	@echo 'OK: ldscript.txt links, in order, src/events_trapdata.o(.data) [Prologue/Ch1], the generated Ch2 symbol, src/events_trapdata.o(.data.trapch2mid) [Ch3..Ch1Hard], the generated Ch2Hard symbol, then src/events_trapdata.o(.data.traptail) [Ch3Hard..EOF]'
	@echo '--- legacy ALL_OBJECTS ---'
	@if [ "$(words $(filter $(GENERATED_DATA_CH2_TRAPS_OBJECT),$(ALL_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_OBJECT) not present exactly once in legacy ALL_OBJECTS" >&2; exit 1; \
	fi
	@if [ "$(words $(filter $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE:.c=.o),$(ALL_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(GENERATED_DATA_CH2_TRAPS_HAND_SOURCE:.c=.o) unexpectedly missing from legacy ALL_OBJECTS -- it must stay linked (it still defines every other chapter's/difficulty's traps)" >&2; exit 1; \
	fi
	@echo 'OK: both the generated object and the (still-required) src/events_trapdata.o are present exactly once each in legacy ALL_OBJECTS'
	@echo '--- modern MODERN_ALL_C_OBJECTS (synthetic adjacency-preserving slot) ---'
	@if [ "$(words $(filter $(MODERN_OUTPUT_DIR)/src/events_t-ch2traps.o,$(MODERN_ALL_C_OBJECTS)))" != 1 ]; then \
		echo "FAIL: $(MODERN_OUTPUT_DIR)/src/events_t-ch2traps.o not present exactly once in modern MODERN_ALL_C_OBJECTS" >&2; exit 1; \
	fi
	@sorted_slot=$$(printf '%s\n' $(sort $(MODERN_ALL_C_OBJECTS)) | grep -n -x -e "$(MODERN_OUTPUT_DIR)/src/events_t-ch2traps.o" -e "$(MODERN_OUTPUT_DIR)/src/events_trapdata.o" | cut -d: -f1 | tr '\n' ' '); \
	first=$$(echo $$sorted_slot | cut -d' ' -f1); second=$$(echo $$sorted_slot | cut -d' ' -f2); \
	if [ -z "$$first" ] || [ -z "$$second" ] || [ "$$((second - first))" != 1 ]; then \
		echo "FAIL: in the sorted modern object list, the synthetic slot is not immediately adjacent (and before) src/events_trapdata.o (positions: $$sorted_slot)" >&2; exit 1; \
	fi
	@echo 'OK: synthetic slot object sorts immediately before src/events_trapdata.o in the modern object list, exactly like the legacy ldscript.txt adjacency'
	@echo '--- generated object symbols (both trap symbols, exactly once each, in their respective sections) ---'
	@nm_out=$$(arm-none-eabi-nm $(GENERATED_DATA_CH2_TRAPS_OBJECT)); \
	for symbol in $(GENERATED_DATA_CH2_TRAPS_SYMBOLS); do \
		symcount=$$(printf '%s\n' "$$nm_out" | grep -c " $$symbol\$$"); \
		if [ "$$symcount" != 1 ]; then \
			echo "FAIL: generated object for traps defines $$symbol $$symcount time(s) (want exactly 1)" >&2; exit 1; \
		fi; \
	done
	@objdump_out=$$(arm-none-eabi-objdump -t $(GENERATED_DATA_CH2_TRAPS_OBJECT)); \
	if ! printf '%s\n' "$$objdump_out" | grep -q "\.data\.trapch2hard.*TrapData_Event_Ch2Hard\$$"; then \
		echo "FAIL: TrapData_Event_Ch2Hard is not defined in the .data.trapch2hard section of the generated object" >&2; exit 1; \
	fi; \
	if ! printf '%s\n' "$$objdump_out" | grep -E "\.data[[:space:]].*TrapData_Event_Ch2\$$" >/dev/null; then \
		echo "FAIL: TrapData_Event_Ch2 is not defined in the plain .data section of the generated object" >&2; exit 1; \
	fi
	@echo 'OK: exactly one definition of each of the 2 expected trap symbols in the generated object, each in its expected section'
	@echo '--- src/events_trapdata.o no longer defines either Ch2 trap symbol, but still defines other chapters'"'"' ---'
	@rm -f src/events_trapdata.o src/events_trapdata.s
	@$(MAKE) --no-print-directory src/events_trapdata.o >/dev/null
	@trapdata_nm=$$(arm-none-eabi-nm src/events_trapdata.o); \
	for symbol in $(GENERATED_DATA_CH2_TRAPS_SYMBOLS); do \
		symcount=$$(printf '%s\n' "$$trapdata_nm" | grep -c " $$symbol\$$"); \
		if [ "$$symcount" != 0 ]; then \
			echo "FAIL: src/events_trapdata.o still defines $$symbol $$symcount time(s) -- the guard did not exclude it (would be a multiple-definition link error against the generated object)" >&2; exit 1; \
		fi; \
	done; \
	for other in TrapData_Event_Ch3 TrapData_Event_Ch1Hard TrapData_Event_Ch3Hard; do \
		if ! printf '%s\n' "$$trapdata_nm" | grep -q " $$other\$$"; then \
			echo "FAIL: src/events_trapdata.o unexpectedly lost unrelated symbol $$other -- a guard over-excluded" >&2; exit 1; \
		fi; \
	done
	@echo 'OK: src/events_trapdata.o defines zero Ch2 trap symbols and still defines the surrounding chapters'"'"' traps untouched'
	@echo '--- clean coverage ---'
	@if [ -z "$(strip $(filter $(GENERATED_DATA_OUT_DIR),$(CLEAN_DIRS)))" ]; then \
		echo "FAIL: $(GENERATED_DATA_OUT_DIR) missing from CLEAN_DIRS -- clean/clean_fast would not remove data_ch2_traps.c/.s/.o" >&2; exit 1; \
	fi
	@echo 'OK: clean/clean_fast remove build/generated/data (covers data_ch2_traps.c/.s/.o)'
	@echo '--- touched-but-unchanged input: content-preserving no-op regenerate (behavior evidence, not mtime) ---'
	@rm -f $(GENERATED_DATA_CH2_TRAPS_C) $(GENERATED_DATA_CH2_TRAPS_OBJECT) $(GENERATED_DATA_CH2_TRAPS_C:.c=.s); \
	$(MAKE) --no-print-directory $(GENERATED_DATA_CH2_TRAPS_OBJECT) >/dev/null; \
	o_hash_before=$$(md5sum "$(GENERATED_DATA_CH2_TRAPS_OBJECT)" | cut -d' ' -f1); \
	json_ref=generated-data-ch2-traps-link-check.json_ref.tmp; \
	regen_log=generated-data-ch2-traps-link-check.regen.log; \
	uptodate_log=generated-data-ch2-traps-link-check.uptodate.log; \
	trap 'touch -r "$$json_ref" src/data/ch2_traps.json 2>/dev/null; rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"' EXIT; \
	touch -r src/data/ch2_traps.json "$$json_ref"; \
	touch src/data/ch2_traps.json; \
	$(MAKE) --no-print-directory "$(GENERATED_DATA_CH2_TRAPS_OBJECT)" >"$$regen_log" 2>&1; \
	if ! grep -q "generate --table traps" "$$regen_log"; then \
		echo "FAIL: touching ch2_traps.json did not trigger a traps regenerate at all" >&2; exit 1; \
	fi; \
	if grep -qE 'arm-none-eabi-as|agbcc' "$$regen_log"; then \
		echo "FAIL: traps unchanged-content regenerate still ran the legacy compile/assemble pipeline (unnecessary object recompile):" >&2; cat "$$regen_log" >&2; exit 1; \
	fi; \
	o_hash_after=$$(md5sum "$(GENERATED_DATA_CH2_TRAPS_OBJECT)" | cut -d' ' -f1); \
	if [ "$$o_hash_before" != "$$o_hash_after" ]; then \
		echo "FAIL: traps object content changed even though no recompile should have run ($$o_hash_before -> $$o_hash_after)" >&2; exit 1; \
	fi; \
	touch -r "$$json_ref" src/data/ch2_traps.json; \
	$(MAKE) --no-print-directory "$(GENERATED_DATA_CH2_TRAPS_OBJECT)" >"$$uptodate_log" 2>&1; \
	if grep -qE "generate --table traps|arm-none-eabi-as|agbcc" "$$uptodate_log"; then \
		echo "FAIL: after restoring ch2_traps.json's original timestamp, the traps object target is not fully up to date:" >&2; cat "$$uptodate_log" >&2; exit 1; \
	fi; \
	rm -f "$$json_ref" "$$regen_log" "$$uptodate_log"; \
	trap - EXIT; \
	echo 'OK: traps touched-but-unchanged JSON input re-invokes the generator but never re-runs the legacy compile/assemble pipeline, proven by captured build-log evidence and stable object content'
	@echo '--- from-scratch parallel build ---'
	@rm -f $(GENERATED_DATA_CH2_TRAPS_C) $(GENERATED_DATA_CH2_TRAPS_OBJECT)
	@$(MAKE) --no-print-directory -j4 $(GENERATED_DATA_CH2_TRAPS_OBJECT) $(GENERATED_DATA_CH2_TRAPS_C) >/dev/null
	@test -e $(GENERATED_DATA_CH2_TRAPS_C) || { echo "FAIL: parallel build did not produce the generated .c for traps" >&2; exit 1; }
	@test -e $(GENERATED_DATA_CH2_TRAPS_OBJECT) || { echo "FAIL: parallel build did not produce the generated object for traps" >&2; exit 1; }
	@echo 'OK: from-scratch parallel (-j4) build of traps'"'"'s generated .c/.o succeeds, no race/duplicate generation'
	@echo 'PASS: generated-data-ch2-traps-link-check'
