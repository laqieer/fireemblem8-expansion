"""Read-only coordinator adapter over Git, GitHub and existing test processes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import types

from scripts.workflow_pilot import reporter


COPILOT = ("Bot", "BOT_kgDOCnlnWA", "copilot-pull-request-reviewer")
MODULES = ("review_family", "review_subjects")
WORKER_CODE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.workflow_pilot.review_subjects import worker
print(json.dumps(worker(json.loads(sys.stdin.buffer.read()))))
"""
REVIEW_QUERY = """
query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    nameWithOwner
    pullRequest(number:$number) {
      number baseRefOid headRefOid
      reviews(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id state submittedAt body commit { oid }
          author { __typename login ... on Node { id } }
          comments(first:100) {
            pageInfo { hasNextPage }
            nodes { id path body }
          }
        }
      }
      reviewThreads(first:100) {
        pageInfo { hasNextPage }
        nodes {
          id isResolved
          comments(first:1) { nodes { pullRequestReview { id } } }
        }
      }
    }
  }
}
"""


class GitTree:
    """An immutable object reader, never an import from the working directory."""

    def __init__(self, root: Path, revision: str):
        if not isinstance(revision, str) or reporter.SHA_RE.fullmatch(revision) is None:
            raise ValueError("exact lowercase revision required")
        self.root = root.resolve(strict=True)
        self.revision = revision
        actual = self.git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
        if actual != revision:
            raise ValueError("revision did not resolve to the intended commit")
        top = Path(self.git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if top != self.root:
            raise ValueError("source root must be the exact repository top level")
        self.entries = {}
        for entry in self.git("ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
            self.entries[path] = mode, kind, oid
        self.captured = {}

    def git(self, *arguments):
        return reporter.run_git(
            self.root, "--no-optional-locks", "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null", "-c", "diff.external=",
            *arguments)

    def oid(self, path):
        if path not in self.entries:
            raise ValueError(f"missing source member: {path}")
        mode, kind, oid = self.entries[path]
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError(f"source is not a regular Git blob: {path}")
        return oid

    def read(self, path):
        if path not in self.captured:
            oid = self.oid(path)
            payload = self.git("cat-file", "blob", oid)
            actual = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
            if actual != oid:
                raise ValueError("Git object bytes differ from the selected source")
            self.captured[path] = payload
        return self.captured[path]

    def under(self, prefix):
        return tuple(sorted(path for path in self.entries if path.startswith(prefix + "/")))

    def materialize(self, root: Path, paths):
        for name in paths:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe source path")
            payload = self.read(name)
            output = root / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)


def load_tools(tree: GitTree):
    package = importlib.import_module("scripts.workflow_pilot")
    loaded = []
    for name in MODULES:
        qualified = "scripts.workflow_pilot." + name
        module = types.ModuleType(qualified)
        module.__file__ = str(tree.root / ("scripts/workflow_pilot/" + name + ".py"))
        module.__package__ = "scripts.workflow_pilot"
        sys.modules[qualified] = module
        setattr(package, name, module)
        source = tree.read("scripts/workflow_pilot/" + name + ".py")
        exec(compile(source, tree.revision + ":" + name, "exec"), module.__dict__)
        loaded.append(module)
    return tuple(loaded)


class GitHub:
    def query(self, repository, number, cursor=None):
        owner, name = repository.split("/")
        command = ["/usr/bin/gh", "api", "graphql", "--hostname", "github.com",
                   "-f", "query=" + REVIEW_QUERY,
                   "-f", "owner=" + owner, "-f", "name=" + name, "-F", f"number={number}"]
        if cursor is not None:
            command.extend(["-f", "cursor=" + cursor])
        result = subprocess.run(command, capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError("GitHub observation unavailable: " +
                             result.stderr.decode(errors="replace")[-1000:])
        return json.loads(result.stdout)

    def snapshot(self, repository, number, model):
        facts = []
        cursor = None
        identity = None
        for _ in range(10):
            response = self.query(repository, number, cursor)
            model.require(not response.get("errors"), "GitHub GraphQL errors")
            repo = response["data"]["repository"]
            pr = repo["pullRequest"]
            model.require(repo["nameWithOwner"] == repository and pr["number"] == number,
                          "wrong GitHub repository or pull request")
            current = (pr["baseRefOid"], pr["headRefOid"])
            for value in current:
                model.sha(value)
            model.require(identity in (None, current), "GitHub head changed during collection")
            identity = current
            model.require(not pr["reviewThreads"]["pageInfo"]["hasNextPage"],
                          "incomplete thread collection")
            unresolved = {}
            for thread in pr["reviewThreads"]["nodes"]:
                model.require(type(thread["isResolved"]) is bool, "invalid thread state")
                if not thread["isResolved"]:
                    nodes = thread["comments"]["nodes"]
                    model.require(bool(nodes), "thread root is missing")
                    unresolved.setdefault(nodes[0]["pullRequestReview"]["id"], []).append(thread["id"])
            for item in pr["reviews"]["nodes"]:
                actor = item.get("author") or {}
                if (actor.get("__typename"), actor.get("id"), actor.get("login")) != COPILOT:
                    continue
                if item["state"] == "PENDING" and item["submittedAt"] is None:
                    continue
                model.require(item["state"] in {"COMMENTED", "APPROVED", "CHANGES_REQUESTED",
                                                "DISMISSED"}
                              and isinstance(item["body"], str), "invalid submitted review")
                model.require(not item["comments"]["pageInfo"]["hasNextPage"],
                              "incomplete review comments")
                model.sha(item["commit"]["oid"])
                reporter.parse_time(item["submittedAt"], "review timestamp")
                comments = tuple((comment["id"], comment["path"], comment["body"])
                                 for comment in item["comments"]["nodes"])
                model.unique([comment[0] for comment in comments], "review comments")
                facts.append(model.ReviewFact(
                    item["id"], item["commit"]["oid"], actor["id"], item["state"],
                    item["submittedAt"], item["body"], comments,
                    tuple(sorted(unresolved.get(item["id"], ())))))
            page = pr["reviews"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            model.require(page["endCursor"] and page["endCursor"] != cursor,
                          "invalid review pagination")
            cursor = page["endCursor"]
        else:
            raise model.ReviewError("review collection budget exceeded")
        model.unique([item.id for item in facts], "review identities")
        facts.sort(key=lambda item: (model.timestamp(item.submitted_at), item.id))
        return identity, tuple(facts)


def _kind(probe):
    if probe.startswith("aoe-arm:"):
        return "arm-object"
    if probe.startswith("aoe-"):
        return "native"
    return "parsed" if probe.startswith("generated-") else "host"


class ReviewTools:
    def __init__(self, tool_tree: GitTree, subject_root: Path, *, arm_tools=None):
        self.tool_tree = tool_tree
        self.subject_root = subject_root.resolve(strict=True)
        self.model, self.subjects = load_tools(tool_tree)
        self.catalog = json.loads(tool_tree.read("docs/test-cases/registry.json"))
        selected = {
            key: os.environ.get(key, "arm-none-eabi-" + tool)
            for key, tool in (("MODERN_CC", "gcc"), ("MODERN_NM", "nm"), ("MODERN_SIZE", "size"))
        }
        if arm_tools is not None:
            self.model.require(set(arm_tools) <= set(selected), "unknown ARM tool setting")
            selected.update(arm_tools)
        self.arm_tools = {key: os.path.abspath(shutil.which(value) or value)
                          for key, value in selected.items()}

    def validate_base(self, request, live_base):
        head = self.tree(request["candidate_sha"])
        bases = head.git("merge-base", "--all", live_base, head.revision).decode().splitlines()
        self.model.require(bases == [request["base_sha"]],
                           "frozen base differs from the unique candidate/live-base merge base")

    def tree(self, revision):
        return GitTree(self.subject_root, revision)

    def members(self, request, origins=()):
        model = self.model
        request = model.validate_request(request)
        head = self.tree(request["candidate_sha"])
        head.git("merge-base", "--is-ancestor", request["base_sha"], head.revision)
        revisions = {head.revision, *origins}
        members = {}
        for item in request["subjects"]:
            spec = self.subjects.resolve_subject(item["case_id"], item["subject"], self.catalog)
            for revision in sorted(revisions):
                for member in self.subjects.expand_members(spec, self.tree(revision), head):
                    previous = members.setdefault(member.identity, member)
                    model.require(previous == member, "member mapping changed across finding origins")
        result = tuple(members[key] for key in sorted(members))
        model.validate_members(result)
        return result

    def _stage(self, tree, root, members):
        paths = set()
        probes = {item.probe for item in members}
        if any(probe.startswith("aoe-") for probe in probes):
            paths.update(tree.under("include"))
            paths.update((self.subjects.AOE_CORE, self.subjects.AOE_REFERENCE))
        if any(probe.startswith("generated-") for probe in probes):
            paths.update(path for member in members for path in member.inputs)
            paths.update(tree.under("include"))
            paths.update(tree.under("src/data"))
            paths.update(tree.under("scripts/generated_data"))
            paths.update(path for path in ("scripts/assets/__init__.py", "scripts/assets/tmx.py")
                         if path in tree.entries)
            paths.update(path for path in tree.entries if
                         path.startswith("reports/generated_data_") or path in {
                             "src/events/ch2-eventinfo.h", "src/events_udefs.c",
                             "src/events_shoplist.c", "src/events_trapdata.c"})
        tree.materialize(root, sorted(paths))
        tool_paths = ("scripts/workflow_pilot/__init__.py",
                      self.subjects.REVIEW_SOURCE, "scripts/workflow_pilot/review_subjects.py",
                      self.subjects.AOE_DRIVER, self.subjects.AOE_DISABLED)
        self.tool_tree.materialize(root, tool_paths)
        (root / "build").mkdir(exist_ok=True)
        if any(probe.startswith(("lifecycle:", "wire:")) for probe in probes):
            (root / "build/review-subject.py").write_bytes(tree.read(self.subjects.REVIEW_SOURCE))

    def run_obligations(self, members, revision):
        model = self.model
        tree = self.tree(revision)
        model.validate_members(tuple(members))
        probes = [item.probe for item in members]
        model.unique(probes, "probe bindings")
        objects = {item.identity: tuple((path, tree.oid(path)) for path in item.inputs)
                   for item in members}
        build = self.subject_root / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="review-family-", dir=build) as directory:
            root = Path(directory)
            self._stage(tree, root, members)
            home = root / "build/home"
            home.mkdir()
            environment = {
                "HOME": str(home), "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8",
                "TMPDIR": str(root / "build"), "PYTHONDONTWRITEBYTECODE": "1",
                **self.arm_tools,
            }
            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", WORKER_CODE, str(root)],
                    input=json.dumps(probes).encode(), cwd=root, env=environment,
                    capture_output=True, timeout=240)
                model.require(completed.returncode == 0, "probe process failed: " +
                              completed.stderr.decode(errors="replace")[-2000:])
                rows = model.parse_json(completed.stdout)
                model.require(isinstance(rows, list) and len(rows) == len(probes),
                              "missing/extra probe observations")
                model.require([row["probe"] for row in rows] == probes,
                              "wrong or duplicated probe observations")
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                rows = [{"probe": probe, "verdict": "unavailable", "checks": 0,
                         "detail": str(error)} for probe in probes]
        observations = tuple(model.Observation(
            member, revision, self.tool_tree.revision, objects[member.identity],
            row["verdict"], member.evidence, row["detail"], row["checks"], _kind(member.probe))
            for member, row in zip(members, rows))
        for observation in observations:
            observation.validate()
        return observations

    def assess(self, request, session, github, triage, *, pre_review_required):
        model = self.model
        request = model.validate_request(request)
        identities, facts = github.snapshot(request["repository"], request["pull_request"], model)
        model.require(identities[1] == request["candidate_sha"], "stale remote candidate")
        self.validate_base(request, identities[0])
        session.validate_local_triage()
        model.require(set(session.rounds.seen) == {item.fact.id for item in triage},
                      "round state has not consumed actual triage")
        fact_origins = {fact.id: fact.head for fact in facts}
        if session.report is not None:
            fact_origins["local:" + str(session.lease.task)] = session.lease.head
            self.tree(request["candidate_sha"]).git(
                "merge-base", "--is-ancestor", session.lease.head, request["candidate_sha"])
        for finding in session.accepted.values():
            model.require(fact_origins.get(finding.review_id) == finding.origin,
                          "finding origin is not an actual review/task observation")
            self.tree(request["candidate_sha"]).git(
                "merge-base", "--is-ancestor", finding.origin, request["candidate_sha"])
        origins = {item.origin for item in session.accepted.values()}
        members = self.members(request, origins)
        observations = []
        for revision in sorted(origins | {request["candidate_sha"]}):
            needed = tuple(member for member in members
                           if revision == request["candidate_sha"] or any(
                               finding.origin == revision and
                               (finding.subject, finding.family) == (member.subject, member.family)
                               for finding in session.accepted.values()))
            observations.extend(self.run_obligations(needed, revision))
        for item in observations:
            tree = self.tree(item.revision)
            model.require(item.source_objects == tuple((path, tree.oid(path))
                                                       for path in item.obligation.inputs),
                          "wrong source objects")
        after_identities, after_facts = github.snapshot(
            request["repository"], request["pull_request"], model)
        model.require((after_identities, after_facts) == (identities, facts),
                      "GitHub evidence changed during execution")
        return model.assess_handoff(
            request, members, tuple(observations), session,
            tool_revision=self.tool_tree.revision, remote_reviews=facts, triage=triage,
            pre_review_required=pre_review_required)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--subject-root", type=Path, required=True)
    parser.add_argument("--tool-revision", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--mode", choices=("plan", "check"), required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated:
            raise ValueError("isolated startup is required")
        tools = ReviewTools(GitTree(args.repository_root, args.tool_revision), args.subject_root)
        request = tools.model.validate_request(tools.model.parse_json(args.request.read_bytes()))
        tools.model.require(args.candidate == request["candidate_sha"], "candidate argument mismatch")
        members = tools.members(request)
        report = {"schema_version": 1, "candidate_sha": args.candidate,
                  "tool_revision": args.tool_revision,
                  "obligations": [asdict(item) for item in members],
                  "coordinator_observations_required": True, "merge_permission": False}
        if args.mode == "check":
            identity, facts = GitHub().snapshot(request["repository"], request["pull_request"],
                                               tools.model)
            tools.model.require(identity[1] == args.candidate, "wrong live GitHub head")
            tools.validate_base(request, identity[0])
            observations = tools.run_obligations(members, args.candidate)
            report["observations"] = [asdict(item) for item in observations]
            report["untriaged_review_ids"] = [item.id for item in facts]
            report["source_audit_complete"] = all(item.verdict == "satisfied" for item in observations)
            # Complete triage and task provenance are in the existing coordinator,
            # not a file argument this diagnostic CLI could authenticate.
            report["handoff_eligible"] = False
        print(json.dumps(report, sort_keys=True))
        return 0 if args.mode == "plan" or report["source_audit_complete"] else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("review-family: " + str(error), file=sys.stderr)
        return 2
