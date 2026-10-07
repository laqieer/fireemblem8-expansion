"""Closed source-derived native resource roles, distinct from retained files."""

import re

if __package__:
    from .authority import relative_path
    from .budget import MakeProbeError
else:
    from authority import relative_path
    from budget import MakeProbeError


KINDS = {"directory", "temporary", "pid-temporary", "atomic-temporary", "shared-lock"}


def resource_plan(rows):
    if not isinstance(rows, (tuple, list)):
        raise MakeProbeError("native resource plan must be a finite sequence")
    result = []
    for row in rows:
        if (
            not isinstance(row, (tuple, list)) or len(row) != 2
            or not isinstance(row[0], str) or row[0] not in KINDS
            or not isinstance(row[1], str)
        ):
            raise MakeProbeError("native resource has an unknown role or path")
        kind, path = row
        relative_path(path)
        if kind == "atomic-temporary" and path.rpartition("/")[2] != ".asset-manifest-write-":
            raise MakeProbeError("native atomic temporary lacks its source-defined prefix")
        if (kind, path) in result or any(other == path for _, other in result):
            raise MakeProbeError("native resource plan contains overlapping roles")
        for other_kind, other in result:
            if (
                _file_ancestor(kind, path, other) or _file_ancestor(other_kind, other, path)
            ):
                raise MakeProbeError("native resource plan contains intersecting concrete paths")
        result.append((kind, path))
    return tuple(result)


def resource_role(rows, path, root_pid):
    for kind, name in resource_plan(rows):
        selected = "/repo/" + name
        if kind == "pid-temporary":
            if path == selected + "." + str(root_pid) + ".tmp":
                return kind
        elif kind == "atomic-temporary":
            if path.startswith(selected) and re.fullmatch("[a-z0-9_]{8}", path[len(selected):]):
                return kind
        elif path == selected:
            return kind
    return None


def _matches(kind, path, name):
    if kind == "pid-temporary":
        return re.fullmatch(re.escape(path) + r"\.[1-9][0-9]*\.tmp", name) is not None
    if kind == "atomic-temporary":
        return name.startswith(path) and re.fullmatch("[a-z0-9_]{8}", name[len(path):]) is not None
    return path == name


def _file_ancestor(kind, path, name):
    if kind == "directory":
        return False
    components = name.split("/")
    return any(_matches(kind, path, "/".join(components[:end])) for end in range(1, len(components) + 1))


def resource_operation(rows, path, root_pid, outputs, operation):
    role = "retained" if path in {"/repo/" + name for name in outputs} else resource_role(rows, path, root_pid)
    allowed = {
        "directory": {"mkdir", "rmdir"},
        "shared-lock": {"open", "lock"},
        "retained": {"open", "write", "mode", "remove", "replace"},
        "temporary": {"open", "write", "mode", "remove", "replace"},
        "pid-temporary": {"open", "write", "mode", "remove", "replace"},
        "atomic-temporary": {"open", "write", "mode", "remove", "replace"},
    }
    return operation in allowed.get(role, set())


def validate_resource_scope(rows, outputs, sources):
    outputs = tuple(outputs)
    sources = tuple(sources)
    for kind, path in resource_plan(rows):
        for source in sources:
            if (
                source == path or source.startswith(path + "/") or path.startswith(source + "/")
                or _file_ancestor(kind, path, source)
            ):
                raise MakeProbeError("native resource conflicts with immutable source")
        for output in outputs:
            if (
                output == path and kind != "pid-temporary"
                or path.startswith(output + "/")
                or _file_ancestor(kind, path, output)
            ):
                raise MakeProbeError("native resource conflicts with retained output")


def require_retained_source(path, outputs):
    if path not in outputs:
        raise MakeProbeError("native resource role cannot become a generated source")


def validate_terminal_resources(outputs, rows, files):
    required = {"/repo/" + name for name in outputs}
    shared = {"/repo/" + name for kind, name in resource_plan(rows) if kind == "shared-lock"}
    current = set(files)
    if not required <= current:
        raise MakeProbeError("native terminal custody omitted a retained output")
    if current - required - shared:
        raise MakeProbeError("native terminal custody has an unretired temporary object")
