"""Minimal isolated entry protocol for the publisher authority."""

import hashlib
import os
import re
import subprocess
import sys


if not hasattr(os, "O_NOFOLLOW") or len(sys.argv) < 4:
    raise SystemExit(125)

root = os.path.abspath(sys.argv[1])
expected = sys.argv[2]
mode = sys.argv[3]
mode_arguments = sys.argv[4:]
root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
for component in root.split(os.sep)[1:]:
    next_fd = os.open(
        component,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=root_fd,
    )
    os.close(root_fd)
    root_fd = next_fd

environment = {
    "GIT_CONFIG_COUNT": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "HOME": "/",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}


def git(*arguments):
    completed = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=f"/proc/self/fd/{root_fd}",
        env=environment,
        pass_fds=(root_fd,),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise SystemExit(125)
    return completed.stdout


format_name = git("rev-parse", "--show-object-format=storage").decode().strip()
formats = {"sha1": (40, 20), "sha256": (64, 32)}
if format_name not in formats:
    raise SystemExit(125)
hex_length, raw_length = formats[format_name]


def is_object_id(value):
    return re.fullmatch(rf"[0-9a-f]{{{hex_length}}}", value) is not None


def object_bytes(object_id, object_type):
    if not is_object_id(object_id) or object_type not in {"blob", "commit", "tree"}:
        raise SystemExit(125)
    size = git("cat-file", "-s", object_id).decode().strip()
    if not size.isdigit() or int(size) > 8388608:
        raise SystemExit(125)
    data = git("cat-file", object_type, object_id)
    identity = hashlib.new(
        format_name,
        f"{object_type} {len(data)}\0".encode() + data,
    ).hexdigest()
    if len(data) != int(size) or identity != object_id:
        raise SystemExit(125)
    return data


def tree_entries(data):
    result = {}
    offset = 0
    while offset < len(data):
        separator = data.find(b" ", offset)
        terminator = data.find(b"\0", separator + 1)
        entry_end = terminator + 1 + raw_length
        if separator <= offset or terminator < 0 or entry_end > len(data):
            raise SystemExit(125)
        entry_mode = data[offset:separator].decode()
        name = data[separator + 1 : terminator].decode()
        object_id = data[terminator + 1 : entry_end].hex()
        if name in {"", ".", ".."} or "/" in name or name in result:
            raise SystemExit(125)
        result[name] = (entry_mode, object_id)
        offset = entry_end
    return result


resolved = git(
    "rev-parse",
    "--verify",
    f"{expected}^{{commit}}",
).decode().strip()
head = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
if (
    not is_object_id(resolved)
    or head != resolved
    or (expected != "HEAD" and expected != resolved)
):
    raise SystemExit(125)
expected = resolved
commit = object_bytes(expected, "commit")
first_line = commit.split(b"\n", 1)[0]
if not first_line.startswith(b"tree "):
    raise SystemExit(125)
object_id = first_line.removeprefix(b"tree ").decode()
path = "scripts/workflow_pilot/publisher_command_signatures.py"
parts = path.split("/")
for index, part in enumerate(parts):
    entry = tree_entries(object_bytes(object_id, "tree")).get(part)
    if entry is None:
        raise SystemExit(125)
    entry_mode, object_id = entry
    if index + 1 < len(parts):
        if entry_mode not in {"40000", "040000"}:
            raise SystemExit(125)
    elif entry_mode != "100644":
        raise SystemExit(125)

source = object_bytes(object_id, "blob")
loader_arguments = [path, "--check"]
if mode in {"upstream-port", "workflows"} and not mode_arguments:
    loader_arguments.extend(["--consumer-suite", mode])
elif mode == "upstream-verify":
    loader_arguments.extend(["--upstream-verify", *mode_arguments])
elif mode != "check" or mode_arguments:
    raise SystemExit(125)
sys.argv = loader_arguments
namespace = {
    "__name__": "__main__",
    "__file__": os.path.join(root, path),
    "__package__": "",
    "_BOOTSTRAP_REVISION": expected,
}
exec(compile(source, path, "exec"), namespace)
