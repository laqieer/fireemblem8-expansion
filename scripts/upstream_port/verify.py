"""Orchestrate existing repository gates against the CURRENT TRUSTED WORKTREE
after a maintainer has manually applied a port batch.

WARNING (see docs/upstream-porting.md): this command builds and checks the
repository's *own* current working tree/commit. It never builds, checks out,
or executes the canonical upstream ref/tree. It is a thin, literal mirror of
the four combined workers in `.github/workflows/build.yml` (kept independent
from that file: this module doesn't parse/execute the workflow, it re-states
the same gate commands so `verify` stays runnable locally without a CI
runner). Only the master-only publisher and serial summary jobs have no local
gate equivalent. The one DELIBERATE command-level exception is build.yml's
"Check documentation (issues #7/#17)" step, which remains a required
standalone workflow gate outside this mirror. Run that standalone command pair
directly to reproduce it locally; see docs/upstream-porting.md.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List

# A leading NAME=VALUE token in a gate command is an inline environment
# assignment (POSIX shell semantics), mirrored verbatim from build.yml so
# the gate list stays an argv-identical copy of the workflow. It is applied
# to the child environment, never exec-ed as a program.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_env_prefix(command):
    """Split any leading ``NAME=VALUE`` env-assignment tokens off the front of
    a gate command, returning ``(env_overrides, argv)``. Only a *leading*
    run is treated as env (matching the shell), so a NAME=VALUE that appears
    after the program (e.g. make variable overrides like ``MODERN_CONFIG=debug``)
    stays part of argv."""
    env_overrides = {}
    argv = list(command)
    while argv and _ENV_ASSIGN_RE.match(argv[0]):
        name, _, value = argv[0].partition("=")
        env_overrides[name] = value
        argv = argv[1:]
    return env_overrides, argv


@dataclass
class Gate:
    name: str
    command: List[str]
    applicable_note: str


def gates(jobs: int = 2) -> List[Gate]:
    """Return the ordered gate list, mirroring build.yml's CI steps.

    Kept as data (not hardcoded shell text) so tests can assert on the exact
    command list without actually executing a multi-minute native build.
    """
    return [
        Gate(
            name="gba-playtest-host-suite",
            command=[
                "GBA_PLAYTEST_HOST_ONLY=1",
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tools/gba-playtest/tests",
                "-v",
            ],
            applicable_note=(
                "issue #13 host lane (build.yml `host-tests` job, textually "
                "first): every tools/gba-playtest host test -- scenario/schema "
                "parsing, generators, config, save/migration fixtures, "
                "timeouts, retry policy, deterministic sorted-JSON output, "
                "provenance/diagnostics. Host-only (build-essential + "
                "libmgba-dev, no arm-none-eabi toolchain); never builds/links "
                "the ROM, so it does not overlap the modern-linker gates below. "
                "GBA_PLAYTEST_HOST_ONLY=1 (mirrored verbatim from build.yml, "
                "and applied to THIS child process only) makes that host-only "
                "scope explicit: the ROM-dependent live-integration tests skip "
                "by mode instead of by whether a git-ignored build artifact "
                "happens to exist, so this gate cannot be perturbed by the "
                "modern-linker/item-expansion gates below rewriting those "
                "artifacts. Live coverage stays with those ROM gates"
            ),
        ),
        Gate(
            name="upstream-port-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/upstream_port",
                "-v",
            ],
            applicable_note=(
                "issue #12/#15 host lane (same `host-tests` job): pure-stdlib "
                "upstream-port review tooling tests; re-run this suite for "
                "the current count (classify/scan/drift/state/ref-binding/"
                "output-safety/merge-commit determinism and this "
                "verify.gates() <-> build.yml mirror, which excludes only "
                "the standalone documentation-governance step). Python/stdlib "
                "only, links no C and never rebuilds the ROM"
            ),
        ),
        Gate(
            name="workflow-contract-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/workflows",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note=(
                "fast host lane (same `host-tests` job): stdlib-only static "
                "contracts for the consolidated Build CI job graph. No "
                "compiler, ROM, linker, network, or subordinate runtime gate "
                "is invoked"
            ),
        ),
        Gate(
            name="localization-host-suite",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/localization/tests",
                "-p",
                "test_*.py",
            ],
            applicable_note=(
                "issue #18 host lane addition (same `host-tests` job, "
                "textually after workflow-contract-tests): the "
                "scripts/localization package's own pure-stdlib unit test "
                "suite (schema/pseudo/catalog/generate/CLI/determinism plus "
                "the host-native resolver-behavior and vanilla-isolation "
                "source-audit tests, which self-skip without a host `cc`). "
                "Python/stdlib only; never builds/links the ROM, so it does "
                "not overlap the localization-runtime-*-check scenarios "
                "reached through the modern-linker gates below"
            ),
        ),
        Gate(
            name="game-localization-width-contract",
            command=["make", "game-localization-test"],
            applicable_note=(
                "issue #18 full-game host lane addition: validates the 3,414 "
                "entry JA/ZH catalog, typed UI/scene width coverage, "
                "metrics-aware generated line breaks, and native text "
                "consumer behavior before the target-ROM gates"
            ),
        ),
        Gate(
            name="game-localization-catalog-check",
            command=["python3", "-m", "scripts.localization.game_locales", "check"],
            applicable_note="Build host lane closure check for the committed full-game locale catalog",
        ),
        Gate(
            name="game-localization-crosswalk-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-crosswalk",
            ],
            applicable_note="Build host lane closure check for full-game source/catalog crosswalk coverage",
        ),
        Gate(
            name="game-localization-raw-closure-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-raw-closure",
            ],
            applicable_note="Build host lane closure check for unresolved raw full-game locale content",
        ),
        Gate(
            name="artifact-guard-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/artifact_guard_tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="Build ROM lane host tests for immutable candidate artifact hygiene",
        ),
        Gate(
            name="artifact-guard",
            command=["python3", "scripts/artifact_guard.py", "--revision", "HEAD"],
            applicable_note="always applicable: rejects prohibited tracked build artifacts",
        ),
        # build.yml's "Check documentation (issues #7/#17)" step
        # (scripts/docs_check_tests followed by scripts/check_docs.py --check
        # --check-examples) intentionally has no Gate(...) entry here. It is
        # independently required immediately after the artifact guard.
        Gate(
            name="default-lane-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_build_default_lane.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts a bare `make`/`make all` always "
                "resolves to the modern release AAPCS lane"
            ),
        ),
        Gate(
            name="quickstart-legacy-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_quickstart.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts quickstart.sh only reaches the "
                "archival agbcc lane via explicit `make legacy`/`make "
                "fireemblem8.gba`, never via env/CLI variable overrides"
            ),
        ),
        Gate(
            name="generated-data-test",
            command=["make", "generated-data-test"],
            applicable_note="applicable when generated-data schema and cross-reference tests exist",
        ),
        Gate(
            name="generated-data-check",
            command=["make", "generated-data-check"],
            applicable_note="applicable when generated_data.mk-tracked tables exist",
        ),
        Gate(
            name="modern-linker-check-debug",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "aggregates the full modern DEBUG ROM/ELF runtime + linker "
                "suite off a single reused object/ELF build -- the runtime "
                "scenarios are covered here and are NOT re-run individually by "
                "verify, so no gate triggers a second/redundant ROM build. "
                "expansion-modern-linker-check depends on -budget-check, "
                "-overlay-audit (-> -relocs), -boot-check, -title-check, "
                "-debugtools-check/-timer-check/-map-check/-tools-check, "
                "-debugtools-prep-check, -debugtools-ch4prep-check, "
                "-newgame-check, -combat-check, -saveload-check (incl. the "
                "suspend/resume save scenario), -savefmt-check (save-format "
                "migration) and -shifted-check, then runs the shift/offset "
                "address scan and the raw-pointer cast audit. Net coverage: "
                "boot, title, new-game, map, prep, combat, save-load, "
                "suspend/resume, debugtools-tools, save migration, budget, "
                "shift/offset, raw-pointer, relocation and cross-overlay"
            ),
        ),
        Gate(
            name="modern-linker-check-release",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the debug gate above: the same "
                "aggregated runtime + linker suite off the reused RELEASE "
                "object/ELF build, additionally exercising the release "
                "debugtools-disabled negative scenarios. Runtime scenarios are "
                "covered here, not re-run individually by verify"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-debug",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "issue #10 acceptance (build.yml ROM `build` job, after the two "
                "default-cap modern-linker gates above -- never the host lane): "
                "boots the real modern debug ROM at an expanded item cap (0xCE, "
                "FE8_EXPANSION_ITEMTEST=1) and runs the item-ID-expansion runtime "
                "probe (expansion-modern-itemexpansion-check). The same single "
                "ROM build also carries the issue #6 bundled-content profile "
                "(EXPANSION_STARTER_CONTENT=1 + hooks + sample), so the authored "
                "content record and its public-registry mechanic are asserted by "
                "this same probe run -- no extra gate and no extra ROM build"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-release",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the item-expansion debug gate above, "
                "and the final step of build.yml ROM `build` job"
            ),
        ),
        Gate(
            name="modern-all-locales-all-features-profile",
            command=["make", "expansion-modern-all-locales-all-features-check", "-j1"],
            applicable_note=(
                "issue #49 trusted-patch preflight: builds and validates the "
                "isolated release/AAPCS 32 MiB all-production-locales and "
                "maximal-supported-features profile without reading a base "
                "image, creating a patch, or publishing an artifact"
            ),
        ),
        Gate(
            name="cjk-font-gates",
            command=["make", "-f", "cjk_fonts.mk", "cjk-fonts-check", "cjk-fonts-test"],
            applicable_note="combined-gate unique CJK font inventory and codec coverage",
        ),
        Gate(
            name="multilang-codec-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/texttools/tests",
                "-p",
                "test_multilang_codec*.py",
                "-v",
            ],
            applicable_note="combined-gate unique multilang texttools codec coverage",
        ),
        Gate(
            name="expansion-config-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_expansion_config.py",
                "-v",
            ],
            applicable_note="combined-gate unique expansion configuration coverage",
        ),
        Gate(
            name="linker-budget-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/linker_report/tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="combined-gate unique linker-budget coverage",
        ),
        Gate(
            name="legacy-build",
            command=["make", "legacy", "-j2"],
            applicable_note="combined-gate archival no-baserom build",
        ),
        Gate(
            name="legacy-payload-identity",
            command=["make", "-C", "mgfembp", "compare"],
            applicable_note="combined-gate archival payload identity comparison",
        ),
    ]


@dataclass
class GateResult:
    gate: Gate
    ran: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.ran and self.returncode == 0


def run_gates(cwd: str, jobs: int = 2, dry_run: bool = False) -> List[GateResult]:
    """Execute (or, if dry_run, just describe) every gate, in the fixed
    order returned by `gates()`.

    Stops at the first failing gate (fail-fast, matching CI). Never
    weakens, reorders, or skips a gate. There is intentionally no gate
    *selection* capability here (no `selected`/subset parameter): closure
    evidence for this tool is only ever the full, ordered gate set --
    partial/unknown/zero-gate "success" is a forged closure signal, not a
    real one. (See docs/upstream-porting.md and cli.py -- the public
    `verify` subcommand has no `--gate` flag for the same reason; this
    function has no internal escape hatch a caller could use to bypass
    that either.)
    """
    results: List[GateResult] = []
    for gate in gates(jobs=jobs):
        if dry_run:
            results.append(GateResult(gate=gate, ran=False, returncode=0, stdout="", stderr=""))
            continue
        env_overrides, argv = _split_env_prefix(gate.command)
        child_env = None
        if env_overrides:
            child_env = dict(os.environ)
            child_env.update(env_overrides)
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = GateResult(
            gate=gate,
            ran=True,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
        results.append(result)
        if not result.passed:
            break
    return results
