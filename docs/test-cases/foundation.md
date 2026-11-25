# Catalog foundation cases

## TC-CATALOG-001: Tester-case catalog foundation

- **Feature / originating issue:** `tester-case-catalog` /
  [issue #54](https://github.com/laqieer/fireemblem8-expansion/issues/54).
- **Supported configuration or artifact:** clean source checkout with Python
  3; no ROM, emulator, artifact, optional feature profile, or save state.
- **Prerequisites and clean starting state:** run from the repository root
  with `docs/test-cases/` and `scripts/check_docs.py` present.

### Actions

1. Read the [catalog index](README.md), [case template](template.md), and
   [`registry.json`](registry.json).
2. Run `python3 -m unittest scripts.docs_check_tests.test_check_docs -v`.
3. Run `python3 scripts/check_docs.py --check`.

### Expected result

The registry exposes distinct feature and case records, this procedure is
addressable through its stable ID, and both commands accept the valid complete
catalog.

### Negative control

The focused fixtures reject malformed or duplicate IDs, unknown feature/case
ownership, missing document/anchor/reference links, empty or success-shaped
required fields, unmapped automation/manual-only evidence, an
excluded/retired feature without a reason, and a missing expected feature in
future `complete` mode.

### Interactions and save compatibility

This depends on issue #53's tester-facing-case contract. The #55 through #58
feature-family entries are present and complete coverage is enforced. It has
no runtime feature interaction and changes no save bytes, migration, or
compatibility epoch.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_check_docs -v` exercises
the schema's positive and negative fixtures. `python3 scripts/check_docs.py
--check` validates the repository registry with the existing documentation
checker. No manual-only criterion applies.

### Cleanup and limitations

No cleanup is required. This case validates the generic catalog/checker seam
and its fail-closed all-current-feature coverage assertion.

## TC-TEST-QUALITY-001: Meaningful test-evidence policy rejects semantic mutations

- **Feature / originating issue:** `meaningful-test-evidence-policy` /
  [issue #100](https://github.com/laqieer/fireemblem8-expansion/issues/100).
- **Reference docs:** the canonical policy lives in the
  [development workflow](../../.github/skills/development-workflow/SKILL.md#meaningful-test-evidence)
  and is mirrored by the
  [project instructions](../../.github/copilot-instructions.md#meaningful-test-evidence).
  This procedure links to those references rather than duplicating their
  policy prose.
- **Supported configuration or artifact:** clean source checkout with Python
  3; no ROM, emulator, artifact, optional feature profile, or save state.
- **Prerequisites and clean starting state:** start in the repository root
  with the referenced policy files, `docs/test-cases/`, and
  `scripts/docs_check_tests/` present.

### Actions

1. Read the referenced policy surfaces and this case's
   [`registry.json`](registry.json) record.
2. Run `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
3. Run `python3 -m unittest scripts.docs_check_tests.test_check_docs -v`.
4. Run `python3 scripts/check_docs.py --check --check-examples`.
5. For [issue #481](https://github.com/laqieer/fireemblem8-expansion/issues/481),
   run the following host-side workflow regression cases:

   ```sh
   python3 -m unittest \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_worker_blocks_can_precede_other_job_fields \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_blank_lines_cannot_hide_step_fields \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_literal_run_scripts_preserve_blank_lines \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_blank_lines_cannot_hide_run_commands \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_every_combined_worker_has_a_closed_execution_context \
     tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_execution_context_key_syntax_bypasses_fail_closed \
     tests.upstream_port.test_verify.VerifyCliCwdTests.test_target_execution_context_is_closed_before_dry_run \
     tests.upstream_port.test_verify.VerifyCliCwdTests.test_worker_mapping_order_preserves_target_gate_equivalence
   ```

### Expected result

The three policy surfaces parse to the same canonical AST. Explicit
whitespace, line-wrap, `behavior`/`behaviour`, ordering, and level-1
terminator variations remain valid; the commands report no findings.
For each Build worker, moving `env` or `steps` as a whole, or swapping the
runner and timeout fields, retains the same workflow contract and gate
equivalence. The step sequence itself remains ordered. Blank lines in literal
`run` blocks retain the full script and command sequence, including when a
blank line has less indentation than the surrounding shell commands.

### Negative control

The focused policy regression rejects each prohibited-category removal,
permission and polarity inversion, contradictory rationale detail, unexpected
paragraph, duplicate or lower-level policy heading, Markdown markup, and
invalid syntax. These mutations preserve enough surrounding text to prove the
semantic parser rather than a raw phrase search is enforcing the contract.
Before the #481 fix, the worker-order regressions reject equivalent mappings.
Changed runners or timeouts, duplicate keys, additional execution fields,
unsupported key syntax and altered permissions remain rejected after the fix.
Blank lines with varying whitespace must not hide an advisory step field or
turn the full-game localization gate into a success.
Adding `exit 73` after a shallow blank in the real full-game width script must
lose exact full-game evidence and change the upstream-port workflow contract.
Before the script-extraction fix, both parsers silently omit that command.

### Interactions and save compatibility

This is issue #100's host-side governance foundation and depends on issue
#54's catalog seam. It has no gameplay, optional-profile, localization,
generated-data, or save interaction; it changes no save bytes, migration, or
compatibility epoch. It does not perform issue #101's duplicate-policy
cleanup.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill
-v` executes the positive and mutation cases. `python3 -m unittest
scripts.docs_check_tests.test_check_docs -v` validates the registry and anchor.
`python3 scripts/check_docs.py --check --check-examples` validates the
repository documentation and named examples. No manual-only criterion applies.
The #481 commands exercise the topology validator and upstream-port verifier
against in-memory permutations and temporary target checkouts.

### Cleanup and limitations

No cleanup is required. This case covers the policy documentation and
host-side semantic mutations only; it does not prove target-ROM behavior or
complete current-feature catalog coverage.
