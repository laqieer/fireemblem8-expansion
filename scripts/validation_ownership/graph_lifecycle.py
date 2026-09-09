"""Artifact lifecycle checks using the already measured report model."""

from pathlib import Path
import tempfile

from .authority import encoded, parse_json
from .budget import MakeProbeError


def check(artifact_root, check_id, *, session, graph, schema, oracle, model):
    from . import reporter

    if check_id not in reporter.LIFECYCLE_CHECKS:
        raise MakeProbeError("lifecycle check is not allowlisted")
    if session is None or session.base is None or model.get("graph") != graph:
        raise MakeProbeError("lifecycle requires the active validated report model")
    artifact_root = Path(artifact_root).resolve(strict=True)
    allowed = (
        session.base,
        session.loader.root / "build/test-artifacts/validation-ownership",
    )
    if not any(root == artifact_root or root in artifact_root.parents for root in allowed):
        raise MakeProbeError("lifecycle artifact root is outside the owned report workspace")
    artifact = artifact_root / reporter.GRAPH_PATH
    if not artifact.is_file() or artifact.is_symlink():
        raise MakeProbeError(
            "validation ownership graph artifact is missing: " + reporter.LIFECYCLE_FAILURE_REASON
        )
    actual = parse_json(session.budget.read_bytes(artifact, "control"), "lifecycle graph")
    reporter.validate_json_schema(actual, schema, schema, budget=session.budget)
    if actual != graph:
        raise MakeProbeError("lifecycle artifact differs from the measured graph")
    reporter.validate_probe_oracle(oracle, actual, model["entries"])
    reporter._measure(oracle, actual, model)
    if check_id != "validation-ownership-check" and check_id not in reporter._load_test_case_registry(session.loader):
        raise MakeProbeError("ownership consistency tester case is stale")
    return 0


def prove(root, graph, *, session, schema, oracle, model):
    from . import reporter

    if session is None or session.base is None:
        raise MakeProbeError("lifecycle proof requires the report's active session")
    triggers = {event["id"]: event for event in graph["lifecycle_events"]
                if event["type"] in reporter.REQUIRED_PROOF_KINDS}
    proofs = [event for event in graph["lifecycle_events"] if event["type"] == "deletion_proof"]
    checks = (graph["artifact"]["executable_consumer"], graph["artifact"]["consistency_check"])
    results = []
    for event in proofs:
        with tempfile.TemporaryDirectory(prefix="graph-lifecycle-", dir=session.base) as directory:
            artifact_root = Path(directory)
            path = artifact_root / reporter.GRAPH_PATH
            path.parent.mkdir()
            payload = encoded(graph)
            session.budget.charge("control", len(payload))
            path.write_bytes(payload)
            for check_id in checks:
                check(artifact_root, check_id, session=session, graph=graph,
                      schema=schema, oracle=oracle, model=model)
            backup = artifact_root / "graph.backup"
            path.replace(backup)
            try:
                for check_id in checks:
                    try:
                        check(artifact_root, check_id, session=session, graph=graph,
                              schema=schema, oracle=oracle, model=model)
                    except MakeProbeError as error:
                        if reporter.LIFECYCLE_FAILURE_REASON not in str(error):
                            raise
                    else:
                        raise MakeProbeError("lifecycle artifact removal did not fail: " + check_id)
            finally:
                backup.replace(path)
            for check_id in checks:
                check(artifact_root, check_id, session=session, graph=graph,
                      schema=schema, oracle=oracle, model=model)
        results.append({
            "trigger_event_id": event["trigger_event_id"],
            "trigger_type": triggers[event["trigger_event_id"]]["type"],
            "proof_id": event["id"], "removal": "fail",
            "reason": reporter.LIFECYCLE_FAILURE_REASON, "restoration": "pass",
        })
    return results
