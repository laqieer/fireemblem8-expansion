#!/usr/bin/env python3
"""Bootstrap the owned, hash-locked workflow schema-test interpreter."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import site
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / ".github" / "requirements" / "host-tests.txt"
DEFAULT_ENVIRONMENT = ROOT / "build" / "host-python"
REQUIRED_FORMATS = ("date-time",)


def require_supported_profile() -> None:
    libc, version = platform.libc_ver()
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or sys.platform != "linux"
        or platform.machine() != "x86_64"
        or libc != "glibc"
        or not re.fullmatch(r"\d+\.\d+", version)
        or tuple(map(int, version.split("."))) < (2, 17)
    ):
        raise ValueError(
            "supported host profile is CPython 3.12 / Linux x86_64 / glibc >= 2.17; "
            "review replacement wheels before changing the profile"
        )


def locked_versions(text: str) -> dict[str, str]:
    records = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current = (current + " " + line).strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        match = re.fullmatch(
            r"([a-z0-9]+(?:-[a-z0-9]+)*)==([0-9]+(?:\.[0-9]+)*)"
            r"\s+--hash=sha256:[0-9a-f]{64}",
            current,
        )
        if not match or match[1] in records:
            raise ValueError("host requirements need unique exact pins and one SHA256 per wheel")
        records[match[1]] = match[2]
        current = ""
    if current or not records:
        raise ValueError("empty or incomplete host requirements")
    return records


def tool_environment(environment: Path) -> dict[str, str]:
    scratch = environment / ".bootstrap"
    return {
        "HOME": str(scratch / "home"),
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PIP_CONFIG_FILE": os.devnull,
        "TMPDIR": str(scratch),
    }


def pip_command(python: Path, *arguments: str) -> list[str]:
    return [
        str(python), "-I", "-m", "pip", "--isolated",
        "--disable-pip-version-check", "--no-cache-dir", *arguments,
    ]


def check_schema_support() -> None:
    from jsonschema import Draft202012Validator, FormatChecker

    missing = set(REQUIRED_FORMATS) - FormatChecker.checkers.keys()
    if missing:
        raise ValueError(f"missing required JSON Schema format validators: {sorted(missing)}")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "array",
        "prefixItems": [
            {"type": "integer"},
            {"type": "string", "format": "date-time"},
        ],
        "items": False,
        "minItems": 2,
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker(REQUIRED_FORMATS))
    for timestamp in ("2024-02-29T12:34:56Z", "2024-02-29T12:34:56.25+01:00"):
        validator.validate([7, timestamp])
    for invalid in (
        [7, "2025-02-29T12:34:56Z"],
        [7, "2024-02-29T25:00:00Z"],
        [7, "not-a-time"],
        [True, "2024-02-29T12:34:56Z"],
        [7],
        [7, "2024-02-29T12:34:56Z", "extra"],
    ):
        if validator.is_valid(invalid):
            raise ValueError(f"JSON Schema draft/format validation accepted invalid input: {invalid}")


def check_environment() -> dict:
    require_supported_profile()
    environment = Path(sys.prefix).resolve()
    if not sys.flags.isolated or sys.prefix == sys.base_prefix or site.ENABLE_USER_SITE:
        raise ValueError("use the bootstrapped virtual environment's Python with -I, not system/user packages")
    configuration = dict(
        line.partition("=")[::2]
        for line in (environment / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    configuration = {key.strip(): value.strip() for key, value in configuration.items()}
    if configuration.get("include-system-site-packages") != "false":
        raise ValueError("system site packages must be disabled")
    expected = locked_versions(REQUIREMENTS.read_text(encoding="ascii"))
    installed = {}
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        if name in installed or not Path(distribution.locate_file("")).resolve().is_relative_to(environment):
            raise ValueError(f"duplicate or ambient Python distribution: {name}")
        installed[name] = distribution.version
    # ensurepip supplies pip from the OS Python; it is not a schema dependency.
    installed.pop("pip", None)
    if installed != expected:
        raise ValueError(f"host dependency versions differ from lock: expected {expected}, found {installed}")
    closure = subprocess.run(
        pip_command(Path(sys.executable), "check"),
        env=tool_environment(environment),
        check=False,
        capture_output=True,
        text=True,
    )
    if closure.returncode:
        raise ValueError("incomplete host dependency closure: " + (closure.stdout + closure.stderr).strip())
    check_schema_support()
    return {
        "environment": str(environment),
        "python": platform.python_version(),
        "packages": installed,
        "schema_draft": "2020-12",
        "formats": list(REQUIRED_FORMATS),
    }


def create_environment(environment: Path, wheelhouse: Path | None = None) -> None:
    require_supported_profile()
    locked_versions(REQUIREMENTS.read_text(encoding="ascii"))
    environment = environment.absolute()
    if (
        environment != environment.resolve()
        or not environment.is_relative_to(ROOT / "build")
        or environment == ROOT / "build"
    ):
        raise ValueError("environment must be a new non-symlink path inside this checkout's build/")
    environment.parent.mkdir(parents=True, exist_ok=True)
    environment.mkdir()  # Never clear, reuse or install into an existing environment.
    (environment / ".bootstrap" / "home").mkdir(parents=True)
    tools = tool_environment(environment)
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", str(environment)],
        env=tools,
        check=True,
    )
    python = environment / "bin" / "python3"
    wheels = environment / "wheelhouse"
    wheels.mkdir()
    source = (
        ["--index-url", "https://pypi.org/simple"]
        if wheelhouse is None
        else ["--no-index", "--find-links", str(wheelhouse.resolve(strict=True))]
    )
    locked = ["--require-hashes", "--only-binary=:all:", "--no-deps", "-r", str(REQUIREMENTS)]
    subprocess.run(
        pip_command(python, "download", *source, "--dest", str(wheels), *locked),
        env=tools,
        check=True,
    )
    subprocess.run(
        pip_command(python, "install", "--no-index", "--find-links", str(wheels), *locked),
        env=tools,
        check=True,
    )
    subprocess.run(
        [str(python), "-I", str(Path(__file__).resolve()), "check"],
        env=tools,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    create = subparsers.add_parser("create", help="create a fresh owned environment, never reuse one")
    create.add_argument("--environment", type=Path, default=DEFAULT_ENVIRONMENT)
    create.add_argument("--wheelhouse", type=Path, help="use only these local wheels, with no network")
    subparsers.add_parser("check", help="check this isolated interpreter's pins, closure, draft and formats")
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated:
            raise ValueError("isolated Python startup (-I) is required")
        if args.operation == "create":
            create_environment(args.environment, args.wheelhouse)
        else:
            print(json.dumps(check_environment(), sort_keys=True))
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as error:
        print(f"host-python: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
