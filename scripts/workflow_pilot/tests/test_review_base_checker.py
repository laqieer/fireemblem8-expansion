import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import unittest
from itertools import count
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.workflow_pilot import (
    reporter,
    review_assertions,
    review_base_checker,
    review_family,
    trusted_review_gate,
)


ROOT = Path(__file__).resolve().parents[3]
ASSERTION_PROGRAM = "scripts/workflow_pilot/review_assertions.py"
ASSERTION_INPUTS = review_base_checker.ASSERTION_INPUT_PATHS
ISSUE_179_URL = "https://github.com/laqieer/fireemblem8-expansion/issues/179"
REPOSITORY = "laqieer/fireemblem8-expansion"
PULL_REQUEST = 189
COPILOT_ACTOR_ID = review_family.COPILOT_GRAPHQL_NODE_ID

def git_text(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

def git_bytes(root, *arguments):
    return subprocess.run(
        reporter.git_command(root, *arguments),
        env=reporter.git_environment(offline=True),
        check=True,
        capture_output=True,
    ).stdout

def optional_file_bytes(path):
    return path.read_bytes() if path.is_file() else None

def changed_files(changes):
    return sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )

def utc_text(value):
    return value.replace("+00:00", "Z")

def copilot_graphql_actor():
    return {
        "__typename": review_family.COPILOT_GRAPHQL_TYPE,
        "id": COPILOT_ACTOR_ID,
        "login": review_family.COPILOT_GRAPHQL_LOGIN,
    }

def viewer_graphql_actor():
    return {"__typename": "User", "id": "VIEWER", "login": "viewer"}

def authoritative_family_comment(
    *,
    base_sha,
    original_head,
    review_id,
    candidate_sha,
    mappings,
    created_at,
):
    payload = {
        "repository_id": "REPO_TEST_189",
        "repository": REPOSITORY,
        "pull_request": PULL_REQUEST,
        "base_sha": base_sha,
        "original_pre_review_head": original_head,
        "review_id": review_id,
        "candidate_sha": candidate_sha,
        "findings": copy.deepcopy(mappings),
    }
    return {
        "id": f"CLASSIFICATION:{review_id}",
        "createdAt": created_at,
        "updatedAt": created_at,
        "body": trusted_review_gate.EXTERNAL_CLASSIFICATION_COMMENT_PREFIX
        + reporter.normalized_json(payload).decode("ascii").rstrip("\n"),
        "author": viewer_graphql_actor(),
    }

def synthetic_decision_record_snapshot():
    decisions = reporter.load_json(ROOT / ".github" / "workflow-pilot-decisions.json")
    decisions["pull_requests"] = [
        entry
        for entry in decisions["pull_requests"]
        if entry["pull_request"] != PULL_REQUEST
    ]
    decisions["pull_requests"].append(
        {
            "pull_request": PULL_REQUEST,
            "risk_boundaries": ["lifecycle", "protocol"],
            "threshold": {
                "triggers": ["changed-files", "risk-boundary"],
                "override_history": [],
            },
            "gate_mode": "concurrent",
            "stack": {
                "depth": 0,
                "parent_pr": None,
                "exception_reason": None,
            },
            "pilot": {
                "included": False,
                "disposition": "baseline-only",
            },
        }
    )
    return reporter.normalized_json(decisions)


class ReviewBaseCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifacts = ROOT / "build" / "test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        cls.root = artifacts / f"review-base-checker-{os.getpid()}"
        cls.repo = cls.root / "repo"
        cls.root.mkdir(parents=True)
        cls.repo.mkdir()
        cls.case_ids = count()
        cls.input_snapshots = {
            relative: optional_file_bytes(ROOT / relative) for relative in ASSERTION_INPUTS
        }
        cls.input_snapshots[".github/workflow-pilot-decisions.json"] = (
            synthetic_decision_record_snapshot()
        )
        subprocess.run(
            reporter.git_command(cls.repo, "init", "-q"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(
                cls.repo, "config", "user.email", "test@example.com"
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "config", "user.name", "Checker Test"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        (cls.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        cls._restore_baseline()
        cls._set_generated_owners_health(False)
        cls.base = cls._commit("base")
        cls.base_tree = git_text(cls.repo, "rev-parse", f"{cls.base}^{{tree}}")
        cls._restore_baseline()
        cls._set_action_items_health(False)
        cls.head1 = cls._commit("head-1")
        cls.head1_tree = git_text(cls.repo, "rev-parse", f"{cls.head1}^{{tree}}")
        cls._restore_baseline()
        cls.head2 = cls._commit("head-2")
        cls.head2_tree = git_text(cls.repo, "rev-parse", f"{cls.head2}^{{tree}}")
        cls.program_blob_oid = git_text(
            cls.repo, "rev-parse", f"{cls.base}:{ASSERTION_PROGRAM}"
        )
        cls.contract_template = reporter.load_json(
            ROOT / "scripts" / "workflow_pilot" / "tests" / "fixtures" / "review_family_complete.json"
        )
        cls.original_changes = review_family.derive_change_records(
            cls.repo, cls.base, cls.head1
        )
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)
    @classmethod
    def _write_relative(cls, relative, data):
        target = cls.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8")
    @classmethod
    def _restore_baseline(cls):
        for relative, payload in cls.input_snapshots.items():
            target = cls.repo / relative
            if payload is None:
                if target.exists():
                    target.unlink()
                continue
            cls._write_relative(relative, payload)
        obsolete = cls.repo / "scripts/workflow_pilot/assertion_subjects"
        if obsolete.exists():
            shutil.rmtree(obsolete)
    @classmethod
    def _replace_once(cls, relative, old, new):
        path = cls.repo / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"missing pattern in {relative}: {old}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    @classmethod
    def _commit(cls, message):
        subprocess.run(
            reporter.git_command(cls.repo, "add", "-A"),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(cls.repo, "commit", "-q", "-m", message),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        return git_text(cls.repo, "rev-parse", "HEAD")
    @classmethod
    def _set_action_actions_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            'ACTION_SEQUENCE = ("read-candidate", "emit-local-report")',
            'ACTION_SEQUENCE = ("emit-local-report", "read-candidate")',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_base_checker.py",
                'ACTION_SEQUENCE = ("emit-local-report", "read-candidate")',
                'ACTION_SEQUENCE = ("read-candidate", "emit-local-report")',
            )
    @classmethod
    def _set_action_items_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            '"finding_member": parsed["member"],',
            '"finding_member": parsed["family"],',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_base_checker.py",
                '"finding_member": parsed["family"],',
                '"finding_member": parsed["member"],',
            )
    @classmethod
    def _make_replace_head(cls, message, relative, old, new):
        cls._restore_baseline()
        cls._replace_once(relative, old, new)
        head = cls._commit(message)
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_action_sequence_head(cls, kind):
        old = "    if actions != list(ACTION_SEQUENCE):"
        message, old, new = {
            "enforcement": (
                "action-enforcement-bypass",
                old,
                "    if False and actions != list(ACTION_SEQUENCE):",
            ),
            "import-name": (
                "action-import-name-bypass",
                old,
                '    if __name__.startswith("review_assertions_") and actions != list(ACTION_SEQUENCE):',
            ),
            "repository": (
                "action-repository-whitelist",
                "    return validate_review_actions(actions)\n",
                '    return validate_review_actions(actions) if repository == "example/project" else actions\n',
            ),
            "argv": (
                "action-argv-bypass",
                "    return validate_review_actions(actions)\n",
                '    return validate_review_actions(actions) if sys.argv == ["review_base_checker.py"] else actions\n',
            ),
            "path": (
                "action-path-bypass",
                "    return validate_review_actions(actions)\n",
                '    return validate_review_actions(actions) if Path.cwd().name in {"origin", "head"} else actions\n',
            ),
        }[kind]
        return cls._make_replace_head(
            message, "scripts/workflow_pilot/review_base_checker.py", old, new
        )
    @classmethod
    def _make_action_binding_head(cls, kind):
        return cls._make_replace_head(
            {
                "special-case": "action-binding-special-case",
                "import-name": "action-binding-import-name",
                "argv": "action-binding-argv",
                "path": "action-binding-path",
            }[kind],
            "scripts/workflow_pilot/review_base_checker.py",
            '"finding_member": parsed["member"],',
            {
                "special-case": '"finding_member": parsed["member"] if finding_id == "FINDING" else parsed["family"],',
                "import-name": '"finding_member": parsed["member"] if __name__.startswith("review_assertions_") else parsed["family"],',
                "argv": '"finding_member": parsed["member"] if sys.argv == ["review_base_checker.py"] else parsed["family"],',
                "path": '"finding_member": parsed["member"] if Path.cwd().name in {"origin", "head"} else parsed["family"],',
            }[kind],
        )
    @classmethod
    def _set_generated_owners_health(cls, healthy):
        registry_path = cls.repo / "docs/test-cases/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        feature = next(
            item
            for item in registry["features"]
            if item["id"] == "workflow-governance"
        )
        urls = [url for url in feature["issue_urls"] if url != ISSUE_179_URL]
        feature["issue_urls"] = (
            sorted(set(feature["issue_urls"])) if healthy else urls
        )
        registry_path.write_bytes(reporter.normalized_json(registry))
    @classmethod
    def _set_lifecycle_entries_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_family.py",
            '"reason": "third-consecutive-change-request",',
            '"reason": "third-consecutive-change-request-broken",',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_family.py",
                '"reason": "third-consecutive-change-request-broken",',
                '"reason": "third-consecutive-change-request",',
            )
    @classmethod
    def _set_resource_enabled_health(cls, healthy):
        path = cls.repo / ".github/workflow-pilot-decisions.json"
        decisions = json.loads(path.read_text(encoding="utf-8"))
        decisions["pull_requests"] = [entry for entry in decisions["pull_requests"] if entry["pull_request"] != PULL_REQUEST]
        if healthy:
            decisions["pull_requests"].append(next(entry for entry in json.loads(synthetic_decision_record_snapshot().decode("ascii"))["pull_requests"] if entry["pull_request"] == PULL_REQUEST))
        path.write_bytes(reporter.normalized_json(decisions))
    @classmethod
    def _set_wire_stale_bindings_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/review_base_checker.py",
            'elif validated_review_context["candidate_sha"] != candidate_sha:',
            'elif validated_review_context["candidate_sha"] != validated_review_context["candidate_sha"]:',
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/review_base_checker.py",
                'elif validated_review_context["candidate_sha"] != validated_review_context["candidate_sha"]:',
                'elif validated_review_context["candidate_sha"] != candidate_sha:',
            )
    @classmethod
    def _set_wire_producers_health(cls, healthy):
        cls._replace_once(
            "scripts/workflow_pilot/trusted_review_gate.py",
            '"result_manifest": build_result_manifest(\n'
            "            execution_receipts, local_remediation_receipt\n"
            "        ),",
            '"candidate_manifest": build_result_manifest(\n'
            "            execution_receipts, local_remediation_receipt\n"
            "        ),",
        )
        if healthy:
            cls._replace_once(
                "scripts/workflow_pilot/trusted_review_gate.py",
                '"candidate_manifest": build_result_manifest(\n'
                "            execution_receipts, local_remediation_receipt\n"
                "        ),",
                '"result_manifest": build_result_manifest(\n'
                "            execution_receipts, local_remediation_receipt\n"
                "        ),",
            )
    @classmethod
    def _make_wire_reporter_break_head(cls):
        cls._restore_baseline()
        cls._replace_once(
            "scripts/workflow_pilot/reporter.py",
            "def run_git(",
            "def broken_run_git(",
        )
        head = cls._commit("wire-reporter-break")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_wire_package_init_break_head(cls):
        cls._restore_baseline()
        (cls.repo / "scripts/workflow_pilot/__init__.py").write_text(
            'raise RuntimeError("workflow_pilot package broken")\n',
            encoding="utf-8",
        )
        head = cls._commit("wire-package-init-break")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_generated_dynamic_loader_break_head(cls):
        cls._restore_baseline()
        (cls.repo / "scripts/check_docs.py").write_text(
            'raise RuntimeError("docs checker broken")\n',
            encoding="utf-8",
        )
        head = cls._commit("generated-dynamic-loader-break")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_namespace_init_addition_head(cls, relative):
        cls._restore_baseline()
        path = cls.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""namespace initializer"""\n', encoding="utf-8")
        head = cls._commit(f"namespace-init-addition:{relative}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_existing_init_removal_head(cls, relative):
        cls._restore_baseline()
        (cls.repo / relative).unlink()
        head = cls._commit(f"existing-init-removal:{relative}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_unrelated_file_head(cls):
        cls._restore_baseline()
        (cls.repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        head = cls._commit("unrelated-file")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_wire_source_kind_producer_head(cls):
        cls._restore_baseline()
        cls._replace_once(
            "scripts/workflow_pilot/trusted_review_gate.py",
            'source_kind="live-gh-api",',
            'source_kind="offline-transform-fixture",',
        )
        head = cls._commit("wire-source-kind-producer")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_wire_source_kind_consumer_head(cls):
        cls._restore_baseline()
        cls._replace_once(
            "scripts/workflow_pilot/review_family.py",
            '{"live-gh-api", "offline-transform-fixture"},',
            '{"offline-transform-fixture"},',
        )
        head = cls._commit("wire-source-kind-consumer")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_wire_producer_spoof_head(cls, kind):
        cls._restore_baseline()
        cls._set_wire_producers_health(False)
        path = cls.repo / "scripts/workflow_pilot/trusted_review_gate.py"
        text = path.read_text(encoding="utf-8")
        marker = (
            "    return {\n"
            '        "schema_version": review_family.SCHEMA_VERSION,\n'
        )
        spoof = (
            "        fake = {\n"
            '            "result_manifest": build_result_manifest(execution_receipts, local_remediation_receipt),\n'
            '            "authoritative_trigger": authoritative_trigger,\n'
            "        }\n"
        )
        if kind == "authoritative-trigger":
            injection = "    if authoritative_trigger:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "false":
            injection = "    if False:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "not-true":
            injection = "    if not True:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "eq-compare":
            injection = "    if 0 == 1:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "gt-compare":
            injection = "    if 1 > 2:\n" + spoof
            text = text.replace(marker, injection + marker, 1)
        elif kind == "dead-function":
            injection = "    def _dead_result_manifest():\n" + spoof + "\n"
            text = text.replace(marker, injection + marker, 1)
        elif kind == "nested-return":
            nested = (
                "    if True:\n"
                "        return {\n"
                '            "schema_version": review_family.SCHEMA_VERSION,\n'
                '            "repository": contract["repository"],\n'
                '            "source": {"kind": source_kind, "complete": True},\n'
                '            "captured_at": captured_at,\n'
                '            "candidate": {"sha": expected_candidate},\n'
                '            "original_pre_review_head": contract["original_pre_review_head"],\n'
                '            "original_receipt_sha256": original_receipt_sha256,\n'
                '            "pull_request": pull_request,\n'
                '            "authoritative_trigger": authoritative_trigger,\n'
                '            "result_source_path": review_family.RESULT_SOURCE_PATH,\n'
                '            "actors": actors,\n'
                '            "pre_reviews": pre_reviews,\n'
                '            "pre_review_findings": pre_review_findings,\n'
                '            "remote_reviews": remote_reviews,\n'
                '            "findings": findings,\n'
                '            "threads": threads,\n'
                '            "candidate_advances": [],\n'
                '            "force_push_events": force_push_events,\n'
                '            "architecture_dispositions": architecture_dispositions,\n'
                '            "execution_receipts": execution_receipts,\n'
                '            "candidate_manifest": build_result_manifest(execution_receipts, local_remediation_receipt),\n'
                "        }\n"
                + spoof
            )
            text = text.replace(marker, nested + marker, 1)
        elif kind == "try-dead":
            nested = (
                "    try:\n"
                "        return {\n"
                '            "schema_version": review_family.SCHEMA_VERSION,\n'
                '            "repository": contract["repository"],\n'
                '            "source": {"kind": source_kind, "complete": True},\n'
                '            "captured_at": captured_at,\n'
                '            "candidate": {"sha": expected_candidate},\n'
                '            "original_pre_review_head": contract["original_pre_review_head"],\n'
                '            "original_receipt_sha256": original_receipt_sha256,\n'
                '            "pull_request": pull_request,\n'
                '            "authoritative_trigger": authoritative_trigger,\n'
                '            "result_source_path": review_family.RESULT_SOURCE_PATH,\n'
                '            "actors": actors,\n'
                '            "pre_reviews": pre_reviews,\n'
                '            "pre_review_findings": pre_review_findings,\n'
                '            "remote_reviews": remote_reviews,\n'
                '            "findings": findings,\n'
                '            "threads": threads,\n'
                '            "candidate_advances": [],\n'
                '            "force_push_events": force_push_events,\n'
                '            "architecture_dispositions": architecture_dispositions,\n'
                '            "execution_receipts": execution_receipts,\n'
                '            "candidate_manifest": build_result_manifest(execution_receipts, local_remediation_receipt),\n'
                "        }\n"
                "    finally:\n"
                "        pass\n"
                + spoof
            )
            text = text.replace(marker, nested + marker, 1)
        else:
            raise AssertionError(kind)
        path.write_text(text, encoding="utf-8")
        head = cls._commit(f"wire-producer-spoof-{kind}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_witness_only_head(cls):
        cls._restore_baseline()
        cls._set_action_items_health(False)
        cls._write_relative(
            "scripts/workflow_pilot/assertion_subjects/action_items.json",
            reporter.normalized_json(
                {
                    "schema_version": 1,
                    "family": "action",
                    "member": "items",
                    "payload": {"items": ["looks", "valid"]},
                }
            ),
        )
        witness_head = cls._commit("witness-only-head")
        return witness_head, git_text(cls.repo, "rev-parse", f"{witness_head}^{{tree}}")
    @classmethod
    def _make_item_spoof_head(cls, kind):
        cls._restore_baseline()
        cls._set_action_items_health(False)
        path = cls.repo / "scripts/workflow_pilot/review_base_checker.py"
        text = path.read_text(encoding="utf-8")
        if kind == "comment":
            text += '\n# "finding_member": parsed["member"],\n'
        elif kind == "docstring":
            text += '\n""" "finding_member": parsed["member"], """\n'
        elif kind == "dead-if":
            text += (
                '\nif False:\n'
                '    SPOOF = {"finding_member": parsed["member"]}\n'
            )
        elif kind == "constant":
            text += '\nSPOOF_FINDING_MEMBER = \'"finding_member": parsed["member"],\'\n'
        else:
            raise AssertionError(kind)
        path.write_text(text, encoding="utf-8")
        head = cls._commit(f"item-spoof-{kind}")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    @classmethod
    def _make_item_refactor_head(cls):
        cls._restore_baseline()
        path = cls.repo / "scripts/workflow_pilot/review_base_checker.py"
        text = path.read_text(encoding="utf-8")
        old = (
            '        "finding_member": parsed["member"],\n'
            '        "finding_review_id": finding["review_id"],\n'
        )
        new = (
            '        "finding_review_id": finding["review_id"],\n'
            '        "finding_member"   :   parsed["member"],\n'
        )
        if old not in text:
            raise AssertionError("missing refactor block")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        head = cls._commit("item-refactor")
        return head, git_text(cls.repo, "rev-parse", f"{head}^{{tree}}")
    def case_dir(self):
        case_root = self.root / f"case-{next(self.case_ids)}"
        case_root.mkdir()
        return case_root
    def materialize_input_root(self, commit_sha, destination):
        destination.mkdir()
        for relative in ASSERTION_INPUTS:
            identity = review_base_checker.git_file_identity_at_revision(
                self.repo,
                commit_sha,
                relative,
                f"test materialized input {relative!r}",
            )
            if identity["mode"] not in review_base_checker.MATERIALIZED_FILE_MODES:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git_bytes(self.repo, "show", f"{commit_sha}:{relative}"))
    def materialize_program(self, destination):
        destination.write_bytes(
            git_bytes(self.repo, "show", f"{self.base}:{ASSERTION_PROGRAM}")
        )
    def configured_family_sweeps(self, local_findings, remote_findings, assertion_requests):
        templates = {}
        for sweep in self.contract_template["family_sweeps"]:
            members = {item["member"] for item in sweep["siblings"]}
            family = next(
                family
                for family, registered in review_family.FAMILY_MEMBERS.items()
                if members == set(registered)
            )
            templates[family] = sweep
        requested = {}
        for request in assertion_requests:
            if request["finding_id"] is None:
                continue
            parts = request["assertion_id"].split(":")
            if len(parts) < 6 or parts[:2] != ["registry", "sibling"]:
                continue
            requested[(request["finding_id"], parts[2], parts[3])] = parts[4]
        sweeps = []
        for finding in [*local_findings, *remote_findings]:
            finding_id = finding["id"] if "id" in finding else finding["node_id"]
            family = finding["family"]
            sweep = copy.deepcopy(templates[family])
            sweep["finding_id"] = finding_id
            explicit_members = {
                member
                for (requested_finding, requested_family, member), _outcome in requested.items()
                if requested_finding == finding_id and requested_family == family
            }
            for sibling in sweep["siblings"]:
                member = sibling["member"]
                default_outcome = (
                    "verified-unaffected"
                    if "verified-unaffected"
                    in review_family.MEMBER_OUTCOME_REGISTRY[family][member]
                    else "affected-fixed"
                )
                outcome = requested.get(
                    (finding_id, family, member),
                    default_outcome,
                )
                sibling["result"] = outcome
                sibling["assertion_id"] = review_family.member_assertion_id(
                    family, member, outcome
                )
            if not any(
                sibling["result"] == "affected-fixed"
                for sibling in sweep["siblings"]
            ):
                fallback = next(
                    (
                        sibling
                        for sibling in sweep["siblings"]
                        if sibling["member"] not in explicit_members
                    ),
                    sweep["siblings"][0],
                )
                fallback["result"] = "affected-fixed"
                fallback["assertion_id"] = review_family.member_assertion_id(
                    family, fallback["member"], "affected-fixed"
                )
            sweeps.append(sweep)
        return sweeps
    def captured_github_payload(self, head_sha, all_remote_reviews, remote_findings):
        commit_shas = []
        for sha in [self.base, self.head1, head_sha, *[review["candidate_sha"] for review in all_remote_reviews]]:
            if sha not in commit_shas:
                commit_shas.append(sha)
        review_nodes = []
        thread_nodes = []
        classification_comments = []
        for review in all_remote_reviews:
            comments = [
                {
                    "id": finding["node_id"],
                    "createdAt": finding["created_at"],
                    "updatedAt": finding["created_at"],
                    "body": "member-specific finding",
                    "author": copilot_graphql_actor(),
                }
                for finding in remote_findings
                if finding["review_id"] == review["node_id"]
            ]
            review_nodes.append(
                {
                    "id": review["node_id"],
                    "databaseId": review["id"],
                    "state": review["state"],
                    "submittedAt": review["submitted_at"],
                    "body": review["body"],
                    "commit": {"oid": review["candidate_sha"]},
                    "author": copilot_graphql_actor(),
                    "comments": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": comments,
                    },
                }
            )
            for comment in comments:
                thread_nodes.append(
                    {
                        "id": f"THREAD-{comment['id']}",
                        "isResolved": False,
                        "comments": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "id": comment["id"],
                                    "createdAt": comment["createdAt"],
                                    "author": comment["author"],
                                    "pullRequestReview": {"id": review["node_id"]},
                                }
                            ],
                        },
                    }
                )
                if comments:
                    submitted = datetime.fromisoformat(
                        review["submitted_at"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    classification_comments.append(
                        authoritative_family_comment(
                            base_sha=self.base,
                            original_head=self.head1,
                            review_id=review["node_id"],
                            candidate_sha=review["candidate_sha"],
                            mappings=[
                                {
                                    "finding_id": finding["node_id"],
                                    "family": finding["family"],
                                }
                                for finding in remote_findings
                                if finding["review_id"] == review["node_id"]
                            ],
                            created_at=utc_text((submitted + timedelta(seconds=30)).isoformat()),
                        )
                    )
        return {
                "data": {
                    "viewer": viewer_graphql_actor(),
                    "repository": {
                        "id": "REPO_TEST_189",
                        "nameWithOwner": REPOSITORY,
                        "viewerPermission": "READ",
                        "owner": {
                            "__typename": "User",
                            "id": "OWNER",
                            "login": "owner",
                    },
                    "pullRequest": {
                        "id": f"PR_{PULL_REQUEST}",
                        "number": PULL_REQUEST,
                        "createdAt": "2026-09-01T00:00:00Z",
                        "baseRefOid": self.base,
                        "mergeable": "MERGEABLE",
                        "headRefOid": head_sha,
                        "author": {
                            "__typename": "User",
                            "id": "IMPLEMENTER_1",
                            "login": "implementer",
                        },
                        "commits": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "commit": {
                                        "id": f"COMMIT_{index}",
                                        "oid": sha,
                                        "pushedDate": None,
                                        "committedDate": git_text(
                                            self.repo,
                                            "show",
                                            "-s",
                                            "--format=%cI",
                                            sha,
                                        ).replace("+00:00", "Z"),
                                    }
                                }
                                for index, sha in enumerate(commit_shas, 1)
                            ],
                        },
                        "timelineItems": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [],
                        },
                        "reviews": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": review_nodes,
                        },
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": thread_nodes,
                        },
                        "comments": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": classification_comments,
                        },
                    },
                },
            }
        }
    def review_contract(self, candidate_sha, *, trust_mode="base-pinned"):
        contract = copy.deepcopy(self.contract_template)
        contract["repository"] = REPOSITORY
        contract["pull_request"] = PULL_REQUEST
        contract["base_sha"] = self.base
        contract["original_pre_review_head"] = self.head1
        contract["candidate_sha"] = candidate_sha
        contract["trust_mode"] = trust_mode
        contract["implementer_actor_id"] = "IMPLEMENTER_1"
        contract["trigger"] = {
            "risk_boundaries": ["lifecycle", "protocol"],
            "threshold_triggers": ["changed-files", "risk-boundary"],
        }
        return contract
    def original_review_receipt(self):
        report_bytes = reporter.normalized_json(self.review_report())
        return {
            "schema_version": 2,
            "repository": REPOSITORY,
            "pull_request": PULL_REQUEST,
            "base_sha": self.base,
            "candidate_sha": self.head1,
            "issued_at": "2026-09-01T00:01:01Z",
            "expires_at": "2026-09-01T00:11:01Z",
            "nonce": "review-base-checker-receipt-0001",
            "key_id": "test-review-root",
            "key_epoch": 7,
            "purpose": "independent-pre-review-report",
            "payload_b64": base64.b64encode(report_bytes).decode("ascii"),
            "hmac_sha256": "a" * 64,
        }
    def review_report(self):
        return {
            "schema_version": 2,
            "report_id": "PRE_REVIEW_001",
            "repository": REPOSITORY,
            "pull_request": PULL_REQUEST,
            "base_sha": self.base,
            "candidate_sha": self.head1,
            "reviewer_actor_id": "REVIEWER_1",
            "reviewer_login": "fresh-reviewer",
            "implementer_actor_id": "IMPLEMENTER_1",
            "implementer_login": "implementer",
            "started_at": "2026-09-01T00:00:00Z",
            "completed_at": "2026-09-01T00:01:00Z",
            "permissions": ["contents:read"],
            "actions": ["read-candidate", "emit-local-report"],
            "reviewed_files": changed_files(self.original_changes),
            "reviewed_changes": copy.deepcopy(self.original_changes),
            "findings": [
                {
                    "id": "LOCAL-GENERATED-1",
                    "family": "generated",
                    "created_at": "2026-09-01T00:00:30Z",
                }
            ],
        }
    def remote_reviews(self, second_head=None):
        second_head = self.head2 if second_head is None else second_head
        round1 = {
            "id": 1001,
            "node_id": "REMOTE_REVIEW_1",
            "round": 1,
            "reviewer_actor_id": COPILOT_ACTOR_ID,
            "candidate_sha": self.head1,
            "submitted_at": "2026-09-01T00:02:00Z",
            "state": "COMMENTED",
            "body": "### 🟡 Changes recommended",
            "body_classification": "changes-recommended",
            "body_has_findings": True,
            "outcome": "changes-requested",
            "finding_ids": ["FINDING-ACTION-1"],
        }
        round2 = {
            "id": 1002,
            "node_id": "REMOTE_REVIEW_2",
            "round": 2,
            "reviewer_actor_id": COPILOT_ACTOR_ID,
            "candidate_sha": second_head,
            "submitted_at": "2026-09-01T00:04:00Z",
            "state": "COMMENTED",
            "body": "### 🟢 Approval recommended",
            "body_classification": "clean-approval",
            "body_has_findings": False,
            "outcome": "clean",
            "finding_ids": [],
        }
        return round1, round2
    def remote_findings(self):
        return [
            {
                "node_id": "FINDING-ACTION-1",
                "review_id": "REMOTE_REVIEW_1",
                "candidate_sha": self.head1,
                "created_at": "2026-09-01T00:01:30Z",
                "author_actor_id": COPILOT_ACTOR_ID,
                "family": "action",
            }
        ]
    def assertion_artifacts(self, origin_sha, head_sha):
        artifacts = []
        for relative in ASSERTION_INPUTS:
            base_identity = review_base_checker.git_file_identity_at_revision(
                self.repo,
                self.base,
                relative,
                f"test base input {relative!r}",
            )
            origin_identity = review_base_checker.git_file_identity_at_revision(
                self.repo,
                origin_sha,
                relative,
                f"test origin input {relative!r}",
            )
            head_identity = review_base_checker.git_file_identity_at_revision(
                self.repo,
                head_sha,
                relative,
                f"test head input {relative!r}",
            )
            artifacts.append(
                {
                    "path": relative,
                    "base_mode": base_identity["mode"],
                    "base_blob_oid": base_identity["blob_oid"],
                    "origin_mode": origin_identity["mode"],
                    "origin_blob_oid": origin_identity["blob_oid"],
                    "head_mode": head_identity["mode"],
                    "head_blob_oid": head_identity["blob_oid"],
                }
            )
        return artifacts
    def build_input(self, *, review_round, candidate_sha=None, candidate_tree=None, assertion_requests=None):
        case_root = self.case_dir()
        origin_sha = self.base if review_round == 1 else self.head1
        head_sha = (self.head1 if review_round == 1 else self.head2) if candidate_sha is None else candidate_sha
        head_tree = git_text(self.repo, "rev-parse", f"{head_sha}^{{tree}}") if candidate_tree is None else candidate_tree
        base_root = case_root / "base"
        origin_root = case_root / "origin"
        head_root = case_root / "head"
        self.materialize_input_root(self.base, base_root)
        self.materialize_input_root(origin_sha, origin_root)
        self.materialize_input_root(head_sha, head_root)
        program_path = case_root / "review_assertions.py"
        self.materialize_program(program_path)
        round1, round2 = self.remote_reviews(second_head=head_sha)
        all_remote_reviews = [round1] if review_round == 1 else [round1, round2]
        review_context = all_remote_reviews[review_round - 1]
        changes = review_family.derive_change_records(self.repo, self.base, head_sha)
        original_receipt = self.original_review_receipt()
        remote_findings = copy.deepcopy(self.remote_findings())
        assertion_requests = copy.deepcopy(
            assertion_requests
            or (
                [
                    {
                        "assertion_id": (
                            "registry:behavior:actor-permission-bounds:positive:v2"
                        ),
                        "finding_id": None,
                    },
                    {
                        "assertion_id": (
                            "registry:sibling:generated:owners:affected-fixed:v2"
                        ),
                        "finding_id": "LOCAL-GENERATED-1",
                    },
                ]
                if review_round == 1
                else [
                    {
                        "assertion_id": (
                            "registry:behavior:round-lifecycle:runtime:v2"
                        ),
                        "finding_id": None,
                    },
                    {
                        "assertion_id": (
                            "registry:sibling:action:items:affected-fixed:v2"
                        ),
                        "finding_id": "FINDING-ACTION-1",
                    },
                ]
            )
        )
        data = {
            "schema_version": 2,
            "repository": REPOSITORY,
            "repository_root": str(self.repo),
            "pull_request": PULL_REQUEST,
            "base_sha": self.base,
            "base_tree": self.base_tree,
            "original_pre_review_head": self.head1,
            "original_changes": copy.deepcopy(self.original_changes),
            "original_receipt_sha256": hashlib.sha256(
                reporter.normalized_json(original_receipt)
            ).hexdigest(),
            "review_contract": self.review_contract(head_sha),
            "original_review_receipt": original_receipt,
            "assertion_program_path": str(program_path),
            "assertion_program_blob_oid": self.program_blob_oid,
            "assertion_program_argv": list(review_base_checker.ASSERTION_PROGRAM_ARGV),
            "finding_origin_sha": origin_sha,
            "finding_origin_tree": git_text(self.repo, "rev-parse", f"{origin_sha}^{{tree}}"),
            "base_root": str(base_root),
            "origin_root": str(origin_root),
            "head_root": str(head_root),
            "assertion_input_artifacts": self.assertion_artifacts(origin_sha, head_sha),
            "candidate_sha": head_sha,
            "candidate_tree": head_tree,
            "head_sha": head_sha,
            "review_round": review_round,
            "review_context": copy.deepcopy(review_context),
            "all_remote_reviews": copy.deepcopy(all_remote_reviews),
            "remote_findings": remote_findings,
            "captured_github_payload": self.captured_github_payload(
                head_sha, all_remote_reviews, remote_findings
            ),
            "trust_mode": "base-pinned",
            "changed_files": changed_files(changes),
            "changes": changes,
            "remote_finding_ids": copy.deepcopy(review_context["finding_ids"]),
            "limits": {
                "max_duration_minutes": 30,
                "max_findings_per_review": 10,
                "max_reviewed_files": 40,
                "max_siblings_per_finding": 5,
                "max_siblings_per_handoff": 50,
            },
            "original_pre_review": self.review_report(),
            "assertion_requests": assertion_requests,
        }
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(
            data["original_pre_review"]["findings"],
            data["remote_findings"],
            data["assertion_requests"],
        )
        return data
    def member_binding(self, data, assertion_id, finding_id):
        validated = review_base_checker.validate_input(copy.deepcopy(data))
        return review_base_checker.bind_member_request(
            validated,
            review_base_checker.parse_assertion_id(assertion_id),
            finding_id,
        )
    def wire_member_input(
        self,
        *,
        candidate_sha=None,
        candidate_tree=None,
        assertion_id="registry:sibling:wire:producers:verified-unaffected:v2",
    ):
        data = self.build_input(
            review_round=2,
            candidate_sha=candidate_sha,
            candidate_tree=candidate_tree,
            assertion_requests=[{"assertion_id": assertion_id, "finding_id": "FINDING-WIRE-1"}],
        )
        data["all_remote_reviews"][0]["finding_ids"] = ["FINDING-WIRE-1"]
        data["remote_findings"] = [
            {
                "node_id": "FINDING-WIRE-1",
                "review_id": "REMOTE_REVIEW_1",
                "candidate_sha": self.head1,
                "created_at": "2026-09-01T00:01:30Z",
                "author_actor_id": COPILOT_ACTOR_ID,
                "family": "wire",
            }
        ]
        data["review_context"]["finding_ids"] = []
        data["remote_finding_ids"] = []
        data["captured_github_payload"] = self.captured_github_payload(
            data["candidate_sha"], data["all_remote_reviews"], data["remote_findings"]
        )
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(
            data["original_pre_review"]["findings"],
            data["remote_findings"],
            data["assertion_requests"],
        )
        binding = self.member_binding(data, assertion_id, "FINDING-WIRE-1")
        return data, binding
    def assert_action_sequence_hold(self, kind):
        bypass_head, bypass_tree = self._make_action_sequence_head(kind)
        data = self.build_input(
            review_round=2,
            candidate_sha=bypass_head,
            candidate_tree=bypass_tree,
            assertion_requests=[{"assertion_id": "registry:sibling:action:actions:verified-unaffected:v2", "finding_id": "FINDING-ACTION-1"}],
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:action:actions:verified-unaffected:v2",
            finding_id="FINDING-ACTION-1",
            dependency_paths=["scripts/workflow_pilot/review_base_checker.py"],
        )
    def assert_action_binding_rejected(self, kind):
        spoof_head, spoof_tree = self._make_action_binding_head(kind)
        data = self.build_input(
            review_round=2,
            candidate_sha=spoof_head,
            candidate_tree=spoof_tree,
        )
        self.assert_cli_rejected(data, "member-item authority binding is incomplete")
    def generated_member_input(
        self,
        *,
        candidate_sha=None,
        candidate_tree=None,
        assertion_id="registry:sibling:generated:consumers:verified-unaffected:v2",
        finding_id="FINDING-GENERATED-1",
    ):
        data = self.build_input(
            review_round=2,
            candidate_sha=candidate_sha,
            candidate_tree=candidate_tree,
            assertion_requests=[{"assertion_id": assertion_id, "finding_id": finding_id}],
        )
        data["all_remote_reviews"][0]["finding_ids"] = [finding_id]
        data["remote_findings"] = [
            {
                "node_id": finding_id,
                "review_id": "REMOTE_REVIEW_1",
                "candidate_sha": self.head1,
                "created_at": "2026-09-01T00:01:30Z",
                "author_actor_id": COPILOT_ACTOR_ID,
                "family": "generated",
            }
        ]
        data["review_context"]["finding_ids"] = []
        data["remote_finding_ids"] = []
        data["captured_github_payload"] = self.captured_github_payload(
            data["candidate_sha"], data["all_remote_reviews"], data["remote_findings"]
        )
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(
            data["original_pre_review"]["findings"],
            data["remote_findings"],
            data["assertion_requests"],
        )
        binding = self.member_binding(data, assertion_id, finding_id)
        return data, binding
    def authority_dependency_paths(self, family, member, *, base_root=None, allowed_paths=None):
        if base_root is None:
            data = self.build_input(review_round=1)
            base_root = Path(data["base_root"])
        return review_assertions.authority_dependency_paths(
            family,
            member,
            base_root=base_root,
            allowed_paths=allowed_paths,
        )
    def execute(self, data):
        return review_base_checker.execute_registry(copy.deepcopy(data))
    def execute_via_cli(self, data):
        case_root = Path(data["assertion_program_path"]).parent
        checker_path = case_root / "review_base_checker.py"
        input_path = case_root / "checker-input.json"
        checker_path.write_bytes(
            (
                ROOT / "scripts/workflow_pilot/review_base_checker.py"
            ).read_bytes()
        )
        input_path.write_bytes(reporter.normalized_json(data))
        completed = subprocess.run(
            (
                "/usr/bin/python3",
                "-I",
                str(checker_path),
                "--input",
                str(input_path),
            ),
            cwd=case_root,
            env={
                "HOME": str(case_root),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
            },
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AssertionError(detail or "review_base_checker CLI failed")
        parsed = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=review_base_checker.object_no_duplicates,
        )
        self.assertEqual(
            review_base_checker.normalized_json(parsed), completed.stdout
        )
        return parsed
    def assert_rejected(self, data, message):
        with self.assertRaisesRegex(review_base_checker.CheckError, message):
            self.execute(data)
    def assert_cli_rejected(self, data, message):
        with self.assertRaisesRegex(AssertionError, message):
            self.execute_via_cli(data)
    def assert_held(
        self,
        data,
        *,
        assertion_id=None,
        finding_id=None,
        dependency_paths=None,
    ):
        result = self.execute(data)
        matches = result["results"]
        if assertion_id is not None:
            matches = [
                item for item in matches if item["assertion_id"] == assertion_id
            ]
        if finding_id is not None:
            matches = [
                item
                for item in matches
                if item["authority_binding"]["finding_id"] == finding_id
            ]
        self.assertEqual(len(matches), 1)
        held = matches[0]
        self.assertEqual(held["status"], "hold")
        self.assertEqual(
            held["output"]["hold_reason"], "authority-dependency-changed"
        )
        self.assertTrue(held["output"]["external_review_required"])
        self.assertTrue(held["output"]["fresh_base_required"])
        if dependency_paths is not None:
            self.assertEqual(
                [item["path"] for item in held["output"]["authority_dependencies"]],
                dependency_paths,
            )
        return held
    def evaluate_member_contract(self, family, member, data, commit_sha, binding):
        case_root = self.case_dir()
        root = case_root / "member-root"
        self.materialize_input_root(commit_sha, root)
        validated = review_base_checker.validate_input(copy.deepcopy(data))
        checker_input = review_base_checker._assertion_program_context(validated)
        return review_assertions.evaluate_member_contract(
            family, member, root, Path(checker_input["base_root"]), binding, checker_input
        )
    def assert_member_rejected(self, family, member, data, commit_sha, message, binding):
        with self.assertRaisesRegex(review_assertions.AssertionFailure, message):
            self.evaluate_member_contract(family, member, data, commit_sha, binding)
    def test_member_parsers_reject_unregistered_verified_unaffected(self):
        valid_ids = (
            "registry:sibling:action:items:verified-unaffected:v2",
            "registry:sibling:lifecycle:entries:verified-unaffected:v2",
            "registry:sibling:wire:stale-bindings:verified-unaffected:v2",
        )
        for parser in (
            review_base_checker.parse_assertion_id,
            review_assertions.parse_assertion,
        ):
            for assertion_id in valid_ids:
                with self.subTest(parser=parser.__module__, assertion_id=assertion_id):
                    self.assertEqual(
                        parser(assertion_id)["outcome"],
                        "verified-unaffected",
                    )
    def test_round_one_executes_local_finding_with_authoritative_binding(self):
        data = self.build_input(review_round=1)
        captured = {}
        real_run = review_base_checker.subprocess.run
        def wrapper(command, *args, **kwargs):
            if not captured and tuple(command[:2]) == review_base_checker.ASSERTION_PROGRAM_ARGV[:2] and command[-1] == "--stdin":
                captured["argv"] = list(command); captured["cwd"] = kwargs.get("cwd")
            return real_run(command, *args, **kwargs)
        review_base_checker.subprocess.run = wrapper
        try: result = self.execute(data)
        finally: review_base_checker.subprocess.run = real_run
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(len(result["results"]), 2)
        behavior = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] is None
        )
        self.assertEqual(captured["argv"], list(review_base_checker.ASSERTION_PROGRAM_ARGV))
        self.assertEqual(Path(captured["cwd"]), Path(data["assertion_program_path"]).parent)
        self.assertTrue(all(item["program_argv"] == captured["argv"] for item in result["results"]))
        self.assertEqual(behavior["authority_binding"]["head_sha"], self.head1)
        self.assertEqual(behavior["authority_binding"]["head_tree"], self.head1_tree)
        member = next(
            item
            for item in result["results"]
            if item["authority_binding"]["finding_id"] == "LOCAL-GENERATED-1"
        )
        self.assertEqual(member["authority_binding"]["finding_family"], "generated")
        self.assertEqual(member["authority_binding"]["finding_member"], "owners")
        self.assertEqual(member["authority_binding"]["finding_review_round"], 0)
        self.assertEqual(member["authority_binding"]["finding_head_sha"], self.head1)
        self.assertEqual(member["authority_binding"]["finding_origin_sha"], self.base)
        self.assertEqual(member["authority_binding"]["head_sha"], self.head1)
        self.assertEqual(member["output"]["origin_status"], "fail")
        self.assertEqual(member["output"]["head_status"], "pass")
    def test_registered_not_applicable_requires_exact_reason_context(self):
        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": (
                        "registry:sibling:resource:disabled:not-applicable:"
                        "feature-disabled-by-contract:v2"
                    ),
                    "finding_id": "LOCAL-RESOURCE-1",
                }
            ],
        )
        data["original_pre_review"]["findings"] = [
            {
                "id": "LOCAL-RESOURCE-1",
                "family": "resource",
                "created_at": "2026-09-01T00:00:30Z",
            }
        ]
        result = self.execute(data)
        self.assertEqual(
            result["results"][0]["output"]["reason"],
            "feature-disabled-by-contract",
        )
    def test_fake_finding_id_and_wrong_family_real_id_fail(self):
        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": "registry:sibling:generated:outputs:verified-unaffected:v2",
                    "finding_id": "LOCAL-FAKE-99",
                }
            ],
        )
        self.assert_rejected(data, "authoritative source round")
        data = self.build_input(
            review_round=1,
            assertion_requests=[
                {
                    "assertion_id": "registry:sibling:wire:producers:verified-unaffected:v2",
                    "finding_id": "LOCAL-GENERATED-1",
                }
            ],
        )
        self.assert_rejected(data, "family does not match")
    def test_reused_checkout_and_fake_origin_or_program_identity_fail(self):
        data = self.build_input(review_round=1)
        data["origin_root"] = str(self.repo)
        data["head_root"] = str(self.repo)
        self.assert_rejected(data, "production inputs|reuse one checkout")
        data = self.build_input(review_round=1)
        data["finding_origin_sha"] = "f" * 40
        self.assert_rejected(data, "finding_origin_sha")
        data = self.build_input(review_round=1)
        data["finding_origin_tree"] = "e" * 40
        self.assert_rejected(data, "authoritative round binding")
        data = self.build_input(review_round=1)
        data["assertion_program_blob_oid"] = "d" * 40
        self.assert_rejected(data, "assertion program")
        sha256 = self.case_dir() / "sha256-repo"; sha256.mkdir()
        subprocess.run(reporter.git_command(sha256, "init", "--object-format=sha256", "-q"), env=reporter.git_environment(offline=True), check=True, capture_output=True)
        with self.assertRaisesRegex(review_base_checker.CheckError, "object format 'sha256'.*sha1"):
            review_base_checker.validate_repository_root(sha256)
    def test_dirty_and_swapped_materialized_roots_fail(self):
        data = self.build_input(review_round=2)
        dirty = Path(data["head_root"]) / "dirty.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        try:
            self.assert_rejected(data, "production inputs")
        finally:
            dirty.unlink()
        data = self.build_input(review_round=2)
        origin_root = data["origin_root"]
        data["origin_root"] = data["head_root"]
        data["head_root"] = origin_root
        self.assert_rejected(data, "exact Git blob authority")
    def test_authority_dependency_closure_is_deterministic_and_cycle_safe(self):
        data = self.build_input(review_round=1)
        base_root = Path(data["base_root"])
        first = self.authority_dependency_paths(
            "wire", "producers", base_root=base_root
        )
        second = self.authority_dependency_paths(
            "wire", "producers", base_root=base_root
        )
        self.assertEqual(first, second)
        self.assertIn("scripts/__init__.py", first)
        self.assertIn("scripts/workflow_pilot/__init__.py", first)
        self.assertIn("scripts/workflow_pilot/reporter.py", first)
        consumers = self.authority_dependency_paths(
            "generated", "consumers", base_root=base_root
        )
        self.assertIn("tests/__init__.py", consumers)
        self.assertIn("tests/workflows/__init__.py", consumers)
        self.assertIn("scripts/workflow_pilot/hydrate_authority.py", consumers)
        self.assertIn("scripts/workflow_pilot/metadata_adapter_contract.py", consumers)
        self.assertIn("scripts/workflow_pilot/summary_continuity_contract.py", consumers)
        drift_checks = self.authority_dependency_paths(
            "generated", "drift-checks", base_root=base_root
        )
        self.assertIn("scripts/docs_check_tests/__init__.py", drift_checks)
        cycle_root = self.case_dir() / "cycle-root"
        (cycle_root / "pkg").mkdir(parents=True)
        (cycle_root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (cycle_root / "pkg" / "a.py").write_text(
            "import pkg.b\n", encoding="utf-8"
        )
        (cycle_root / "pkg" / "b.py").write_text(
            "import pkg.a\n", encoding="utf-8"
        )
        closure = review_assertions.resolve_authority_import_closure(
            cycle_root,
            (("module", "pkg.a"),),
            allowed_paths={"pkg/__init__.py", "pkg/a.py", "pkg/b.py"},
        )
        self.assertEqual(closure, ("pkg/__init__.py", "pkg/a.py", "pkg/b.py"))
    def test_namespace_initializer_additions_trigger_member_holds(self):
        scripts_head, scripts_tree = self._make_namespace_init_addition_head(
            "scripts/__init__.py"
        )
        data, _binding = self.wire_member_input(
            candidate_sha=scripts_head,
            candidate_tree=scripts_tree,
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:wire:producers:verified-unaffected:v2",
            finding_id="FINDING-WIRE-1",
            dependency_paths=["scripts/__init__.py"],
        )
        tests_head, tests_tree = self._make_namespace_init_addition_head(
            "tests/__init__.py"
        )
        data, _binding = self.generated_member_input(
            candidate_sha=tests_head,
            candidate_tree=tests_tree,
            assertion_id="registry:sibling:generated:consumers:verified-unaffected:v2",
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:generated:consumers:verified-unaffected:v2",
            finding_id="FINDING-GENERATED-1",
            dependency_paths=["tests/__init__.py"],
        )
        docs_head, docs_tree = self._make_namespace_init_addition_head(
            "scripts/docs_check_tests/__init__.py"
        )
        data, _binding = self.generated_member_input(
            candidate_sha=docs_head,
            candidate_tree=docs_tree,
            assertion_id=(
                "registry:sibling:generated:drift-checks:verified-unaffected:v2"
            ),
        )
        self.assert_held(
            data,
            assertion_id=(
                "registry:sibling:generated:drift-checks:verified-unaffected:v2"
            ),
            finding_id="FINDING-GENERATED-1",
            dependency_paths=["scripts/docs_check_tests/__init__.py"],
        )
    def test_existing_initializer_removal_triggers_hold(self):
        candidate_head, candidate_tree = self._make_existing_init_removal_head(
            "tests/workflows/__init__.py"
        )
        data, _binding = self.generated_member_input(
            candidate_sha=candidate_head,
            candidate_tree=candidate_tree,
            assertion_id="registry:sibling:generated:consumers:verified-unaffected:v2",
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:generated:consumers:verified-unaffected:v2",
            finding_id="FINDING-GENERATED-1",
            dependency_paths=["tests/workflows/__init__.py"],
        )
    def test_stale_round_head_and_current_finding_collection_fail(self):
        data = self.build_input(review_round=2)
        data["review_context"]["candidate_sha"] = self.head1
        self.assert_rejected(data, "current assertion round/head")
        data = self.build_input(review_round=2)
        data["review_context"]["round"] = 1
        self.assert_rejected(data, "current assertion round/head")
        data = self.build_input(review_round=2)
        data["remote_finding_ids"] = ["FINDING-ACTION-1"]
        self.assert_rejected(data, "current review findings")
    def test_status_coverage_and_original_report_are_exact(self):
        data = self.build_input(review_round=1)
        data["original_pre_review"]["reviewed_changes"].pop()
        self.assert_rejected(data, "status/blob evidence")
        data = self.build_input(review_round=1)
        data["changes"][0]["status"] = "X"
        self.assert_rejected(data, "status is not supported")
        data = self.build_input(review_round=1)
        data["changed_files"].append("fabricated.txt")
        self.assert_rejected(data, "changed files do not match status record")
    def test_local_namespace_actor_and_limits_remain_strict(self):
        data = self.build_input(review_round=1)
        data["original_pre_review"]["findings"][0]["id"] = "REMOTE_NODE"
        self.assert_rejected(data, "LOCAL- namespace")
        data = self.build_input(review_round=1)
        data["original_pre_review"]["reviewer_login"] = "IMPLEMENTER_bot"
        self.assert_rejected(data, "identities overlap")
        data = self.build_input(review_round=1)
        data["limits"]["max_reviewed_files"] = True
        self.assert_rejected(data, "must be an integer")
    def test_obsolete_witness_json_cannot_manufacture_pass(self):
        witness_head, witness_tree = self._make_witness_only_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=witness_head,
            candidate_tree=witness_tree,
        )
        self.assert_cli_rejected(
            data,
            "member-item authority binding is incomplete",
        )
    def test_gate_import_closure_dependency_changes_hold(self):
        for factory, dependency in (
            (
                self._make_wire_reporter_break_head,
                "scripts/workflow_pilot/reporter.py",
            ),
            (
                self._make_wire_package_init_break_head,
                "scripts/workflow_pilot/__init__.py",
            ),
        ):
            with self.subTest(dependency=dependency):
                candidate_head, candidate_tree = factory()
                data, _binding = self.wire_member_input(
                    candidate_sha=candidate_head,
                    candidate_tree=candidate_tree,
                )
                self.assert_held(
                    data,
                    assertion_id=(
                        "registry:sibling:wire:producers:verified-unaffected:v2"
                    ),
                    finding_id="FINDING-WIRE-1",
                    dependency_paths=[dependency],
                )
    def test_dynamic_loader_dependency_change_holds_generated_drift_checks(self):
        candidate_head, candidate_tree = self._make_generated_dynamic_loader_break_head()
        data, _binding = self.generated_member_input(
            candidate_sha=candidate_head,
            candidate_tree=candidate_tree,
            assertion_id=(
                "registry:sibling:generated:drift-checks:verified-unaffected:v2"
            ),
        )
        self.assert_held(
            data,
            assertion_id=(
                "registry:sibling:generated:drift-checks:verified-unaffected:v2"
            ),
            finding_id="FINDING-GENERATED-1",
            dependency_paths=["scripts/check_docs.py"],
        )
    def test_comment_docstring_dead_branch_and_constant_spoofs_fail(self):
        for kind in ("comment", "docstring", "dead-if", "constant"):
            with self.subTest(kind=kind):
                spoof_head, spoof_tree = self._make_item_spoof_head(kind)
                data = self.build_input(
                    review_round=2,
                    candidate_sha=spoof_head,
                    candidate_tree=spoof_tree,
                )
                self.assert_cli_rejected(
                    data,
                    "member-item authority binding is incomplete",
                )
    def test_action_sequence_enforcement_bypass_fails(self): self.assert_action_sequence_hold("enforcement")
    def test_action_import_name_probe_bypass_fails(self): self.assert_action_sequence_hold("import-name")
    def test_action_repository_whitelist_bypass_fails(self): self.assert_action_sequence_hold("repository")
    def test_action_argv_probe_bypass_fails(self): self.assert_action_sequence_hold("argv")
    def test_action_path_probe_bypass_fails(self): self.assert_action_sequence_hold("path")
    def test_lifecycle_entries_affected_fixed_uses_review_progression(self):
        self._restore_baseline()
        self._set_lifecycle_entries_health(False)
        (self.repo / "changed.txt").write_text("lifecycle origin\n", encoding="utf-8"); origin = self._commit("lifecycle-origin"); origin_tree = git_text(self.repo, "rev-parse", f"{origin}^{{tree}}")
        self._restore_baseline()
        (self.repo / "changed.txt").write_text("lifecycle head\n", encoding="utf-8"); head = self._commit("lifecycle-head"); head_tree = git_text(self.repo, "rev-parse", f"{head}^{{tree}}")
        data = self.build_input(review_round=2, candidate_sha=head, candidate_tree=head_tree, assertion_requests=[{"assertion_id": "registry:sibling:lifecycle:entries:affected-fixed:v2", "finding_id": "FINDING-LIFECYCLE-1"}])
        shutil.rmtree(data["origin_root"])
        self.materialize_input_root(origin, Path(data["origin_root"]))
        data["all_remote_reviews"][0]["candidate_sha"] = origin; data["all_remote_reviews"][0]["finding_ids"] = ["FINDING-LIFECYCLE-1"]
        data["remote_findings"] = [{"node_id": "FINDING-LIFECYCLE-1", "review_id": "REMOTE_REVIEW_1", "candidate_sha": origin, "created_at": "2026-09-01T00:01:30Z", "author_actor_id": COPILOT_ACTOR_ID, "family": "lifecycle"}]
        data["finding_origin_sha"] = origin; data["finding_origin_tree"] = origin_tree
        data["assertion_input_artifacts"] = self.assertion_artifacts(origin, head)
        data["review_context"]["finding_ids"] = []; data["remote_finding_ids"] = []
        data["captured_github_payload"] = self.captured_github_payload(head, data["all_remote_reviews"], data["remote_findings"])
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(data["original_pre_review"]["findings"], data["remote_findings"], data["assertion_requests"])
        member = self.execute(data)["results"][0]
        self.assertEqual((member["status"], member["output"]["program_case"]), ("pass", "member/lifecycle/entries/affected-fixed"))
        self.assertEqual((member["output"]["origin_status"], member["output"]["head_status"]), ("fail", "pass"))
    def test_action_member_requires_real_origin_failure(self):
        self._restore_baseline()
        (self.repo / "changed.txt").write_text("clean action candidate\n", encoding="utf-8")
        action_head = self._commit("action-clean-head"); action_tree = git_text(self.repo, "rev-parse", f"{action_head}^{{tree}}")
        original_changes = review_family.derive_change_records(self.repo, self.base, action_head)
        data = self.build_input(review_round=1, candidate_sha=action_head, candidate_tree=action_tree, assertion_requests=[{"assertion_id": "registry:sibling:action:items:affected-fixed:v2", "finding_id": "LOCAL-ACTION-1"}])
        data["original_pre_review_head"] = action_head
        data["original_changes"] = copy.deepcopy(original_changes)
        data["original_pre_review"] = {**self.review_report(), "candidate_sha": action_head, "reviewed_files": changed_files(original_changes), "reviewed_changes": copy.deepcopy(original_changes), "findings": [{"id": "LOCAL-ACTION-1", "family": "action", "created_at": "2026-09-01T00:00:30Z"}]}
        original_receipt = copy.deepcopy(self.original_review_receipt())
        original_receipt["candidate_sha"] = action_head; original_receipt["payload_b64"] = base64.b64encode(reporter.normalized_json(data["original_pre_review"])).decode("ascii")
        data["original_review_receipt"] = original_receipt; data["original_receipt_sha256"] = hashlib.sha256(reporter.normalized_json(original_receipt)).hexdigest()
        data["review_context"]["candidate_sha"] = action_head
        data["all_remote_reviews"][0]["candidate_sha"] = action_head
        data["review_context"]["finding_ids"] = []; data["all_remote_reviews"][0]["finding_ids"] = []; data["remote_findings"] = []; data["remote_finding_ids"] = []
        data["review_contract"] = self.review_contract(action_head)
        data["review_contract"]["original_pre_review_head"] = action_head
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(data["original_pre_review"]["findings"], [], data["assertion_requests"])
        data["captured_github_payload"] = self.captured_github_payload(action_head, data["all_remote_reviews"], [])
        self.assert_cli_rejected(data, "affected-fixed origin assertion unexpectedly passed")
    def test_resource_enabled_affected_fixed_reads_subject_decision_record(self):
        self._restore_baseline()
        self._set_resource_enabled_health(False)
        (self.repo / "changed.txt").write_text("resource origin\n", encoding="utf-8"); origin = self._commit("resource-origin"); origin_tree = git_text(self.repo, "rev-parse", f"{origin}^{{tree}}")
        self._restore_baseline()
        (self.repo / "changed.txt").write_text("resource head\n", encoding="utf-8"); head = self._commit("resource-head"); head_tree = git_text(self.repo, "rev-parse", f"{head}^{{tree}}")
        data = self.build_input(review_round=2, candidate_sha=head, candidate_tree=head_tree, assertion_requests=[{"assertion_id": "registry:sibling:resource:enabled:affected-fixed:v2", "finding_id": "FINDING-RESOURCE-1"}])
        shutil.rmtree(data["origin_root"])
        self.materialize_input_root(origin, Path(data["origin_root"]))
        data["all_remote_reviews"][0]["candidate_sha"] = origin; data["all_remote_reviews"][0]["finding_ids"] = ["FINDING-RESOURCE-1"]
        data["remote_findings"] = [{"node_id": "FINDING-RESOURCE-1", "review_id": "REMOTE_REVIEW_1", "candidate_sha": origin, "created_at": "2026-09-01T00:01:30Z", "author_actor_id": COPILOT_ACTOR_ID, "family": "resource"}]
        data["finding_origin_sha"] = origin; data["finding_origin_tree"] = origin_tree
        data["assertion_input_artifacts"] = self.assertion_artifacts(origin, head)
        data["review_context"]["finding_ids"] = []; data["remote_finding_ids"] = []
        data["captured_github_payload"] = self.captured_github_payload(head, data["all_remote_reviews"], data["remote_findings"])
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(data["original_pre_review"]["findings"], data["remote_findings"], data["assertion_requests"])
        member = self.execute(data)["results"][0]
        self.assertEqual((member["status"], member["output"]["program_case"]), ("pass", "member/resource/enabled/affected-fixed"))
        self.assertEqual((member["output"]["origin_status"], member["output"]["head_status"]), ("fail", "pass"))
    def test_member_binding_special_case_does_not_spoof_real_finding_id(self): self.assert_action_binding_rejected("special-case")
    def test_member_binding_import_name_whitelist_fails(self): self.assert_action_binding_rejected("import-name")
    def test_member_binding_argv_whitelist_fails(self): self.assert_action_binding_rejected("argv")
    def test_member_binding_path_whitelist_fails(self): self.assert_action_binding_rejected("path")
    def test_whitespace_and_order_refactor_preserves_member_fix(self):
        refactor_head, refactor_tree = self._make_item_refactor_head()
        data = self.build_input(
            review_round=2,
            candidate_sha=refactor_head,
            candidate_tree=refactor_tree,
        )
        result = next(
            item
            for item in self.execute_via_cli(data)["results"]
            if item["assertion_id"]
            == "registry:sibling:action:items:affected-fixed:v2"
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            result["output"]["program_case"],
            "member/action/items/affected-fixed",
        )
    def test_wire_producer_dead_ast_spoofs_fail(self):
        for kind in (
            "authoritative-trigger",
            "false",
            "not-true",
            "eq-compare",
            "gt-compare",
            "dead-function",
            "nested-return",
            "try-dead",
        ):
            with self.subTest(kind=kind):
                spoof_head, spoof_tree = self._make_wire_producer_spoof_head(kind)
                data, binding = self.wire_member_input(
                    candidate_sha=spoof_head,
                    candidate_tree=spoof_tree,
                )
                self.assert_held(
                    data,
                    assertion_id="registry:sibling:wire:producers:verified-unaffected:v2",
                    finding_id="FINDING-WIRE-1",
                    dependency_paths=["scripts/workflow_pilot/trusted_review_gate.py"],
                )
    def test_wire_producer_real_contract_still_passes(self):
        self._restore_baseline()
        (self.repo / "changed.txt").write_text("wire producer healthy\n", encoding="utf-8")
        healthy_head = self._commit("wire-producer-healthy")
        healthy_tree = git_text(self.repo, "rev-parse", f"{healthy_head}^{{tree}}")
        data, binding = self.wire_member_input(
            candidate_sha=healthy_head,
            candidate_tree=healthy_tree,
        )
        result = self.evaluate_member_contract("wire", "producers", data, healthy_head, binding)
        self.assertFalse(any((Path(data[root]) / "scripts/__init__.py").exists() for root in ("base_root", "head_root")))
        self.assertEqual((result["live_source_kind"], result["offline_source_kind"], result["result_manifest_size"]), ("live-gh-api", "offline-transform-fixture", 0))
        replay_data, replay_binding = self.wire_member_input(
            candidate_sha=healthy_head,
            candidate_tree=healthy_tree,
            assertion_id="registry:sibling:wire:replay:verified-unaffected:v2",
        )
        replay = self.evaluate_member_contract("wire", "replay", replay_data, healthy_head, replay_binding)
        self.assertEqual((replay["replay_sha256"], len(replay["replay_entries"]), replay["replay_entries"][0].startswith("original-")), (replay_data["original_receipt_sha256"], 1, True))
        self.assertIn("re-signed", replay["replay_rejection"])
    def test_unrelated_initializer_outside_member_closure_does_not_hold(self):
        candidate_head, candidate_tree = self._make_namespace_init_addition_head(
            "scripts/docs_check_tests/__init__.py"
        )
        data, _binding = self.wire_member_input(
            candidate_sha=candidate_head,
            candidate_tree=candidate_tree,
        )
        result = self.execute(data)
        member = result["results"][0]
        self.assertEqual(member["status"], "pass")
        self.assertEqual(
            member["output"]["program_case"],
            "member/wire/producers/verified-unaffected",
        )
    def test_unrelated_file_change_does_not_trigger_authority_hold(self):
        candidate_head, candidate_tree = self._make_unrelated_file_head()
        data, _binding = self.wire_member_input(
            candidate_sha=candidate_head,
            candidate_tree=candidate_tree,
        )
        result = self.execute(data)
        member = result["results"][0]
        self.assertEqual(member["status"], "pass")
        self.assertEqual(
            member["output"]["program_case"],
            "member/wire/producers/verified-unaffected",
        )
    def test_wire_source_kind_special_cases_fail(self):
        producer_head, producer_tree = self._make_wire_source_kind_producer_head()
        data, binding = self.wire_member_input(
            candidate_sha=producer_head,
            candidate_tree=producer_tree,
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:wire:producers:verified-unaffected:v2",
            finding_id="FINDING-WIRE-1",
            dependency_paths=["scripts/workflow_pilot/trusted_review_gate.py"],
        )
        consumer_head, consumer_tree = self._make_wire_source_kind_consumer_head()
        data, binding = self.wire_member_input(
            candidate_sha=consumer_head,
            candidate_tree=consumer_tree,
            assertion_id="registry:sibling:wire:consumers:verified-unaffected:v2",
        )
        self.assert_held(
            data,
            assertion_id="registry:sibling:wire:consumers:verified-unaffected:v2",
            finding_id="FINDING-WIRE-1",
            dependency_paths=["scripts/workflow_pilot/review_family.py"],
        )
    def test_wire_stale_bindings_affected_fixed_uses_current_review_binding(self):
        self._restore_baseline()
        self._set_wire_stale_bindings_health(False)
        (self.repo / "changed.txt").write_text("wire stale origin\n", encoding="utf-8"); origin = self._commit("wire-stale-origin")
        self._restore_baseline()
        (self.repo / "changed.txt").write_text("wire stale head\n", encoding="utf-8"); head = self._commit("wire-stale-head"); head_tree = git_text(self.repo, "rev-parse", f"{head}^{{tree}}")
        data, _binding = self.wire_member_input(candidate_sha=head, candidate_tree=head_tree, assertion_id="registry:sibling:wire:stale-bindings:affected-fixed:v2")
        shutil.rmtree(data["origin_root"])
        self.materialize_input_root(origin, Path(data["origin_root"]))
        data["all_remote_reviews"][0]["candidate_sha"] = origin; data["remote_findings"][0]["candidate_sha"] = origin
        data["finding_origin_sha"] = origin; data["finding_origin_tree"] = git_text(self.repo, "rev-parse", f"{origin}^{{tree}}")
        data["assertion_input_artifacts"] = self.assertion_artifacts(origin, head)
        data["captured_github_payload"] = self.captured_github_payload(head, data["all_remote_reviews"], data["remote_findings"])
        data["review_contract"]["family_sweeps"] = self.configured_family_sweeps(data["original_pre_review"]["findings"], data["remote_findings"], data["assertion_requests"])
        member = self.execute(data)["results"][0]
        self.assertEqual((member["status"], member["output"]["program_case"]), ("pass", "member/wire/stale-bindings/affected-fixed"))
        self.assertEqual((member["output"]["origin_status"], member["output"]["head_status"]), ("fail", "pass"))


if __name__ == "__main__":
    unittest.main()
