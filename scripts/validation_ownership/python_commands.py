"""Shared source-only Python command adapters on ProbeSession authority."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import PurePosixPath

from .authority import parse_json, relative_path
from .budget import MakeProbeError, text
from .make_probe import Command, ProbeSession, TRUSTED_ROOT


PYTHON = "/usr/bin/python3"
GENERATED_REGISTRY_DRIVER = "generated_registry_probe.py"
GENERATED_DEPENDENCY_MODULES = {
    "scripts.generated_data.autoplaystrategies.deps": {
        "selectors": (
            ("--source", "scripts.generated_data.autoplaystrategies.schema"),
            ("--objectives-source", "scripts.generated_data.chapterobjectives.schema"),
            ("--bundle-source", "scripts.generated_data.chapterbundle.schema"),
        ),
        "support_from_bundle": True,
    },
    "scripts.generated_data.chapterobjectives.deps": {
        "selectors": (
            ("--source", "scripts.generated_data.chapterobjectives.schema"),
            ("--bundle-source", "scripts.generated_data.chapterbundle.schema"),
        ),
        "support_from_bundle": True,
    },
    "scripts.generated_data.eventlists.deps": {
        "selectors": (
            ("--strategy-source", "scripts.generated_data.autoplaystrategies.schema"),
            ("--bundle-source", "scripts.generated_data.chapterbundle.schema"),
        ),
        "support_from_bundle": False,
    },
}


def python_import_directories(code):
    return tuple(sorted({
        parent.as_posix() for path in code for parent in PurePosixPath(path).parents
    }))


def _python_package_name(path):
    path = relative_path(path)
    if path.endswith("/__init__.py"):
        return path[:-12].replace("/", ".")
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent.replace("/", ".")


def _python_package_inits(path, available):
    result = []
    current = PurePosixPath(relative_path(path)).parent
    while current.as_posix() != ".":
        init = current.as_posix() + "/__init__.py"
        if init in available and init != path:
            result.append(init)
        current = current.parent
    return tuple(reversed(result))


def _python_module_paths(available, module, *, main=False):
    if not module:
        return ()
    base = module.replace(".", "/")
    candidates = ([base + "/__main__.py"] if main else []) + [base + ".py", base + "/__init__.py"]
    for candidate in candidates:
        if candidate in available:
            return (*_python_package_inits(candidate, available), candidate)
    return ()


def _python_import_targets(tree, package=""):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if not package:
                continue
            try:
                base = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), package,
                )
            except ImportError:
                continue
        else:
            base = node.module or ""
        if base:
            modules.add(base)
        for alias in node.names:
            if alias.name != "*" and base:
                modules.add(base + "." + alias.name)
    return modules


def _available_python_paths(session):
    return {
        path for path in session.snapshot.files
        if path.endswith(".py")
    }


def _python_source_paths(session, source, package=""):
    available = _available_python_paths(session)
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise MakeProbeError(f"trusted Python adapter body is not valid Python: {error}") from error
    result = []
    for module in _python_import_targets(tree, package):
        result.extend(_python_module_paths(available, module))
    return tuple(dict.fromkeys(result))


def python_code_closure(session, body, code=()):
    available = _available_python_paths(session)
    explicit = tuple(dict.fromkeys(relative_path(path) for path in code))
    result = set(explicit)
    pending = []

    def add_path(path):
        path = relative_path(path)
        if path in result:
            return
        result.add(path)
        if path.endswith(".py") and path in available:
            pending.append(path)
        for init in _python_package_inits(path, available):
            if init not in result:
                result.add(init)
                pending.append(init)

    for path in explicit:
        if path.endswith(".py") and path in available:
            pending.append(path)
        for init in _python_package_inits(path, available):
            if init not in result:
                result.add(init)
                pending.append(init)

    for path in _python_source_paths(session, body):
        add_path(path)

    while pending:
        path = pending.pop()
        try:
            tree = ast.parse(
                text(session.snapshot.files[path], f"Python code {path}", "utf-8"),
                filename=path,
            )
        except SyntaxError as error:
            raise MakeProbeError(f"Python code {path!r} is not valid Python: {error}") from error
        for module in _python_import_targets(tree, _python_package_name(path)):
            for imported in _python_module_paths(available, module):
                add_path(imported)
    return tuple(sorted(result))


def python_command(session, body, arguments=(), *, sources=(), outputs=(), directories=(), code=()):
    modules = python_code_closure(session, body, code)
    prefix = "import sys;"
    if modules:
        prefix += "sys.path.insert(0,'/repo');"
    return Command(
        (PYTHON, "-I", "-S", "-B", "-c",
         prefix + body, *arguments),
        code=modules, sources=tuple(sources), outputs=tuple(outputs),
        directories=tuple(sorted(set(directories) | set(python_import_directories(modules)))),
    )


def _directory_closure(paths):
    result = set()
    for directory in paths:
        if directory == ".":
            result.add(directory)
            continue
        directory = relative_path(directory)
        result.add(directory)
        result.update(
            parent.as_posix() for parent in PurePosixPath(directory).parents
            if parent.as_posix() != "."
        )
    return tuple(sorted(result))


def directory_python_command(session, body, arguments=(), *, sources=(), outputs=(), directories=(), code=()):
    return python_command(
        session, body, arguments, sources=sources, outputs=outputs,
        directories=_directory_closure(directories), code=code,
    )


def _python_module_code(session, module, *, main=False):
    result = _python_module_paths(_available_python_paths(session), module, main=main)
    if not result:
        raise MakeProbeError(f"registered Python module source is missing: {module}")
    return result


def _python_module_closure(session: ProbeSession, module: str):
    return python_code_closure(
        session,
        "import importlib;importlib.import_module(" + repr(module) + ")",
        code=_python_module_code(session, module),
    )


def _registry_code(session: ProbeSession):
    if "scripts/generated_data/registry.py" not in session.snapshot.files:
        raise MakeProbeError("generated-data registry has no captured Python authority")
    return python_code_closure(session, "from scripts.generated_data.registry import REGISTRY")


def _registry_driver(session: ProbeSession):
    return text(
        session.budget.read_bytes(TRUSTED_ROOT / GENERATED_REGISTRY_DRIVER, "control"),
        "generated registry driver",
    )


def generated_registry_command(session: ProbeSession, name: str, source: str):
    relative_path(source)
    code = _registry_code(session)
    directories = () if source in session.snapshot.files else (source,)
    pool = (
        {source} if source in session.snapshot.files
        else {path for path in session.snapshot.files if path.startswith(source + "/")}
    )
    if not pool:
        raise MakeProbeError("registry input has no captured source candidates")
    driver = _registry_driver(session)
    observed = session.command(directory_python_command(
        session, driver, (name, source, "--manifest-inputs", "directory" if directories else "file"),
        directories=directories, code=code,
    ))
    declaration = parse_json(observed.stdout, "registry manifest input discovery")
    if (
        not isinstance(declaration, dict)
        or set(declaration) != {"source_paths", "support_paths"}
        or any(
            not isinstance(paths, list)
            or any(not isinstance(path, str) for path in paths)
            for paths in declaration.values()
        )
    ):
        raise MakeProbeError("registry manifest input declaration is invalid")
    selected = {
        key: tuple(_repository_report_path(path) for path in paths)
        for key, paths in declaration.items()
    }
    primary, support = selected["source_paths"], selected["support_paths"]
    if (
        any(paths != tuple(sorted(set(paths))) for paths in selected.values())
        or not primary or not set(primary) <= pool
        or not set(support) <= session.snapshot.files.keys()
        or set(primary) & set(support)
    ):
        raise MakeProbeError("registry manifest input declaration is invalid")
    return directory_python_command(
        session,
        driver,
        (name, source),
        sources=tuple(sorted((*primary, *support))),
        directories=directories,
        code=code,
    )


def generated_registry_source_paths_command(session: ProbeSession, name: str, source: str):
    source = relative_path(source)
    code = _registry_code(session)
    return directory_python_command(
        session,
        _registry_driver(session),
        (name, source, "--source-paths"),
        directories=(source,),
        code=code,
    )


def generated_registry_source_paths(session: ProbeSession, name: str, source: str):
    source = relative_path(source)
    pool = tuple(sorted(path for path in session.snapshot.files if path.startswith(source + "/")))
    if not pool:
        raise MakeProbeError("registry directory has no captured source candidates")
    observed = session.command(generated_registry_source_paths_command(session, name, source))
    paths = parse_json(observed.stdout, "registry directory source discovery")
    if (
        not isinstance(paths, list) or not paths
        or any(not isinstance(path, str) for path in paths)
    ):
        raise MakeProbeError("registry directory discovery names invalid source candidates")
    resolved = tuple(_repository_report_path(path) for path in paths)
    if resolved != tuple(sorted(set(resolved))) or not set(resolved) <= set(pool):
        raise MakeProbeError("registry directory discovery names invalid source candidates")
    return resolved


def _repository_report_path(path):
    if path == "/repo":
        return "."
    if path.startswith("/repo/"):
        path = path[6:]
    return relative_path(path)


def _bundle_dependency_module_names(session: ProbeSession):
    schema_path = next(
        path for path in _python_module_code(session, "scripts.generated_data.chapterbundle.schema")
        if path.endswith("chapterbundle/schema.py")
    )
    try:
        tree = ast.parse(
            text(session.snapshot.files[schema_path], f"Python code {schema_path}", "utf-8"),
            filename=schema_path,
        )
    except SyntaxError as error:
        raise MakeProbeError(f"Python code {schema_path!r} is not valid Python: {error}") from error
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DEPENDENCY_SCHEMA_MODULES"
                   for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError) as error:
            raise MakeProbeError("chapterbundle dependency module declaration is not literal") from error
        if (
            not isinstance(value, dict)
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items())
        ):
            raise MakeProbeError("chapterbundle dependency module declaration is malformed")
        return tuple(sorted(set(value.values())))
    raise MakeProbeError("chapterbundle dependency module declaration is missing")


def _generated_dependency_option_values(module, details, option_values):
    expected = {option for option, _selector in details["selectors"]}
    actual = set(option_values)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("extra " + ", ".join(extra))
        raise MakeProbeError(
            f"{module} options differ from the declared command contract ({'; '.join(detail)})"
        )
    return tuple(option_values[option] for option, _selector in details["selectors"])


def _generated_dependency_source_paths(session, selector, source):
    source = relative_path(source)
    if source in session.snapshot.files:
        return (source,)
    pool = tuple(sorted(
        path for path in session.snapshot.files
        if path.startswith(source + "/")
    ))
    if not pool:
        return (source,)
    observed = session.command(directory_python_command(
        session,
        (
            "import importlib,json\n"
            "from pathlib import Path\n"
            "module=importlib.import_module(sys.argv[1])\n"
            "source=str(Path('/repo') / Path(sys.argv[2]))\n"
            "def rel(value):\n"
            " path=Path(value)\n"
            " if path.is_absolute():\n"
            "  path=path.relative_to('/repo')\n"
            " if '..' in path.parts:\n"
            "  raise ValueError('dependency source must be repository-relative without parent components')\n"
            " return path.as_posix()\n"
            "print(json.dumps(sorted(rel(path) for path in module.source_paths(source)),separators=(',',':')))\n"
        ),
        (selector, source),
        directories=(source,),
        code=_python_module_code(session, selector),
    ))
    result = parse_json(observed.stdout, "generated dependency primary source selection")
    if (
        not isinstance(result, list) or not result
        or any(not isinstance(path, str) for path in result)
    ):
        raise MakeProbeError("generated dependency source selector returned no concrete inputs")
    paths = tuple(_repository_report_path(path) for path in result)
    if paths != tuple(sorted(set(paths))) or not set(paths) <= set(pool):
        raise MakeProbeError("generated dependency source selector returned invalid concrete inputs")
    return paths


def _chapterbundle_support(session, module, bundle_source, bundle_sources):
    code = set(_python_module_closure(session, module))
    code.update(_python_module_closure(session, "scripts.generated_data.chapterobjectives.deps"))
    for dependency in _bundle_dependency_module_names(session):
        code.update(_python_module_closure(session, dependency))
    bundle_directories = ("assets/tmx", "graphics/map/layout")
    if bundle_source not in session.snapshot.files:
        bundle_directories = (*bundle_directories, bundle_source)
    observed = session.command(directory_python_command(
        session,
        (
            "import glob,importlib,json\n"
            "from pathlib import Path\n"
            "from scripts.generated_data.chapterbundle import schema as bundle_schema\n"
            "from scripts.generated_data.chapterobjectives import deps as objectives_deps\n"
            "root=Path(bundle_schema.REPO_ROOT)\n"
            "importlib.import_module(sys.argv[2])\n"
            "bundle_schema.dependency_module_paths()\n"
            "records=bundle_schema.load_records(str(Path('/repo') / Path(sys.argv[1])))\n"
            "def rel(value):\n"
            " path=Path(value)\n"
            " if path.is_absolute():\n"
            "  path=path.relative_to(root)\n"
            " if '..' in path.parts:\n"
            "  raise ValueError('dependency source must be repository-relative without parent components')\n"
            " return path.as_posix()\n"
            "bundle_refs=set()\n"
            "for bundle in records:\n"
            " for table in bundle.tables:\n"
            "  bundle_refs.add(rel(table.source))\n"
            " bundle_refs.add(rel(bundle.support_owners.source))\n"
            "print(json.dumps({\n"
            " 'directories':sorted({\n"
            "  rel(Path(bundle_schema.ASSET_MANIFEST_PATH).parent),\n"
            "  rel(root / 'assets' / 'tmx'),\n"
            "  rel(bundle_schema.MAP_LAYOUT_DIR),\n"
            " }),\n"
            " 'implementation':sorted(rel(path) for path in objectives_deps._implementation_module_paths()),\n"
            " 'bundle_refs':sorted(bundle_refs),\n"
            " 'members':sorted({rel(path) for path in glob.glob(str(root / 'assets' / 'tmx' / '*.tmx'))}\n"
            "          | {rel(path) for path in glob.glob(str(root / 'graphics' / 'map' / 'layout' / '*.json'))}),\n"
            " 'sources':sorted({\n"
            "  rel(bundle_schema.ASSET_MANIFEST_PATH),\n"
            "  rel(bundle_schema.CHAPTER_DATA_ASSET_TABLE_SOURCE),\n"
            "  rel(bundle_schema.CHAPTER_SETTINGS_JSON),\n"
            "  rel(objectives_deps.objectives_schema.CHAPTERS_HEADER),\n"
            "  rel(objectives_deps.objectives_schema.EVENT_FLAGS_HEADER),\n"
            "  rel(objectives_deps.objectives_schema.character_refs.CHARACTERS_HEADER),\n"
            " })\n"
            "},sort_keys=True,separators=(',',':')))\n"
        ),
        (bundle_source, module),
        sources=bundle_sources,
        directories=bundle_directories,
        code=tuple(sorted(code)),
    ))
    result = parse_json(observed.stdout, "chapterbundle dependency support")
    if (
        not isinstance(result, dict)
        or set(result) != {"bundle_refs", "directories", "implementation", "members", "sources"}
        or not isinstance(result["bundle_refs"], list)
        or not isinstance(result["directories"], list)
        or not isinstance(result["implementation"], list)
        or not isinstance(result["members"], list)
        or not isinstance(result["sources"], list)
        or any(not isinstance(item, str) or not item for group in result.values() for item in group)
    ):
        raise MakeProbeError("chapterbundle dependency support is malformed")
    implementation = tuple(sorted(relative_path(path) for path in result["implementation"]))
    if any(
        not path.endswith(".py") or path not in session.snapshot.files or path not in code
        for path in implementation
    ):
        raise MakeProbeError("chapterbundle dependency support exceeded its admitted Python closure")
    return (
        implementation,
        tuple(sorted(relative_path(path) for path in result["bundle_refs"])),
        tuple(sorted(relative_path(path) for path in result["directories"])),
        tuple(sorted(relative_path(path) for path in result["members"])),
        tuple(sorted(relative_path(path) for path in result["sources"])),
    )


def generated_dependency_command(
    session: ProbeSession,
    module: str,
    *,
    option_values,
    make_target: str,
    depfile: str,
    code=(),
):
    details = GENERATED_DEPENDENCY_MODULES.get(module)
    if details is None:
        raise MakeProbeError(f"unsupported generated dependency module: {module}")
    selector_arguments = _generated_dependency_option_values(module, details, option_values)
    python_code = [
        path for path in code if path.endswith(".py")
    ]
    python_code.extend(_python_module_code(session, module, main=True))
    directories = set()
    expected_inputs = set()
    bundle_sources = ()
    bundle_source = None
    for (option, selector), source in zip(details["selectors"], selector_arguments):
        paths = _generated_dependency_source_paths(session, selector, source)
        expected_inputs.update(paths)
        expected_inputs.add(relative_path(source))
        if option == "--bundle-source":
            bundle_source = source
            bundle_sources = paths
        source = relative_path(source)
        if paths == (source,) and source not in session.snapshot.files:
            directories.add(source)
        elif source not in session.snapshot.files and (session.tree / source).is_dir():
            directories.add(source)
    if details["support_from_bundle"]:
        implementation_code, bundle_refs, dependency_directories, dependency_members, dependency_sources = (
            _chapterbundle_support(session, module, bundle_source, bundle_sources)
        )
        python_code.extend(implementation_code)
        directories.update(dependency_directories)
        directories.update(("assets", "graphics", "include", "include/constants", "src", "src/data"))
        expected_inputs.update(bundle_refs)
        expected_inputs.update(dependency_directories)
        expected_inputs.update(dependency_members)
        expected_inputs.update(dependency_sources)
        expected_inputs.update(implementation_code)
    reported = tuple(sorted(expected_inputs))
    files = []
    declared_directories = set(directories)
    for path in reported:
        relative = _repository_report_path(path)
        if relative.endswith(".py"):
            python_code.append(relative)
        elif relative in session.snapshot.files:
            files.append(relative)
        elif (session.tree / relative).is_dir():
            declared_directories.add(relative)
        else:
            raise MakeProbeError(f"source declaration resolves no regular inputs: {relative}")
    files = tuple(sorted(set(files)))
    source_identities = session.source_owners(files)
    return directory_python_command(
        session,
        (
            "import hashlib,importlib,json,stat\n"
            "from pathlib import Path\n"
            "module=importlib.import_module(sys.argv[1])\n"
            "arguments=json.loads(sys.argv[2])\n"
            "expected=json.loads(sys.argv[5])\n"
            "tracked=json.loads(sys.argv[6])\n"
            "identities={row[0]:tuple(row[1:]) for row in json.loads(sys.argv[7])}\n"
            "collected=[]\n"
            "if set(identities) != set(tracked):\n"
            " raise ValueError('dependency publication source identities must match tracked inputs exactly')\n"
            "def rooted(value):\n"
            " path=Path(value)\n"
            " return str(Path('/repo') / path)\n"
            "def report(value):\n"
            " path=Path(value)\n"
            " if path.is_absolute():\n"
            "  path=path.relative_to('/repo')\n"
            " if '..' in path.parts:\n"
            "  raise ValueError('dependency source must be repository-relative without parent components')\n"
            " return path.as_posix()\n"
            "collected=[report(path) for path in module.collect_input_paths(*(rooted(value) for value in arguments))]\n"
            "if collected != sorted(set(collected)):\n"
            " raise ValueError('dependency collection must be canonical, unique and sorted')\n"
            "if collected != expected:\n"
            " raise ValueError('dependency collection differs from admitted selector/support evidence')\n"
            "for path in tracked:\n"
            " source=Path('/repo') / Path(path)\n"
            " status=source.stat()\n"
            " digest=hashlib.sha256(source.read_bytes()).hexdigest()\n"
            " mode=f'{stat.S_IFREG | stat.S_IMODE(status.st_mode):06o}'\n"
            " if (mode,digest) != identities[path]:\n"
            "  raise ValueError(f'captured source identity changed: {path}')\n"
            "output=Path('/work') / Path(sys.argv[3])\n"
            "output.parent.mkdir(parents=True,exist_ok=True)\n"
            "content=module.render_depfile(sys.argv[4],[rooted(path) for path in collected])\n"
            "output.write_text(content,encoding='utf-8')\n"
        ),
        (
            module,
            json.dumps(selector_arguments, separators=(",", ":")),
            relative_path(depfile),
            make_target,
            json.dumps(reported, separators=(",", ":")),
            json.dumps(files, separators=(",", ":")),
            json.dumps(source_identities, separators=(",", ":")),
        ),
        sources=files,
        outputs=(relative_path(depfile),),
        directories=tuple(sorted(declared_directories)),
        code=tuple(sorted(set(python_code))),
    )
