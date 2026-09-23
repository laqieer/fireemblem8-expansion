"""Hosted-only outer owner for the disposable diagnostic and its evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import pwd
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import kernel, policy, runtime_view, volume_mount
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kernel
    import policy
    import runtime_view
    import volume_mount


HERE = Path(__file__).resolve().parent
LIFECYCLE = HERE.parent / "validation_ownership/lifecycle.py"
CGROOT = Path("/sys/fs/cgroup")
ROOT_ENV = {**policy.CLEAN_ENV, "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
PREPARATION_SHA = "4dcbcb7e462a3d0953fea5b54d29c30954193ea7"
RETAINED_HARNESS_SHA = "1a2d177749cec443c05021855e4f006cdae821f1"
ROOT18_HARNESS_SHA = "e4c42d0f831806e4ecf1587ef7cbb977a7ff57e8"
REVIEWED_HARNESS_SHA = "f50cbd175b02aef847e344c84154f6574aa5e457"
COMPONENT_BASE_SHA = "f00bc2610d7031d14268855deb2979682ea196af"
CORRECTION_BASE_SHA = "b854e3cd466166dbc79bfe8f717af956b424365c"
REPORT_BASE_SHA = "d7172b7f6adf5cb43c005ba6cb7dc31142cc812b"
REPORT_PREPARATION_SHA = "246bad229efe8674ece41b0b91af89fcc4cb4585"
REPORT_ERROR_SHA = "c8f2fdeac66f5c50ed4859396765ec12f6f462d0"
REPORT_FINALIZATION_SHA = "4dad318411d6e191d79db1a3570d611f5464afad"
REPORT_ACCOUNTING_SHA = "ae3fd7a9589e50903fbc88a8df72baaaea2d0423"
REPORT_TELEMETRY_SHA = "e4034683d4fc68871847c58a2130d954c03370fc"
REPORT_REBIND_SHA = "4cc7c1635927d9ce8785704ed4780a9c18b6b592"
REPORT_LOCALIZATION_SHA = "1fdd209ed4c823dec55a01fb6790504090545579"
REPORT_REGISTRATION_SHA = "1e6a767673e96f546bf9f2bc70b2e312e2fd0105"
REPORT_CODE_METADATA_SHA = "4360e557a843f0feee214918bc0d9320127d5817"
CORRECTED_REPORT_SHA = "6c2bc38fe17ada38a21ec6698373c725fd8f32fe"
PYTHON_REPORT_SHA = "440cbb613857c7802d19ce7dc55b03dd29941b04"
PARTIAL_ANCHOR_SHA = "43feaf98a048390a36b1b380e97f7b8b64ce8b69"
TRACELESS_ANCHOR_SHA = "3d1e5ac5416be38906148a87ee20bf44dbb63dbd"
ORIGINAL_IMPORT_SHA = "64e2e9ccaff88ca7ccdfcb1dc750fb35ea057af9"
IMPORT_RELEASE_SHA = "ed693cd52e1ad202c0f30d04e13e2582389fbd23"
MAKE_CONTEXT_SHA = "b7f4ca2d6b4d2d4777a8913ee7bd2cfec4e9695f"
MAKE_BOUNDARY_SHA = "f8144879c4a36fa4e3afa7645b41140234aacb94"
RUNTIME_REPORT_SHA = "f5a70aaf62c44e43b5f12dc7f78577c8a3fb292c"
COMPONENT_PATHS = frozenset({
    policy.COMPONENT_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "worker.py", "root_stage.py", "supervisor.py", "observation_failure.py", "README.md",
        "test_ci_calibration.py", "test_root_stage.py", "test_observation_failure.py",
    )),
})
CORRECTION_PATHS = frozenset(f"scripts/ci_calibration/{name}" for name in (
    "supervisor.py", "root_stage.py", "worker.py", "policy.py", "README.md",
    "test_ci_calibration.py", "test_root_stage.py",
))
REPORT_PATHS = (COMPONENT_PATHS - {policy.COMPONENT_WORKFLOW}) | {policy.FULL_REPORT_WORKFLOW}
REPORT_ERROR_PATHS = frozenset(f"scripts/ci_calibration/{name}" for name in (
    "policy.py", "worker.py", "supervisor.py", "README.md",
    "test_ci_calibration.py", "test_root_stage.py", "test_observation_failure.py",
))
ACCOUNTING_PATHS = REPORT_PATHS
SOURCE_REBIND_PATHS = frozenset({
    policy.FULL_REPORT_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "supervisor.py", "README.md", "test_ci_calibration.py",
    )),
})
LOCALIZATION_PATHS = frozenset({
    policy.LOCALIZATION_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "worker.py", "supervisor.py", "observation_failure.py", "README.md",
        "test_ci_calibration.py", "test_root_stage.py", "test_observation_failure.py",
    )),
})
REGISTRATION_PATHS = frozenset(f"scripts/ci_calibration/{name}" for name in (
    "observation_failure.py", "supervisor.py", "README.md",
    "test_ci_calibration.py", "test_observation_failure.py",
))
CORRECTED_REPORT_PATHS = frozenset({
    policy.CORRECTED_REPORT_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "supervisor.py", "README.md", "test_ci_calibration.py",
    )),
})
PYTHON_REPORT_PATHS = (CORRECTED_REPORT_PATHS - {policy.CORRECTED_REPORT_WORKFLOW}) | {policy.PYTHON_REPORT_WORKFLOW}
PARTIAL_ANCHOR_PATHS = frozenset({
    policy.PARTIAL_ANCHOR_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "observation_failure.py", "policy.py", "supervisor.py", "README.md",
        "test_ci_calibration.py", "test_observation_failure.py",
    )),
})
TRACELESS_ANCHOR_PATHS = (PARTIAL_ANCHOR_PATHS - {policy.PARTIAL_ANCHOR_WORKFLOW}) | {policy.TRACELESS_ANCHOR_WORKFLOW}
ORIGINAL_IMPORT_PATHS = (COMPONENT_PATHS - {policy.COMPONENT_WORKFLOW}) | {policy.ORIGINAL_IMPORT_WORKFLOW}
IMPORT_RELEASE_PATHS = frozenset(f"scripts/ci_calibration/{name}" for name in (
    "observation_failure.py", "root_stage.py", "worker.py", "supervisor.py",
    "test_ci_calibration.py", "test_root_stage.py", "README.md",
))
MAKE_CONTEXT_PATHS = frozenset({
    policy.MAKE_CONTEXT_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "observation_failure.py", "policy.py", "supervisor.py", "README.md",
        "test_ci_calibration.py", "test_root_stage.py", "test_observation_failure.py",
    )),
})
MAKE_BOUNDARY_PATHS = frozenset(f"scripts/ci_calibration/{name}" for name in (
    "observation_failure.py", "supervisor.py", "README.md",
    "test_ci_calibration.py", "test_observation_failure.py",
))
RUNTIME_REPORT_PATHS = frozenset({
    policy.RUNTIME_REPORT_WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "supervisor.py", "test_ci_calibration.py", "README.md",
    )),
})
APPEND_REPORT_PATHS = frozenset({
    policy.WORKFLOW, *(f"scripts/ci_calibration/{name}" for name in (
        "policy.py", "supervisor.py", "test_ci_calibration.py", "README.md",
    )),
})


def validate_harness_lineage(lines, head):
    if lines != [
        f"{head} {RUNTIME_REPORT_SHA}",
        f"{RUNTIME_REPORT_SHA} {MAKE_BOUNDARY_SHA}",
        f"{MAKE_BOUNDARY_SHA} {MAKE_CONTEXT_SHA}",
        f"{MAKE_CONTEXT_SHA} {IMPORT_RELEASE_SHA}",
        f"{IMPORT_RELEASE_SHA} {ORIGINAL_IMPORT_SHA}",
        f"{ORIGINAL_IMPORT_SHA} {TRACELESS_ANCHOR_SHA}",
        f"{TRACELESS_ANCHOR_SHA} {PARTIAL_ANCHOR_SHA}",
        f"{PARTIAL_ANCHOR_SHA} {PYTHON_REPORT_SHA}",
        f"{PYTHON_REPORT_SHA} {CORRECTED_REPORT_SHA}",
        f"{CORRECTED_REPORT_SHA} {REPORT_CODE_METADATA_SHA}",
        f"{REPORT_CODE_METADATA_SHA} {REPORT_REGISTRATION_SHA}",
        f"{REPORT_REGISTRATION_SHA} {REPORT_LOCALIZATION_SHA}",
        f"{REPORT_LOCALIZATION_SHA} {REPORT_REBIND_SHA}",
        f"{REPORT_REBIND_SHA} {REPORT_TELEMETRY_SHA}",
        f"{REPORT_TELEMETRY_SHA} {REPORT_ACCOUNTING_SHA}",
        f"{REPORT_ACCOUNTING_SHA} {REPORT_FINALIZATION_SHA}",
        f"{REPORT_FINALIZATION_SHA} {REPORT_ERROR_SHA}",
        f"{REPORT_ERROR_SHA} {REPORT_PREPARATION_SHA}",
        f"{REPORT_PREPARATION_SHA} {REPORT_BASE_SHA}",
        f"{REPORT_BASE_SHA} {CORRECTION_BASE_SHA}",
        f"{CORRECTION_BASE_SHA} {COMPONENT_BASE_SHA}",
        f"{COMPONENT_BASE_SHA} {REVIEWED_HARNESS_SHA}",
        f"{REVIEWED_HARNESS_SHA} {ROOT18_HARNESS_SHA}",
        f"{ROOT18_HARNESS_SHA} {RETAINED_HARNESS_SHA}",
        f"{RETAINED_HARNESS_SHA} {PREPARATION_SHA}",
        f"{PREPARATION_SHA} {policy.BASE}",
    ]:
        raise policy.GuardError("diagnostic requires its exact normal append-report/runtime-report/make-boundary/make-context/import-release/original-import/traceless/partial-anchor/Python-corrected/corrected-report/code-metadata/registration/localization/rebind/telemetry/accounting/finalization/error-correction/report/correction/component/root20/root19/root18/root17/preparation/BASE lineage")


def validate_correction_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("correction inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("correction requires a nonempty normal modification")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in CORRECTION_PATHS}
    if (
        any(kind != b"M" or name not in allowed for kind, name in changes)
        or len({name for _, name in changes}) != len(changes)
    ):
        raise policy.GuardError("correction changed an unfrozen surface")


def validate_report_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("report inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("report preparation requires a complete nonempty inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.FULL_REPORT_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in REPORT_PATHS}
    if (
        (b"A", workflow) not in changes
        or any(name not in allowed or kind != (b"A" if name == workflow else b"M") for kind, name in changes)
        or len({name for _, name in changes}) != len(changes)
    ):
        raise policy.GuardError("report preparation changed a closed or unallocated surface")


def validate_report_error_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("report error correction inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("report error correction requires nonempty normal modifications")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in REPORT_ERROR_PATHS}
    if any(kind != b"M" or name not in allowed for kind, name in changes) or (
        len({name for _, name in changes}) != len(changes)
    ):
        raise policy.GuardError("report error correction changed an unfrozen surface")


def validate_accounting_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("accounting inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("accounting preparation requires nonempty normal modifications")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in ACCOUNTING_PATHS}
    if (
        (b"M", policy.FULL_REPORT_WORKFLOW.encode("ascii")) not in changes
        or any(kind != b"M" or name not in allowed for kind, name in changes)
        or len({name for _, name in changes}) != len(changes)
    ):
        raise policy.GuardError("accounting preparation changed an unallocated surface")


def validate_accounting_workflow(before, after):
    old = b"        ref: d5337fc1db5b36f701328cad7c2444384dc88bb5\n"
    new = b"        ref: " + policy.LOCALIZATION_SOURCE.encode("ascii") + b"\n"
    if type(before) is not bytes or type(after) is not bytes or before.count(old) != 1 or (
        after != before.replace(old, new, 1)
    ):
        raise policy.GuardError("accounting workflow may change only its exact immutable source binding")


def validate_source_rebind_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("source rebind inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("source rebind requires nonempty normal modifications")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in SOURCE_REBIND_PATHS}
    if any(kind != b"M" for kind, _ in changes) or (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
    ):
        raise policy.GuardError("source rebind changed or omitted a frozen identity surface")


def validate_localization_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("localization inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("localization requires a complete nonempty inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.LOCALIZATION_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in LOCALIZATION_PATHS}
    if (
        (b"A", workflow) not in changes
        or any(name not in allowed or kind != (b"A" if name == workflow else b"M") for kind, name in changes)
        or {name for _, name in changes} != allowed
        or len({name for _, name in changes}) != len(changes)
    ):
        raise policy.GuardError("localization changed a closed workflow or unallocated surface")


def validate_registration_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("registration correction inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("registration correction requires complete normal modifications")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in REGISTRATION_PATHS}
    if any(kind != b"M" for kind, _ in changes) or (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
    ):
        raise policy.GuardError("registration correction changed or omitted a frozen surface")


def validate_corrected_report_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("corrected report inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("corrected report preparation requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.CORRECTED_REPORT_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in CORRECTED_REPORT_PATHS}
    if (
        (b"A", workflow) not in changes
        or {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("corrected report changed a spent workflow or unallocated mechanism")


def validate_python_report_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("Python-corrected report inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("Python-corrected report requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.PYTHON_REPORT_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in PYTHON_REPORT_PATHS}
    if (
        (b"A", workflow) not in changes
        or {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("Python-corrected report changed a spent workflow or unallocated mechanism")


def validate_partial_anchor_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("partial-anchor inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("partial-anchor preparation requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.PARTIAL_ANCHOR_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in PARTIAL_ANCHOR_PATHS}
    if (
        (b"A", workflow) not in changes
        or {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("partial anchors changed a spent workflow or unallocated mechanism")


def validate_traceless_anchor_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("traceless-anchor inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("traceless anchors require their complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.TRACELESS_ANCHOR_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in TRACELESS_ANCHOR_PATHS}
    if (
        (b"A", workflow) not in changes
        or {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("traceless anchors changed a spent workflow or unallocated mechanism")


def validate_original_import_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("original-import inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("original-import observation requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    workflow = policy.ORIGINAL_IMPORT_WORKFLOW.encode("ascii")
    allowed = {name.encode("ascii") for name in ORIGINAL_IMPORT_PATHS}
    if (
        (b"A", workflow) not in changes
        or {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("original-import observation changed a spent workflow or unallocated surface")


def validate_import_release_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("import-release inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("import-release correction requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in IMPORT_RELEASE_PATHS}
    if (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != b"M" for kind, name in changes)
    ):
        raise policy.GuardError("import-release correction changed a workflow or unallocated surface")


def validate_make_context_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("Make context inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("Make context requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in MAKE_CONTEXT_PATHS}
    workflow = policy.MAKE_CONTEXT_WORKFLOW.encode("ascii")
    if (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("Make context changed a spent workflow or unallocated surface")


def validate_make_boundary_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("Make boundary inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("Make boundary correction requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in MAKE_BOUNDARY_PATHS}
    if (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != b"M" for kind, name in changes)
    ):
        raise policy.GuardError("Make boundary correction changed a workflow or unallocated surface")


def validate_runtime_report_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("runtime-corrected report inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("runtime-corrected report requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in RUNTIME_REPORT_PATHS}
    workflow = policy.RUNTIME_REPORT_WORKFLOW.encode("ascii")
    if (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("runtime-corrected report changed a spent workflow or unallocated surface")


def validate_append_report_inventory(data):
    if type(data) is not bytes:
        raise policy.GuardError("append-corrected report inventory is not a Git byte record")
    rows = data.split(b"\0")
    if len(rows) < 3 or len(rows) % 2 != 1 or rows[-1] != b"":
        raise policy.GuardError("append-corrected report requires its complete fixed inventory")
    changes = list(zip(rows[:-1:2], rows[1:-1:2]))
    allowed = {name.encode("ascii") for name in APPEND_REPORT_PATHS}
    workflow = policy.WORKFLOW.encode("ascii")
    if (
        {name for _, name in changes} != allowed or len(changes) != len(allowed)
        or any(kind != (b"A" if name == workflow else b"M") for kind, name in changes)
    ):
        raise policy.GuardError("append-corrected report changed a spent workflow or unallocated surface")


def apparmor_text(name):
    if not re.fullmatch(r"vo-ci180-[1-9][0-9]{0,19}", name):
        raise policy.GuardError("invalid process-only AppArmor profile name")
    return f"abi <abi/4.0>,\nprofile {name} flags=(default_allow) {{\n  userns,\n}}\n"


def io_peaks(previous, current, elapsed, peaks):
    if elapsed <= 0:
        raise policy.GuardError("nonmonotonic I/O sampling clock")
    for device, values in current.items():
        for key in ("rbytes", "wbytes", "rios", "wios", "dbytes", "dios"):
            difference = values.get(key, 0) - previous.get(device, {}).get(key, 0)
            if difference < 0:
                raise policy.GuardError("nonmonotonic cgroup I/O accounting")
            name = device + "/" + key + "_per_second"
            peaks[name] = max(peaks.get(name, 0), difference / elapsed)


def tool(argv, *, timeout=60, maximum=policy.OUTPUT_BYTES, allowed=(0,)):
    reader, writer = os.pipe2(os.O_CLOEXEC)
    child = None
    buffers = [bytearray(), bytearray()]
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-S", "-B", str(LIFECYCLE), str(deadline), "--", *argv],
            stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ROOT_ENV, close_fds=True,
        )
        os.close(reader)
        reader = None
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            total = 0
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise policy.GuardError("trusted setup command exhausted its deadline")
                for key, _ in selector.select(0.05):
                    data = os.read(key.fd, min(65536, maximum - total + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(data)
                    if total > maximum:
                        raise policy.GuardError("trusted setup output exceeded its bound")
                    buffers[key.data].extend(data)
        code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        if code not in allowed:
            detail = bytes(buffers[1]).decode("utf-8", "replace")
            if len(detail.encode()) > policy.ERROR_BYTES:
                raise policy.GuardError("trusted setup failure evidence exceeded its bound")
            raise policy.GuardError(f"trusted setup failed ({argv[0]}, {code}): {detail}")
        return code, bytes(buffers[0])
    finally:
        os.close(writer)
        if reader is not None:
            os.close(reader)
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise policy.GuardError("trusted setup cleanup is unconfirmed; retain owned state") from error
            child.stdout.close()
            child.stderr.close()


def git(root, *arguments, allowed=(0,), safe_children=()):
    return tool([
        "/usr/bin/git", "--no-replace-objects", "--no-optional-locks", "-C", str(root),
        "-c", "safe.directory=" + str(root),
        *(argument for path in safe_children for argument in ("-c", "safe.directory=" + str(root / path))),
        "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "-c", "core.preloadindex=false", "-c", "diff.external=",
        *arguments,
    ], maximum=32 * policy.MIB, allowed=allowed)[1]


class Artifacts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o755, parents=False, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise policy.GuardError("artifact root is not an owned directory")
        self.root.chmod(0o755)

    def _capacity(self, name, size, append):
        if name not in policy.ARTIFACT_NAMES:
            raise policy.GuardError("artifact is not in the upload allowlist")
        before = self.root / name
        if before.is_symlink() or before.exists() and not stat.S_ISREG(before.lstat().st_mode):
            raise policy.GuardError("artifact destination is not a regular owned record")
        current = before.stat().st_size if before.exists() else 0
        maximum = (
            policy.METRICS_BYTES if name == "metrics.jsonl" else
            policy.PROGRESS_BYTES if name == "progress.jsonl" else policy.OUTPUT_BYTES
        )
        if size + (current if append else 0) > maximum:
            raise policy.GuardError("diagnostic artifact exceeded its individual bound")
        total = sum(path.stat().st_size for path in self.root.iterdir() if path.name in policy.ARTIFACT_NAMES)
        if total + size - (0 if append else current) > policy.ARTIFACT_BYTES:
            raise policy.GuardError("diagnostic artifacts exceeded their total bound")

    def write(self, name, value):
        data = policy.encoded(value) + b"\n"
        self._capacity(name, len(data), False)
        path = self.root / name
        temporary = self.root / (name + ".tmp")
        created = False
        try:
            with temporary.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            if created:
                temporary.unlink(missing_ok=True)

    def append(self, name, value):
        data = policy.encoded(value) + b"\n"
        self._capacity(name, len(data), True)
        path = self.root / name
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o644)


def capacity_facts(directory):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise policy.GuardError("hosted Linux x86-64 is required")
    release = dict(
        line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
    )
    if release.get("ID", "").strip('"') != "ubuntu":
        raise policy.GuardError("only the selected ubuntu-latest runner is supported")
    if kernel.filesystem_type(CGROOT) != kernel.CGROUP2_MAGIC:
        raise policy.GuardError("cgroup-v2 is unavailable")
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    cgroup_mounts = [row for row in mounts if row[4] == str(CGROOT)]
    if len(cgroup_mounts) != 1 or cgroup_mounts[0][3] != "/" or "rw" not in cgroup_mounts[0][5].split(","):
        raise policy.GuardError("requires writable full-host cgroup-v2, not a delegated/container view")
    mount_record = cgroup_mounts[0]
    if "nsdelegate" not in mount_record[mount_record.index("-") + 3].split(","):
        raise policy.GuardError("existing cgroup namespace delegation protection is required; no remount fallback")
    if kernel.read("/proc/1/comm").strip() != b"systemd":
        raise policy.GuardError("requires the disposable hosted VM, not a job container")
    values = {}
    for line in kernel.read("/proc/meminfo").decode().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}:
            amount, unit = value.split()
            if unit != "kB":
                raise policy.GuardError("unsupported memory observation unit")
            values[key] = int(amount) * 1024
    current = CGROOT / kernel.membership().lstrip("/")
    ancestors = []
    while True:
        row = {"path": str(current)}
        for name in ("memory", "pids"):
            maximum = current / (name + ".max")
            observed = current / (name + ".current")
            row[name + "_max"] = kernel.number(maximum, maximum=True) if maximum.exists() else None
            row[name + "_current"] = kernel.number(observed) if observed.exists() else 0
            if current != CGROOT and (not maximum.exists() or not observed.exists()):
                raise policy.GuardError("ancestor resource controllers are not observable")
        ancestors.append(row)
        if current == CGROOT:
            break
        current = current.parent
    load = kernel.read("/proc/loadavg", 4096).decode().split()
    threads_current = int(load[3].split("/")[1])
    disk = os.statvfs(directory)
    return {
        "memory_total": values["MemTotal"], "memory_available": values["MemAvailable"],
        "disk_available": disk.f_bavail * disk.f_frsize, "cpus": len(os.sched_getaffinity(0)),
        "threads_max": kernel.number("/proc/sys/kernel/threads-max"),
        "threads_current": threads_current, "cgroup_ancestors": ancestors,
        "kernel": platform.release(), "ubuntu": release.get("VERSION_ID", "").strip('"'),
        "cgroup_nsdelegate": True,
    }


class Cgroup:
    def __init__(self, name, memory, pids):
        self.path = CGROOT / name
        if not name.startswith("vo-ci180-") or self.path.parent != CGROOT:
            raise policy.GuardError("invalid cgroup ownership name")
        self.memory, self.pids = memory, pids
        self.created = False

    def prepare(self):
        self.path.mkdir(mode=0o755)
        self.created = True
        for field, value in (("memory.max", self.memory), ("memory.swap.max", 0), ("memory.oom.group", 1), ("pids.max", self.pids)):
            (self.path / field).write_text(str(value))
            if kernel.number(self.path / field) != value:
                raise policy.GuardError("cgroup limit write did not become effective")
        for name in (*policy.CGROUP_FILES, "cgroup.kill"):
            info = (self.path / name).lstat()
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode):
                raise policy.GuardError("required root-owned cgroup control is unavailable")

    def snapshot(self):
        io = {}
        for line in kernel.read(self.path / "io.stat").decode().splitlines():
            parts = line.split()
            io[parts[0]] = {key: int(value) for key, value in (item.split("=") for item in parts[1:])}
        return {
            "memory_current": kernel.number(self.path / "memory.current"),
            "memory_peak": kernel.number(self.path / "memory.peak"),
            "memory_events": kernel.fields(self.path / "memory.events"),
            "pids_current": kernel.number(self.path / "pids.current"),
            "pids_peak": kernel.number(self.path / "pids.peak"),
            "pids_events": kernel.fields(self.path / "pids.events"),
            "cpu": kernel.fields(self.path / "cpu.stat"), "io": io,
            "populated": kernel.fields(self.path / "cgroup.events")["populated"],
        }

    def empty(self):
        return kernel.fields(self.path / "cgroup.events")["populated"] == 0 and not kernel.read(self.path / "cgroup.procs").strip()

    def kill(self):
        (self.path / "cgroup.kill").write_text("1")

    def remove(self):
        if not self.created:
            return
        if not self.empty():
            raise policy.GuardError("cgroup remains populated; preserve its resources")
        self.path.rmdir()
        self.created = False


class Volume:
    def __init__(self, directory, size, uid, gid):
        self.image = directory / "workspace.img"
        self.mountpoint = directory / "volume"
        self.size = size
        self.device = None
        self.mounted = False
        self.closed = False
        self.uncertain_device = False
        self.uid, self.gid = uid, gid
        self.filesystem_device = None
        self.mount_request = directory / "mount.json"
        self.mount_id = 0

    def mount_control(self, action):
        result = policy.parse_json(tool([
            "/usr/bin/python3", "-I", "-S", "-B", str(HERE / "volume_mount.py"),
            action, str(self.mount_request), str(self.mount_id),
        ], timeout=15)[1])
        if not isinstance(result, dict) or set(result) != {"state", "mount_id", "filesystem_device"}:
            raise policy.GuardError("volume helper returned an invalid observation")
        if result["state"] not in {"mounted", "absent"} or type(result["filesystem_device"]) is not int:
            raise policy.GuardError("volume state is not confirmed")
        if result["state"] == "mounted":
            if type(result["mount_id"]) is not int or result["mount_id"] <= 0:
                raise policy.GuardError("volume mount identity is invalid")
            self.mount_id = result["mount_id"]
        elif result["mount_id"] is not None:
            raise policy.GuardError("absent volume has a false mount identity")
        return result

    def prepare(self):
        self.mountpoint.mkdir()
        with self.image.open("xb"):
            pass
        tool(["/usr/bin/fallocate", "--length", str(self.size), str(self.image)])
        tool(["/usr/sbin/mkfs.ext4", "-q", "-F", "-m", "0", "-E",
              "lazy_itable_init=0,lazy_journal_init=0", str(self.image)], timeout=60)
        self.uncertain_device = True
        device = tool(["/usr/sbin/losetup", "--find", "--show", "--nooverlap", str(self.image)])[1].decode().strip()
        if not device.startswith("/dev/loop") or not device.removeprefix("/dev/loop").isdigit():
            raise policy.GuardError("loop allocation did not return an owned loop device")
        self.device = device
        request = volume_mount.make_spec(self.image, device, self.mountpoint, self.uid, self.gid)
        self.mount_request.write_bytes(policy.encoded(request))
        self.mount_request.chmod(0o444)
        self.uncertain_device = False
        self.mounted = None
        result = self.mount_control("mount")
        if result["state"] != "mounted":
            raise policy.GuardError("volume mount was not confirmed")
        self.mounted = True
        os.chown(self.mountpoint, self.uid, self.gid)
        self.filesystem_device = self.mountpoint.stat().st_dev
        observed = os.statvfs(self.mountpoint)
        if observed.f_blocks * observed.f_frsize > self.size:
            raise policy.GuardError("workspace filesystem exceeds its allocated image")

    def close(self):
        if self.closed:
            return
        if self.uncertain_device:
            raise policy.GuardError("loop allocation identity is unconfirmed; preserve its image")
        if self.mounted is not False:
            result = self.mount_control("inspect")
            if result["state"] == "mounted":
                result = self.mount_control("unmount")
            if result["state"] != "absent":
                raise policy.GuardError("volume cleanup remains uncertain")
            self.mounted = False
        if self.device is not None:
            tool(["/usr/sbin/losetup", "--detach", self.device], timeout=15)
            self.device = None
        self.image.unlink(missing_ok=True)
        self.mount_request.unlink(missing_ok=True)
        if self.mountpoint.exists():
            self.mountpoint.rmdir()
        self.closed = True


class OutputLimitExceeded(policy.GuardError):
    pass


class Protocol:
    def __init__(self, scope, maximum, *, raw_after_ready=False, report_binding=None, deadline=None):
        self.scope, self.maximum = scope, maximum
        self.total = 0
        self.stderr_total = 0
        self.output_exceeded = False
        self.buffer = bytearray()
        self.ready = False
        self.raw_after_ready = raw_after_ready
        self.finished = False
        self.report_binding, self.deadline = report_binding, deadline
        self.report_started = self.failed = False
        self.report_error = None
        self.report_error_records = 0
        self.report_result = None
        self.result_error_seen = False
        self.accounting_sequence = 0
        self.accounting_elapsed = 0
        self.accounting_values = self.accounting_quotas = None
        self.accounting_crossings = {}
        self.accounting_failure = None
        if report_binding is not None:
            policy.validate_report_binding(report_binding)
            if scope != report_binding["run_id"] + "/report" or (
                type(deadline) not in (int, float) or not 0 < deadline < float("inf")
            ) or raw_after_ready:
                raise policy.GuardError("report stream lacks its exact run and clock binding")

    def observe_output(self, data, *, stderr=False):
        if stderr:
            self.stderr_total += len(data)
        else:
            self.total += len(data)
        if self.total + self.stderr_total > self.maximum:
            self.output_exceeded = True
            raise OutputLimitExceeded("external combined stdout/stderr bound exceeded")

    def observe_accounting(self, value, counters, *, final=False):
        if self.accounting_failure is not None:
            raise self.accounting_failure
        try:
            self._observe_accounting(value, counters, final=final)
        except BaseException as error:
            raise self._accounting_error(error)

    def _accounting_error(self, error):
        if self.accounting_failure is None:
            self.accounting_failure = error
        self.finished = False
        return self.accounting_failure

    def _observe_accounting(self, value, counters, *, final=False):
        policy.validate_component_counters(counters)
        if value is None:
            if final or self.accounting_values is not None or counters["budget"] is not None:
                raise policy.GuardError("established or required accounting observation disappeared")
            return
        sequence, elapsed = value["sequence"], value["elapsed_seconds"]
        observed = policy.accounting_values(counters)
        quotas = counters["budget"]["quotas"]
        if sequence <= self.accounting_sequence or elapsed < self.accounting_elapsed or (
            self.accounting_quotas is not None and self.accounting_quotas != quotas
        ):
            raise policy.GuardError("accounting stream changed quotas, regressed or replayed a sample")
        if self.accounting_values is not None and any(
            before is not None and (observed[name] is None or observed[name] < before)
            for name, before in self.accounting_values.items()
        ):
            raise policy.GuardError("accounting stream decreased or lost an observed counter")
        crossings = {
            name: row["first_observed"] for name, row in value["records"].items()
            if row["first_observed"] is not None
        } if final else value["first_observed"]
        if final:
            if any(crossings.get(name) != row for name, row in self.accounting_crossings.items()) or any(
                name not in self.accounting_crossings and row["sequence"] != sequence
                for name, row in crossings.items()
            ):
                raise policy.GuardError("final accounting omitted or changed first-observed crossings")
        elif crossings.keys() & self.accounting_crossings.keys():
            raise policy.GuardError("accounting stream repeated a first-observed crossing")
        if any(
            number is not None and number > quotas[name]["original"]
            and name not in self.accounting_crossings and name not in crossings
            for name, number in observed.items()
        ):
            raise policy.GuardError("accounting stream omitted an observed quota crossing")
        self.accounting_sequence, self.accounting_elapsed = sequence, elapsed
        self.accounting_values, self.accounting_quotas = observed, quotas
        self.accounting_crossings.update(crossings)

    def feed(self, data):
        self.observe_output(data)
        if self.raw_after_ready and self.ready:
            return []
        self.buffer.extend(data)
        records = []
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            terminal_result = self.report_result is not None
            if terminal_result:
                if self.result_error_seen:
                    raise policy.GuardError("report terminal publication outcome is already closed")
                self.result_error_seen = True
                self.finished = False
            record = policy.parse_json(line)
            if (
                not isinstance(record, dict) or set(record) != {"scope", "kind", "data"}
                or record["scope"] != self.scope or not isinstance(record["data"], dict)
                or record["kind"] not in {"ready", "report-start", "progress", "error", "cleanup-error", "result", "probe-result", "escaped"}
            ):
                raise policy.GuardError("foreign or malformed diagnostic protocol record")
            kind = record["kind"]
            if terminal_result and (kind != "error" or self.failed) or (
                self.finished or not self.ready and kind not in {"ready", "error"}
            ):
                raise policy.GuardError("diagnostic record is out of order")
            if "source_refusal" in record["data"]:
                raise policy.GuardError("report scope cannot publish unrelated root source-refusal metadata")
            if self.report_binding is not None:
                value = record["data"]
                if kind == "report-start":
                    if self.report_started or self.failed:
                        raise policy.GuardError("report stream repeated or resumed its invocation")
                    policy.validate_report_start(value, self.report_binding, self.deadline)
                    self.report_started = True
                elif kind == "result":
                    if not self.report_started or self.failed:
                        raise policy.GuardError("report result precedes invocation or follows a failure")
                    if self.accounting_failure is not None:
                        raise self.accounting_failure
                    try:
                        policy.validate_report_worker(value, self.report_binding, self.deadline)
                        self.observe_accounting(value["counters"]["accounting"], value["counters"]["counters"], final=True)
                    except BaseException as error:
                        raise self._accounting_error(error)
                    self.report_result = value
                elif kind == "progress":
                    if self.accounting_failure is not None:
                        raise self.accounting_failure
                    try:
                        policy.validate_report_progress(value)
                        self.observe_accounting(value["accounting"], value["counters"])
                    except BaseException as error:
                        raise self._accounting_error(error)
                elif kind == "error":
                    if value.keys() == {"chain", "frames"} and not self.ready and not self.failed:
                        value = policy.project_entry_failure(value, self.report_binding)
                        record["data"] = value
                    policy.validate_report_error(value, self.report_binding)
                    if terminal_result:
                        policy.validate_result_publication_failure(
                            value, self.report_result, self.report_binding, self.deadline,
                        )
                    if self.report_error_records >= 2:
                        raise policy.GuardError("report stream exceeded its first publication and one fallback")
                    if self.report_error is None:
                        self.report_error = value
                    else:
                        record["data"] = policy.merge_report_failure(self.report_error, value, self.report_binding)
                    self.report_error_records += 1
                    self.failed = True
                elif kind == "ready" and self.failed:
                    raise policy.GuardError("failed report setup cannot subsequently assert readiness")
                elif kind != "ready":
                    raise policy.GuardError("report stream contains an unallocated workload record")
            if kind == "ready":
                if self.ready:
                    raise policy.GuardError("duplicate containment readiness")
                self.ready = True
            if kind in {"result", "probe-result"}:
                self.finished = True
            records.append(record)
            if self.raw_after_ready and self.ready:
                self.buffer.clear()
                break
        return records


def phase(owner, mode, volume, *, memory, pids, seconds):
    group = Cgroup(f"vo-ci180-{owner.scope['run_id']}-{mode}", memory, pids)
    owner.groups.append(group)
    group.prepare()
    directory = owner.control / mode
    directory.mkdir()
    scope = owner.scope["run_id"] + "/" + mode
    started = time.monotonic()
    deadline = started + seconds
    config = {
        "scope": scope, "mode": mode, "cgroup": str(group.path), "cgroup_relative": "/" + group.path.name,
        "uid": owner.uid, "gid": owner.gid, "memory_max": memory, "pids_max": pids,
        "deadline": deadline, "disk_bytes": volume.size,
        "workspace_device": volume.filesystem_device, "volume": str(volume.mountpoint),
        "candidate": str(owner.candidate), "harness_code": str(HERE),
        "rootfs": str(directory / "rootfs"), "etc": str(owner.etc),
        "namespace_helper": str(owner.harness / "scripts/validation_ownership/sandbox_exec.py"),
        "host_namespaces": kernel.namespaces(), "host_listener_port": owner.listener.getsockname()[1],
        "apparmor_profile": owner.apparmor_profile,
        "tracked_paths": owner.tracked_paths,
        "runtime_manifest": owner.runtime_manifest,
        **({"report_binding": policy.report_binding(owner.scope)} if mode == "report" else {}),
        "cgroup_file_identities": {
            name: [(group.path / name).stat().st_dev, (group.path / name).stat().st_ino]
            for name in policy.CGROUP_FILES
        },
    }
    path = directory / "config.json"
    path.write_bytes(policy.encoded(config))
    path.chmod(0o444)
    argv = [
        "/usr/bin/unshare", "--mount", "--net", "--ipc", "--uts", "--pid", "--fork",
        "--kill-child", "--propagation", "private",
        "/usr/bin/python3", "-I", "-S", "-B", str(HERE / "entry.py"), str(path),
    ]
    if owner.apparmor_profile is not None:
        argv = ["/usr/bin/aa-exec", "-p", owner.apparmor_profile, "--", *argv]
    reader, writer = os.pipe2(os.O_CLOEXEC)
    child = None
    protocol = Protocol(
        scope, 65536 if mode == "output" else policy.OUTPUT_BYTES, raw_after_ready=mode == "output",
        report_binding=config.get("report_binding"), deadline=deadline,
    )
    result = {"mode": mode, "deadline": deadline, "started_at": started,
              "report_starts": 0, "report_check_attempts": None if mode == "report" else 0,
              "report_returned": None, "report_completed": False, **policy.ABSENT_WORKLOADS}
    cause = None
    last_sample = 0
    previous_io = {}
    sampled_io_peaks = {}
    sampled_disk_peak = 0
    lifetime_closed = False
    held = []
    try:
        child = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-S", "-B", str(LIFECYCLE), str(deadline), "--", *argv],
            stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ROOT_ENV, close_fds=True,
        )
        os.close(reader)
        reader = None
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                now = time.monotonic()
                if now - last_sample >= policy.SAMPLE_SECONDS:
                    sample = group.snapshot()
                    disk = os.statvfs(volume.mountpoint)
                    sample["disk_used_bytes"] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
                    sample["disk_available_bytes"] = disk.f_bavail * disk.f_frsize
                    sampled_disk_peak = max(sampled_disk_peak, sample["disk_used_bytes"])
                    if last_sample:
                        io_peaks(previous_io, sample["io"], now - last_sample, sampled_io_peaks)
                    previous_io = sample["io"]
                    owner.artifacts.append("metrics.jsonl", {"scope": scope, "monotonic": now, **sample})
                    last_sample = now
                    if sample["memory_events"].get("oom_kill", 0) and cause is None:
                        cause = {"type": "cgroup-oom", "message": "kernel memory.max/oom.group termination"}
                if now >= deadline and (cause is None or cause.get("type") == "lifetime-eof"):
                    cause = {"type": "deadline", "message": "original absolute phase deadline exhausted"}
                if protocol.output_exceeded or cause is not None and cause.get("type") != "lifetime-eof":
                    group.kill()
                    if writer is not None:
                        os.close(writer)
                        writer = None
                for key, _ in selector.select(0.05):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 1:
                        try:
                            protocol.observe_output(data, stderr=True)
                        except OutputLimitExceeded as error:
                            cause = cause or {"type": "output-bound", "message": str(error)}
                            continue
                        if mode != "report" and not protocol.ready and protocol.stderr_total <= policy.ERROR_BYTES:
                            result["trusted_setup_stderr"] = result.get("trusted_setup_stderr", "") + data.decode("utf-8", "replace")
                        if cause is None:
                            cause = {
                                "type": "deadline" if time.monotonic() >= deadline else
                                "lifetime-eof" if lifetime_closed else "unexpected-stderr",
                                "message": "Expected caller lifetime closure" if lifetime_closed else
                                "Unframed stderr; raw candidate/scratch output is not published",
                            }
                        continue
                    try:
                        records = protocol.feed(data)
                    except OutputLimitExceeded as error:
                        cause = cause or {"type": "output-bound", "message": str(error)}
                        continue
                    except policy.GuardError as error:
                        cause = cause or {"type": "protocol", "message": str(error)}
                        continue
                    for record in records:
                        kind, value = record["kind"], record["data"]
                        if mode == "report" and kind in {"error", "result"}:
                            states = value["report"]["states"] if kind == "result" else value["states"]
                            if states is not None:
                                result["report_check_attempts"] = states["check_attempts"]
                                result["report_returned"] = states["check_returned"] == 1
                                result["report_completed"] = states["completed"]
                                owner.scope["report_attempted"] = states["check_attempts"] == 1
                                owner.scope["report_returned"] = states["check_returned"] == 1
                        if kind == "error":
                            cause = cause or {"type": "worker-error", "error": value}
                        elif kind == "cleanup-error":
                            result.setdefault("cleanup_errors", []).append(value)
                            cause = cause or {"type": "worker-cleanup", "error": value}
                        elif kind == "result":
                            result["worker"] = value
                        elif kind == "probe-result":
                            result["probe"] = value
                        elif kind == "ready":
                            result["identity"] = value
                        elif kind == "report-start":
                            result["report_starts"] += 1
                            if mode != "report" or result["report_starts"] != 1:
                                raise policy.GuardError("report invocation violated its exact single-attempt scope")
                            policy.validate_report_start(value, config["report_binding"], deadline)
                            owner.artifacts.write("scope.json", owner.scope)
                            owner.artifacts.append("progress.jsonl", record)
                        elif kind == "escaped":
                            result["escaped"] = value
                            for pid in kernel.read(group.path / "cgroup.procs").decode().split():
                                held.append(os.pidfd_open(int(pid)))
                            if mode == "lifetime":
                                os.close(writer)
                                writer = None
                                lifetime_closed = True
                        else:
                            owner.artifacts.append("progress.jsonl", record)
                if now > deadline + 10:
                    raise policy.GuardError("watchdog/stream termination is unconfirmed")
        if mode == "report" and (protocol.buffer or not protocol.finished and cause is None):
            raise policy.GuardError("report stream ended without its complete terminal result")
        result["returncode"] = child.wait(timeout=5)
        result["empty_before_outer_cleanup"] = group.empty()
        if time.monotonic() >= deadline and cause is None:
            cause = {"type": "deadline", "message": "phase terminated at the original deadline"}
    except BaseException as error:
        result["supervisor_error"] = (
            policy.component_secondary_error(error) if mode == "report" else policy.error_record(error)
        )
        cause = cause or {"type": "supervisor-error", "error": result["supervisor_error"]}
    finally:
        group.kill()
        if writer is not None:
            os.close(writer)
        if reader is not None:
            os.close(reader)
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise policy.GuardError("watchdog cleanup unconfirmed; retain containment resources") from error
            child.stdout.close()
            child.stderr.close()
            result.setdefault("returncode", child.returncode)
        until = time.monotonic() + 5
        while not group.empty() and time.monotonic() < until:
            time.sleep(0.02)
        if not group.empty():
            raise policy.GuardError("owned cgroup remains populated after kill; retain workspace")
        with selectors.DefaultSelector() as selector:
            for descriptor in held:
                selector.register(descriptor, selectors.EVENT_READ)
            if held and len(selector.select(0)) != len(held):
                raise policy.GuardError("escaped descendant pidfds did not become terminal")
        for descriptor in held:
            os.close(descriptor)
        result["kernel"] = group.snapshot()
        disk = os.statvfs(volume.mountpoint)
        result["kernel"]["disk_used_bytes"] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
        result["kernel"]["disk_available_bytes"] = disk.f_bavail * disk.f_frsize
        result["sampled_disk_peak_bytes"] = max(sampled_disk_peak, result["kernel"]["disk_used_bytes"])
        if last_sample:
            io_peaks(previous_io, result["kernel"]["io"], time.monotonic() - last_sample, sampled_io_peaks)
        result["sampled_io_peak_rates"] = sampled_io_peaks
        result["io_peak_semantics"] = "Maximum observed interval rates, not an unsampled instantaneous kernel peak."
        result["empty"] = True
        result["watchdog_reaped"] = child is None or child.returncode is not None
        result["lifetime_writer_closed"] = True
        result["caller_lifetime_control_exercised"] = lifetime_closed
        result["held_descendants_terminal"] = len(held)
        result["stdout_bytes"] = protocol.total
        result["stderr_bytes"] = protocol.stderr_total
        result["output_bytes"] = protocol.total + protocol.stderr_total
        result["output_exceeded"] = protocol.output_exceeded
        result["first_cause"] = cause
        result["ended_at"] = time.monotonic()
        result["elapsed_seconds"] = result["ended_at"] - started
        owner.artifacts.append("metrics.jsonl", {"scope": scope, "terminal": True, **result["kernel"]})
    return result


def validate_probe(result):
    mode = result["mode"]
    if mode not in {"identity", "memory", "pids", "disk", "output", "deadline", "lifetime"}:
        raise policy.GuardError("graph or unknown mode is not an expected benign negative")
    if result.get("supervisor_error"):
        raise policy.GuardError("benign containment probe had a supervisor failure")
    if "identity" not in result or not result.get("empty") or not result.get("watchdog_reaped"):
        raise policy.GuardError("benign probe did not establish identity and terminal cleanup")
    if mode != "output" and result.get("output_exceeded"):
        raise policy.GuardError("unexpected output overflow outside the dedicated output negative")
    if mode in {"identity", "pids", "disk"}:
        if result.get("returncode") != 0 or result.get("first_cause") or "probe" not in result:
            raise policy.GuardError(f"required benign {mode} probe failed")
        if mode == "identity":
            identity = result["identity"]
            policy.validate_proc_protection(result["probe"].get("proc_protection"))
            policy.validate_nested_probe(
                result["probe"].get("nested"), uid=identity["uid"], gid=identity["gid"],
                group=identity["cgroup"], parent_user_namespace=identity["namespaces"]["user"],
            )
    elif mode == "memory":
        if result["kernel"]["memory_events"].get("oom_kill", 0) < 1:
            raise policy.GuardError("kernel memory rejection was not proved")
    elif mode == "output":
        if (
            (result.get("first_cause") or {}).get("type") != "output-bound"
            or result.get("output_exceeded") is not True or result["output_bytes"] <= 65536
        ):
            raise policy.GuardError("external output rejection was not proved")
    elif mode in {"deadline", "lifetime"}:
        if not result.get("escaped") or result["held_descendants_terminal"] < 2:
            raise policy.GuardError("real escaped-descendant cleanup was not proved")
        if mode == "deadline" and (result.get("first_cause") or {}).get("type") != "deadline":
            raise policy.GuardError("independent deadline control was not proved")
        if mode == "lifetime" and (
            not result["caller_lifetime_control_exercised"]
            or not result.get("empty_before_outer_cleanup")
            or (result.get("first_cause") or {}).get("type") not in {None, "lifetime-eof"}
        ):
            raise policy.GuardError("lifetime-pipe reaper did not independently empty the cgroup")


class Owner:
    def __init__(self, arguments, scope, artifacts):
        self.harness = Path(arguments.harness).resolve(strict=True)
        self.candidate = Path(arguments.candidate).resolve(strict=True)
        self.scope, self.artifacts = scope, artifacts
        self.groups = []
        self.volumes = []
        self.apparmor_profile = None
        self.account = None
        self.uid = self.gid = None
        self.candidate_owner = None
        self.listener = None
        self.control = Path(tempfile.mkdtemp(prefix="vo-ci180-" + scope["run_id"] + "-", dir=artifacts.root.parent))
        self.etc = self.control / "etc"
        self.etc.mkdir()
        self.tracked_paths = 0
        self.gitlinks = []
        self.runtime_manifest = None

    def prepare(self):
        if os.geteuid() != 0:
            raise policy.GuardError("hosted containment setup requires its narrow root supervisor")
        if HERE != self.harness / "scripts/ci_calibration" or LIFECYCLE.resolve() != (
            self.harness / "scripts/validation_ownership/lifecycle.py"
        ).resolve():
            raise policy.GuardError("executing harness differs from the workflow checkout")
        if self.harness == self.candidate or self.harness in self.candidate.parents or self.candidate in self.harness.parents:
            raise policy.GuardError("harness and candidate must be separate checkouts")
        if git(self.harness, "rev-parse", "HEAD").decode().strip() != self.scope["harness_sha"]:
            raise policy.GuardError("harness does not match the actual workflow SHA")
        if git(self.harness, "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise policy.GuardError("workflow harness has uncommitted source changes")
        validate_harness_lineage(
            git(self.harness, "rev-list", "--parents", "--max-count=26", "HEAD").decode().splitlines(),
            self.scope["harness_sha"],
        )
        changed = git(self.harness, "diff", "--name-only", "-z", policy.BASE, "HEAD").split(b"\0")
        if any(
            name and name.decode() not in {
                policy.WORKFLOW, policy.RUNTIME_REPORT_WORKFLOW,
                policy.MAKE_CONTEXT_WORKFLOW, policy.ORIGINAL_IMPORT_WORKFLOW,
                policy.TRACELESS_ANCHOR_WORKFLOW, policy.PARTIAL_ANCHOR_WORKFLOW,
                policy.PYTHON_REPORT_WORKFLOW, policy.CORRECTED_REPORT_WORKFLOW,
                policy.LOCALIZATION_WORKFLOW, policy.FULL_REPORT_WORKFLOW,
                policy.COMPONENT_WORKFLOW, policy.PREVIOUS_WORKFLOW,
            }
            and not name.decode().startswith("scripts/ci_calibration/")
            for name in changed
        ):
            raise policy.GuardError("diagnostic branch modified a production surface")
        preserved = (
            policy.PREVIOUS_WORKFLOW, "scripts/ci_calibration/entry.py", "scripts/ci_calibration/kernel.py",
            "scripts/ci_calibration/runtime_view.py", "scripts/ci_calibration/volume_mount.py",
            "scripts/validation_ownership/sandbox_exec.py", "scripts/validation_ownership/lifecycle.py",
        )
        if git(self.harness, "diff", "--name-only", COMPONENT_BASE_SHA, "HEAD", "--", *preserved).strip():
            raise policy.GuardError("component preparation changed a preserved containment surface")
        if git(self.harness, "diff", "--name-only", REPORT_BASE_SHA, "HEAD", "--", policy.COMPONENT_WORKFLOW).strip():
            raise policy.GuardError("full-report preparation changed the closed component workflow")
        delta = git(self.harness, "diff", "--name-status", "-z", COMPONENT_BASE_SHA, REPORT_BASE_SHA).split(b"\0")
        if not delta or delta[-1] != b"" or len(delta) % 2 != 1:
            raise policy.GuardError("component preparation inventory is malformed")
        changes = list(zip(delta[:-1:2], delta[1:-1:2]))
        if (
            (b"A", policy.COMPONENT_WORKFLOW.encode()) not in changes
            or any(name.decode() not in COMPONENT_PATHS or kind != (
                b"A" if name.decode() == policy.COMPONENT_WORKFLOW else b"M"
            ) for kind, name in changes)
            or len({name for _, name in changes}) != len(changes)
        ):
            raise policy.GuardError("component preparation exceeds its exact allowed surfaces")
        validate_correction_inventory(git(
            self.harness, "diff", "--name-status", "-z", CORRECTION_BASE_SHA, REPORT_BASE_SHA,
        ))
        validate_report_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_BASE_SHA, REPORT_PREPARATION_SHA,
        ))
        validate_report_error_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_PREPARATION_SHA, REPORT_ERROR_SHA,
        ))
        validate_report_error_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_ERROR_SHA, REPORT_FINALIZATION_SHA,
        ))
        validate_accounting_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_FINALIZATION_SHA, REPORT_ACCOUNTING_SHA,
        ))
        validate_report_error_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_ACCOUNTING_SHA, REPORT_TELEMETRY_SHA,
        ))
        validate_source_rebind_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_TELEMETRY_SHA, REPORT_REBIND_SHA,
        ))
        validate_localization_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_REBIND_SHA, REPORT_LOCALIZATION_SHA,
        ))
        validate_registration_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_LOCALIZATION_SHA, REPORT_REGISTRATION_SHA,
        ))
        validate_registration_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_REGISTRATION_SHA, REPORT_CODE_METADATA_SHA,
        ))
        validate_corrected_report_inventory(git(
            self.harness, "diff", "--name-status", "-z", REPORT_CODE_METADATA_SHA, CORRECTED_REPORT_SHA,
        ))
        validate_python_report_inventory(git(
            self.harness, "diff", "--name-status", "-z", CORRECTED_REPORT_SHA, PYTHON_REPORT_SHA,
        ))
        validate_partial_anchor_inventory(git(
            self.harness, "diff", "--name-status", "-z", PYTHON_REPORT_SHA, PARTIAL_ANCHOR_SHA,
        ))
        validate_traceless_anchor_inventory(git(
            self.harness, "diff", "--name-status", "-z", PARTIAL_ANCHOR_SHA, TRACELESS_ANCHOR_SHA,
        ))
        validate_original_import_inventory(git(
            self.harness, "diff", "--name-status", "-z", TRACELESS_ANCHOR_SHA, ORIGINAL_IMPORT_SHA,
        ))
        validate_import_release_inventory(git(
            self.harness, "diff", "--name-status", "-z", ORIGINAL_IMPORT_SHA, IMPORT_RELEASE_SHA,
        ))
        validate_make_context_inventory(git(
            self.harness, "diff", "--name-status", "-z", IMPORT_RELEASE_SHA, MAKE_CONTEXT_SHA,
        ))
        validate_make_boundary_inventory(git(
            self.harness, "diff", "--name-status", "-z", MAKE_CONTEXT_SHA, MAKE_BOUNDARY_SHA,
        ))
        validate_runtime_report_inventory(git(
            self.harness, "diff", "--name-status", "-z", MAKE_BOUNDARY_SHA, RUNTIME_REPORT_SHA,
        ))
        validate_append_report_inventory(git(
            self.harness, "diff", "--name-status", "-z", RUNTIME_REPORT_SHA, "HEAD",
        ))
        validate_accounting_workflow(
            git(self.harness, "show", REPORT_FINALIZATION_SHA + ":" + policy.FULL_REPORT_WORKFLOW),
            git(self.harness, "show", REPORT_REBIND_SHA + ":" + policy.FULL_REPORT_WORKFLOW),
        )
        self.source_status("before")
        tree = git(self.candidate, "ls-tree", "-rz", "--full-tree", policy.GRAPH)
        self.tracked_paths = len([row for row in tree.split(b"\0") if row])
        self.scope["tracked_paths"] = self.tracked_paths
        self.scope["changed_paths"] = policy.changed_path_binding(policy.changed_path_set(git(
            self.candidate, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
            "--ignore-submodules=none", "--name-status", "-z", policy.BASE, policy.GRAPH, "--",
        )))
        policy.report_binding(self.scope)
        if git(self.candidate, "ls-tree", "-r", "--name-only", policy.GRAPH, "--", "build", "scripts/ci_calibration", policy.WORKFLOW).strip():
            raise policy.GuardError("source inventory overlaps writable build or diagnostic harness paths")
        for root in (self.harness, self.candidate, *(self.candidate / name for name in self.gitlinks)):
            found = git(root, "config", "--local", "--name-only", "--get-regexp",
                        r"^(credential\.|http\..*extraheader|url\..*insteadof|include\.|includeif\.|core\.sshcommand)",
                        allowed=(0, 1))
            if found.strip():
                raise policy.GuardError("checkout persisted credential configuration")
        self.runtime_manifest = runtime_view.observe()
        runtime_view.validate(self.runtime_manifest)
        self.scope["host_runtime_manifest"] = self.runtime_manifest
        controllers = set(kernel.read(CGROOT / "cgroup.controllers").decode().split())
        if not {"memory", "pids", "io"} <= controllers:
            raise policy.GuardError("required cgroup memory/PID/IO controllers are unavailable")
        enabled = set(kernel.read(CGROOT / "cgroup.subtree_control").decode().split())
        missing = {"memory", "pids", "io"} - enabled
        if missing:
            raise policy.GuardError("host controllers must already be enabled; no global cgroup-policy fallback")
        name = "vo180-" + self.scope["run_id"]
        try:
            pwd.getpwnam(name)
        except KeyError:
            pass
        else:
            raise policy.GuardError("one-shot worker identity already exists")
        tool(["/usr/sbin/useradd", "--system", "--no-create-home", "--user-group",
              "--shell", "/usr/sbin/nologin", name])
        self.account = name
        account = pwd.getpwnam(name)
        self.uid, self.gid = account.pw_uid, account.pw_gid
        if self.uid <= 0 or self.gid <= 0:
            raise policy.GuardError("worker account is not unprivileged")
        current = self.candidate.stat()
        self.candidate_owner = current.st_uid, current.st_gid
        build = self.candidate / "build"
        if build.exists() and (build.is_symlink() or not build.is_dir()):
            raise policy.GuardError("candidate build mountpoint is not a real directory")
        build.mkdir(exist_ok=True)
        tool(["/usr/bin/chown", "-R", "--no-dereference", f"{self.uid}:{self.gid}", str(self.candidate)], timeout=60)
        for name, content in (
            ("passwd", f"root:x:0:0:root:/nonexistent:/usr/sbin/nologin\ncalibration:x:{self.uid}:{self.gid}:diagnostic:/nonexistent:/usr/sbin/nologin\n"),
            ("group", f"root:x:0:\ncalibration:x:{self.gid}:\n"),
            ("nsswitch.conf", "passwd: files\ngroup: files\nhosts: files\n"),
            ("hosts", "127.0.0.1 localhost\n"),
        ):
            path = self.etc / name
            path.write_text(content)
            path.chmod(0o444)
        enabled_file = Path("/sys/module/apparmor/parameters/enabled")
        if enabled_file.exists() and kernel.read(enabled_file).strip() == b"Y":
            if kernel.read("/proc/self/attr/current").strip() != b"unconfined":
                raise policy.GuardError("refusing to replace an already confined supervisor AppArmor policy")
            name = "vo-ci180-" + self.scope["run_id"]
            profile = self.control / "apparmor.profile"
            profile.write_text(apparmor_text(name))
            tool(["/usr/sbin/apparmor_parser", "--skip-cache", "--jobs", "1", "--max-jobs", "1",
                  "--replace", str(profile)])
            self.apparmor_profile = name
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)

    def source_status(self, label):
        if git(self.candidate, "rev-parse", "HEAD").decode().strip() != policy.GRAPH:
            raise policy.GuardError("candidate HEAD differs from the immutable workload")
        if git(self.candidate, "rev-parse", policy.BASE + "^{commit}").decode().strip() != policy.BASE:
            raise policy.GuardError("candidate lacks the exact BASE history")
        if git(self.candidate, "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise policy.GuardError("candidate source is not clean")
        gitlinks = []
        for record in git(self.candidate, "ls-tree", "-rz", "--full-tree", policy.GRAPH).split(b"\0"):
            if record and record.startswith(b"160000 commit "):
                path = record.split(b"\t", 1)[1].decode("utf-8", "strict")
                if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
                    raise policy.GuardError("noncanonical captured gitlink")
                gitlinks.append(path)
        self.gitlinks = gitlinks
        if any(line[:1] in {b"-", b"+", b"U"} for line in git(
            self.candidate, "submodule", "status", "--recursive", safe_children=gitlinks,
        ).splitlines()):
            raise policy.GuardError("candidate submodules are missing or differ from their Git pins")
        self.scope["source_status_" + label] = "exact HEAD/BASE and clean with initialized pinned submodules"

    def volume(self, name, size):
        directory = self.control / name
        directory.mkdir()
        volume = Volume(directory, size, self.uid, self.gid)
        self.volumes.append(volume)
        volume.prepare()
        return volume

    def cleanup(self):
        failures = []
        for group in self.groups:
            if not group.created:
                continue
            try:
                if not group.empty():
                    group.kill()
                    until = time.monotonic() + 5
                    while not group.empty() and time.monotonic() < until:
                        time.sleep(0.02)
                if not group.empty():
                    raise policy.GuardError("unconfirmed cgroup population")
            except (OSError, policy.GuardError) as error:
                failures.append(str(error))
        if failures:
            raise policy.GuardError("preserve all owned resources: " + "; ".join(failures))
        for volume in reversed(self.volumes):
            volume.close()
        for group in reversed(self.groups):
            group.remove()
        if self.candidate_owner is not None:
            uid, gid = self.candidate_owner
            tool(["/usr/bin/chown", "-R", "--no-dereference", f"{uid}:{gid}", str(self.candidate)], timeout=60)
        if self.apparmor_profile is not None:
            tool(["/usr/sbin/apparmor_parser", "--skip-cache", "--jobs", "1", "--max-jobs", "1",
                  "--remove", str(self.control / "apparmor.profile")])
        if self.account is not None:
            tool(["/usr/sbin/userdel", self.account])
        if self.listener is not None:
            self.listener.close()
        shutil.rmtree(self.control)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "run"))
    for name in ("harness", "candidate", "output", "event", "sha", "event-name", "run-id",
                 "attempt", "run-number", "runner-environment", "runner-os"):
        parser.add_argument("--" + name, required=True)
    return parser.parse_args()


def validate_report_phase(result, binding):
    if (
        not isinstance(result, dict) or result.get("mode") != "report"
        or result.get("first_cause") or type(result.get("returncode")) is not int or result["returncode"] != 0
        or type(result.get("report_starts")) is not int or result["report_starts"] != 1
        or type(result.get("report_check_attempts")) is not int or result["report_check_attempts"] != 1
        or result.get("report_returned") is not True or result.get("report_completed") is not True
        or any(type(result.get(name)) is not int or result[name] != 0 for name in policy.ABSENT_WORKLOADS)
        or result.get("empty") is not True or result.get("watchdog_reaped") is not True
        or result.get("empty_before_outer_cleanup") is not True
        or result.get("lifetime_writer_closed") is not True
        or result.get("output_exceeded") or result.get("cleanup_errors") or result.get("supervisor_error")
        or type(result.get("deadline")) not in (int, float)
    ):
        raise policy.GuardError("single report failed or lacks actual completion/cleanup")
    return policy.validate_report_worker(result.get("worker"), binding, result["deadline"])


def report_retention(result):
    if type(result) is not dict or result.get("mode") != "report":
        return True
    if any(result.get(name) is not True for name in (
        "empty_before_outer_cleanup", "empty", "watchdog_reaped", "lifetime_writer_closed",
    )):
        return True
    worker = result.get("worker")
    report = worker.get("report") if isinstance(worker, dict) else None
    cause = None if result is None else result.get("first_cause")
    error = cause.get("error") if isinstance(cause, dict) else None
    if isinstance(error, dict) and error.get("source_cleanup_failures") != 0:
        return True
    if isinstance(error, dict) and error.get("stage") in {
        "sampler-close", "budget-close", "cleanup-observation", "counter-observation", "counter-publication",
        "constructor-reference", "report-reference", "serialization-reference",
        "import-get-code-restore", "import-source-to-code-restore", "import-reference",
    }:
        return True
    if isinstance(error, dict) and any(
        row.get("stage") in {
            "sampler-close", "budget-close", "import-get-code-restore", "import-source-to-code-restore",
            "import-reference",
        }
        for row in error.get("secondary", ()) if isinstance(row, dict)
    ):
        return True
    if isinstance(error, dict):
        counters = error.get("counters")
        budget = counters.get("budget") if isinstance(counters, dict) else None
        if not isinstance(budget, dict) or budget.get("failed") is not False:
            return True
    cleanup = report.get("cleanup") if isinstance(report, dict) else error.get("cleanup") if isinstance(error, dict) else None
    if cleanup is None:
        return True
    policy.validate_report_cleanup(cleanup)
    try:
        policy.validate_report_cleanup(cleanup, complete=True)
    except policy.GuardError:
        return True
    return False


def main():
    args = arguments()
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise policy.GuardError("supervisor requires isolated no-site startup")
    event = policy.parse_json(kernel.read(args.event, policy.MIB))
    scope = policy.validate_event(
        event, sha=args.sha, run_id=args.run_id, attempt=args.attempt, run_number=args.run_number,
        environment=args.runner_environment, operating_system=args.runner_os, event_name=args.event_name,
    )
    output = Path(args.output).absolute()
    if output.name != policy.OUTPUT_PREFIX + args.run_id or output.is_symlink():
        raise policy.GuardError("output does not identify the single owned artifact directory")
    artifacts = Artifacts(output)
    if args.operation == "plan":
        if (output / "scope.json").exists():
            raise policy.GuardError("one-shot scope already exists")
        scope.update(
            planned_at_monotonic=time.monotonic(), report_launch_requested=False,
            report_attempted=False, report_returned=False, report_completed=False,
            policy=policy.profile_manifest(
                policy.ORIGINAL_LIMITS, observation_count=policy.ORIGINAL_LIMITS["entries"],
            ),
            policy_basis="Expected pinned defaults; actual Limits/effective properties are checked after containment.",
            output_allowlist=list(policy.ARTIFACT_NAMES),
        )
        artifacts.write("scope.json", scope)
        return 0
    previous = policy.parse_json(kernel.read(output / "scope.json", policy.OUTPUT_BYTES))
    if (
        any(previous.get(key) != value for key, value in scope.items())
        or previous.get("report_launch_requested") is not False
        or previous.get("report_attempted") is not False
        or previous.get("report_returned") is not False
        or previous.get("report_completed") is not False
    ):
        raise policy.GuardError("run does not match the unspent planned scope")
    scope = previous
    if (output / "result.json").exists() or (output / "preflight.json").exists():
        raise policy.GuardError("one-shot scope already has an attempted execution")
    owner = None
    first = None
    cleanup_error = None
    result = None
    failing_phase = None
    preflight = []
    started = time.monotonic()
    artifacts.write("result.json", {
        "status": "preflight-started", "diagnostic_only": True,
        "production_acceptance": False, "report_launch_requested": False, **policy.ABSENT_WORKLOADS,
    })
    try:
        if os.geteuid() != 0:
            raise policy.GuardError("run requires the hosted root supervisor")
        if started - previous["planned_at_monotonic"] > 15 * 60:
            raise policy.GuardError("setup consumed the reserved job/cleanup margin; report will not start")
        os.chown(output, 0, 0)
        facts = capacity_facts(output.parent)
        policy.choose_envelope(facts)
        scope["preflight_runner_facts"] = facts
        owner = Owner(args, scope, artifacts)
        owner.prepare()
        artifacts.write("scope.json", scope)
        probe_volume = owner.volume("probe-volume", 64 * policy.MIB)
        for mode in ("identity", "memory", "pids", "disk", "output", "deadline", "lifetime"):
            if time.monotonic() - started > 5 * 60:
                raise policy.GuardError("preflight exceeded its external setup allowance")
            observed = phase(
                owner, mode, probe_volume,
                memory=64 * policy.MIB if mode == "memory" else 256 * policy.MIB,
                pids=8 if mode == "pids" else 64,
                seconds=2 if mode == "deadline" else 30,
            )
            preflight.append(observed)
            artifacts.write("preflight.json", {"status": "qualifying", "probes": preflight})
            failing_phase = observed
            validate_probe(observed)
            failing_phase = None
        artifacts.write("preflight.json", {"status": "qualified", "probes": preflight})
        probe_volume.close()
        facts = capacity_facts(output.parent)
        envelope = policy.choose_envelope(facts)
        scope.update(runner_facts=facts, envelope=envelope)
        graph_volume = owner.volume("graph-volume", envelope["disk_bytes"])
        if time.monotonic() - started > 5 * 60:
            raise policy.GuardError("preflight/setup exceeded its reserved margin; report will not start")
        scope["report_launch_requested"] = True
        scope["report_attempted"] = scope["report_returned"] = None
        artifacts.write("scope.json", scope)
        result = phase(
            owner, "report", graph_volume, memory=envelope["memory_max"],
            pids=envelope["pids_max"], seconds=policy.GRAPH_SECONDS,
        )
        failing_phase = result
        checked = validate_report_phase(result, policy.report_binding(scope))
        failing_phase = None
        scope["report_completed"] = True
        result["validation"] = checked
    except BaseException as error:
        observed_cause = failing_phase.get("first_cause") if isinstance(failing_phase, dict) else None
        first = observed_cause or policy.component_secondary_error(error)
    finally:
        if owner is not None:
            try:
                if scope["report_launch_requested"] and (result is None or report_retention(result)):
                    raise policy.GuardError("inner report ownership remains uncertain; retain outer resources after owned-process termination")
                owner.cleanup()
            except BaseException as error:
                cleanup_error = policy.component_secondary_error(error)
            try:
                owner.source_status("after")
            except BaseException as error:
                scope["source_status_after_error"] = policy.component_secondary_error(error)
                if first is None:
                    first = scope["source_status_after_error"]
        artifacts.write("scope.json", scope)
        artifacts.write("result.json", {
            "status": "completed-report-diagnostic-only" if first is None and cleanup_error is None else "failed",
            "diagnostic_only": True, "production_acceptance": False,
            "first_error": first, "phase": result, "cleanup_error": cleanup_error,
            "cleanup_confirmed": cleanup_error is None,
            "elapsed_seconds": time.monotonic() - started,
        })
    return 0 if first is None and cleanup_error is None else 1


if __name__ == "__main__":
    def interrupted(signum, _frame):
        raise policy.GuardError(f"outer supervisor interrupted by signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        raise SystemExit(main())
    except (OSError, ValueError, policy.GuardError) as error:
        print(f"issue180 diagnostic supervisor: {error}", file=sys.stderr)
        raise SystemExit(1)
