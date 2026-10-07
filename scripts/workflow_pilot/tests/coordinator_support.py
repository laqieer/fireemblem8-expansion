"""Pure coordinator fixture values shared by isolated and host test runners."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scripts.workflow_pilot import adaptive_gate as gate


def at_offset(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def decisions(number=191, risks=("none",), mode="concurrent", *, paused=False):
    return {"schema_version": 1, "artifacts": [], "pull_requests": [{
        "pull_request": number, "risk_boundaries": list(risks), "gate_mode": mode,
        "threshold": {"triggers": ["none"], "override_history": []},
        "stack": {"depth": 0, "parent_pr": None, "exception_reason": None},
        "pilot": {"included": False, "disposition": "paused" if paused else "excluded"},
    }]}


def model_control(decision, pr):
    """Explicit typed observation for reducer-only fixtures, not provider evidence."""
    return replace(decision, control=gate.PilotControl(
        pr.repository, pr.repository_id, "master", pr.base_sha, "c" * 40, False, at_offset(-150)))
