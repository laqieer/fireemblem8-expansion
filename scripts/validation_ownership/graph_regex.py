"""Candidate regex/schema evaluation within the existing report watchdog."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .authority import ENVIRONMENT, encoded, parse_json
from .budget import MakeProbeError, ProbeBudget


def evaluate(budget: ProbeBudget, operation: str, payload):
    if operation not in {"compile", "fullmatch", "schema"}:
        raise MakeProbeError("unsupported ownership regex operation")
    budget.remaining()
    request = encoded(payload)
    if len(request) > min(budget.limits.file_bytes, budget.limits.pending_bytes):
        budget.reject("ownership regex request exceeds the existing input byte bound")
    completed = budget.run(
        [
            "/usr/bin/python3", "-I", "-S", "-B", "-c", WORKER,
            operation, str(budget.limits.address_space_bytes),
            str(budget.limits.pending_bytes), str(Path(__file__).resolve().parents[2]),
        ],
        env=ENVIRONMENT, input_data=request,
    )
    records = [parse_json(line, "ownership regex worker") for line in completed.stdout.splitlines()]
    if not records or not isinstance(records[-1], dict):
        budget.reject("ownership regex worker returned no bounded result")
    final = records[-1]
    phases = records[:-1]
    phase = lambda name: {"phase": name, "address_space_bytes": budget.limits.address_space_bytes}
    expected = [phase("schema-start")] if operation == "schema" else [phase("compile-start")]
    if operation == "schema" and phases == [phase("schema-start"), phase("schema-pattern-start")]:
        expected = phases
    if operation == "fullmatch" and final.get("ok") is True:
        expected.append(phase("match-start"))
    if final.get("ok") is False and phases == [phase("compile-start"), phase("match-start")]:
        expected = phases
    if phases != expected:
        budget.reject("ownership regex worker returned invalid execution phases")
    if completed.returncode or final.get("ok") is not True:
        detail = final.get("error", "worker failed")
        raise MakeProbeError(f"ownership {operation} validation failed: {detail}")
    if operation == "fullmatch":
        indices = final.get("indices")
        if (
            not isinstance(indices, list) or any(type(index) is not int for index in indices)
            or indices != sorted(set(indices))
            or any(index < 0 or index >= len(payload["patterns"]) for index in indices)
        ):
            budget.reject("ownership regex worker returned invalid match indices")
        return tuple(indices)
    if set(final) != {"ok"}:
        budget.reject("ownership regex worker returned unexpected fields")
    return None


def validate_patterns(budget: ProbeBudget, patterns):
    checked = _patterns(budget, patterns)
    if checked:
        evaluate(budget, "compile", {"patterns": checked})


def _patterns(budget, patterns):
    if (
        not isinstance(patterns, (tuple, list))
        or len(patterns) > budget.limits.entries
        or any(not isinstance(pattern, str) or len(pattern) > 8192 for pattern in patterns)
    ):
        raise MakeProbeError("ownership regex patterns exceed their declared input bounds")
    size = 2
    for pattern in patterns:
        size += len(encoded(pattern)) + 1
        if size > budget.limits.pending_bytes:
            budget.reject("ownership regex pattern batch exceeds its input byte bound")
    return tuple(patterns)


class CommandPatterns:
    """Batch all supported command patterns; reuse only exact completed matches."""

    def __init__(self, budget: ProbeBudget, patterns):
        self.budget = budget
        self.patterns = _patterns(budget, patterns)
        self.matches = {}
        budget.charge("cache", len(encoded(self.patterns)))

    def fullmatch(self, command):
        self.budget.remaining()
        if not isinstance(command, str):
            raise MakeProbeError("ownership regex command must be a string")
        if len(command.encode("utf-8")) > 65536:
            raise MakeProbeError("ownership regex command exceeds its byte bound")
        if command not in self.matches:
            indices = evaluate(self.budget, "fullmatch", {
                "patterns": self.patterns, "command": command,
            }) if self.patterns else ()
            self.budget.charge("cache", len(command.encode("utf-8")) + len(encoded(indices)))
            self.matches[command] = indices
        return self.matches[command]


WORKER = r"""
import json,resource,sys
resource.setrlimit(resource.RLIMIT_AS,(int(sys.argv[2]),int(sys.argv[2])))
operation,limit=sys.argv[1],int(sys.argv[3])
def phase(name):
    print(json.dumps({"phase":name,"address_space_bytes":resource.getrlimit(resource.RLIMIT_AS)[0]},
                     separators=(",",":")),flush=True)
try:
    raw=sys.stdin.buffer.read(limit+1)
    if len(raw)>limit: raise ValueError("input byte bound")
    payload=json.loads(raw)
    if operation=="schema":
        phase("schema-start")
        sys.path.insert(0,sys.argv[4])
        from scripts.validation_ownership.reporter import _validate_json_schema
        started=[False]
        def pattern_started():
            if not started[0]:
                started[0]=True
                phase("schema-pattern-start")
        _validate_json_schema(*payload,_pattern_started=pattern_started)
        result={"ok":True}
    else:
        import re
        phase("compile-start")
        flags=re.DOTALL if operation=="fullmatch" else 0
        patterns=[re.compile(pattern,flags) for pattern in payload["patterns"]]
        if operation=="fullmatch":
            phase("match-start")
            result={"ok":True,"indices":[index for index,pattern in enumerate(patterns)
                                       if pattern.fullmatch(payload["command"]) is not None]}
        else:
            result={"ok":True}
    print(json.dumps(result,sort_keys=True,separators=(",",":")),flush=True)
except Exception as error:
    print(json.dumps({"ok":False,"error":type(error).__name__+": "+str(error)[:1024]},
                     sort_keys=True,separators=(",",":")),flush=True)
    raise SystemExit(1)
"""
