"""Closed root-only ext4 loop-volume mount control, behind the existing watchdog."""

import ctypes
import os
from pathlib import Path
import re
import stat
import sys

if __package__:
    from . import kernel, policy
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kernel
    import policy


def identity(path):
    value = Path(path).lstat()
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_nlink, value.st_rdev]


def make_spec(image, device, target, uid, gid):
    result = {
        "image": str(image), "device": str(device), "target": str(target),
        "image_identity": identity(image), "device_identity": identity(device),
        "target_identity": identity(target), "parent_identity": identity(Path(target).parent),
        "worker_uid": uid, "worker_gid": gid,
    }
    validate_spec(result)
    return result


def unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def validate_spec(spec):
    if os.geteuid() != 0:
        raise policy.GuardError("volume control requires the trusted root supervisor")
    required = {
        "image", "device", "target", "image_identity", "device_identity",
        "target_identity", "parent_identity", "worker_uid", "worker_gid",
    }
    if not isinstance(spec, dict) or set(spec) != required:
        raise policy.GuardError("malformed closed volume request")
    image, device, target = (Path(spec[name]) for name in ("image", "device", "target"))
    if (
        not all(path.is_absolute() and ".." not in path.parts for path in (image, device, target))
        or image.name != "workspace.img" or target.name != "volume" or image.parent != target.parent
        or target.parent.name not in {"probe-volume", "graph-volume"}
        or not re.fullmatch(r"vo-ci180-[1-9][0-9]*-[A-Za-z0-9_-]+", target.parent.parent.name)
        or not re.fullmatch(r"/dev/loop[0-9]+", str(device))
        or not all(type(spec[name]) is int and spec[name] > 0 for name in ("worker_uid", "worker_gid"))
    ):
        raise policy.GuardError("volume paths/identity are outside the fixed setup contract")
    for key in ("image_identity", "device_identity", "target_identity", "parent_identity"):
        if not isinstance(spec[key], list) or len(spec[key]) != 8 or any(type(value) is not int for value in spec[key]):
            raise policy.GuardError("invalid volume object identity")
    image_info, device_info, parent_info = identity(image), identity(device), identity(target.parent)
    control = identity(target.parent.parent)
    if not stat.S_ISDIR(control[2]) or control[3] != 0 or control[2] & 0o077:
        raise policy.GuardError("volume is outside its private root-owned diagnostic directory")
    # Directory size/link count may change when the root owner creates its request file.
    if parent_info[:5] != spec["parent_identity"][:5] or parent_info[3] != 0 or (
        not stat.S_ISDIR(parent_info[2]) or parent_info[2] & 0o022
    ):
        raise policy.GuardError("volume parent ownership changed")
    if image_info != spec["image_identity"] or (
        not stat.S_ISREG(image_info[2]) or image_info[3] != 0
        or image_info[2] & 0o022 or image_info[5] <= 0 or image_info[6] != 1
    ):
        raise policy.GuardError("root-owned volume image changed")
    if device_info != spec["device_identity"] or (
        not stat.S_ISBLK(device_info[2]) or device_info[3] != 0 or os.major(device_info[7]) != 7
    ):
        raise policy.GuardError("root-owned loop device changed")
    if spec["target_identity"][3] != 0 or not stat.S_ISDIR(spec["target_identity"][2]) or spec["target_identity"][2] & 0o022:
        raise policy.GuardError("mountpoint was not an owned root directory")
    backing = unescape(kernel.read(
        f"/sys/dev/block/{os.major(device_info[7])}:{os.minor(device_info[7])}/loop/backing_file",
        8192,
    ).decode("utf-8").strip())
    if not backing.startswith("/"):
        backing = "/" + backing
    if backing != str(image):
        raise policy.GuardError("loop device no longer belongs to the exact owned image")
    return image, device, target


def inspect_mount(spec, expected_mount_id=0):
    _, device, target = validate_spec(spec)
    matches = []
    for line in kernel.read("/proc/self/mountinfo", 1024 * 1024).decode().splitlines():
        fields = line.split()
        point = unescape(fields[4])
        if point.startswith(str(target) + "/"):
            raise policy.GuardError("volume still has child mounts; cleanup is not confirmed")
        if point == str(target):
            matches.append(fields)
    current = identity(target)
    if not matches:
        if current[:5] != spec["target_identity"][:5]:
            raise policy.GuardError("absent volume has a replaced or foreign mountpoint")
        return {"state": "absent", "mount_id": None, "filesystem_device": current[0]}
    if len(matches) != 1:
        raise policy.GuardError("stacked volume mounts are ambiguous")
    record, = matches
    separator = record.index("-")
    rdev = spec["device_identity"][7]
    if (
        record[2] != f"{os.major(rdev)}:{os.minor(rdev)}" or record[3] != "/"
        or record[separator + 1] != "ext4" or unescape(record[separator + 2]) != str(device)
        or not {"rw", "nosuid", "nodev"} <= set(record[5].split(","))
        or current[0] != rdev or not stat.S_ISDIR(current[2])
        or (current[3], current[4]) not in {(0, 0), (spec["worker_uid"], spec["worker_gid"])}
        or expected_mount_id and int(record[0]) != expected_mount_id
    ):
        raise policy.GuardError("mounted volume differs from its exact image/device/ownership")
    return {"state": "mounted", "mount_id": int(record[0]), "filesystem_device": current[0]}


def mount_syscall(device, target):
    if kernel.LIBC.mount(os.fsencode(device), os.fsencode(target), b"ext4", 2 | 4, None):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def unmount_syscall(target):
    if kernel.LIBC.umount2(os.fsencode(target), 0):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def operate(action, spec, expected_mount_id=0):
    if action not in {"mount", "inspect", "unmount"} or type(expected_mount_id) is not int or expected_mount_id < 0:
        raise policy.GuardError("unsupported closed volume operation")
    before = inspect_mount(spec, expected_mount_id)
    if action == "inspect":
        return before
    if action == "mount":
        if before["state"] != "absent":
            raise policy.GuardError("volume mount already exists; no repeated setup")
        mount_syscall(spec["device"], spec["target"])
        after = inspect_mount(spec)
        if after["state"] != "mounted":
            raise policy.GuardError("mount syscall did not establish the owned volume")
        return after
    if before["state"] == "mounted":
        unmount_syscall(spec["target"])
    after = inspect_mount(spec)
    if after["state"] != "absent":
        raise policy.GuardError("non-lazy unmount did not remove the owned volume")
    return after


if __name__ == "__main__":
    try:
        if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) != 4:
            raise policy.GuardError("volume helper requires isolated Python and one immutable request")
        request = kernel.owned_config(sys.argv[2])
        if not isinstance(request, dict) or not isinstance(request.get("target"), str) or (
            Path(sys.argv[2]) != Path(request["target"]).parent / "mount.json"
        ):
            raise policy.GuardError("volume request is outside its owned setup directory")
        value = operate(sys.argv[1], request, int(sys.argv[3]))
        print(policy.encoded(value).decode(), flush=True)
    except (OSError, ValueError, policy.GuardError) as error:
        print(f"owned volume setup: {error}", file=sys.stderr)
        raise SystemExit(1)
