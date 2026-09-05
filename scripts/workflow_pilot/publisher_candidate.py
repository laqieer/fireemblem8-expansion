"""Fixed no-fork candidate exec; the foreground Bash caller owns wait/reap."""

import os
import stat
import sys

MAX_FD = 1_048_576

if len(sys.argv) != 5:
    raise SystemExit(125)
uid, gid, script_path, host_runner_temp = sys.argv[1:]
if not uid.isascii() or not uid.isdecimal():
    raise SystemExit(125)
if not gid.isascii() or not gid.isdecimal():
    raise SystemExit(125)
if not host_runner_temp.startswith("/home/runner/"):
    raise SystemExit(125)

script_stat = os.lstat(script_path)
if not stat.S_ISREG(script_stat.st_mode):
    raise SystemExit(125)
if script_stat.st_uid != 0 or script_stat.st_nlink != 1:
    raise SystemExit(125)
if stat.S_IMODE(script_stat.st_mode) != 0o555:
    raise SystemExit(125)
with open(script_path, "rb") as script_file:
    script_bytes = script_file.read(65537)
if not script_bytes or len(script_bytes) > 65536:
    raise SystemExit(125)
try:
    script = script_bytes.decode("ascii")
except UnicodeDecodeError:
    raise SystemExit(125)

candidate_argv = [
    "/usr/bin/setpriv",
    f"--reuid={uid}",
    f"--regid={gid}",
    "--clear-groups",
    "--no-new-privs",
    "--bounding-set=-all",
    "--inh-caps=-all",
    "--ambient-caps=-all",
    "/bin/bash",
    "--noprofile",
    "--norc",
    "-euo",
    "pipefail",
    "-c",
    script,
    "candidate-build",
]
candidate_env = {
    "GITHUB_WORKSPACE": "/mnt/source",
    "HANDOFF": "/mnt/handoff",
    "HOME": "/mnt/home",
    "HOST_RUNNER_TEMP": host_runner_temp,
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PIP_CONFIG_FILE": "/dev/null",
    "RUNNER_TEMP": "/mnt/tmp",
    "TMPDIR": "/mnt/tmp",
    "WHEELHOUSE": "/mnt/wheelhouse",
}

close_range = getattr(os, "close_range", None)
try:
    if close_range is not None:
        close_range(3, MAX_FD - 1)
    elif hasattr(os, "closerange"):
        os.closerange(3, MAX_FD)
    else:
        raise OSError("no safe descriptor closer")
except OSError:
    raise SystemExit(125)

try:
    os.execve(candidate_argv[0], candidate_argv, candidate_env)
except OSError:
    raise SystemExit(126)
