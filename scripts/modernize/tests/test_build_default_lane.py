"""Issue #15 -- default build-lane targeted tests.

Confirms, with deterministic evidence (a `make -rR -p` database probe plus
`make -n` dry runs -- never a real, multi-minute compile/link/boot-check
inside the automated suite), that:

* A bare `make`/`make all` unconditionally resolves to the single
  supported modern GCC AAPCS *release* boot-check chain, and its dry-run
  plan never mentions a `tools/agbcc` executable or library.
* This is a *structural* guarantee, not an environment/command-line
  convention: `all:` takes no lane-selection variable of any kind, so
  neither ambient environment pollution (`FE8_DEFAULT_LANE=legacy` set in
  the calling shell before invoking `make`) nor a `make`
  command-line variable assignment (`make all FE8_DEFAULT_LANE=legacy`,
  highest-precedence origin) can redirect a bare/`all` build to the
  archival agbcc lane. These are *negative* regression tests: an earlier
  candidate implementation of this issue made `FE8_DEFAULT_LANE=legacy` an
  ambient-environment escape hatch for the default goal, which let a bare
  `make`/`make all` silently fall back to the unsupported agbcc lane --
  exactly the binding-decision violation this issue exists to close. The
  fix removed that variable/gate entirely; these tests prove it cannot be
  reintroduced (under this name or a `make`-noise equivalent) without
  failing here.
* The explicit, clearly-named archival alias `make legacy` still exists and
  reaches `fireemblem8.gba` directly. The obsolete source/object/ROM identity
  hash gate is gone, while the archival lane itself remains reachable *only*
  by naming it.
* The GNU Autoconf front end persists validated feature/profile choices in an
  ignored Make fragment and its generated GNUmakefile forwards those values to
  the committed Make backend without changing direct `make` defaults.
* `scripts/quickstart.sh --legacy` calls `make legacy` by name directly
  (never a bare `make -j<jobs>` plus a lane-selection variable of any
  kind) -- proven both by grepping the script for that exact invocation
  and by a regression guard that the retired `FE8_DEFAULT_LANE` glue is
  gone from the script entirely (a stale reference would mean quickstart
  and the Makefile disagree about how the archival lane is reached).

A single real, non-dry-run bare `make` (modern release, boot-verified) was
run manually as part of this issue's closure evidence -- see the
Agent/TRF closure handoff notes; it is intentionally not repeated here
because a full modern build/link/boot-check takes several minutes and this
suite must stay fast and deterministic.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = ROOT / "Makefile"
QUICKSTART = ROOT / "scripts" / "quickstart.sh"
CONFIGURE = ROOT / "configure"
GNUMAKEFILE_IN = ROOT / "GNUmakefile.in"
COPILOT_INSTRUCTIONS = ROOT / ".github" / "copilot-instructions.md"
CLAUDE_INSTRUCTIONS = ROOT / "CLAUDE.md"


def run_make(args, env_overrides=None):
    env = os.environ.copy()
    env.pop("MAKEFLAGS", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["make", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def parse_make_assignments(fragment):
    values = {}
    for line in fragment.splitlines():
        if ":=" not in line:
            continue
        name, value = line.split(":=", 1)
        name = name.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            values[name] = value.strip()
    return values


def parse_print_variables(output):
    values = {}
    pattern = re.compile(
        r"^([A-Z][A-Z0-9_]*) is a (?:simple|recursive) variable set to \[(.*)\]$"
    )
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def has_successful_workflow(runs, commit, branch):
    return any(
        run["commit"] == commit
        and run["branch"] == branch
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        for run in runs
    )


class DefaultGoalDatabaseProbeTests(unittest.TestCase):
    """Mirrors generated-data-link-check's own build-state-independent probe:
    a `-rR -p` database dump against a nonexistent target name never
    executes a recipe or recurses into a sub-make, and `-rR`
    (--no-builtin-rules --no-builtin-variables) keeps GNU Make's implicit
    suffix-rule search from matching the bogus probe name."""

    def probe(self, env_overrides=None):
        result = run_make(
            ["--no-print-directory", "-rR", "-p", "__issue15_default_goal_probe__"],
            env_overrides=env_overrides,
        )
        return result.stdout

    def test_default_goal_is_still_all(self):
        probe = self.probe()
        default_goal = next(
            (line for line in probe.splitlines() if line.startswith(".DEFAULT_GOAL ")),
            None,
        )
        self.assertEqual(default_goal, ".DEFAULT_GOAL := all")

    def test_all_rule_has_no_file_prerequisite_of_any_kind(self):
        # Issue #15: `all:` is a bare recipe target -- no fireemblem8.gba
        # (legacy $(ROM)) prerequisite, and no other file prerequisite --
        # that unconditionally recurses into the modern release boot-check
        # chain. There is no ifeq/variable-gated branch left in the
        # Makefile that could give `all:` a legacy prerequisite under any
        # condition; this is the load-bearing structural change this issue
        # makes. See the tested
        # `legacy -> fireemblem8.gba` chain below for the one place that
        # prerequisite still lives, reachable only by name.
        probe = self.probe()
        all_rule = next(
            (line for line in probe.splitlines() if line.startswith("all:")), None
        )
        self.assertIsNotNone(all_rule, probe[:400])
        self.assertEqual(all_rule.strip(), "all:")

    def test_all_rule_is_identical_under_ambient_and_command_line_fe8_default_lane_noise(self):
        # Negative regression coverage for the exact defect this issue
        # closes: an ambient `FE8_DEFAULT_LANE=legacy` in the calling
        # environment must not change `all:`'s prerequisite/shape at all,
        # because the Makefile no longer reads that (or any) variable to
        # decide `all:`'s recipe.
        baseline = self.probe()
        baseline_all_rule = next(
            (line for line in baseline.splitlines() if line.startswith("all:")), None
        )
        polluted = self.probe(env_overrides={"FE8_DEFAULT_LANE": "legacy"})
        polluted_all_rule = next(
            (line for line in polluted.splitlines() if line.startswith("all:")), None
        )
        self.assertEqual(baseline_all_rule, polluted_all_rule)
        self.assertEqual(polluted_all_rule.strip(), "all:")


class BareMakeDryRunTests(unittest.TestCase):
    def test_bare_make_dry_run_plans_modern_release_aapcs_boot_check(self):
        result = run_make(["-n"])
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn(
            "make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            result.stdout,
        )

    def test_bare_make_dry_run_never_mentions_agbcc(self):
        result = run_make(["-n"])
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertNotIn("agbcc", result.stdout)

    def test_missing_autotools_fragment_never_triggers_implicit_rule_search(self):
        result = run_make(["-n"])
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertNotIn("config.autotools.mk.s", result.stdout)
        self.assertNotIn("can't open config.autotools.mk", result.stdout)

    def test_make_all_dry_run_matches_bare_make(self):
        bare = run_make(["-n"])
        explicit = run_make(["all", "-n"])
        self.assertEqual(bare.returncode, 0, bare.stdout[-4000:])
        self.assertEqual(explicit.returncode, 0, explicit.stdout[-4000:])
        self.assertIn(
            "make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            explicit.stdout,
        )
        self.assertNotIn("agbcc", explicit.stdout)

    def test_bare_make_dry_run_is_deterministic_against_ambient_modern_config(self):
        # Issue #15 requires this to be deterministic: an ambient
        # MODERN_CONFIG/MODERN_ABI override must never leak into the
        # default lane's pinned release/aapcs invocation.
        result = run_make(["-n"], env_overrides={"MODERN_CONFIG": "debug", "MODERN_ABI": "apcs-gnu"})
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn(
            "make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            result.stdout,
        )

    def test_bare_make_dry_run_is_immune_to_ambient_fe8_default_lane_pollution(self):
        # Negative regression test: this is the exact defect flagged
        # against an earlier candidate fix for this issue --
        # `FE8_DEFAULT_LANE=legacy` set ambiently in the calling
        # environment (not passed as a `make` argument at all) must not
        # change what a bare `make`/`make -n` plans. The Makefile no
        # longer defines or reads this variable, so this must resolve
        # identically to a clean environment: still the modern release
        # AAPCS boot-check, still no agbcc reference anywhere.
        result = run_make(["-n"], env_overrides={"FE8_DEFAULT_LANE": "legacy"})
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn(
            "make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            result.stdout,
        )
        self.assertNotIn("agbcc", result.stdout)

    def test_make_all_dry_run_is_immune_to_fe8_default_lane_command_line_variable_noise(self):
        # Same defect, but as a `make` command-line variable assignment
        # (`make all FE8_DEFAULT_LANE=legacy`) -- GNU Make's
        # highest-precedence variable origin. Command-line variables win
        # over ambient environment *and* Makefile `=`/`:=` assignments (but
        # not `override`), so this is the strongest possible way to try to
        # smuggle a lane switch in; it must still have zero effect, because
        # nothing in the Makefile references this (or any) variable name to
        # select `all:`'s recipe.
        result = run_make(["all", "-n", "FE8_DEFAULT_LANE=legacy"])
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn(
            "make expansion-modern-boot-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            result.stdout,
        )
        self.assertNotIn("agbcc", result.stdout)


class LegacyLaneStillReachableTests(unittest.TestCase):
    """The archival lane is explicitly preserved (never deleted); these
    prove it stays structurally reachable -- but only by naming it."""

    def test_make_legacy_target_builds_rom_without_identity_hash_gate(self):
        # Deliberately a `-p` database probe, never a `-n`/real build of
        # `legacy`/`fireemblem8.gba`: both targets' prerequisite chain
        # reaches mgfembp/mgfembp.bin, whose own recipe invokes $(MAKE) --
        # a recipe form GNU Make always actually *executes* (compiling
        # mgfembp's real tools), never merely prints, even under -n. A `-p`
        # probe against an unrelated, nonexistent target dumps the parsed
        # rule database without evaluating or running any real target's
        # recipe, so this stays a pure, side-effect-free static check of
        # the complete intentional chain:
        #
        #   legacy -> fireemblem8.gba
        result = run_make(
            ["--no-print-directory", "-rR", "-p", "__issue15_legacy_alias_probe__"]
        )
        self.assertNotEqual(result.returncode, 0)
        legacy_rule = next(
            (line for line in result.stdout.splitlines() if line.startswith("legacy:")),
            None,
        )
        self.assertIsNotNone(legacy_rule, result.stdout[:400])
        self.assertEqual(legacy_rule.strip(), "legacy: fireemblem8.gba")
        legacy_prerequisites = legacy_rule.split(":", 1)[1].split()
        self.assertEqual(legacy_prerequisites, ["fireemblem8.gba"])
        self.assertNotIn("legacy-identity-check", legacy_prerequisites)

    def test_fe8_default_lane_env_var_no_longer_routes_bare_make_to_agbcc(self):
        # Negative regression test (inverts the pre-fix assumption): an
        # earlier candidate implementation made
        # `FE8_DEFAULT_LANE=legacy` route a bare `all:` to the archival
        # $(ROM) prerequisite. That gate has been removed outright, so
        # this ambient variable must now be fully inert -- `all:`'s
        # prerequisite must stay empty (no `fireemblem8.gba` reference at
        # all) even with it set, and the archival lane must only be
        # reachable by naming `legacy`/`fireemblem8.gba` directly.
        #
        # Deliberately a `-p` *database* probe against a nonexistent target
        # (never a `-n` dry run of `all`/`legacy` themselves): those targets'
        # own prerequisite chain reaches mgfembp/mgfembp.bin, whose recipe
        # invokes $(MAKE) -- and GNU Make always actually *executes* (not
        # just prints) any recipe line referencing $(MAKE), even under -n.
        # A `-p` probe against an unrelated, nonexistent target dumps the
        # parsed rule database without evaluating or running any real
        # target's recipe at all, so this stays a pure, side-effect-free
        # static check of which prerequisite `all:` resolves to.
        result = run_make(
            ["--no-print-directory", "-rR", "-p", "__issue15_legacy_lane_probe__"],
            env_overrides={"FE8_DEFAULT_LANE": "legacy"},
        )
        all_rule = next(
            (line for line in result.stdout.splitlines() if line.startswith("all:")),
            None,
        )
        self.assertIsNotNone(all_rule, result.stdout[:400])
        self.assertEqual(all_rule.strip(), "all:")
        self.assertNotIn("fireemblem8.gba", all_rule)


class AutotoolsConfigureTests(unittest.TestCase):
    @staticmethod
    def run_configure(build_dir, *args):
        return subprocess.run(
            [str(CONFIGURE), *args],
            cwd=build_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_configure_help_lists_public_feature_and_profile_options(self):
        result = subprocess.run(
            [str(CONFIGURE), "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        for option in (
            "--enable-mechanics-hooks",
            "--enable-mechanics-sample",
            "--enable-danger-overlay-menu",
            "--enable-starter-content",
            "--enable-localized-text-auto-wrap",
            "--enable-pseudo-locale",
            "--with-enabled-locales=LIST",
            "--with-default-locale=ID",
            "--with-rom-size=16M|32M",
            "--with-item-id-cap=VALUE",
        ):
            self.assertIn(option, result.stdout)

    def test_configure_full_starter_profile_reaches_make_backend(self):
        with tempfile.TemporaryDirectory() as build_dir:
            result = self.run_configure(
                build_dir,
                "--enable-mechanics-hooks",
                "--enable-mechanics-sample",
                "--enable-danger-overlay-menu",
                "--enable-starter-content",
                "--with-item-id-cap=0xCE",
            )
            self.assertEqual(result.returncode, 0, result.stdout[-4000:])

            fragment = parse_make_assignments(
                (Path(build_dir) / "config.autotools.mk").read_text(encoding="utf-8")
            )
            self.assertEqual(fragment["EXPANSION_MECHANICS_HOOKS"], "1")
            self.assertEqual(fragment["EXPANSION_MECHANICS_SAMPLE"], "1")
            self.assertEqual(fragment["EXPANSION_DANGER_OVERLAY_MENU"], "1")
            self.assertEqual(fragment["EXPANSION_STARTER_CONTENT"], "1")
            self.assertEqual(fragment["FE8_ITEM_ID_CAP"], "0xCE")
            self.assertTrue((Path(build_dir) / "GNUmakefile").is_file())

            make_result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "print-EXPANSION_MECHANICS_HOOKS",
                    "print-EXPANSION_STARTER_CONTENT",
                    "print-FE8_ITEM_ID_CAP",
                    "print-GENERATED_DATA_ITEM_CAP",
                ],
                cwd=build_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(make_result.returncode, 0, make_result.stdout[-4000:])
            values = parse_print_variables(make_result.stdout)
            self.assertEqual(values["EXPANSION_MECHANICS_HOOKS"], "1")
            self.assertEqual(values["EXPANSION_STARTER_CONTENT"], "1")
            self.assertEqual(values["FE8_ITEM_ID_CAP"], "0xCE")
            self.assertEqual(values["GENERATED_DATA_ITEM_CAP"], "0xCE")

    def test_recursive_wrapper_options_precede_makeoverrides(self):
        wrapper = GNUMAKEFILE_IN.read_text(encoding="utf-8")
        recipe = next(
            line for line in wrapper.splitlines()
            if line.startswith("\t+$(MAKE)")
        )
        separator = recipe.index("$(MAKEOVERRIDES)")
        self.assertLess(recipe.index("--no-print-directory"), separator)
        self.assertLess(recipe.index("-C "), separator)
        self.assertLess(recipe.index("-f Makefile"), separator)

    def test_configured_wrapper_recurses_with_command_line_overrides(self):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as build_dir:
            result = self.run_configure(build_dir, "--disable-custom-spell-effects")
            self.assertEqual(result.returncode, 0, result.stdout[-4000:])
            fragment = parse_make_assignments(
                (Path(build_dir) / "config.autotools.mk").read_text(encoding="utf-8")
            )
            self.assertEqual(fragment["EXPANSION_CUSTOM_SPELL_EFFECTS"], "0")

            make_result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-C",
                    build_dir,
                    "print-EXPANSION_CUSTOM_SPELL_EFFECTS",
                    "print-MODERN_CONFIG",
                    "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                    "MODERN_CONFIG=release",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(make_result.returncode, 0, make_result.stdout[-4000:])
            values = parse_print_variables(make_result.stdout)
            self.assertEqual(values["EXPANSION_CUSTOM_SPELL_EFFECTS"], "1")
            self.assertEqual(values["MODERN_CONFIG"], "release")

    def test_configure_rejects_invalid_feature_dependency(self):
        with tempfile.TemporaryDirectory() as build_dir:
            result = self.run_configure(build_dir, "--enable-mechanics-sample")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "EXPANSION_MECHANICS_SAMPLE=1 requires EXPANSION_MECHANICS_HOOKS=1",
            result.stdout,
        )

    def test_configure_without_options_preserves_committed_defaults(self):
        with tempfile.TemporaryDirectory() as build_dir:
            result = self.run_configure(build_dir)
            self.assertEqual(result.returncode, 0, result.stdout[-4000:])
            fragment = (Path(build_dir) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )

            assignments = [
                line
                for line in fragment.splitlines()
                if re.match(r"^[A-Z][A-Z0-9_]*\s*:=", line)
            ]
            self.assertEqual(assignments, [])


class RemoteCompletionGateTests(unittest.TestCase):
    def test_makefile_gate_checks_clean_pushed_exact_sha_ci_and_open_issues(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("remote-completion-check:", text)
        self.assertIn("all-issues-completion-check: remote-completion-check", text)
        self.assertIn("git status --porcelain", text)
        self.assertIn("git branch --show-current", text)
        self.assertIn('if [ -z "$$branch" ]; then', text)
        self.assertIn("HEAD is detached", text)
        self.assertIn('remote completion requires master, not', text)
        self.assertIn("git rev-parse '@{u}'", text)
        self.assertIn(
            "--event push --branch master --commit \"$$head_sha\" --workflow build.yml",
            text,
        )
        self.assertIn("gh issue list", text)
        self.assertIn("--state open", text)

    def test_same_sha_feature_build_cannot_replace_missing_master_build(self):
        exact_master_sha = "a" * 40
        runs = [
            {
                "commit": exact_master_sha,
                "branch": "feature/build",
                "status": "completed",
                "conclusion": "success",
            }
        ]

        self.assertFalse(has_successful_workflow(runs, exact_master_sha, "master"))
        self.assertTrue(
            has_successful_workflow(
                runs,
                exact_master_sha,
                "feature/build",
            )
        )

    def test_same_sha_pr_build_cannot_replace_missing_master_build(self):
        exact_master_sha = "b" * 40
        runs = [
            {
                "commit": exact_master_sha,
                "branch": "agent/issue-93-review",
                "status": "completed",
                "conclusion": "success",
            }
        ]

        self.assertFalse(has_successful_workflow(runs, exact_master_sha, "master"))
        self.assertTrue(
            has_successful_workflow(
                runs,
                exact_master_sha,
                "agent/issue-93-review",
            )
        )

    def test_agent_instructions_make_remote_gate_mandatory(self):
        for path in (COPILOT_INSTRUCTIONS, CLAUDE_INSTRUCTIONS):
            text = path.read_text(encoding="utf-8")
            self.assertIn("make remote-completion-check", text)
            self.assertIn("make all-issues-completion-check", text)
            self.assertIn("commit", text.lower())
            self.assertIn("push", text.lower())
            self.assertIn("Build CI", text)


class QuickstartLegacyGlueRegressionGuardTests(unittest.TestCase):
    """test_quickstart.py's mocked `make` never actually executes GNU Make,
    so it can only tell apart different literal argument strings logged
    for `make`. These tests guard the actual glue text/mechanism directly:
    quickstart's --legacy build must call the `legacy` target by name, and
    must not resurrect the retired `FE8_DEFAULT_LANE` environment-variable
    indirection this issue removed."""

    def test_quickstart_legacy_branch_calls_make_legacy_target_by_name(self):
        script = QUICKSTART.read_text(encoding="utf-8")
        fn_match = re.search(
            r"^build_project\(\) \{(.*?)^\}", script, re.DOTALL | re.MULTILINE
        )
        self.assertIsNotNone(fn_match, "could not find build_project()")
        body = fn_match.group(1)
        branch_match = re.search(
            r"if \(\( LEGACY_MODE == 1 \)\); then(.*?)\n\s*else\n", body, re.DOTALL
        )
        self.assertIsNotNone(branch_match, "could not find the --legacy build_project branch")
        branch = branch_match.group(1)
        self.assertRegex(branch, r"(?<!\S)make\s+legacy\s+-j")
        self.assertNotIn("FE8_DEFAULT_LANE", branch)

    def test_quickstart_never_mentions_retired_fe8_default_lane_variable(self):
        script = QUICKSTART.read_text(encoding="utf-8")
        self.assertNotIn("FE8_DEFAULT_LANE", script)


if __name__ == "__main__":
    unittest.main()
