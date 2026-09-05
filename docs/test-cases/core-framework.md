# Core framework and authoring cases

These procedures cover the shipped core framework and contributor-facing
authoring capabilities listed by issue [#57](https://github.com/laqieer/fireemblem8-expansion/issues/57).
They reuse existing commands and semantic scenarios. They do not introduce a
second registry, harness, generated-data schema, or project ruleset.

## TC-CORE-001: Modern default build and archival split

- **Feature / originating issue:** `modern-framework-build` /
  [#3](https://github.com/laqieer/fireemblem8-expansion/issues/3),
  [#4](https://github.com/laqieer/fireemblem8-expansion/issues/4), and
  [#28](https://github.com/laqieer/fireemblem8-expansion/issues/28);
  [#102](https://github.com/laqieer/fireemblem8-expansion/issues/102)
  preserves these checks through resolved Make data, compiler diagnostics,
  generated linker inputs, and linked artifacts rather than source spelling.
- **Supported configuration or artifact:** clean supported-host source checkout;
  the default modern release AAPCS build.
- **Prerequisites and clean starting state:** run at the repository root with
  the modern compiler and libmGBA installed by `./scripts/quickstart.sh`.

### Actions

1. Run `make expansion-modern-toolchain-check`, then bare `make`.
2. Run `make expansion-modern-linker-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. Run `make -n fireemblem8.gba` only when intentionally investigating the
   separately named archival lane; never use it as a substitute for step 2.

### Expected result

The bare target produces and boot-verifies the modern release ROM. The linker
gate verifies the release layout, header, budget, shifted run, and runtime
fingerprints through the supported modern path.

### Negative control

The default-lane and quickstart checks reject a missing or invalid modern
toolchain clearly. Bare `make` never resolves `tools/agbcc`; the legacy target
is reached only by its explicit name.

### Interactions and save compatibility

The default profile is independent of optional feature profiles. This build
selection changes no save bytes, save layout, migration, or compatibility
epoch; the archival lane retains its default-cap-only boundary.

### Automation

- `python3 -m unittest discover -s scripts/modernize/tests -p "test_build_default_lane.py" -v`
  — `scripts/modernize/tests/test_build_default_lane.py`.
- `python3 -m unittest discover -s scripts/modernize/tests -p "test_quickstart.py" -v`
  — `scripts/modernize/tests/test_quickstart.py`.
- `python3 -m unittest discover -s scripts/modernize/tests -p "test_modern_elf.py" -v`
  and
  `python3 -m unittest discover -s scripts/modernize/tests -p "test_build_mgfembp.py" -v`
  — resolved Make graph, generated linker input, and payload-artifact checks.
- `python3 -m unittest discover -s scripts/modernize/tests -p "test_expansion_config.py" -v`
  — parsed configuration and real preprocessor dependency controls.
- `make expansion-modern-linker-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/boot.json`.

### Cleanup and limitations

Use `make clean_fast` only to remove build artifacts. This proves the modern
release framework boundary; it does not claim byte identity with the original
ROM or make the archival lane a release requirement.

## TC-BUILD-GCC14-MENU-RETURN-001: GCC 14 menu callback return compatibility

- **Feature / originating issue:** `modern-framework-build` /
  [#197](https://github.com/laqieer/fireemblem8-expansion/issues/197).
- **Supported configuration or artifact:** modern release AAPCS
  `build/expansion-modern/release/aapcs/fireemblem8.gba`; explicit archival
  `fireemblem8.gba` control build.
- **Prerequisites and clean starting state:** repository root with
  `arm-none-eabi-gcc` 14.2.1, the remaining modern quickstart dependencies,
  and the archival toolchain installed. Record
  `arm-none-eabi-gcc --version | head -1` with the test result.

### Actions

1. Run `arm-none-eabi-gcc --version | head -1` and confirm GCC 14.2.1.
2. Run `make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. Run `make fireemblem8.gba`.

### Expected result

GCC compiles each integer-returning menu callback without a return-mismatch
diagnostic, and the modern ROM links and passes boot verification. The
archival ROM also builds without changing its callback source behavior.

### Negative control

Before the fix, GCC 14 rejects the modern build because
`StealItemMenuCommand_Draw` and `ItemMenu_SwitchOut_DoNothing` use valueless
returns in integer-returning callbacks. Applying `return 0` to the shared
archival path is also invalid because it changes agbcc code generation.

### Interactions and save compatibility

The callbacks depend only on the existing `MenuItemDef` signatures. There are
no feature conflicts, generated-data or localization effects, or save-format
changes.

### Automation

- `make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — compiles, links, and boot-verifies the supported modern release ROM.
- `make fireemblem8.gba`
  — proves the guarded source preserves the explicit archival build lane.

### Cleanup and limitations

Use `make clean_fast` only when generated outputs must be removed. This case
proves build compatibility and the archival lane boundary; it does not claim
that modern ROM output is byte-identical to the original game.

## TC-CORE-002: Configuration identity rejects invalid values

- **Feature / originating issue:** `configuration-identity` /
  [#8](https://github.com/laqieer/fireemblem8-expansion/issues/8).
- **Supported configuration or artifact:** modern AAPCS `debug` and `release`
  source profiles, configured either through `./configure` or a one-off Make
  override.
- **Prerequisites and clean starting state:** repository root; remove an
  unwanted `config.autotools.mk` or use explicit command-line values.

### Actions

1. Run `./configure --help`, configure a supported profile, then run `make`.
2. Run `make expansion-modern-rom MODERN_CONFIG=debug MODERN_ABI=aapcs` and
   the matching release command.
3. Run `python3 -m unittest scripts.modernize.tests.test_expansion_config -v`.

### Expected result

Each ROM has a valid header and embedded metadata record. Debug and release
produce distinct deterministic configuration fingerprints, while direct
one-off overrides retain the documented precedence.

### Negative control

The configuration suite and Make resolver reject malformed fields,
non-binary flags, incompatible locales, and `MODERN_ABI=apcs-gnu` for linked
targets before compilation or linking starts.

### Interactions and save compatibility

Configuration identity is diagnostic unless a documented save-compatible
setting says otherwise. These profiles preserve the existing save format and
epoch; optional feature dependencies remain validated by their own resolver.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_expansion_config -v`
  — `scripts/modernize/tests/test_expansion_config.py`.
- `make expansion-modern-rom MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `scripts/modernize/verify_rom_header.py`.
- `make expansion-modern-rom MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `scripts/modernize/verify_rom_header.py`.

### Cleanup and limitations

Run `make distclean` only to remove Autoconf output and `make clean_fast` for
build artifacts. This case does not add a runtime kill switch or a new
configuration registry.

## TC-CORE-003: Save compatibility preserves data

- **Feature / originating issue:** `save-compatibility` /
  [#2](https://github.com/laqieer/fireemblem8-expansion/issues/2).
- **Supported configuration or artifact:** modern AAPCS debug and release
  source builds with synthetic test SRAM only.
- **Prerequisites and clean starting state:** start from the repository root;
  do not use a committed save, savestate, or ROM fixture.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_save_format_tool -v`.
2. Run
   `GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -p "test_save_compat_scenarios.py" -v`.
3. Run `make expansion-modern-savefmt-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
   and repeat for release.
4. In the debug clean-boot route, observe CURRENT, choose Back for an
   incompatible fixture, then confirm Erase only in its separate fixture.

### Expected result

Host migration performs a non-destructive out-of-place conversion. The live
scenarios cover CURRENT, every classifier state, Back preservation, confirmed
Erase, ordinary save/load, and Suspend/Resume through real engine paths.

### Negative control

Back leaves synthetic SRAM unchanged. Corrupt, older, newer, and
configuration-incompatible inputs are diagnosed rather than silently wiped;
Erase occurs only after its confirmation action.

### Interactions and save compatibility

This is the save contract itself. It remains compatible with existing
debug/release profiles and optional framework flags unless a future change
documents and bumps the format or epoch.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_save_format_tool -v`
  — `scripts/modernize/tests/test_save_format_tool.py`.
- `GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -p "test_save_compat_scenarios.py" -v`
  — `tools/gba-playtest/tests/test_save_compat_scenarios.py`.
- `make expansion-modern-savefmt-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/run_save_compat_checks.py`.

### Cleanup and limitations

Generated SRAM belongs under ignored build output and may be removed with
`make clean_fast`. In-console structural migration remains intentionally
unsupported; the documented host migration tool owns it.

## TC-CORE-004: Generated data loop reports diagnostics

- **Feature / originating issue:** `generated-data-platform` /
  [#5](https://github.com/laqieer/fireemblem8-expansion/issues/5).
- **Supported configuration or artifact:** clean source checkout with Python
  3; generated output is build-local.
- **Prerequisites and clean starting state:** use a disposable worktree for
  an authored edit; do not hand-edit `build/generated/data/`.

### Actions

1. Edit one supported JSON input in the disposable worktree.
2. Run `make generated-data-validate`, `make generated-data-generate`,
   `make generated-data-check`, and `make generated-data-test`.
3. Deliberately use an invalid or dangling reference in the disposable copy
   and retain its file, line, column, and breadcrumb diagnostic before reset.

### Expected result

Valid authored input generates deterministic C89 output and passes the drift
gate. A bad cross-table, range, duplicate, or reference value reports an
actionable source diagnostic instead of generating a partial success.

### Negative control

The invalid disposable input fails validation and the check gate reports
drift. Generated output is never accepted as a new hand-authored source of
truth.

### Interactions and save compatibility

Tables resolve through the existing schema/dependency graph. A data-only
authoring round trip does not alter save layout or epoch; a future authored
ID expansion must use the separate typed-ID contract.

### Automation

- `make generated-data-validate && make generated-data-generate && make generated-data-check`
  — `scripts/generated_data/tests/test_cli.py`.
- `make generated-data-test`
  — `scripts/generated_data/tests/test_validators.py`.
- `python3 -m unittest scripts.generated_data.tests.test_cli scripts.generated_data.tests.test_cli_new_tables scripts.generated_data.tests.test_validators -v`
  — `scripts/generated_data/tests/test_cli_new_tables.py`.

### Cleanup and limitations

Reset the disposable source edit and run `make clean_fast` if needed. This
does not create a content pack, alter default Chapter 2, or replace a
hand-written callback with a second event router.

## TC-CORE-005: Typed ID cap preserves default boundary

- **Feature / originating issue:** `typed-id-item-cap` /
  [#10](https://github.com/laqieer/fireemblem8-expansion/issues/10).
- **Supported configuration or artifact:** modern AAPCS default cap and
  expanded `FE8_ITEM_ID_CAP=0xCE` debug/release profiles.
- **Prerequisites and clean starting state:** repository root with a clean
  build directory; inspect DEFAULT and build-local ACTIVE contracts separately.

### Actions

1. Run `make expansion-modern-idspace-active-check` and
   `FE8_ITEM_ID_CAP=0xCE make expansion-modern-idspace-active-check`.
2. Run both expanded-cap runtime gates:

   ```sh
   FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 \
     make expansion-modern-itemexpansion-check MODERN_CONFIG=debug MODERN_ABI=aapcs
   FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 \
     make expansion-modern-itemexpansion-check MODERN_CONFIG=release MODERN_ABI=aapcs
   ```

3. Compare the DEFAULT committed header with the ACTIVE generated header.

### Expected result

The expanded record round-trips through item, UI, event, link, game-save, and
suspend routes while the active cap/table agree. The committed DEFAULT
contract remains vanilla-cap and unchanged.

### Negative control

The default cap refuses the expansion record, stale ACTIVE output self-heals
or fails clearly, and the archival lane fast-fails any widened-cap request.

### Interactions and save compatibility

Starter content may use the cap only with its documented dependencies. Item
encoding, packed save layout, checksum domain, and compatibility epoch remain
unchanged; class/chapter/unit widening is outside this pilot.

### Automation

- `python3 -m unittest scripts.generated_data.tests.test_idspace scripts.generated_data.tests.test_idspace_active -v`
  — `scripts/generated_data/tests/test_idspace_active.py`.
- `make expansion-modern-idspace-active-check`
  — `scripts/modernize/tests/test_idspace_active_check_gate_hermetic.py`.
- `FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 make expansion-modern-itemexpansion-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/run_item_expansion_checks.py`.

### Cleanup and limitations

Use `make clean_fast` to remove active generated artifacts. This case is the
item-cap pilot only, not a claim that every ID domain is widened or archival
compatible.

## TC-CORE-006: Debug tools are debug only

- **Feature / originating issue:** `debug-tools` /
  [#11](https://github.com/laqieer/fireemblem8-expansion/issues/11).
- **Supported configuration or artifact:** modern AAPCS debug ROM for the
  positive route and matching release ROM for the compiled-out control.
- **Prerequisites and clean starting state:** clean boot with no savestate;
  libmGBA is required by the mapped runtime scenarios.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_debugtools_registry.py" -v`.
2. Run `make expansion-modern-debugtools-tools-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Repeat the gate for release and use the same clean input sequence.

### Expected result

The debug route opens the bounded hub, invokes each shipped tool with its
documented semantic effect, returns to an interactive map, and reports
diagnostics without persisting debug state.

### Negative control

Release compiles out the launcher and tool symbols; its runtime probe remains
all-zero over the same route rather than exposing an unreachable menu.

### Interactions and save compatibility

The subsystem uses the existing menu/proc seams and is independent of
generated data and optional gameplay. It changes neither save bytes, layout,
migration, nor compatibility epoch.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_debugtools_registry.py" -v`
  — `tools/gba-playtest/tests/test_debugtools_registry.py`.
- `make expansion-modern-debugtools-tools-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/tests/test_tools_scenario.py`.
- `make expansion-modern-debugtools-tools-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/debugtools-tools-modern-release.json`.

### Cleanup and limitations

Exit the hub before stopping the scenario and use `make clean_fast` for
artifacts. The bounded tools are not an arbitrary memory editor, interactive
debugger, or release feature.

## TC-CORE-007: Runtime harness detects mismatch

- **Feature / originating issue:** `runtime-playtest-harness` /
  [#13](https://github.com/laqieer/fireemblem8-expansion/issues/13).
- **Supported configuration or artifact:** Python 3 host-only suite and modern
  AAPCS debug/release ROMs built from the documented source commands.
- **Prerequisites and clean starting state:** clean boot with no save,
  savestate, or externally supplied ROM baseline.

### Actions

1. Run `GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -v`.
2. Build and capture a fresh debug ROM, then verify the capture under the
   behavior policy:

   ```sh
   make expansion-modern-rom MODERN_CONFIG=debug MODERN_ABI=aapcs
   python3 tools/gba-playtest/gba_playtest.py capture \
     --rom build/expansion-modern/debug/aapcs/fireemblem8.gba \
     --scenario tools/gba-playtest/scenarios/boot.json \
     --output build/tc-core-007-boot.json
   python3 tools/gba-playtest/gba_playtest.py verify \
     --policy behavior \
     --rom build/expansion-modern/debug/aapcs/fireemblem8.gba \
     --scenario tools/gba-playtest/scenarios/boot.json \
     --expected build/tc-core-007-boot.json
   ```

3. Create a build-local deliberately mismatched copy and verify that it fails
   without changing the matching capture:

   ```sh
   cp build/tc-core-007-boot.json build/tc-core-007-boot-mismatch.json
   python3 -c 'import json; from pathlib import Path; path = Path("build/tc-core-007-boot-mismatch.json"); expected = json.loads(path.read_text()); expected["checkpoints"][0]["framebuffer_hash"] = "fnv1a64-rgb24:0000000000000000"; path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")'
   if python3 tools/gba-playtest/gba_playtest.py verify \
     --policy behavior \
     --rom build/expansion-modern/debug/aapcs/fireemblem8.gba \
     --scenario tools/gba-playtest/scenarios/boot.json \
     --expected build/tc-core-007-boot-mismatch.json; then
     echo "expected mismatch unexpectedly passed"
     exit 1
   fi
   python3 -m unittest discover -s tools/gba-playtest/tests \
     -p "test_baseline_no_autorefresh.py" -v
   ```

   The fixture checks the matching capture's bytes and modification time after
   each `verify`; do not capture over an expected fingerprint.

### Expected result

The harness records provenance and semantic probes deterministically. Valid
behavior-policy verification succeeds while a changed checkpoint reports the
JSON path, expected value, and observed value.

### Negative control

`verify` cannot write or refresh a baseline. Host-only mode skips live tests
by its explicit switch, not artifact timing; malformed scenarios and mismatch
are failures, never retries or success-shaped fallbacks.

### Interactions and save compatibility

Runtime scenarios start clean and may observe save semantics without committing
save data. The harness is shared by debug/release target gates and changes no
ROM, save layout, or configuration identity itself.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_baseline_no_autorefresh.py" -v`
  — `tools/gba-playtest/tests/test_baseline_no_autorefresh.py`.
- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_host_only_mode.py" -v`
  — `tools/gba-playtest/tests/test_host_only_mode.py`.
- `make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/gba_playtest.py`.

### Cleanup and limitations

Keep captures under ignored build output and remove them with `make clean_fast`
when finished. Screenshots are supplementary only; semantic probes, not
pointer/timing/source-text coincidences, are the behavioral oracle.

## TC-CORE-008: Upstream scan records a human decision

- **Feature / originating issue:** `upstream-port-tooling` /
  [#12](https://github.com/laqieer/fireemblem8-expansion/issues/12).
- **Supported configuration or artifact:** a local source checkout with a
  verified canonical `decomp` remote and ignored `build/upstream-port/` output.
- **Prerequisites and clean starting state:** no unreviewed local state update;
  use a locally available canonical ref or explicitly fetch it first.

### Actions

1. Run `python3 -m scripts.upstream_port scan --ref decomp/master --format text`
   and `drift` without an output path.
2. Generate a report only for explicitly selected non-merge SHAs under an
   ignored `build/upstream-port/` directory.
3. Review the report and manually record each port/skip/supersede/conflict
   decision with rationale and validation evidence, then run `verify`.
4. From the source repository root, invoke the documented
   the [authenticated upstream verifier dry-run command](../publisher_authority_bootstrap.md#full-upstream-verifier-dry-run). Exercise implicit
   source-checkout selection and explicit `--repo <target-root>` selection.

### Expected result

Scan and drift are read-only and deterministic. Report output contains only
the explicitly selected local commits; an accepted human decision is recorded
with its rationale/evidence before the port boundary can advance.
Both public `verify` paths execute repository-relative gates at the resolved
target Git top level. That same root supplies `$GITHUB_WORKSPACE` expansion
and workflow-pilot repository authority, and no gate has a separate working
directory. The source-tree module is launched from its source repository root;
it is not an installed nested/external entrypoint.

### Negative control

The tool rejects a wrong remote, stale/diverged ref, merge report request,
unsafe/tracked/outside/symlink output path, missing decision evidence, or
attempt to mutate upstream source. A non-root target cannot become gate
authority or execution cwd.

### Interactions and save compatibility

This tool never builds, checks out, applies, merges, or executes upstream
source. It has no runtime, generated-data, configuration, ROM, RAM, or save
compatibility impact.

### Automation

- the [authenticated upstream-port command](../publisher_authority_bootstrap.md#authenticated-upstream-port-consumers)
  — `tests/upstream_port/test_cli_readonly.py`.
- the [authenticated upstream verifier dry-run command](../publisher_authority_bootstrap.md#full-upstream-verifier-dry-run)
  — `scripts/upstream_port/verify.py`.

### Cleanup and limitations

Remove only the named ignored report directory if it was created. Choosing
whether an actual reviewed upstream commit belongs in this project is the
precise human decision; automation validates the recorded decision contract
but never infers acceptance.

## TC-CORE-009: Reproduce default release from a successful Build CI run

- **Feature / originating issue:** `build-ci-default-release-reproduction` /
  [#20](https://github.com/laqieer/fireemblem8-expansion/issues/20).
- **Supported configuration or artifact:** successful Build CI run URL and head
  SHA, plus a clean checkout at that exact SHA for a local modern AAPCS
  release reproduction.
- **Prerequisites and clean starting state:** record the successful Build CI
  run URL and head SHA; use a clean source checkout at that head with the
  modern toolchain installed.

### Actions

1. Run `gh run view <run-id> --json url,headSha,conclusion,workflowName`.
2. Confirm `workflowName` is `Build CI`, `conclusion` is `success`, and
   `headSha` equals `git rev-parse HEAD` in the clean local checkout.
3. Reproduce and verify the default release locally:

   ```sh
   make assets-clean
   make assets-generate
   make expansion-modern-rom MODERN_CONFIG=release MODERN_ABI=aapcs
   python3 scripts/modernize/verify_rom_header.py \
     build/expansion-modern/release/aapcs/fireemblem8.gba
   ```

### Expected result

The Build CI URL and head SHA identify the source that passed CI. The local
default AAPCS release rebuild passes the same header/checksum verification at
`build/expansion-modern/release/aapcs/fireemblem8.gba`.

### Negative control

A failed, stale, wrong-workflow, or wrong-SHA run is rejected before local
reproduction; a malformed local ROM fails header verification. This procedure
does not claim the default Build workflow publishes an artifact, and it is not
issue #49's maximal BPS artifact.

### Interactions and save compatibility

The locally reproduced default release introduces no new feature flags, save
migration, generated-data output, or archival-lane claim. Issue #49's
separate BPS artifact contract remains the only published artifact path.

### Automation

- the [authenticated workflow command](../publisher_authority_bootstrap.md#authenticated-workflow-consumers)
  — `.github/workflows/build.yml`.
- `make assets-clean && make assets-generate && make expansion-modern-rom MODERN_CONFIG=release MODERN_ABI=aapcs && python3 scripts/modernize/verify_rom_header.py build/expansion-modern/release/aapcs/fireemblem8.gba`
  — `scripts/modernize/verify_rom_header.py`.

### Cleanup and limitations

Use `make clean_fast` to remove locally reproduced build artifacts. Build CI
does not publish a default modern ROM/map artifact; the run URL/head SHA and
local source build are the supported reproducibility boundary.

## TC-CORE-010: Typed authoring lowers through existing routes

- **Feature / originating issue:** `typed-authoring-conveniences` /
  [#45](https://github.com/laqieer/fireemblem8-expansion/issues/45) and
  [#46](https://github.com/laqieer/fireemblem8-expansion/issues/46).
- **Supported configuration or artifact:** disposable source worktree with
  Python 3 and the default Chapter 2 generated-data bundle.
- **Prerequisites and clean starting state:** copy the intended JSON source to
  a disposable worktree; never edit build-local generated C directly.

### Actions

1. Add or modify a typed class/unit/event-list helper record in the disposable
   source and run `make generated-data-ch2-check`.
2. Run the class, unit, event-list, and chapter-bundle semantic/round-trip
   suites.
3. Introduce one unknown helper/reference or invalid range/arity in the
   disposable source, retain the diagnostic, then reset the source.

### Expected result

Valid records lower through the existing event macros and cross-table
references. The chapter bundle remains coherent, and the default Chapter 2
source with no helper scripts retains its unchanged generated behavior.

### Negative control

Unknown families, operations, symbols, argument counts, types, and ranges
fail with source location and breadcrumb context; no helper creates another
event router or silently substitutes a default.

### Interactions and save compatibility

Typed helpers compose with the existing eventscripts/eventlists/unit/class
schemas. The authoring convenience changes no save representation, migration,
compatibility epoch, optional gameplay profile, or archival contract.

### Automation

- `make generated-data-ch2-check`
  — `scripts/generated_data/tests/test_chapterbundle_schema.py`.
- `python3 -m unittest scripts.generated_data.tests.test_classes_schema scripts.generated_data.tests.test_units_schema scripts.generated_data.tests.test_eventlists_schema -v`
  — `scripts/generated_data/tests/test_eventlists_schema.py`.
- `python3 -m unittest scripts.generated_data.tests.test_classes_roundtrip scripts.generated_data.tests.test_units_roundtrip scripts.generated_data.tests.test_eventlists_roundtrip -v`
  — `scripts/generated_data/tests/test_eventlists_roundtrip.py`.

### Cleanup and limitations

Reset the disposable worktree edits and use `make clean_fast` only for build
artifacts. This is a typed lowering convenience, not a new event language,
campaign, class pack, or unit ruleset.
