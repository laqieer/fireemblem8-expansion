"""Real live-Make producer/publication controls, independent of extension V/R/D."""

import hashlib
import errno
import gc
import json
import os
import signal
import socket
import stat
import struct
import subprocess
import time
import unittest
import weakref
import zlib
from collections import Counter
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from scripts.validation_ownership.make_probe import Command, NativeTool, TRUSTED_ROOT
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.authority import (
    ENVIRONMENT, _command_hash, _event_command, _read_event_frames, parse_json,
    relative_path,
)
from scripts.validation_ownership.producer_channel import (
    ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
)
from scripts.validation_ownership.python_commands import (
    GENERATED_DEPENDENCY_MODULES,
    _chapterbundle_support,
    _generated_dependency_option_values,
    _generated_dependency_source_paths,
    _python_module_code,
    _repository_report_path,
    directory_python_command,
    generated_dependency_command,
    python_code_closure,
    python_command,
)
from scripts.validation_ownership.syscall_guard import VO_PRODUCE
from scripts.validation_ownership.tests import test_foundation as foundation


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self):
        self.fixture.tearDown()

    def producer_fixture(self):
        self.fixture.add("producer.py", "open('/work/generated.txt','w').write('actual')\nprint('observed')\n")
        self.fixture.add("Makefile", "VALUE := $(shell python3 producer.py)\nall: ;\n")
        return Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",), outputs=("generated.txt",),
        )

    def test_declared_output_results_are_retained_only_by_actual_owners(self):
        command = self.producer_fixture()
        expected_inputs = (
            ("producer.py", "100644", hashlib.sha256((self.root / "producer.py").read_bytes()).hexdigest()),
        )
        with self.fixture.session() as session:
            execute, executions = session._sandbox_run, []
            def record(root, **kwargs):
                result = execute(root, **kwargs)
                if kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]:
                    executions.append(tuple(kwargs["argv"]))
                return result
            with patch.object(session, "_sandbox_run", record):
                first = session.command(command)
                self.assertEqual(first.stdout, b"observed\n")
                self.assertEqual(first.generated[0].data, b"actual")
                self.assertEqual(first.generated[0].path, "generated.txt")
                self.assertEqual(first.input_identities, expected_inputs)
                first_mode = first.generated[0].mode
                charged = session.budget.bytes["cache"]
                self.assertGreater(charged, len(first.generated[0].data))
                released = weakref.ref(first)
                del first
                gc.collect()
                self.assertIsNone(released())
                second = session.command(command)
            self.assertEqual(len(executions), 2)
            self.assertEqual(executions[0], executions[1])
            self.assertEqual(second.generated[0].mode, first_mode)
            self.assertEqual(second.input_identities, expected_inputs)
            second_metadata, second_execution = second.metadata, second.executed
            self.assertGreater(session.budget.bytes["cache"], charged)
            self.assertFalse(session.cache)
            retained = weakref.ref(second)
        self.fixture.assert_clean(session)
        self.assertIs(retained(), second)
        self.assertEqual(second.stdout, b"observed\n")
        self.assertEqual(second.generated[0].data, b"actual")
        self.assertEqual(second.input_identities, expected_inputs)
        self.assertEqual((second.metadata, second.executed), (second_metadata, second_execution))
        del second
        gc.collect()
        self.assertIsNone(retained())

    def add_repo_file(self, path):
        source = foundation.ROOT / path
        mode = "100755" if source.stat().st_mode & 0o111 else "100644"
        self.fixture.add(path, source.read_bytes(), mode=mode)

    def add_generated_dependency_fixture(self):
        for path in (foundation.ROOT / "scripts" / "generated_data").rglob("*.py"):
            if "tests" not in path.relative_to(foundation.ROOT).parts:
                self.add_repo_file(path.relative_to(foundation.ROOT).as_posix())
        for name in ("scripts/assets/__init__.py", "scripts/assets/tmx.py"):
            if (foundation.ROOT / name).is_file():
                self.add_repo_file(name)

        fixture_root = foundation.ROOT / "scripts" / "generated_data" / "tests" / "fixtures"
        chapterbundle = fixture_root / "chapterbundle"
        self.fixture.add("include/constants/characters.h", "#define CHARACTER_EIRIKA 1\n")
        self.fixture.add("include/constants/event-flags.h", "#define EVFLAG_TMP(n) (n)\n")
        self.fixture.add("include/bmunit.h", "#define FACTION_ID_BLUE 0\n")
        self.fixture.add("include/constants/chapters.h", (chapterbundle / "chapters.h").read_bytes())
        self.fixture.add("src/data/chapter_settings.json", (chapterbundle / "chapter_settings.json").read_bytes())
        self.fixture.add("src/data/data_8B363C.c", (chapterbundle / "data_8B363C.c").read_bytes())
        self.fixture.add("assets/manifest.json", "{}\n")
        self.fixture.add("assets/tmx/Example.tmx", "<map width='1' height='1'/>\n")
        self.fixture.add("assets/tmx/placeholder.txt", "keep\n")
        self.fixture.add("graphics/map/layout/Example.json", "{}\n")

        for name in (
            "deps_units.json",
            "deps_shops.json",
            "deps_traps.json",
            "deps_eventscripts.json",
            "deps_eventlists.json",
            "deps_supports.json",
        ):
            self.fixture.add("testdata/deps/" + name, (chapterbundle / name).read_bytes())
        self.fixture.add(
            "testdata/objectives/el_objectives.json",
            (chapterbundle / "deps_chapterobjectives.json").read_bytes(),
        )
        self.fixture.add(
            "testdata/strategies/el_strategies.json",
            (chapterbundle / "deps_autoplaystrategies.json").read_bytes(),
        )
        bundle = json.loads((chapterbundle / "valid.json").read_text(encoding="utf-8"))
        for table in bundle["tables"].values():
            table["source"] = "testdata/deps/" + Path(table["source"]).name
        bundle["supportOwners"]["source"] = "testdata/deps/deps_supports.json"
        self.fixture.add("testdata/bundles/el_bundle.json", json.dumps(bundle, indent=2) + "\n")
        return [
            {
                "name": "chapterobjectives",
                "module": "scripts.generated_data.chapterobjectives.deps",
                "options": {
                    "--source": "testdata/objectives",
                    "--bundle-source": "testdata/bundles",
                },
                "make_target": "build/generated/data/data_chapter_objectives.c",
                "depfile": "build/generated/data/chapterobjectives.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps --source '
                    '"testdata/objectives" --bundle-source "testdata/bundles" '
                    '--make-target "build/generated/data/data_chapter_objectives.c" '
                    '--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
            },
            {
                "name": "autoplaystrategies",
                "module": "scripts.generated_data.autoplaystrategies.deps",
                "options": {
                    "--source": "testdata/strategies/el_strategies.json",
                    "--objectives-source": "testdata/objectives/el_objectives.json",
                    "--bundle-source": "testdata/bundles/el_bundle.json",
                },
                "make_target": "build/generated/data/data_autoplay_strategies.c",
                "depfile": "build/generated/data/autoplaystrategies.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps --source '
                    '"testdata/strategies/el_strategies.json" --objectives-source '
                    '"testdata/objectives/el_objectives.json" '
                    '--bundle-source "testdata/bundles/el_bundle.json" '
                    '--make-target "build/generated/data/data_autoplay_strategies.c" '
                    '--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "reordered_command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps --bundle-source '
                    '"testdata/bundles/el_bundle.json" --make-target "build/generated/data/data_autoplay_strategies.c" '
                    '--source "testdata/strategies/el_strategies.json" '
                    '--objectives-source "testdata/objectives/el_objectives.json" '
                    '--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "reordered_options": {
                    "--bundle-source": "testdata/bundles/el_bundle.json",
                    "--source": "testdata/strategies/el_strategies.json",
                    "--objectives-source": "testdata/objectives/el_objectives.json",
                },
            },
            {
                "name": "eventlists",
                "module": "scripts.generated_data.eventlists.deps",
                "options": {
                    "--strategy-source": "testdata/strategies/el_strategies.json",
                    "--bundle-source": "testdata/bundles/el_bundle.json",
                },
                "make_target": "build/generated/data/.ch2-eventlists.validated",
                "depfile": "build/generated/data/eventlists.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.eventlists.deps --strategy-source '
                    '"testdata/strategies/el_strategies.json" '
                    '--bundle-source "testdata/bundles/el_bundle.json" '
                    '--make-target "build/generated/data/.ch2-eventlists.validated" '
                    '--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
            },
        ]

    def add_compact_generated_dependency_fixture(self):
        for path in (
            "scripts/generated_data/chapterbundle",
            "scripts/generated_data/chapterobjectives",
            "scripts/generated_data/autoplaystrategies",
            "scripts/generated_data/eventlists",
            "scripts/generated_data/units",
        ):
            self.fixture.add(path + "/__init__.py", "")
        self.fixture.add("scripts/generated_data/character_refs.py", (
            "import os\n"
            "REPO_ROOT=os.path.realpath('/repo')\n"
            "CHARACTERS_HEADER=os.path.join(REPO_ROOT,'include','constants','characters.h')\n"
        ))
        selector = (
            "import glob,os\n"
            "def source_paths(source):\n"
            " if os.path.isdir(source):\n"
            "  paths=sorted(glob.glob(os.path.join(source,'*.json')))\n"
            "  if not paths:\n"
            "   raise ValueError('directory has no sources')\n"
            "  return paths\n"
            " return [source]\n"
        )
        self.fixture.add("scripts/generated_data/autoplaystrategies/schema.py", selector)
        self.fixture.add("scripts/generated_data/chapterobjectives/schema.py", (
            "import glob,os\n"
            "from .. import character_refs\n"
            "REPO_ROOT=os.path.realpath('/repo')\n"
            "CHAPTERS_HEADER=os.path.join(REPO_ROOT,'include','constants','chapters.h')\n"
            "EVENT_FLAGS_HEADER=os.path.join(REPO_ROOT,'include','constants','event-flags.h')\n"
            "def source_paths(source):\n"
            " if os.path.isdir(source):\n"
            "  paths=sorted(glob.glob(os.path.join(source,'*.json')))\n"
            "  if not paths:\n"
            "   raise ValueError('directory has no sources')\n"
            "  return paths\n"
            " return [source]\n"
        ))
        self.fixture.add("scripts/generated_data/units/schema.py", "MARKER = 'units'\n")
        self.fixture.add("scripts/generated_data/chapterbundle/schema.py", (
            "import glob,importlib,json,os\n"
            "from types import SimpleNamespace\n"
            "REPO_ROOT=os.path.realpath('/repo')\n"
            "ASSET_MANIFEST_PATH=os.path.join(REPO_ROOT,'assets','manifest.json')\n"
            "CHAPTER_DATA_ASSET_TABLE_SOURCE=os.path.join(REPO_ROOT,'src','data','chapter_data.c')\n"
            "CHAPTER_SETTINGS_JSON=os.path.join(REPO_ROOT,'src','data','chapter_settings.json')\n"
            "MAP_LAYOUT_DIR=os.path.join(REPO_ROOT,'graphics','map','layout')\n"
            "DEPENDENCY_SCHEMA_MODULES={'units':'scripts.generated_data.units.schema'}\n"
            "def _canonical(path):\n"
            " return os.path.normcase(os.path.realpath(os.path.abspath(path)))\n"
            "def source_paths(source_path, repository_root=REPO_ROOT):\n"
            " source_path=_canonical(source_path)\n"
            " if os.path.isdir(source_path):\n"
            "  paths=sorted(glob.glob(os.path.join(source_path,'*.json')))\n"
            "  if not paths:\n"
            "   raise ValueError('directory has no sources')\n"
            "  return paths\n"
            " return [source_path]\n"
            "def load_records(source_path, repository_root=REPO_ROOT):\n"
            " records=[]\n"
            " for path in source_paths(source_path, repository_root):\n"
            "  raw=json.load(open(path))\n"
            "  tables=[SimpleNamespace(source=entry['source']) for entry in raw['tables']]\n"
            "  support=SimpleNamespace(source=raw['supportOwners']['source'])\n"
            "  records.append(SimpleNamespace(tables=tables,support_owners=support))\n"
            " return records\n"
            "def dependency_module_paths():\n"
            " paths=set([_canonical(__file__)])\n"
            " for name in sorted(DEPENDENCY_SCHEMA_MODULES.values()):\n"
            "  module=importlib.import_module(name)\n"
            "  if getattr(module,'__file__',None):\n"
            "   paths.add(_canonical(module.__file__))\n"
            " return tuple(sorted(paths))\n"
        ))
        self.fixture.add("scripts/generated_data/chapterobjectives/deps.py", (
            "import glob,os,sys\n"
            "from . import schema as objectives_schema\n"
            "from ..chapterbundle import schema as bundle_schema\n"
            "def _canonical(path):\n"
            " return os.path.normcase(os.path.realpath(os.path.abspath(path)))\n"
            "def _implementation_module_paths():\n"
            " package_root=_canonical(os.path.join(bundle_schema.REPO_ROOT,'scripts','generated_data'))\n"
            " bundle_schema.dependency_module_paths()\n"
            " paths=set()\n"
            " for module in tuple(sys.modules.values()):\n"
            "  module_path=getattr(module,'__file__',None)\n"
            "  if module_path is None:\n"
            "   continue\n"
            "  module_path=_canonical(module_path)\n"
            "  if module_path.startswith(package_root + os.sep) and '/tests/' not in module_path:\n"
            "   paths.add(module_path)\n"
            " return tuple(sorted(paths))\n"
            "def collect_input_paths(objectives_source,bundle_source):\n"
            " bundles=bundle_schema.load_records(bundle_source)\n"
            " paths=set(objectives_schema.source_paths(objectives_source))\n"
            " paths.update(bundle_schema.source_paths(bundle_source))\n"
            " paths.update(_implementation_module_paths())\n"
            " paths.update((_canonical(objectives_source),_canonical(bundle_source),\n"
            "              _canonical(objectives_schema.CHAPTERS_HEADER),\n"
            "              _canonical(objectives_schema.EVENT_FLAGS_HEADER),\n"
            "              _canonical(objectives_schema.character_refs.CHARACTERS_HEADER),\n"
            "              _canonical(bundle_schema.ASSET_MANIFEST_PATH),\n"
            "              _canonical(bundle_schema.CHAPTER_DATA_ASSET_TABLE_SOURCE),\n"
            "              _canonical(bundle_schema.CHAPTER_SETTINGS_JSON),\n"
            "              _canonical(os.path.dirname(bundle_schema.ASSET_MANIFEST_PATH)),\n"
            "              _canonical(bundle_schema.MAP_LAYOUT_DIR),\n"
            "              _canonical(os.path.join(bundle_schema.REPO_ROOT,'assets','tmx'))))\n"
            " paths.update(_canonical(path) for path in glob.glob(os.path.join(bundle_schema.REPO_ROOT,'assets','tmx','*.tmx')))\n"
            " paths.update(_canonical(path) for path in glob.glob(os.path.join(bundle_schema.MAP_LAYOUT_DIR,'*.json')))\n"
            " for bundle in bundles:\n"
            "  for table in bundle.tables:\n"
            "   paths.add(_canonical(os.path.join(bundle_schema.REPO_ROOT, table.source)))\n"
            "  paths.add(_canonical(os.path.join(bundle_schema.REPO_ROOT, bundle.support_owners.source)))\n"
            " return tuple(sorted(paths))\n"
            "def render_depfile(target, inputs):\n"
            " return target+': '+' '.join(inputs)+'\\n'\n"
        ))
        self.fixture.add("scripts/generated_data/autoplaystrategies/deps.py", (
            "import os\n"
            "from ..chapterobjectives import deps as objectives_deps\n"
            "from . import schema as strategies_schema\n"
            "def _canonical(path):\n"
            " return os.path.normcase(os.path.realpath(os.path.abspath(path)))\n"
            "def collect_input_paths(strategy_source, objectives_source, bundle_source):\n"
            " paths=set(objectives_deps.collect_input_paths(objectives_source, bundle_source))\n"
            " paths.update(strategies_schema.source_paths(strategy_source))\n"
            " paths.add(_canonical(strategy_source))\n"
            " return tuple(sorted(paths))\n"
            "def render_depfile(target, inputs):\n"
            " return target+': '+' '.join(inputs)+'\\n'\n"
        ))
        self.fixture.add("scripts/generated_data/eventlists/deps.py", (
            "import os\n"
            "from ..autoplaystrategies import schema as strategies_schema\n"
            "from ..chapterbundle import schema as bundle_schema\n"
            "def _canonical(path):\n"
            " return os.path.normcase(os.path.realpath(os.path.abspath(path)))\n"
            "def collect_input_paths(strategy_source, bundle_source):\n"
            " paths=set(strategies_schema.source_paths(strategy_source))\n"
            " paths.update(bundle_schema.source_paths(bundle_source))\n"
            " paths.add(_canonical(strategy_source))\n"
            " paths.add(_canonical(bundle_source))\n"
            " return tuple(sorted(paths))\n"
            "def render_depfile(target, inputs):\n"
            " return target+': '+' '.join(inputs)+'\\n'\n"
        ))
        for path, data in (
            ("include/constants/characters.h", "#define CHARACTER_EIRIKA 1\n"),
            ("include/constants/chapters.h", "#define CHAPTER_L_1 1\n"),
            ("include/constants/event-flags.h", "#define EVFLAG_TMP(n) (n)\n"),
            ("assets/manifest.json", "{}\n"),
            ("assets/tmx/Map.json", "{}\n"),
            ("assets/tmx/Map.tmx", "<map/>\n"),
            ("graphics/map/layout/Map.json", "{}\n"),
            ("src/data/chapter_settings.json", "{\"chapters\":[]}\n"),
            ("src/data/chapter_data.c", "const int gChapterDataAssetTable = 0;\n"),
            ("data/deps_units.json", "{\"value\":\"units\"}\n"),
            ("data/deps_supports.json", "{\"value\":\"supports\"}\n"),
            ("data/objectives/ch1_objectives.json", "{\"value\":\"objectives\"}\n"),
            ("data/strategies/ch1_strategies.json", "{\"value\":\"strategies\"}\n"),
            ("data/bundles/ch1_bundle.json", "{\"tables\":[{\"source\":\"data/deps_units.json\"}],\"supportOwners\":{\"source\":\"data/deps_supports.json\"}}\n"),
        ):
            self.fixture.add(path, data)
        return [
            {
                "name": "chapterobjectives",
                "module": "scripts.generated_data.chapterobjectives.deps",
                "options": {"--source": "data/objectives", "--bundle-source": "data/bundles"},
                "make_target": "build/generated/data/objectives.c",
                "depfile": "build/generated/data/chapterobjectives.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps --source "data/objectives" '
                    '--bundle-source "data/bundles" --make-target "build/generated/data/objectives.c" '
                    '--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
            },
            {
                "name": "autoplaystrategies",
                "module": "scripts.generated_data.autoplaystrategies.deps",
                "options": {
                    "--source": "data/strategies/ch1_strategies.json",
                    "--objectives-source": "data/objectives/ch1_objectives.json",
                    "--bundle-source": "data/bundles/ch1_bundle.json",
                },
                "make_target": "build/generated/data/strategies.c",
                "depfile": "build/generated/data/autoplay.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps '
                    '--source "data/strategies/ch1_strategies.json" '
                    '--objectives-source "data/objectives/ch1_objectives.json" '
                    '--bundle-source "data/bundles/ch1_bundle.json" '
                    '--make-target "build/generated/data/strategies.c" '
                    '--depfile "build/generated/data/autoplay.inputs.mk"'
                ),
            },
            {
                "name": "eventlists",
                "module": "scripts.generated_data.eventlists.deps",
                "options": {
                    "--strategy-source": "data/strategies/ch1_strategies.json",
                    "--bundle-source": "data/bundles",
                },
                "make_target": "build/generated/data/eventlists.validated",
                "depfile": "build/generated/data/eventlists.inputs.mk",
                "command": (
                    'python3 -m scripts.generated_data.eventlists.deps '
                    '--strategy-source "data/strategies/ch1_strategies.json" '
                    '--bundle-source "data/bundles" '
                    '--make-target "build/generated/data/eventlists.validated" '
                    '--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
            },
        ]

    def rewrite_generated_dependency_bundle(self, *, units_source):
        bundle_path = self.root / "testdata/bundles/el_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["tables"]["units"]["source"] = units_source
        self.fixture.add("testdata/bundles/el_bundle.json", json.dumps(bundle, indent=2) + "\n")

    def capture_loader(self, budget):
        return foundation.AuthorityLoader(
            self.root, foundation.GitTreeEntries(self.fixture.entries, budget=budget), budget=budget,
        )

    def capture_complete_loader(self, budget):
        from scripts.validation_ownership.consumer import registry_entries

        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *args],
                env=ENVIRONMENT, capture_output=True, check=True, timeout=15,
            ).stdout

        git("init", "--quiet")
        git("add", "-A", "--", ".")
        revision = git("write-tree").decode().strip()
        entries = registry_entries(self.root, revision, budget)
        return foundation.AuthorityLoader(self.root, entries, revision, budget=budget)

    def legacy_generated_dependency_command(self, session, case):
        details = GENERATED_DEPENDENCY_MODULES[case["module"]]
        selector_arguments = _generated_dependency_option_values(case["module"], details, case["options"])
        python_code = list(_python_module_code(session, case["module"], main=True))
        directories = set()
        discovery_sources = []
        bundle_sources = ()
        bundle_source = None
        for (option, selector), source in zip(details["selectors"], selector_arguments):
            paths = _generated_dependency_source_paths(session, selector, source)
            discovery_sources.extend(paths)
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
                _chapterbundle_support(session, case["module"], bundle_source, bundle_sources)
            )
            python_code.extend(implementation_code)
            directories.update(dependency_directories)
            directories.update(("assets", "graphics", "include", "include/constants", "src", "src/data"))
            discovery_sources.extend(bundle_refs)
            discovery_sources.extend(dependency_members)
            discovery_sources.extend(dependency_sources)
        observed = session.command(directory_python_command(
            session,
            (
                "import importlib,json\n"
                "from pathlib import Path\n"
                "module=importlib.import_module(sys.argv[1])\n"
                "arguments=json.loads(sys.argv[2])\n"
                "def rooted(value):\n"
                " path=Path(value)\n"
                " return str(Path('/repo') / path)\n"
                "def report(value):\n"
                " path=Path(value)\n"
                " if path.is_absolute():\n"
                "  path=path.relative_to('/repo')\n"
                " if '..' in path.parts:\n"
                "  raise ValueError('dependency source must be repository-relative without parent components')\n"
                " return '/repo/' + path.as_posix()\n"
                "print(json.dumps([\n"
                " report(path)\n"
                " for path in module.collect_input_paths(*(rooted(value) for value in arguments))\n"
                "],separators=(',',':')))\n"
            ),
            (case["module"], json.dumps(selector_arguments, separators=(",", ":"))),
            sources=tuple(sorted(set(discovery_sources))),
            directories=tuple(sorted(directories)),
            code=tuple(sorted(set(python_code))),
        ))
        discovery = parse_json(observed.stdout, "generated dependency input discovery")
        if (
            not isinstance(discovery, list) or not discovery
            or any(not isinstance(path, str) or not path for path in discovery)
        ):
            raise MakeProbeError("generated dependency discovery returned no inputs")
        files = []
        declared_directories = set(directories)
        for path in discovery:
            relative = _repository_report_path(path)
            if relative.endswith(".py"):
                python_code.append(relative)
            elif relative in session.snapshot.files:
                files.append(relative)
            else:
                declared_directories.add(relative)
        files = tuple(sorted(set(files)))
        source_identities = session.source_owners(files)
        return directory_python_command(
            session,
            (
                "import hashlib,importlib,json,stat\n"
                "from pathlib import Path\n"
                "module=importlib.import_module(sys.argv[1])\n"
                "inputs=json.loads(sys.argv[4])\n"
                "tracked=json.loads(sys.argv[5])\n"
                "identities={row[0]:tuple(row[1:]) for row in json.loads(sys.argv[6])}\n"
                "if set(identities) != set(tracked):\n"
                " raise ValueError('dependency publication source identities must match tracked inputs exactly')\n"
                "for path in tracked:\n"
                " source=Path('/repo') / Path(path)\n"
                " status=source.stat()\n"
                " digest=hashlib.sha256(source.read_bytes()).hexdigest()\n"
                " mode=f'{stat.S_IFREG | stat.S_IMODE(status.st_mode):06o}'\n"
                " if (mode,digest) != identities[path]:\n"
                "  raise ValueError(f'captured source identity changed: {path}')\n"
                "def rooted(value):\n"
                " path=Path(value)\n"
                " if path.is_absolute():\n"
                "  return '/repo/' + path.relative_to('/repo').as_posix()\n"
                " return '/repo/' + path.as_posix()\n"
                "output=Path('/work') / Path(sys.argv[2])\n"
                "output.parent.mkdir(parents=True,exist_ok=True)\n"
                "content=module.render_depfile(sys.argv[3],[rooted(path) for path in inputs])\n"
                "output.write_text(content,encoding='utf-8')\n"
            ),
            (
                case["module"],
                relative_path(case["depfile"]),
                case["make_target"],
                json.dumps(tuple(discovery), separators=(",", ":")),
                json.dumps(files, separators=(",", ":")),
                json.dumps(source_identities, separators=(",", ":")),
            ),
            sources=files,
            outputs=(relative_path(case["depfile"]),),
            directories=tuple(sorted(declared_directories)),
            code=tuple(sorted(set(python_code))),
        )

    def capture_command_capsules(self, session, callback):
        seen = []
        run = session._sandbox_run

        def record(root, **kwargs):
            seen.append((kwargs["mode"], tuple(kwargs["argv"])))
            return run(root, **kwargs)

        with patch.object(session, "_sandbox_run", record):
            result = callback()
        return result, seen

    def test_static_query_does_not_grant_or_copy_unused_publication_authority(self):
        from scripts.validation_ownership.syscall_guard import Violation
        self.fixture.add("Makefile", "all: ;\n")
        configurations = []
        with self.fixture.session() as session:
            run = session.budget.run
            def capture(argv, **kwargs):
                if str(TRUSTED_ROOT / "sandbox_exec.py") in argv:
                    configurations.append(json.loads(Path(argv[-1]).read_bytes()))
                return run(argv, **kwargs)
            with patch.object(session.budget, "run", capture):
                observed = session.make("all")
            self.assertEqual(observed.events, ())
            self.assertEqual(len(configurations), 1)
            self.assertIsNone(configurations[0]["reserved_paths"])
            self.assertEqual(configurations[0]["publication_limit"], session.budget.limits.created_files)
            self.assertTrue(configurations[0]["producer_endpoint"])
        self.fixture.assert_clean(session)
        policy, outputs = self.publication_policy(False)
        policy.config["reserved_paths"] = configurations[0]["reserved_paths"]
        with self.assertRaisesRegex(Violation, "query has no generated publication authority"):
            policy.publish(1, owner="01"*32, outputs=list(outputs))
        self.assertEqual(policy.created, 0)
        self.assertEqual(policy.written, 0)
        self.assertTrue(all(not (self.root / name).exists() for name in outputs))

    def test_runtime_capture_composes_with_live_remake_and_keeps_env_metadata_only(self):
        generated = "generated-" + self.fixture.directory.name + ".mk"
        absent = "/usr/include/" + generated
        requested = ("/usr/include/stdio.h", "/usr/bin/env", absent)
        self.assertTrue(Path(requested[0]).is_file())
        self.assertFalse(Path(absent).exists())
        for invalid in (False, True):
            with self.subTest(invalid_optional_spelling=invalid):
                _, command, producer = self.include_fixture(generated)
                runtime = "/usr/bin/../bin/env" if invalid else requested[0]
                self.fixture.add("producer.py", (self.root / "producer.py").read_text() + (
                    "with output.open('a') as stream:\n"
                    f" stream.write('RUNTIME := $(wildcard {runtime})\\n')\n"
                ))
                self.fixture.add("sentinel.py", "open('env-executed','w').write('ordinary only')\n")
                self.fixture.add("Makefile", (
                    f"include {generated}\n{generated}: choice.txt\n"
                    f"\t@python3 producer.py {generated}\nall: $(SELECTED)\n"
                    "\t@env /usr/bin/python3 sentinel.py\n"
                ))
                ordinary = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(ordinary.stdout, b"")
                self.assertEqual((self.root / "env-executed").read_text(), "ordinary only")
                (self.root / "env-executed").unlink()
                (self.root / generated).unlink()
                with self.fixture.session(seconds=45, runtime_files=requested) as session:
                    captured, backing = session.runtime_inputs, session.runtime_root
                    if invalid:
                        with self.assertRaisesRegex(MakeProbeError, "optional Make runtime parent spelling"):
                            session.make("all", commands={command: producer})
                    else:
                        observed = session.make(
                            "all", variables=("RUNTIME", "MAKE_RESTARTS"), commands={command: producer},
                        )
                        self.assertEqual(observed.semantics["domains"]["RUNTIME"]["value"], requested[0])
                        self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                        self.assertEqual(len(observed.events), 1)
                        self.assertNotEqual(observed.execution_digest, session.snapshot.digest)
                        self.assertIs(session.runtime_inputs, captured)
                        self.assertIs(session.runtime_root, backing)
                    self.assertFalse((session.tree / generated).exists())
                    self.assertFalse((session.tree / "env-executed").exists())
                    self.assertFalse((self.root / "env-executed").exists())
                self.fixture.assert_clean(session)
                self.assertFalse(backing.exists())

    def test_runtime_backing_preserves_nested_publication_and_source_metadata(self):
        create = self.fixture.session
        runtime = self.fixture.runtime_data_path()
        def session(**limits):
            return create(runtime_files=(runtime, "/usr/bin/env"), **limits)
        with patch.object(self.fixture, "session", session):
            self.test_nested_scope_inherits_ownership_and_preserves_all_file_stat_fields()

    def test_runtime_backing_preserves_exact_generated_execute_permission(self):
        create = self.fixture.session
        def session(**limits):
            return create(runtime_files=("/usr/include/stdio.h", "/usr/bin/env"), **limits)
        with patch.object(self.fixture, "session", session):
            self.test_generated_execute_denial_uses_owner_permissions_not_any_execute_bit()

    def test_original_linker_lookup_denies_instead_of_accepting_empty_output(self):
        path = "scripts/arm_compressing_linker.py"
        self.fixture.add(path, (foundation.ROOT / path).read_bytes(), mode="100755")
        (self.root / path).chmod(0o755)
        self.fixture.add("linker_script_banim.txt", (foundation.ROOT / "linker_script_banim.txt").read_bytes())
        original = "./scripts/arm_compressing_linker.py -t linker_script_banim.txt -m"
        self.fixture.add("original.mk", (
            "INPUTS := $(shell " + original + ")\nall:\n\t@printf '%s\\n' '$(INPUTS)'\n"
        ))
        current = (foundation.ROOT / "Makefile").read_text()
        line = next(line for line in current.splitlines() if line.startswith("$(BANIM_OBJECT):"))
        expression = line.split(":", 1)[1].strip().removesuffix(" $(ASSET_BANIM_COMBINED_LINKER_SCRIPT)")
        self.fixture.add("adapted.mk", "PYTHON := python3\nINPUTS := " + expression + "\nall:\n\t@printf '%s\\n' '$(INPUTS)'\n")
        outputs = []
        for makefile in ("original.mk", "adapted.mk"):
            result = subprocess.run(
                ["/usr/bin/make", "--no-print-directory", "-f", makefile, "all"],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.fixture.directory)},
                capture_output=True, check=True, timeout=15,
            )
            outputs.append(result.stdout)
        self.assertTrue(outputs[0])
        self.assertEqual(outputs[0], outputs[1])
        registration = Command(
            ("/usr/bin/python3", "/repo/" + path, "-t", "linker_script_banim.txt", "-m"),
            code=(path,), sources=("linker_script_banim.txt",),
        )
        with self.fixture.session(seconds=30) as session:
            calls = []
            class Original:
                def __contains__(self, value):
                    calls.append(value)
                    return value == original
                def __getitem__(self, value):
                    return registration
            with self.assertRaisesRegex(MakeProbeError, "Make source executable lookup denied by noexec view"):
                session.make("all", makefile="original.mk", variables=("INPUTS",), commands=Original())
            self.assertEqual(calls, [])
            self.assertEqual(session.processes_used, 1)
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=30) as session:
            observed = session.make(
                "all", makefile="adapted.mk", variables=("INPUTS",),
                commands={"python3 scripts/arm_compressing_linker.py -t linker_script_banim.txt -m": registration},
            )
            self.assertEqual(observed.semantics["domains"]["INPUTS"]["value"], outputs[0].decode().strip())
            self.assertEqual(observed.stderr, b"")
            self.assertEqual(len(observed.events), 1)
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertEqual(dynamic["command"]["argv"], list(registration.argv))
            self.assertEqual(dynamic["command"]["inputs"], session.snapshot.owners((path, "linker_script_banim.txt")))
            self.assertEqual(dynamic["output_sha256"], hashlib.sha256(outputs[0]).hexdigest())
        self.fixture.assert_clean(session)

    def test_make_lookup_guard_preserves_ordinary_absence_nonexecutables_and_metadata(self):
        self.fixture.add("not-executable.py", "print('must not execute')\n")
        self.fixture.add("executable.py", "print('metadata only')\n", mode="100755")
        self.fixture.add("reader.py", (
            "import os\nprint(int(os.access('executable.py',os.X_OK)))\n"
        ))
        self.fixture.add("Makefile", (
            "MISSING := $(shell ./missing-program)\n"
            "NONEXEC := $(shell ./not-executable.py)\n"
            "NAMES := $(wildcard *.py)\nall: ;\n"
        ))
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", variables=("MISSING", "NONEXEC", "NAMES"))
            self.assertEqual(observed.semantics["domains"]["MISSING"]["value"], "")
            self.assertEqual(observed.semantics["domains"]["NONEXEC"]["value"], "")
            self.assertEqual(set(observed.semantics["domains"]["NAMES"]["value"].split()), {
                "executable.py", "not-executable.py", "reader.py",
            })
            command = Command(
                ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("executable.py",),
            )
            # A failed access is not successful source consumption.
            with self.assertRaisesRegex(MakeProbeError, "declared/consumed"):
                session.command(command)
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=30) as session:
            command = Command(
                ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "executable.py"),
            )
            output = session.command(command)
            self.assertEqual(output.stdout, b"0\n")
            self.assertTrue(any(row[0] in {21, 269, 439} and row[6] == -errno.EACCES for row in output.metadata))
            self.assertTrue(session._metadata_matches(output.metadata))
        self.fixture.assert_clean(session)

    def test_generated_execute_denial_uses_owner_permissions_not_any_execute_bit(self):
        from scripts.validation_ownership.sandbox_exec import drop_privileges

        ordinary_drop = (lambda: drop_privileges({"sudo_drop": False})) if os.geteuid() == 0 else None
        self.fixture.add("producer.py", (
            "import os,sys\nfrom pathlib import Path\n"
            "path=Path(sys.argv[1])/'generated.sh'\n"
            "with path.open('wb') as out:\n"
            " out.write(b'#!/bin/sh\\nprintf ordinary-value\\n')\n"
            " os.fchmod(out.fileno(),int(sys.argv[2],8))\n"
        ))
        for mode in (0o644, 0o641, 0o650, 0o601, 0o610, 0o701, 0o741):
            with self.subTest(mode=oct(mode)):
                command = f"python3 producer.py . {mode:o}"
                self.fixture.add("Makefile", (
                    f"GENERATE := $(shell {command})\n"
                    "VALUE := $(shell ./generated.sh)\nall:\n\t@printf '%s\\n' '$(VALUE)'\n"
                ))
                ordinary = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10, preexec_fn=ordinary_drop,
                )
                self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                path = self.root / "generated.sh"
                access = subprocess.run(
                    ["/usr/bin/python3", "-I", "-S", "-B", "-c",
                     "import os,sys;print(int(os.access(sys.argv[1],os.X_OK)))", str(path)],
                    env=ENVIRONMENT, capture_output=True, check=True, timeout=10, preexec_fn=ordinary_drop,
                )
                executable = access.stdout == b"1\n"
                self.assertIn(access.stdout, (b"0\n", b"1\n"))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                self.assertEqual(ordinary.stdout, b"ordinary-value\n" if executable else b"\n")
                path.unlink()
                producer = Command(
                    ("/usr/bin/python3", "/repo/producer.py", "/work", f"{mode:o}"),
                    code=("producer.py",), outputs=("generated.sh",),
                )
                with self.fixture.session(seconds=30) as session:
                    execute, captured = session.command, []
                    def record(value):
                        result = execute(value)
                        captured.extend(result.generated)
                        return result
                    with patch.object(session, "command", record):
                        if executable:
                            with self.assertRaisesRegex(MakeProbeError, "Make source executable lookup denied"):
                                session.make("all", variables=("VALUE",), commands={command: producer})
                        else:
                            result = session.make("all", variables=("VALUE",), commands={command: producer})
                            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "")
                            self.assertEqual(len(result.events), 1)
                    self.assertEqual([item.mode for item in captured], [mode])
                    self.assertFalse((session.tree / "generated.sh").exists())
                self.fixture.assert_clean(session)

    def test_execute_permission_class_precedence_and_actual_kernel_owner_results(self):
        from scripts.validation_ownership.syscall_guard import execute_mode_allows

        owner, primary, extra, unrelated = 17, 29, 31, 41
        for mode, expected in (
            (0o641, (False, False, True)),
            (0o650, (False, True, False)),
            (0o701, (True, False, True)),
            (0o010, (False, True, False)),
            (0o100, (True, False, False)),
            (0o001, (False, False, True)),
        ):
            info = SimpleNamespace(st_uid=owner, st_gid=extra, st_mode=stat.S_IFREG | mode)
            self.assertEqual(
                (
                    execute_mode_allows(info, owner, {primary, extra}),
                    execute_mode_allows(info, unrelated, {primary, extra}),
                    execute_mode_allows(info, unrelated, {primary}),
                ),
                expected,
            )
        info = SimpleNamespace(st_uid=owner, st_gid=extra, st_mode=stat.S_IFREG | 0o001)
        self.assertTrue(execute_mode_allows(info, 0, {primary}))
        root_owned = SimpleNamespace(
            st_uid=0, st_gid=extra, st_mode=stat.S_IFREG | 0o001,
        )
        self.assertFalse(execute_mode_allows(root_owned, 0, {primary}))
        if os.getuid() != 0:
            path = self.fixture.directory / "kernel-permission"
            path.write_bytes(b"owned permission input")
            for mode in (0o644, 0o641, 0o650, 0o701, 0o741):
                path.chmod(mode)
                self.assertEqual(
                    execute_mode_allows(path.stat(), os.getuid(), {os.getgid(), *os.getgroups()}),
                    os.access(path, os.X_OK),
                )

    def test_execute_credentials_use_real_or_filesystem_ids_and_reject_unproven_caps(self):
        from scripts.validation_ownership.syscall_guard import Violation

        policy, _ = self.publication_policy(False)
        status = (
            b"Uid:\t17\t19\t23\t29\nGid:\t31\t37\t41\t43\n"
            b"Groups:\t47 53\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n"
        )
        with patch("builtins.open", mock_open(read_data=status)):
            self.assertEqual(policy.execute_credentials(os.getpid(), False), (17, {31, 47, 53}))
            self.assertEqual(policy.execute_credentials(os.getpid(), True), (29, {43, 47, 53}))
        for malformed in (
            status.replace(b"17\t19\t23\t29", b"17\t19"),
            status + b"Uid:\t17\t19\t23\t29\n",
            status.replace(b"Groups:\t47 53\n", b""),
            status.replace(b"CapPrm:\t0000000000000000", b"CapPrm:\t0000000000000002"),
            status.replace(b"CapEff:\t0000000000000000", b"CapEff:\t0000000000000002"),
        ):
            with self.subTest(status=malformed):
                with patch("builtins.open", mock_open(read_data=malformed)):
                    with self.assertRaises(Violation):
                        policy.execute_credentials(os.getpid(), False)
        if os.getuid() != 0:
            self.assertEqual(
                policy.execute_credentials(os.getpid(), False), (os.getuid(), {os.getgid(), *os.getgroups()}),
            )
            self.assertEqual(
                policy.execute_credentials(os.getpid(), True), (os.geteuid(), {os.getegid(), *os.getgroups()}),
            )

    def test_source_execute_permission_checks_group_acl_and_identity_boundaries(self):
        from scripts.validation_ownership.syscall_guard import Violation

        policy, _ = self.publication_policy(False)
        policy.config["root"] = str(self.fixture.directory)
        self.fixture.add("permission-input", "owned input")
        path = self.root / "permission-input"
        owner, group = path.stat().st_uid, path.stat().st_gid
        with patch.object(policy, "execute_credentials", return_value=(owner + 1, {group})):
            path.chmod(0o601)
            self.assertFalse(policy.source_execute_allowed(os.getpid(), "/repo/permission-input", False))
            path.chmod(0o650)
            self.assertTrue(policy.source_execute_allowed(os.getpid(), "/repo/permission-input", False))
            with patch("os.getxattr", return_value=b"extended ACL is outside the managed mode contract"):
                with self.assertRaisesRegex(Violation, "extended ACL"):
                    policy.source_execute_allowed(os.getpid(), "/repo/permission-input", False)
        with patch.object(policy, "execute_credentials", return_value=(owner + 1, {group + 1})):
            path.chmod(0o601)
            self.assertTrue(policy.source_execute_allowed(os.getpid(), "/repo/permission-input", False))
            with patch("builtins.open", mock_open(read_data=b"0 0 1\n")):
                with self.assertRaisesRegex(Violation, "unrepresentable"):
                    policy.source_execute_allowed(os.getpid(), "/repo/permission-input", False)

    def test_explicit_python_link_recipe_preserves_real_arm_outputs_and_failed_publish(self):
        required = ("/usr/bin/arm-none-eabi-ld", "/usr/bin/arm-none-eabi-objcopy")
        if not all(Path(path).is_file() for path in required):
            self.skipTest("tiny ordinary linker equivalence requires the existing ARM binutils")
        script = "scripts/arm_compressing_linker.py"
        self.fixture.add(script, (foundation.ROOT / script).read_bytes(), mode="100755")
        (self.root / script).chmod(0o755)
        self.fixture.add("input.bin", b"owned linker input\0" * 4)
        self.fixture.add("input.lnk", "input.bin\n")
        lines = (foundation.ROOT / "Makefile").read_text().splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("$(BANIM_OBJECT):"))
        recipe = lines[index + 1]
        control = recipe.replace("$(PYTHON) scripts/arm_compressing_linker.py", "./scripts/arm_compressing_linker.py", 1)
        self.assertNotEqual(recipe, control)
        prelude = (
            "PYTHON := /usr/bin/python3\nLD := /usr/bin/arm-none-eabi-ld\n"
            "OBJCOPY := /usr/bin/arm-none-eabi-objcopy\n"
            "ASSET_BANIM_COMBINED_LINKER_SCRIPT := input.lnk\n"
            "input.bin input.lnk: ;\n"
            "result.o: input.bin input.lnk\n"
        )
        outputs, cleanup_states = [], []
        environment = {**ENVIRONMENT, "TMPDIR": str(self.fixture.directory), "PYTHONDONTWRITEBYTECODE": "1"}
        def collect_residue():
            paths = sorted(self.root.glob(".result.o.*"))
            result = [(path.suffix, path.read_bytes()) for path in paths]
            for path in paths:
                path.unlink()
            return result
        for name, command in (("control.mk", control), ("adapted.mk", recipe)):
            self.fixture.add(name, prelude + command + "\n")
            result = subprocess.run(
                ["/usr/bin/make", "--no-print-directory", "-B", "-f", name, "result.o"],
                cwd=self.root, env=environment, capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(((self.root / "result.o").read_bytes(), (self.root / "result.o.sym.o").read_bytes()))
            cleanup_states.append(collect_residue())
            self.assertFalse((self.root / "result.o.previous").exists())
            self.assertFalse((self.root / "result.o.sym.o.previous").exists())
        self.assertTrue(all(output.startswith(b"\x7fELF") for output in outputs[0]))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(cleanup_states[0], cleanup_states[1])
        self.fixture.add("input.lnk", "missing.bin\n")
        failures = []
        for name in ("control.mk", "adapted.mk"):
            result = subprocess.run(
                ["/usr/bin/make", "--no-print-directory", "-B", "-f", name, "result.o"],
                cwd=self.root, env=environment, capture_output=True, timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            failures.append((result.returncode, collect_residue()))
            self.assertEqual(
                ((self.root / "result.o").read_bytes(), (self.root / "result.o.sym.o").read_bytes()), outputs[0],
            )
            self.assertFalse((self.root / "result.o.previous").exists())
            self.assertFalse((self.root / "result.o.sym.o.previous").exists())
        self.assertEqual(failures[0], failures[1])

    @staticmethod
    def two_color_png():
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        pixels = b"".join(b"\0" + bytes((x + y) % 2 for x in range(8)) for y in range(8))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", b"\0\0\0\xff\xff\xff")
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
        )

    def test_source_authored_gbagfx_family_preserves_argv_bytes_status_and_metadata(self):
        source_root = foundation.ROOT / "tools/gbagfx"
        for path in source_root.iterdir():
            if path.suffix in {".c", ".h"} or path.name == "Makefile":
                self.fixture.add("tools/gbagfx/" + path.name, path.read_bytes())
        built = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-C", "tools/gbagfx"], cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.fixture.directory)},
            capture_output=True, timeout=45,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        recorder = self.fixture.directory / "record-argv.py"
        recorded = self.fixture.directory / "argv.json"
        recorder.write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            f"Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "os.execv(sys.argv[1],sys.argv[1:])\n"
        )
        lines = (foundation.ROOT / "Makefile").read_text().splitlines()
        rules = []
        for index, line in enumerate(lines):
            if line.startswith("%") and ";" in line and any(
                token in line for token in ("$(GBAGFX)", "$(PAL2GBAPAL)")
            ):
                rules.append(line)
            elif line.startswith("\t$(GBAGFX) "):
                rules.append(lines[index - 1] + "\n" + line)
        self.assertEqual(len(rules), 9)
        real_image = "graphics/banim/banim_lorm_sp1_sheet_0.png"
        cases = {
            "%.1bpp:": ("tile.1bpp", "tile.png", self.two_color_png(), []),
            "%.4bpp:": (real_image.removesuffix(".png") + ".4bpp", real_image,
                        (foundation.ROOT / real_image).read_bytes(), []),
            "%.8bpp:": ("tile.8bpp", "tile.png", self.two_color_png(), []),
            "%.gbapal: %.pal": ("tile.gbapal", "tile.pal", b"JASC-PAL\r\n0100\r\n2\r\n0 0 0\r\n255 255 255\r\n", []),
            "%.gbapal: %.png": ("tile.gbapal", "tile.png", self.two_color_png(), []),
            "%.lz: %": ("sample.bin.lz", "sample.bin", b"actual compression input\0"*64, ["-mindist", "2"]),
            "fe6sio_payload.bin.lz:": ("fe6sio_payload.bin.lz", "mgfembp/mgfembp.bin", b"owned raw input"*64, ["-mindist", "1"]),
            "%.rl:": ("sample.bin.rl", "sample.bin", b"repeated input\0"*64, []),
            "%.lz:$(MAP_LAYOUT_SUBDIR)": ("layout.lz", "maps/layout.bin", b"owned map input\0"*64, []),
        }
        expected_outputs = {}
        for index, rule in enumerate(rules):
            key, = [key for key in cases if rule.startswith(key)]
            target, source, data, flags = cases[key]
            self.fixture.add(source, data)
            current = f"rule-{index}.mk"
            control = f"control-{index}.mk"
            prelude = "GBAGFX := tools/gbagfx/gbagfx\nPAL2GBAPAL := $(GBAGFX)\nMAP_LAYOUT_SUBDIR := maps\nLZ_FLAGS := -mindist 2\n"
            self.fixture.add(current, prelude + rule + "\n")
            self.fixture.add(control, prelude + rule.removesuffix(";") + "\n")
            outputs, arguments = [], []
            for makefile in (control, current):
                (self.root / target).unlink(missing_ok=True)
                result = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-f", makefile, target,
                     f"GBAGFX=/usr/bin/python3 {recorder} tools/gbagfx/gbagfx"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, (rule, result.stderr))
                outputs.append((self.root / target).read_bytes())
                arguments.append(json.loads(recorded.read_bytes()))
            self.assertTrue(outputs[0])
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(arguments, [["tools/gbagfx/gbagfx", source, target, *flags]]*2)
            expected_outputs[target] = outputs[1]
            (self.root / target).unlink()
            (self.root / target).mkdir()
            rejected = []
            for makefile in (control, current):
                result = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-B", "-f", makefile, target],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=15,
                )
                self.assertNotEqual(result.returncode, 0, rule)
                rejected.append(result.returncode)
                self.assertTrue((self.root / target).is_dir())
                self.assertEqual(list((self.root / target).iterdir()), [])
            self.assertEqual(rejected[0], rejected[1])
            (self.root / target).rmdir()
            if source.endswith(".png"):
                original = (self.root / source).read_bytes()
                (self.root / source).write_bytes(b"invalid PNG")
                failed = []
                for makefile in (control, current):
                    result = subprocess.run(
                        ["/usr/bin/make", "--no-print-directory", "-f", makefile, target],
                        cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=15,
                    )
                    self.assertNotEqual(result.returncode, 0, rule)
                    failed.append((result.returncode, (self.root / target).exists()))
                    (self.root / target).unlink(missing_ok=True)
                self.assertEqual(failed[0], failed[1])
                (self.root / source).write_bytes(original)
        with self.fixture.session(seconds=60) as session:
            for index, rule in enumerate(rules):
                key, = [key for key in cases if rule.startswith(key)]
                target, source, _, _ = cases[key]
                observed = session.make(target, makefile=f"rule-{index}.mk")
                self.assertEqual(observed.events, ())
                self.assertEqual(observed.semantics["dynamic_commands"], [])
                self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                    {"name": source, "order_only": False},
                ])
                self.assertFalse((session.tree / target).exists())
                self.assertEqual(observed.semantics["files"][0]["recipe"].strip().rstrip(";"),
                                 rule.split(";", 1)[1].strip().rstrip(";") if "\n" not in rule
                                 else rule.split("\n", 1)[1].strip().rstrip(";"))
        self.fixture.assert_clean(session)
    def capture_reports(self, session, reports):
        read = session.budget.read_bytes
        def capture(path, category):
            data = read(path, category)
            if path.parent == session.base and path.name.startswith("report-") and path.suffix == ".json":
                reports.append(json.loads(data))
            return data
        return patch.object(session.budget, "read_bytes", capture)

    def assert_settled_reports(self, session, reports):
        self.assertTrue(reports)
        for field, attribute in (
            ("processes", "processes_used"), ("syscalls", "syscalls_used"),
            ("observations", "observations_used"), ("created_files", "files_created"),
        ):
            self.assertEqual(getattr(session, attribute), sum(report[field] for report in reports))
        self.assertEqual(session.budget.bytes["sandbox"], sum(report["written_bytes"] for report in reports))

    def include_fixture(self, path="generated.mk", *, parse_time=False):
        self.fixture.add("choice.txt", "observed")
        self.fixture.add("producer.py", (
            "from pathlib import Path\nimport sys\n"
            "value=Path('choice.txt').read_text().split()[0]\n"
            "output=Path(sys.argv[1]); output.parent.mkdir(parents=True,exist_ok=True)\n"
            "output.write_text('SELECTED := '+value+'\\n'+value+': ;\\n')\n"
        ))
        names = (
            "SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS", "MAKEFLAGS", "MAKEOVERRIDES",
            "MAKECMDGOALS", "MAKE", "LD_PRELOAD", "VO_OBSERVE_TARGET", "FIRST",
        )
        command = f"python3 producer.py {path}"
        aliases = "".join(
            f"CTX_{index}_{field} = $({form}{name})\n"
            for index, name in enumerate(names)
            for field, form in (("value", ""), ("origin", "origin "), ("flavor", "flavor "))
        )
        recipe = " ".join(
            f"'$(CTX_{index}_{field})'" for index in range(len(names))
            for field in ("value", "origin", "flavor")
        )
        self.fixture.add("Makefile", (
            aliases + f"FIRST := $(if $(wildcard {path}),present,missing)\n"
            + (f"$(shell {command})\n" if parse_time else "")
            + f"include {path}\n{path}: choice.txt\n\t@{command}\n"
            + f"all: $(SELECTED)\n\t@printf '%s\\n' '$^' {recipe}\n"
        ))
        return names, command, Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work/" + path),
            code=("producer.py",), sources=("choice.txt",), outputs=(path,),
        )

    def publication_policy(self, privileged):
        from scripts.validation_ownership.syscall_guard import Policy
        root = self.fixture.directory / "publication-control"
        mapping = root / "control/map"
        mapping.mkdir(parents=True, exist_ok=True)
        outputs = {
            "generated/deeper/nested.mk": (b"SELECTED := observed\n", 0o644),
            "source/binary.bin": (b"\0\xffproof", 0o444),
        }
        frame = bytearray(
            PUBLICATION_MAGIC + b"\x01"*32
            + struct.pack("<II", PUBLICATION_POLICIES.index("replace"), len(outputs))
        )
        for name, (data, mode) in outputs.items():
            name = name.encode()
            frame.extend(struct.pack("<III", len(name), mode, len(data)) + name + data)
        (mapping / "0000000000000001.files").write_bytes(frame)
        return Policy({
            "root": str(root), "mode": "make", "code": [], "sources": [],
            "enumerations": [], "executables": [], "python_version": "3.12", "argv": [],
            "mounts": [{"target": "/repo", "source": str(self.root)}],
            "reserved_paths": list(self.fixture.entries),
            "sudo_drop": privileged, "runner_uid": os.getuid(), "runner_gid": os.getgid(),
            "creation_limit": 8, "file_limit": 4096, "observation_limit": 8192,
            "write_limit": 4096, "deadline": time.monotonic() + 10,
        }), outputs

    @unittest.skipIf(os.geteuid() == 0, "requires an unprivileged cleanup runner")
    def test_publication_transfers_only_new_objects_on_the_privileged_route(self):
        self.fixture.add("source/owner", "immutable")
        (self.root / "source").chmod(0o750)
        for privileged in (False, True):
            with self.subTest(privileged=privileged):
                policy, outputs = self.publication_policy(privileged)
                protected = (
                    self.root, self.root / "source", self.root / "source/owner",
                    Path(policy.config["root"]) / "control/map/0000000000000001.files",
                )
                def ownership(path):
                    info = path.stat(follow_symlinks=False)
                    return info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)
                before = {path: ownership(path) for path in protected}
                transferred, fchown = [], os.fchown
                def transfer(descriptor, uid, gid):
                    self.assertTrue(privileged)
                    info = os.fstat(descriptor)
                    transferred.append((info.st_dev, info.st_ino, uid, gid))
                    fchown(descriptor, uid, gid)
                with patch.object(os, "fchown", transfer):
                    effective = policy.publish(1, owner="01"*32, outputs=list(outputs))
                    self.assertEqual([item["path"] for item in effective], list(outputs))
                    self.assertTrue(all(item["effect"] == "created" for item in effective))
                created = [self.root / name for name in outputs]
                created.extend((self.root / "generated", self.root / "generated/deeper"))
                expected = []
                for path in created:
                    info = path.stat(follow_symlinks=False)
                    self.assertEqual((info.st_uid, info.st_gid), (os.getuid(), os.getgid()))
                    mode = outputs[path.relative_to(self.root).as_posix()][1] if path.is_file() else 0o755
                    self.assertEqual(stat.S_IMODE(info.st_mode), mode)
                    expected.append((info.st_dev, info.st_ino, os.getuid(), os.getgid()))
                self.assertCountEqual(transferred, expected if privileged else [])
                self.assertEqual({path: ownership(path) for path in protected}, before)
                self.assertEqual(policy.created, 4)
                self.assertEqual(policy.written, sum(len(data) for data, _ in outputs.values()))
                for name, (data, _) in outputs.items():
                    self.assertEqual((self.root / name).read_bytes(), data)
                    (self.root / name).unlink()
                (self.root / "generated/deeper").rmdir()
                (self.root / "generated").rmdir()
                self.assertEqual({path: ownership(path) for path in protected}, before)
                self.assertEqual((self.root / "source/owner").read_bytes(), b"immutable")

    @unittest.skipIf(os.geteuid() == 0, "requires an unprivileged cleanup runner")
    def test_publication_transfer_failure_preserves_primary_error_descriptors_and_cleanup(self):
        self.fixture.add("source/owner", "immutable")
        for kind in ("directory", "file"):
            for number in (errno.EPERM, errno.EOPNOTSUPP):
                with self.subTest(kind=kind, error=number):
                    policy, outputs = self.publication_policy(True)
                    failure = OSError(number, "publication ownership transfer unavailable")
                    fchown = os.fchown
                    def transfer(descriptor, uid, gid):
                        mode = os.fstat(descriptor).st_mode
                        if stat.S_ISDIR(mode) == (kind == "directory"):
                            raise failure
                        fchown(descriptor, uid, gid)
                    descriptors = len(list(Path("/proc/self/fd").iterdir()))
                    try:
                        with patch.object(os, "fchown", transfer):
                            with self.assertRaises(OSError) as caught:
                                policy.publish(1, owner="01"*32, outputs=list(outputs))
                        self.assertIs(caught.exception, failure)
                        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), descriptors)
                        self.assertFalse(policy.published)
                        leaf = self.root / "generated/deeper/nested.mk"
                        if leaf.exists():
                            self.assertEqual(leaf.read_bytes(), b"")
                        self.assertFalse((self.root / "source/binary.bin").exists())
                    finally:
                        foundation.shutil.rmtree(self.root / "generated", ignore_errors=False)
                        (self.root / "source/binary.bin").unlink(missing_ok=True)
                    self.assertFalse((self.root / "generated").exists())
                    self.assertEqual((self.root / "source/owner").read_bytes(), b"immutable")

    def test_parse_time_and_remade_includes_preserve_full_context_and_repeated_query_cleanup(self):
        for parse_time, path in ((False, "generated.mk"), (True, "generated/nested.mk")):
            with self.subTest(parse_time=parse_time):
                names, command, registration = self.include_fixture(path, parse_time=parse_time)
                assignments = (("command-line", "A", "one"), ("command-line", "B", "$(A)-two"))
                ordinary = self.fixture.ordinary_assignment_context(assignments, names)
                data = (self.root / path).read_bytes()
                (self.root / path).unlink()
                if "/" in path:
                    (self.root / path).parent.rmdir()
                with self.fixture.session(seconds=45) as session:
                    output = session.command(registration)
                    self.assertEqual(output.consumed, ("choice.txt",))
                    self.assertEqual(output.stdout, b"")
                    self.assertEqual(
                        [(item.path, item.data, item.mode) for item in output.generated],
                        [(path, data, 0o644)],
                    )
                    results = []
                    for _ in range(2):
                        observed = session.make(
                            "all", variables=names, assignments=assignments, commands={command: registration},
                        )
                        results.append(observed)
                        self.assertEqual(observed.semantics["domains"], ordinary[1])
                        self.assertEqual(observed.semantics["files"][0]["prerequisites"], ordinary[0])
                        self.assertEqual(
                            observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "" if parse_time else "1",
                        )
                        self.assertEqual(observed.semantics["domains"]["FIRST"]["value"], "missing" if parse_time else "present")
                        dynamic, = observed.semantics["dynamic_commands"]
                        self.assertEqual(dynamic["generated_outputs"], [
                            (path, "100644", hashlib.sha256(data).hexdigest()),
                        ])
                        self.assertIn(dynamic["generated_outputs"][0], observed.semantics["owner_inputs"])
                        self.assertFalse((session.tree / path).exists())
                        if "/" in path:
                            self.assertFalse((session.tree / path).parent.exists())
                    self.assertEqual(results[0].semantic_digest, results[1].semantic_digest)
                self.fixture.assert_clean(session)

    def test_immutable_views_isolate_cache_native_files_and_generated_make_outputs(self):
                names, source_command, producer = self.include_fixture("generated/vars.mk")
                self.fixture.add("reader.py", "print(open('choice.txt').read())\n")
                self.fixture.add("native.c", '#include <stdio.h>\nint main(void) { puts("observed"); }\n')
                self.fixture.add("stable.mk", "NATIVE := $(shell tools/native;)\nstable: ;\n")
                budget = foundation.ProbeBudget()
                self.fixture.add("choice.txt", "base")
                base = self.fixture.capture_view(budget)
                self.fixture.add("choice.txt", "current")
                current = self.fixture.capture_view(budget)
                reader = Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("choice.txt",),
                )
                def observe(session, tool, value):
                    stable = session.make("stable", makefile="stable.mk", variables=("NATIVE",), commands={
                        "tools/native;": Command(("/native/tool",), native_tool=tool),
                    })
                    generated = session.make("all", variables=names, commands={source_command: producer})
                    self.assertEqual(generated.semantics["domains"]["SELECTED"]["value"], value)
                    self.assertEqual(generated.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                    self.assertEqual(generated.semantics["files"][0]["prerequisites"], [
                        {"name": value, "order_only": False},
                    ])
                    self.assertFalse((session.tree / "generated").exists())
                    self.assertFalse(session.published_sources)
                    self.assertFalse(session.published_versions)
                    self.assertFalse(session.generated_paths)
                    return stable, generated
                with foundation.ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget) as session:
                    original = session.tree, session.cache, session.mappings, session.native_tools
                    deadline = budget.deadline
                    current_result = session.command(reader)
                    current_tool = session.compile_native(("native.c",))
                    current_stable, current_generated = observe(session, current_tool, "current")
                    before = budget.runs, session.files_created, session.processes_used, dict(budget.bytes)
                    with session.select_view(base):
                        self.assertFalse(session.cache)
                        self.assertFalse(session.native_tools)
                        self.assertEqual(session.command(reader).stdout, b"base\n")
                        base_tool = session.compile_native(("native.c",))
                        self.assertEqual(base_tool.digest, current_tool.digest)
                        self.assertNotEqual(base_tool.path, current_tool.path)
                        base_stable, base_generated = observe(session, base_tool, "base")
                        self.assertEqual(base_stable.semantic_digest, current_stable.semantic_digest)
                        self.assertNotEqual(base_stable.execution_digest, current_stable.execution_digest)
                        self.assertNotEqual(base_generated.semantic_digest, current_generated.semantic_digest)
                        self.assertFalse((original[0] / "generated").exists())
                        self.assertGreater(budget.runs, before[0])
                        self.assertGreater(session.files_created, before[1])
                        self.assertGreater(session.processes_used, before[2])
                        self.assertTrue(all(budget.bytes.get(key, 0) >= value for key, value in before[3].items()))
                    self.assertEqual((session.tree, session.cache, session.mappings, session.native_tools), original)
                    self.assertEqual(budget.deadline, deadline)
                    self.assertFalse(base_tool.path.exists())
                    self.assertTrue(current_tool.path.exists())
                    self.assertEqual(session.command(reader).stdout, current_result.stdout)
                    self.assertEqual(session.native(current_tool).stdout, b"observed\n")
                    with self.assertRaisesRegex(MakeProbeError, "not issued"):
                        session.command(Command(("/native/tool",), native_tool=base_tool))
                self.fixture.assert_clean(session)

    def test_explicit_enumeration_tracks_selected_and_generated_views_without_extra_reads(self):
                self.fixture.add("data/base.txt", "base")
                self.fixture.add("reader.py", "import os\nprint(' '.join(sorted(os.listdir('data'))))\n")
                self.fixture.add("producer.py", (
                    "import os\nos.mkdir('/work/data')\n"
                    "open('/work/data/generated.txt','w').write('generated')\n"
                    "open('/work/trigger.mk','w').write('VALUE := $(shell python3 reader.py)\\n')\n"
                ))
                self.fixture.add("Makefile", "include trigger.mk\ntrigger.mk:\n\t@python3 producer.py\nall: ;\n")
                budget = foundation.ProbeBudget()
                base = self.fixture.capture_view(budget)
                (self.root / "data/base.txt").unlink()
                self.fixture.entries.pop("data/base.txt")
                self.fixture.add("data/current.txt", "current")
                current = self.fixture.capture_view(budget)
                reader = Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
                )
                producer = Command(
                    ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
                    outputs=("trigger.mk", "data/generated.txt"),
                )
                with foundation.ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget) as session:
                    current_tree, current_cache = session.tree, session.cache
                    first = session.command(reader)
                    self.assertEqual(first.stdout, b"current.txt\n")
                    self.assertEqual(first.consumed, ())
                    with session.select_view(base):
                        self.assertEqual(session.command(reader).stdout, b"base.txt\n")
                        before = session.observations_used
                        observed = session.make("all", variables=("VALUE",), commands={
                            "python3 reader.py": reader, "python3 producer.py": producer,
                        })
                        self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "base.txt generated.txt")
                        self.assertGreater(session.observations_used, before)
                        self.assertFalse((current_tree / "data/generated.txt").exists())
                        self.assertFalse((session.tree / "data/generated.txt").exists())
                    self.assertIs(session.tree, current_tree)
                    self.assertIs(session.cache, current_cache)
                    self.assertEqual(session.command(reader).stdout, b"current.txt\n")
                    observed = session.make("all", variables=("VALUE",), commands={
                        "python3 reader.py": reader, "python3 producer.py": producer,
                    })
                    self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "current.txt generated.txt")
                    self.assertFalse((session.tree / "data/generated.txt").exists())
                    self.assertFalse(session.published_sources)
                self.fixture.assert_clean(session)

    def test_view_selection_cannot_replace_a_live_publication_after_native_exit(self):
                _, command, producer = self.include_fixture()
                budget = foundation.ProbeBudget()
                loader = self.fixture.capture_view(budget)
                blocked = []
                with foundation.ProbeSession(loader, scratch_root=self.fixture.scratch, budget=budget) as session:
                    original = session._sandbox_run
                    tree = session.tree
                    def completed(root, **kwargs):
                        result = original(root, **kwargs)
                        if kwargs["mode"] == "make":
                            self.assertFalse(budget.children)
                            self.assertEqual(session.pending_commands, 0)
                            self.assertIn("generated.mk", session.published_sources)
                            with self.assertRaisesRegex(MakeProbeError, "active report execution"):
                                with session.select_view(loader):
                                    self.fail("replaced an active publication view")
                            self.assertIs(session.tree, tree)
                            blocked.append(True)
                        return result
                    with patch.object(session, "_sandbox_run", completed):
                        observed = session.make("all", variables=("SELECTED",), commands={command: producer})
                    self.assertEqual(observed.semantics["domains"]["SELECTED"]["value"], "observed")
                    self.assertEqual(blocked, [True])
                    self.assertFalse(session.published_sources)
                self.fixture.assert_clean(session)

    def test_native_registration_rejects_missing_forged_foreign_wrong_argv_and_changed_tools(self):
        self.fixture.add("native.c", '#include <stdio.h>\nint main(void) { puts("observed"); }\n')
        self.fixture.add("Makefile", "VALUE := $(shell tools/native;)\nall: ;\n")
        with self.fixture.session(seconds=30) as session:
            foreign = session.compile_native(("native.c",))
            positive = session.make("all", variables=("VALUE",), commands={
                "tools/native;": Command(("/native/tool",), native_tool=foreign),
            })
            self.assertEqual(positive.semantics["domains"]["VALUE"]["value"], "observed")
        self.fixture.assert_clean(session)
        for case in ("missing", "forged", "foreign", "wrong-argv", "changed"):
            with self.subTest(case=case):
                with self.fixture.session(seconds=30) as session:
                    tool = session.compile_native(("native.c",))
                    argv = ("/native/tool",)
                    expected = "issued by this exact probe session"
                    if case == "missing":
                        tool, expected = None, "supported exact trusted argv"
                    elif case == "forged":
                        tool = NativeTool(tool.path, tool.digest, tool.inputs)
                    elif case == "foreign":
                        tool = foreign
                    elif case == "wrong-argv":
                        argv, expected = ("/usr/bin/printf", "fake"), "exact /native/tool argv"
                    else:
                        tool.path.chmod(0o700)
                        tool.path.write_bytes(b"changed")
                        expected = "sealed native tool changed"
                    before = session.processes_used
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.make("all", commands={"tools/native;": Command(argv, native_tool=tool)})
                    self.assertEqual(session.processes_used - before, 2)
                self.fixture.assert_clean(session)

    def test_native_file_results_use_the_existing_capture_publication_and_remake_seam(self):
        self.fixture.add("native.c", (
            "#include <stdio.h>\n"
            "int main(int argc, char **argv) { int c; FILE *in, *out;"
            "if(argc!=3) return 1; in=fopen(argv[1],\"rb\"); if(!in) return 2;"
            "out=fopen(argv[2],\"wb\"); if(!out) return 3;"
            "while((c=fgetc(in))!=EOF) if(fputc(c,out)==EOF) return 4;"
            "return fclose(in)||fclose(out); }\n"
        ))
        self.fixture.add("input.mk", "SELECTED := observed\n")
        self.fixture.add("Makefile", (
            "include generated.mk\ngenerated.mk: input.mk\n"
            "\t@tools/native input.mk generated.mk;\nall: $(SELECTED)\nobserved: ;\n"
        ))
        with self.fixture.session(seconds=40) as session:
            tool = session.compile_native(("native.c",))
            output = session.native(
                tool, ("input.mk", "/work/generated.mk"), sources=("input.mk",), outputs=("generated.mk",),
            )
            self.assertEqual(output.generated[0].data, b"SELECTED := observed\n")
            self.assertEqual(output.consumed, ("input.mk",))
            self.assertFalse((session.tree / "generated.mk").exists())
            observed = session.make("all", variables=("MAKE_RESTARTS",), commands={
                "tools/native input.mk generated.mk;": Command(
                    ("/native/tool", "input.mk", "/work/generated.mk"),
                    native_tool=tool, sources=("input.mk",), outputs=("generated.mk",),
                ),
            })
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "observed", "order_only": False},
            ])
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertEqual(dynamic["command"]["native_tool"], {
                "sha256": tool.digest, "inputs": list(tool.inputs),
            })
            self.assertEqual(dynamic["command"]["inputs"], session.snapshot.owners(("input.mk",)))
            self.assertFalse((session.tree / "generated.mk").exists())
            self.assertFalse((session.tree / "native/tool").exists())
        self.fixture.assert_clean(session)

    def test_missing_direct_native_path_is_not_synthesized_into_the_make_view(self):
        self.fixture.add("native.c", '#include <stdio.h>\nint main(void) { puts("observed"); }\n')
        self.fixture.add("Makefile", "all:\n\t+@tools/native\n")
        with self.fixture.session(seconds=30) as session:
            tool = session.compile_native(("native.c",))
            requested = []
            class Commands:
                def __contains__(self, command):
                    requested.append(command)
                    return command == "tools/native"
                def __getitem__(self, command):
                    return Command(("/native/tool",), native_tool=tool)
            with self.assertRaisesRegex(MakeProbeError, "GNU Make failed.*No such file or directory"):
                session.make("all", commands=Commands())
            self.assertEqual(requested, [])
            self.assertFalse((session.tree / "tools/native").exists())
        self.fixture.assert_clean(session)

    def test_live_multi_output_aliases_keep_modes_bytes_and_real_effects(self):
        self.fixture.add("producer.py", (
            "from pathlib import Path\nimport os,sys\nroot=Path(sys.argv[1])\n"
            "(root/'generated.mk').write_text('SELECTED := observed\\nobserved: ;\\n')\n"
            "binary=root/'proof.bin'; binary.unlink(missing_ok=True)\n"
            "with binary.open('wb') as out:\n out.write(b'\\x00\\xffproof'); os.fchmod(out.fileno(),0o444)\n"
        ))
        direct = "python3 producer.py ."
        alias = direct + "; printf ''"
        self.fixture.add("Makefile", (
            f"FIRST := $(shell {direct})\nSECOND := $(shell {alias})\n"
            "include generated.mk\nall: $(SELECTED) proof.bin\n\t@printf '%s\\n' '$^'\n"
        ))
        ordinary = self.fixture.ordinary_assignment_context((), ())
        for name in ("generated.mk", "proof.bin"):
            (self.root / name).unlink()
        command = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work"), code=("producer.py",),
            outputs=("generated.mk", "proof.bin"),
        )
        with self.fixture.session(seconds=30) as session:
            execute, results = session.command, []
            def capture(value):
                result = execute(value)
                results.append(result)
                return result
            with patch.object(session, "command", capture):
                observed = session.make("all", commands={
                    direct: command, alias: replace(command, outputs=tuple(reversed(command.outputs))),
                })
            self.assertEqual(len(results), 2)
            self.assertIsNot(results[0], results[1])
            self.assertEqual(
                [(item.data, item.mode) for item in results[0].generated if item.path == "proof.bin"],
                [(b"\0\xffproof", 0o444)],
            )
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], ordinary[0])
            self.assertEqual(len(observed.events), 2)
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertEqual(dynamic["generated_outputs"], [
                (item.path, f"{stat.S_IFREG | item.mode:06o}", hashlib.sha256(item.data).hexdigest())
                for item in results[0].generated
            ])
            self.assertFalse(any((session.tree / path).exists() for path in command.outputs))
        self.fixture.assert_clean(session)

    def test_generated_make_dispatched_sources_and_output_namespaces_remain_exact(self):
        for case in ("missing-registration", "unused-source", "undeclared-source", "symlink-source"):
            with self.subTest(case=case):
                _, command, registration = self.include_fixture()
                if case == "unused-source":
                    self.fixture.add("unused.txt", "not consumed")
                    registration = replace(registration, sources=("choice.txt", "unused.txt"))
                elif case == "undeclared-source":
                    registration = replace(registration, sources=())
                elif case == "symlink-source":
                    registration = replace(registration, outputs=("link/generated.mk",))
                    (self.root / "link").symlink_to("source")
                    self.fixture.entries["link"] = foundation.GitTreeEntry("link", "120000", "blob", "0"*40)
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaisesRegex(MakeProbeError, {
                        "missing-registration": "unregistered eager/recursive",
                        "unused-source": "declared/consumed", "undeclared-source": "undeclared source",
                        "symlink-source": "conflicts with immutable",
                    }[case]):
                        session.make("all", commands={} if case == "missing-registration" else {command: registration})
                    self.assertFalse((session.tree / "generated.mk").exists())
                self.fixture.assert_clean(session)

    def test_generated_include_cannot_acquire_authority_after_a_proven_native_restart(self):
        for expression, expected in (
            ("", None),
            ("$(file >/control/result,forged)", "supervisor channel denied"),
            ("$(file >/repo/Makefile,changed)", "write outside"),
            ("$(file </control/map/count)", "supervisor channel denied"),
            ("load /lib/vo-observer.so", "GNU Make failed after live producers: 125"),
        ):
            with self.subTest(expression=expression):
                self.fixture.add("payload.txt", (
                    "RESTART_WITNESS := $(shell printf %s $(MAKE_RESTARTS))\n"
                    + expression + "\nSELECTED := observed\n"
                ))
                self.fixture.add("producer.py", (
                    "open('/work/generated.mk','wb').write(open('payload.txt','rb').read())\n"
                ))
                makefile = (
                    "include generated.mk\ngenerated.mk:\n\t@python3 producer.py\n"
                    "all: $(SELECTED)\nobserved: ;\n"
                )
                self.fixture.add("Makefile", makefile)
                producer = Command(
                    ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
                    sources=("payload.txt",), outputs=("generated.mk",),
                )
                witness = Command(("/usr/bin/printf", "%s", "1"))
                with self.fixture.session(seconds=30) as session:
                    execute, calls = session.command, []
                    def capture(command):
                        result = execute(command)
                        calls.append((command, result.stdout))
                        return result
                    commands = {"python3 producer.py": producer, "printf %s 1": witness}
                    with patch.object(session, "command", capture):
                        if expected is None:
                            observed = session.make(
                                "all", variables=("MAKE_RESTARTS", "RESTART_WITNESS"), commands=commands,
                            )
                            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                            self.assertEqual(observed.semantics["domains"]["RESTART_WITNESS"]["value"], "1")
                        else:
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.make("all", commands=commands)
                    self.assertEqual(calls, [(producer, b""), (witness, b"1")])
                    self.assertFalse((session.tree / "generated.mk").exists())
                self.assertEqual((self.root / "Makefile").read_text(), makefile)
                self.fixture.assert_clean(session)

    def test_generated_output_declarations_and_actual_capture_reject_each_boundary(self):
        self.fixture.add("Makefile", "all: ;\n")
        self.fixture.add("source/data", "immutable")
        for paths in (
            ("../escape",), ("/outside",), ("a/../escape",), ("source",), ("source/data",),
            ("Makefile/child",), ("same", "same"), ("a", "a-b", "a/child"),
        ):
            with self.subTest(paths=paths):
                with self.fixture.session(seconds=30) as session:
                    runs = session.budget.runs
                    with self.assertRaisesRegex(MakeProbeError, "path|conflict"):
                        session.command(Command(("/usr/bin/printf", ""), outputs=paths))
                    self.assertEqual(session.budget.runs, runs)
                self.fixture.assert_clean(session)
        write = "from pathlib import Path\nPath('/work/generated.mk').write_bytes(b'value')\n"
        for case, code, expected in (
            ("extra-dir", write + "Path('/work/extra').mkdir()", "undeclared"),
            ("directory", "import os; os.mkdir('/work/generated.mk')", "nonregular"),
            ("symlink", "import os; os.symlink('/repo/Makefile','/work/generated.mk')", "symlink"),
            ("write-source", "open('/repo/Makefile','w').write('changed')", "write outside"),
            ("escape", "open('/work/../repo/Makefile','w').write('changed')", "write outside"),
        ):
            with self.subTest(case=case):
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.command(Command(("/usr/bin/python3", "-c", code), outputs=("generated.mk",)))
                self.assertEqual((self.root / "Makefile").read_text(), "all: ;\n")
                self.fixture.assert_clean(session)
        with self.fixture.session(seconds=30, file_bytes=3*1024*1024) as session:
            self.assertEqual(
                session.command(Command(
                    ("/usr/bin/python3", "-c", write), outputs=("generated.mk",),
                )).generated[0].data, b"value",
            )
            with self.assertRaisesRegex(MakeProbeError, "sandbox signal|unsuccessfully"):
                session.command(Command(
                    ("/usr/bin/python3", "-c",
                     "open('/work/generated.mk','wb').write(b'x'*(3*1024*1024+1))"),
                    outputs=("generated.mk",),
                ))
        self.fixture.assert_clean(session)

    def test_generated_publication_creation_mapping_cache_and_write_charges_are_cumulative(self):
        for case in ("positive", "creation", "mapping", "cache", "publication"):
            with self.subTest(case=case):
                amount = 128*1024 if case in {"cache", "publication"} else 8
                self.fixture.add("producer.py", (
                    f"open('/work/generated.bin','wb').write(b'x'*{amount})\n"
                ))
                self.fixture.add("Makefile", (
                    "A := $(shell python3 producer.py)\nB := $(shell python3 producer.py)\nall: ;\n"
                ))
                command = Command(
                    ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",), outputs=("generated.bin",),
                )
                limits = {
                    "positive": {},
                    "creation": {"created_files": 2},
                    "mapping": {"mapping_bytes": 128},
                    "cache": {"cache_bytes": 256*1024},
                    "publication": {"sandbox_bytes": 128*1024 + 4096},
                }[case]
                expected = {
                    "creation": "creation bound|creation budget",
                    "mapping": "mapping byte",
                    "cache": "cache byte",
                    "publication": "aggregate sandbox byte budget exhausted",
                }
                with self.fixture.session(seconds=30, **limits) as session:
                    run, completed, reports = session._sandbox_run, [], []
                    execute, returned = session.command, []
                    def record(root, **kwargs):
                        result = run(root, **kwargs)
                        if kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]:
                            completed.append(result[1])
                        return result
                    def record_result(registration):
                        result = execute(registration)
                        returned.append(result)
                        return result
                    with patch.object(session, "_sandbox_run", record), \
                         patch.object(session, "command", record_result), self.capture_reports(session, reports):
                        if case == "positive":
                            result = session.make("all", commands={"python3 producer.py": command})
                            self.assertEqual(len(result.events), 2)
                            self.assertEqual(len(completed), 2)
                        else:
                            with self.assertRaisesRegex(MakeProbeError, expected[case]):
                                session.make("all", commands={"python3 producer.py": command})
                            self.assertGreaterEqual(len(completed), 2 if case == "cache" else 1)
                            if case == "publication":
                                self.assertEqual(len(completed), 1)
                                self.assertEqual(reports[-1]["error"], "aggregate generated publication byte budget exhausted")
                                self.assertTrue(any(
                                    result.generated and len(result.generated[0].data) == amount
                                    for result in returned
                                ))
                    self.assertFalse((session.tree / "generated.bin").exists())
                    self.assertFalse(session.budget.children)
                    self.assertFalse(list(session.base.glob("make-root-*")))
                    self.assertFalse(list(session.base.glob("control-*")))
                self.fixture.assert_clean(session)

    def test_only_reachable_native_tool_and_actual_build_inputs_bind_semantics(self):
        self.fixture.add("chosen.c", '#include <stdio.h>\nint main(void) { puts("observed"); }\n')
        self.fixture.add("discarded.c", '#include <stdio.h>\nint main(void) { puts("unused"); }\n')
        self.fixture.add("Makefile", (
            "SELECT := $(shell tools/chosen;)\nifeq ($(SELECT),)\n"
            "UNUSED := $(shell tools/discarded;)\nendif\nall: $(SELECT)\nobserved: ;\n"
        ))
        results, binaries = [], []
        for name in (None, "discarded.c", "chosen.c"):
            if name:
                self.fixture.add(name, (self.root / name).read_text() + "/* build-input change */\n")
            with self.fixture.session(seconds=40) as session:
                chosen = session.compile_native(("chosen.c",))
                discarded = session.compile_native(("discarded.c",))
                binaries.append(chosen.digest)
                run, executions = session.command, []
                def record(command):
                    executions.append(command.native_tool)
                    return run(command)
                with patch.object(session, "command", record):
                    observed = session.make("all", commands={
                        "tools/chosen;": Command(("/native/tool",), native_tool=chosen),
                        "tools/discarded;": Command(("/native/tool",), native_tool=discarded),
                    })
                results.append(observed)
                dynamic, = observed.semantics["dynamic_commands"]
                self.assertEqual(dynamic["command"]["native_tool"]["inputs"], list(chosen.inputs))
                self.assertEqual(executions, [chosen])
                self.assertEqual(len(observed.events), 1)
                self.assertFalse(any(
                    output.stdout == b"unused\n" for variants in session.cache.values() for output in variants
                ))
            self.fixture.assert_clean(session)
        self.assertEqual(len(set(binaries)), 1)
        self.assertEqual(results[0].semantic_digest, results[1].semantic_digest)
        self.assertNotEqual(results[1].semantic_digest, results[2].semantic_digest)

    def test_generated_source_and_output_bytes_bind_identity_without_changing_make_context(self):
        names, command, producer = self.include_fixture()
        results, contents = [], []
        for name, value in (
            (None, None),
            ("choice.txt", "observed different-source"),
            ("producer.py", (self.root / "producer.py").read_text()
             + "with output.open('a') as stream: stream.write('# different generated bytes\\n')\n"),
        ):
            if name is not None:
                self.fixture.add(name, value)
            ordinary = self.fixture.ordinary_assignment_context((), names)
            contents.append((self.root / "generated.mk").read_bytes())
            (self.root / "generated.mk").unlink()
            with self.fixture.session(seconds=30) as session:
                observed = session.make("all", variables=names, commands={command: producer})
                results.append(observed)
                self.assertEqual(observed.semantics["domains"], ordinary[1])
                self.assertEqual(observed.semantics["files"][0]["prerequisites"], ordinary[0])
            self.fixture.assert_clean(session)
        self.assertEqual(contents[0], contents[1])
        self.assertNotEqual(contents[1], contents[2])
        self.assertEqual(len({result.semantic_digest for result in results}), 3)
        self.assertEqual(results[0].semantics["domains"], results[2].semantics["domains"])

    def test_effectful_replacement_keeps_every_real_call_across_native_restart(self):
        self.fixture.add("state/current", "current")
        self.fixture.add("writer.py", (
            "import os,sys\nfrom pathlib import Path\nroot=Path(sys.argv[1])\n"
            "value=str(len(os.listdir('state'))-1)\n"
            "(root/'data').mkdir(exist_ok=True)\n(root/'data/value').write_text(value)\nprint(value)\n"
        ))
        self.fixture.add("producer.py", (
            "import sys\nfrom pathlib import Path\nroot=Path(sys.argv[1])\n"
            "(root/'state').mkdir(exist_ok=True)\n(root/'state/generated').write_text('generated')\n"
            "(root/'trigger.mk').write_text('SECOND := $(shell python3 writer.py .)\\n')\n"
        ))
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 writer.py .)\ninclude trigger.mk\n"
            "trigger.mk:\n\t@python3 producer.py .\n"
            "all:\n\t@printf '%s\\n' '$(FIRST)' '$(SECOND)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.splitlines(), [b"1", b"1"])
        for name in ("data/value", "state/generated", "trigger.mk"):
            (self.root / name).unlink()
        (self.root / "data").rmdir()
        writer = Command(
            ("/usr/bin/python3", "/repo/writer.py", "/work"), code=("writer.py",),
            directories=("state",), outputs=("data/value",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work"), code=("producer.py",),
            outputs=("trigger.mk", "state/generated"),
        )
        with self.fixture.session(seconds=30) as session:
            execute, writers = session.command, []
            def record(command):
                result = execute(command)
                if command is writer:
                    writers.append(result)
                return result
            with patch.object(session, "command", record):
                observed = session.make("all", variables=("FIRST", "SECOND", "MAKE_RESTARTS"), commands={
                    "python3 writer.py .": writer, "python3 producer.py .": producer,
                })
            self.assertEqual([output.stdout for output in writers], [b"0\n", b"1\n", b"1\n"])
            self.assertIsNot(writers[1], writers[2])
            self.assertEqual(
                [observed.semantics["domains"][name]["value"] for name in ("FIRST", "SECOND", "MAKE_RESTARTS")],
                ["1", "1", "1"],
            )
            self.assertEqual(len(observed.events), 4)
            self.assertFalse((session.tree / "data").exists())
        self.fixture.assert_clean(session)

    def test_unreachable_generated_input_changes_do_not_become_provenance_or_effects(self):
        self.fixture.add("choice.txt", "observed first")
        self.fixture.add("choice.py", "print(open('choice.txt').read().split()[0])\n")
        self.fixture.add("unused.txt", "first")
        self.fixture.add("unused.py", "open('/work/unused.mk','w').write(open('unused.txt').read())\n")
        self.fixture.add("Makefile", (
            "SELECT := $(shell python3 choice.py)\nifeq ($(SELECT),)\n"
            "UNUSED := $(shell python3 unused.py)\nendif\nall: $(SELECT)\nobserved: ;\n"
        ))
        commands = {
            "python3 choice.py": Command(
                ("/usr/bin/python3", "/repo/choice.py"), code=("choice.py",), sources=("choice.txt",),
            ),
            "python3 unused.py": Command(
                ("/usr/bin/python3", "/repo/unused.py"), code=("unused.py",), sources=("unused.txt",),
                outputs=("unused.mk",),
            ),
        }
        results = []
        for path, value in (
            (None, None), ("unused.txt", "second"),
            ("unused.py", "open('/work/unused.mk','w').write(open('unused.txt').read().upper())\n"),
            ("choice.txt", "observed second"),
        ):
            if path is not None:
                self.fixture.add(path, value)
            with self.fixture.session(seconds=30) as session:
                execute, submitted = session.command, []
                def record(registration):
                    submitted.append(registration)
                    return execute(registration)
                with patch.object(session, "command", record):
                    observed = session.make("all", commands=commands, owner_inputs=("Makefile",))
                results.append(observed)
                dynamic, = observed.semantics["dynamic_commands"]
                self.assertNotIn("generated_outputs", dynamic)
                self.assertEqual(len(observed.events), 1)
                self.assertEqual(submitted, [commands["python3 choice.py"]])
                self.assertFalse((session.tree / "unused.mk").exists())
            self.fixture.assert_clean(session)
        self.assertEqual(len({result.semantic_digest for result in results[:3]}), 1)
        self.assertEqual(len({result.execution_digest for result in results}), 4)
        self.assertNotEqual(results[2].semantic_digest, results[3].semantic_digest)

    def test_cached_and_already_dispatched_listings_follow_live_publication(self):
        self.fixture.add("data/current.txt", "current")
        self.fixture.add("reader.py", "import os\nprint(' '.join(sorted(os.listdir('data'))))\n")
        self.fixture.add("producer.py", (
            "import os\nos.mkdir('/work/data')\n"
            "open('/work/data/generated.txt','w').write('generated')\n"
            "open('/work/trigger.mk','w').write('VALUE := $(shell python3 reader.py)\\n')\n"
        ))
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
            outputs=("trigger.mk", "data/generated.txt"),
        )
        for installed in (False, True):
            with self.subTest(installed=installed):
                self.fixture.add("Makefile", (
                    ("EARLY := $(shell python3 reader.py)\n" if installed else "")
                    + "include trigger.mk\ntrigger.mk:\n\t@python3 producer.py\nall: ;\n"
                ))
                with self.fixture.session(seconds=30) as session:
                    first = session.command(reader)
                    self.assertEqual(first.stdout, b"current.txt\n")
                    execute, reads = session.command, []
                    def record(command):
                        result = execute(command)
                        if command == reader:
                            reads.append(result)
                        return result
                    with patch.object(session, "command", record):
                        observed = session.make("all", variables=("VALUE",), commands={
                            "python3 reader.py": reader, "python3 producer.py": producer,
                        })
                    self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "current.txt generated.txt")
                    self.assertEqual(reads[-1].stdout, b"current.txt generated.txt\n")
                    self.assertIsNot(reads[-1], first)
                    if installed:
                        self.assertIs(reads[0], first)
                        self.assertIs(reads[-1], reads[-2])
                    self.assertEqual(len({event["match"] for event in observed.events}), len(observed.events))
                    self.assertFalse((session.tree / "data/generated.txt").exists())
                    self.assertEqual(session.command(reader).stdout, b"current.txt\n")
                self.fixture.assert_clean(session)

    def test_code_ancestor_metadata_tracks_publication_without_granting_enumeration(self):
        self.fixture.add("data/module.py", "VALUE=1\n")
        self.fixture.add("other/current", "other")
        self.fixture.add("reader.py", (
            "import json,os\nvalue=os.stat('data')\n"
            "print(json.dumps({name:getattr(value,name) for name in "
            "('st_dev','st_ino','st_mode','st_nlink','st_uid','st_gid','st_rdev','st_size',"
            "'st_blksize','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')}))\n"
        ))
        self.fixture.add("producer.py", (
            "import os\nos.makedirs('/work/data/nested')\n"
            "open('/work/data/nested/value','w').write('generated')\n"
            "open('/work/trigger.mk','w').write('VALUE := $(shell python3 reader.py)\\n')\n"
        ))
        self.fixture.add("Makefile", "include trigger.mk\ntrigger.mk:\n\t@python3 producer.py\nall: ;\n")
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "data/module.py"),
            directories=("other",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
            outputs=("trigger.mk", "data/nested/value"),
        )
        with self.fixture.session(seconds=30) as session:
            first = session.command(reader)
            initial = json.loads(first.stdout)
            self.assertEqual(len(initial), 13)
            self.assertEqual(initial["st_nlink"], 2)
            self.assertTrue(any(row[1] == "/repo/data" and row[6] == 0 for row in first.metadata))
            run, execute, actual, outputs = session._sandbox_run, session.command, [], []
            def capture(root, **kwargs):
                result = run(root, **kwargs)
                if kwargs["mode"] == "make":
                    status = (session.tree / "data").stat()
                    actual.append({name: getattr(status, name) for name in (
                        "st_dev", "st_ino", "st_mode", "st_nlink", "st_rdev", "st_size", "st_blksize",
                        "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns",
                    )})
                return result
            def command(value):
                result = execute(value)
                if value is reader:
                    outputs.append(result)
                return result
            with patch.object(session, "_sandbox_run", capture), patch.object(session, "command", command):
                observed = session.make("all", variables=("VALUE",), commands={
                    "python3 reader.py": reader, "python3 producer.py": producer,
                })
            value = json.loads(observed.semantics["domains"]["VALUE"]["value"])
            self.assertEqual(len(value), 13)
            self.assertEqual({name: value[name] for name in actual[0]}, actual[0])
            self.assertEqual((value["st_uid"], value["st_gid"]), (initial["st_uid"], initial["st_gid"]))
            self.assertEqual(value["st_ino"], initial["st_ino"])
            self.assertEqual(value["st_nlink"], 3)
            self.assertNotEqual(value["st_mtime_ns"], initial["st_mtime_ns"])
            self.assertNotEqual(value["st_ctime_ns"], initial["st_ctime_ns"])
            self.assertIsNot(outputs[-1], first)
        self.fixture.assert_clean(session)
        self.fixture.add("reader.py", "import os\nos.listdir('data')\n")
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source directory enumeration: /repo/data"):
                session.make("all", commands={"python3 reader.py": reader, "python3 producer.py": producer})
            self.assertFalse((session.tree / "data/nested").exists())
        self.fixture.assert_clean(session)

    def test_unrelated_publication_reuses_only_actually_unchanged_observations(self):
        self.fixture.add("data/current", "current")
        self.fixture.add("other/current", "other")
        self.fixture.add("reader.py", "import os\nprint(' '.join(sorted(os.listdir('data'))))\n")
        self.fixture.add("producer.py", (
            "import os\nos.mkdir('/work/other')\nopen('/work/other/generated','w').write('unrelated')\n"
        ))
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 reader.py)\nOTHER := $(shell python3 producer.py)\n"
            "SECOND := $(shell python3 reader.py)\nall: ;\n"
        ))
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",), outputs=("other/generated",),
        )
        with self.fixture.session(seconds=30) as session:
            first = session.command(reader)
            execute, outputs = session.command, []
            def record(command):
                result = execute(command)
                if command is reader:
                    outputs.append(result)
                return result
            with patch.object(session, "command", record):
                observed = session.make("all", variables=("FIRST", "SECOND"), commands={
                    "python3 reader.py": reader, "python3 producer.py": producer,
                })
            self.assertEqual([value["value"] for value in observed.semantics["domains"].values()], ["current"]*2)
            self.assertEqual(len(outputs), 2)
            self.assertTrue(all(output is first for output in outputs))
        self.fixture.assert_clean(session)

    def test_live_publications_preserve_actual_intermediate_directory_order(self):
        self.fixture.add("data/current.txt", "current")
        self.fixture.add("reader.py", "import os\nprint(' '.join(os.listdir('data')))\n")
        for name in ("z", "a"):
            self.fixture.add("producer_" + name + ".py", (
                "import sys\nfrom pathlib import Path\n"
                "path=Path(sys.argv[1])/'data/" + name + ".txt'\n"
                "path.parent.mkdir(parents=True,exist_ok=True)\npath.write_text('generated')\n"
            ))
        self.fixture.add("Makefile", (
            "all: z first a second\nz:\n\t+@python3 producer_z.py .\n"
            "first:\n\t+@python3 reader.py\na:\n\t+@python3 producer_a.py .\n"
            "second:\n\t+@python3 reader.py\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        for name in ("z", "a"):
            (self.root / ("data/" + name + ".txt")).unlink()
        commands = {
            "python3 reader.py": Command(
                ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
            ),
            **{
                f"python3 producer_{name}.py .": Command(
                    ("/usr/bin/python3", f"/repo/producer_{name}.py", "/work"),
                    code=(f"producer_{name}.py",), outputs=(f"data/{name}.txt",),
                ) for name in ("z", "a")
            },
        }
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", commands=commands)
            self.assertEqual(observed.stdout, ordinary.stdout)
            self.assertEqual(len(observed.events), 4)
        self.fixture.assert_clean(session)

    def test_generated_presence_invalidates_absence_and_requires_explicit_code_admission(self):
        self.fixture.add("reader.py", "import os\nprint(int(os.path.exists('__init__.py')))\n")
        self.fixture.add("producer.py", (
            "open('/work/__init__.py','w').write('present')\n"
            "open('/work/trigger.mk','w').write('VALUE := $(shell python3 reader.py)\\n')\n"
        ))
        reader = Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",))
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
            outputs=("trigger.mk", "__init__.py"),
        )
        for installed, admit in ((False, False), (True, False), (True, True)):
            with self.subTest(installed=installed, admit=admit):
                self.fixture.add("Makefile", (
                    ("EARLY := $(shell python3 reader.py)\n" if installed else "")
                    + "include trigger.mk\ntrigger.mk:\n\t@python3 producer.py\nall: ;\n"
                ))
                with self.fixture.session(seconds=30) as session:
                    first = session.command(reader)
                    self.assertEqual(first.stdout, b"0\n")
                    self.assertTrue(any(
                        row[1] == "/repo/__init__.py" and row[6] == -errno.ENOENT for row in first.metadata
                    ))
                    execute, outputs = session.command, []
                    def record(command):
                        result = execute(command)
                        outputs.append((command, result))
                        return result
                    class Commands:
                        def __contains__(self, command):
                            return command in ("python3 reader.py", "python3 producer.py")
                        def __getitem__(self, command):
                            if command == "python3 producer.py":
                                return producer
                            if admit and "__init__.py" in session.published_sources:
                                return replace(reader, code=("reader.py", "__init__.py"))
                            return reader
                    with patch.object(session, "command", record):
                        if admit:
                            observed = session.make("all", variables=("VALUE",), commands=Commands())
                            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "1")
                        else:
                            with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                                session.make("all", commands=Commands())
                    self.assertEqual(sum(command is producer for command, _ in outputs), 1)
                    if installed:
                        self.assertIs(outputs[0][1], first)
                    self.assertFalse((session.tree / "__init__.py").exists())
                self.fixture.assert_clean(session)

    def nested_fixture(self, *, parent_first=False):
        self.fixture.add("data/current", "original")
        self.fixture.add("choice.txt", "observed")
        self.fixture.add("before.py", "import os\nprint(os.stat('data').st_nlink)\n")
        self.fixture.add("reader.py", "print(open('data/nested/value').read())\n")
        self.fixture.add("producer.py", (
            "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])\n"
            "value=Path('choice.txt').read_text()\n"
            "(root/'data/nested').mkdir(parents=True,exist_ok=True)\n"
            "(root/'data/nested/value').write_text(value)\n"
            "(root/'inner.generated.mk').write_text('SELECTED := '+value+'\\n')\n"
        ))
        self.fixture.add("inner.mk", (
            "MAKEFLAGS += -s --no-print-directory\ninclude inner.generated.mk\n"
            "inner.generated.mk: choice.txt\n\t@python3 producer.py .\ninner: ;\n"
        ))
        compound = "/usr/bin/make -s --no-print-directory -f inner.mk inner && python3 reader.py"
        self.fixture.add("Makefile", (
            ("PARENT := $(shell python3 producer.py .)\n" if parent_first else "")
            + "BEFORE := $(shell python3 before.py)\n"
            f"VALUE := $(shell {compound})\n"
            "AFTER := $(shell python3 before.py)\n"
            "all:\n\t@printf '%s\\n' '$(BEFORE)' '$(VALUE)' '$(AFTER)'\n"
        ))
        before = Command(
            ("/usr/bin/python3", "/repo/before.py"), code=("before.py",), directories=("data",),
        )
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("data/nested/value",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work"), code=("producer.py",),
            sources=("choice.txt",), outputs=("data/nested/value", "inner.generated.mk"),
        )
        return compound, before, reader, producer

    def nested_commands(self, session, compound, before, reader, producer, *, nested=None, child_producer=None):
        class Commands:
            def __contains__(self, command):
                return command in ("python3 before.py", "python3 producer.py .", compound)
            def __getitem__(self, command):
                if command == compound:
                    result = session.make(
                        "inner", makefile="inner.mk", variables=("SELECTED", "MAKE_RESTARTS", "MAKEFILE_LIST"),
                        commands={"python3 producer.py .": producer if child_producer is None else child_producer},
                    )
                    if nested is not None:
                        nested.append(result)
                    if result.stdout:
                        raise AssertionError("the real nested producer query must be silent for this compound")
                    return reader
                return producer if command == "python3 producer.py ." else before
        return Commands()

    def test_nested_generated_query_keeps_one_live_view_until_outer_completion(self):
        compound, before, reader, producer = self.nested_fixture()
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.splitlines(), [b"2", b"observed", b"3"])
        for name in ("data/nested/value", "inner.generated.mk"):
            (self.root / name).unlink()
        (self.root / "data/nested").rmdir()
        nested, reports, readers = [], [], []
        with self.fixture.session(seconds=45) as session:
            before_inode = (session.tree / "data").stat().st_ino
            execute = session.command
            def capture(command):
                result = execute(command)
                if command is reader:
                    readers.append(result)
                return result
            with patch.object(session, "command", capture), self.capture_reports(session, reports):
                observed = session.make(
                    "all", variables=("BEFORE", "VALUE", "AFTER"),
                    commands=self.nested_commands(session, compound, before, reader, producer, nested=nested),
                )
            self.assertEqual(
                [observed.semantics["domains"][name]["value"] for name in ("BEFORE", "VALUE", "AFTER")],
                ["2", "observed", "3"],
            )
            child, = nested
            self.assertEqual(child.semantics["domains"]["SELECTED"]["value"], "observed")
            self.assertEqual(child.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(child.semantics["domains"]["MAKEFILE_LIST"]["value"], "inner.mk inner.generated.mk")
            self.assertEqual(readers[0].consumed, ("data/nested/value",))
            self.assertIn(
                ("data/nested/value", "100644", hashlib.sha256(b"observed").hexdigest()),
                readers[0].input_identities,
            )
            self.assertEqual((session.tree / "data").stat().st_ino, before_inode)
            self.assertEqual((session.tree / "data").stat().st_nlink, 2)
            self.assertFalse((session.tree / "data/nested").exists())
            self.assertFalse((session.tree / "inner.generated.mk").exists())
            self.assertFalse(session.published_sources)
            self.assertEqual(len(observed.events), 3)
            self.assert_settled_reports(session, reports)
        self.fixture.assert_clean(session)

    def test_nested_scope_inherits_ownership_and_preserves_all_file_stat_fields(self):
        fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_rdev", "st_size",
            "st_blksize", "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns",
        )
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                compound, before, reader, producer = self.nested_fixture(parent_first=True)
                self.fixture.add("reader.py", (
                    "import json,os\nvalue=os.stat('data/nested/value')\n"
                    f"print(json.dumps({{name:getattr(value,name) for name in {fields!r}}}))\n"
                ))
                self.fixture.add("inner.mk", (
                    "MAKEFLAGS += -s --no-print-directory\ninclude inner.generated.mk\n"
                    "WRITE := $(shell python3 producer.py .)\n"
                    "VALUE := $(shell python3 reader.py)\ninner: ;\n"
                ))
                nested, reports, outputs = [], [], []
                child = replace(producer, argv=(*producer.argv, "other-owner")) if conflict else producer
                with self.fixture.session(seconds=45) as session:
                    execute = session.command
                    def record(command):
                        result = execute(command)
                        if command is reader:
                            outputs.append(result)
                        return result
                    class Commands:
                        def __contains__(self, command):
                            return command in ("python3 producer.py .", "python3 before.py", compound)
                        def __getitem__(self, command):
                            if command == compound:
                                nested.append(session.make(
                                    "inner", makefile="inner.mk", variables=("VALUE",),
                                    commands={"python3 producer.py .": child, "python3 reader.py": reader},
                                ))
                                return reader
                            return producer if command == "python3 producer.py ." else before
                    with patch.object(session, "command", record), self.capture_reports(session, reports):
                        if conflict:
                            with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
                                session.make("all", commands=Commands())
                        else:
                            result = session.make("all", variables=("VALUE",), commands=Commands())
                            parent = json.loads(result.semantics["domains"]["VALUE"]["value"])
                            child_value = json.loads(nested[0].semantics["domains"]["VALUE"]["value"])
                            self.assertEqual(set(parent), set(fields))
                            self.assertEqual(parent, child_value)
                            self.assertEqual(len(outputs), 2)
                            self.assertIs(outputs[0], outputs[1])
                            self.assert_settled_reports(session, reports)
                    self.assertFalse((session.tree / "data/nested").exists())
                    self.assertFalse(session.published_sources)
                    self.assertFalse(session.published_versions)
                self.fixture.assert_clean(session)

    def test_nested_publication_transfer_rejects_corruption_and_never_retries_work(self):
        for defect in (
            "reply-hash", "missing-field", "missing-file", "empty",
            "hash", "mode", "duplicate", "immutable", "missing",
        ):
            with self.subTest(defect=defect):
                compound, before, reader, producer = self.nested_fixture()
                sent, changed, completed = ProducerChannel.send, [], []
                with self.fixture.session(seconds=45) as session:
                    execute = session.command
                    def record(command):
                        result = execute(command)
                        completed.append(command)
                        return result
                    def corrupt(channel, payload):
                        message = json.loads(payload)
                        if channel.charge is not None and "adopt_sha256" in message:
                            self.assertFalse(changed)
                            changed.append(defect)
                            if defect == "reply-hash":
                                message["adopt_sha256"] = "0"*64
                            elif defect == "missing-field":
                                del message["adopt_sha256"]
                            else:
                                root = message["scope"].split("/")[-1]
                                control = session.base / root.replace("make-root-", "control-", 1) / "map"
                                path = control / f"{message['slot']:016x}.adopt"
                                records = json.loads(path.read_bytes())
                                if defect == "missing-file":
                                    path.unlink()
                                    return sent(channel, payload)
                                if defect == "empty":
                                    records = []
                                elif defect == "hash":
                                    records[0][4] = "0"*64
                                elif defect == "mode":
                                    records[0][2] = 0o444
                                    records[0][5][2] = stat.S_IFREG | 0o444
                                elif defect == "duplicate":
                                    records.append(records[0])
                                elif defect == "immutable":
                                    records[0][0] = "Makefile"
                                else:
                                    records[0][0] = "missing-generated"
                                data = json.dumps(records).encode()
                                session.budget.charge("mapping", len(data))
                                path.write_bytes(data)
                                message["adopt_sha256"] = hashlib.sha256(data).hexdigest()
                            payload = json.dumps(message).encode()
                        return sent(channel, payload)
                    with patch.object(ProducerChannel, "send", corrupt), patch.object(session, "command", record):
                        with self.assertRaisesRegex(MakeProbeError, {
                            "reply-hash": "nested publication transfer differs",
                            "missing-field": "unacknowledged nested publication transfer",
                            "missing-file": "missing nested publication transfer",
                            "empty": "empty nested publication transfer",
                            "hash": "published source changed", "mode": "published source type, mode or size changed",
                            "duplicate": "invalid completed publication identity",
                            "immutable": "conflicts with admitted source authority",
                            "missing": "No such file or directory",
                        }[defect]):
                            session.make(
                                "all", commands=self.nested_commands(session, compound, before, reader, producer),
                            )
                    self.assertEqual(changed, [defect])
                    self.assertEqual(sum(command is producer for command in completed), 1)
                    self.assertEqual(sum(command is reader for command in completed), 1)
                    self.assertFalse((session.tree / "data/nested").exists())
                    self.assertFalse(session.budget.children)
                self.fixture.assert_clean(session)

    def test_parent_publication_inherits_same_owner_and_rejects_a_different_producer(self):
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                _, _, _, producer = self.nested_fixture()
                self.fixture.add("Makefile", "VALUE := $(shell python3 producer.py .)\nall: ;\n")
                outer = replace(producer, argv=(*producer.argv, "other-owner")) if conflict else producer
                completed, reports = [], []
                with self.fixture.session(seconds=45) as session:
                    execute = session.command
                    def record(command):
                        result = execute(command)
                        completed.append(command)
                        return result
                    class Commands:
                        def __contains__(self, command):
                            return command == "python3 producer.py ."
                        def __getitem__(self, command):
                            session.make(
                                "inner", makefile="inner.mk", commands={"python3 producer.py .": producer},
                            )
                            return outer
                    with patch.object(session, "command", record), self.capture_reports(session, reports):
                        if conflict:
                            with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
                                session.make("all", commands=Commands())
                        else:
                            observed = session.make("all", commands=Commands())
                            self.assertEqual(len(observed.events), 1)
                            self.assert_settled_reports(session, reports)
                    self.assertEqual(completed, [producer, outer])
                    self.assertFalse((session.tree / "data/nested").exists())
                    self.assertFalse(session.published_sources)
                    self.assertFalse(session.published_versions)
                self.fixture.assert_clean(session)

    def test_nested_generation_respects_residual_processes_and_outer_lifetime(self):
        for case in ("processes", "lifetime", "interrupt"):
            with self.subTest(case=case):
                compound, before, reader, producer = self.nested_fixture()
                limits = {"processes": 4} if case == "processes" else {}
                with self.fixture.session(seconds=45, **limits) as session:
                    run, lost = session._sandbox_run, []
                    def capture(root, **kwargs):
                        result = run(root, **kwargs)
                        if case != "processes" and kwargs["mode"] == "make" and session.parked_capsules:
                            self.assertFalse(lost)
                            self.assertIn("data/nested/value", session.published_sources)
                            outer, = session.budget.children
                            lost.append(outer.pid)
                            if case == "interrupt":
                                os.kill(os.getpid(), signal.SIGTERM)
                            else:
                                outer.stdin.close()
                        return result
                    with patch.object(session, "_sandbox_run", capture):
                        with self.assertRaisesRegex(
                            KeyboardInterrupt if case == "interrupt" else MakeProbeError,
                            "interrupted by signal" if case == "interrupt" else
                            "resource budget exhausted" if case == "processes" else "producer|rendezvous",
                        ):
                            session.make(
                                "all", commands=self.nested_commands(session, compound, before, reader, producer),
                            )
                    if case == "processes":
                        self.assertEqual(session.live_process_peak, 4)
                    else:
                        self.assertEqual(len(lost), 1)
                    self.assertFalse((session.tree / "data/nested").exists())
                    self.assertFalse(session.published_sources)
                    self.assertFalse(session.budget.children)
                    self.assertEqual(session.pending_commands, 0)
                self.fixture.assert_clean(session)

    def test_generated_makefile_entry_is_admitted_only_inside_its_live_publication_scope(self):
        self.fixture.add("producer.py", (
            "open('/work/nested.mk','w').write('VALUE := observed\\ninner: ;\\n')\n"
        ))
        self.fixture.add("reader.py", "print('observed')\n")
        self.fixture.add("Makefile", (
            "PUBLISH := $(shell python3 producer.py)\nVALUE := $(shell python3 reader.py)\nall: ;\n"
        ))
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",), outputs=("nested.mk",),
        )
        reader = Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",))
        nested = []
        with self.fixture.session(seconds=30) as session:
            class Commands:
                def __contains__(self, name):
                    return name in ("python3 producer.py", "python3 reader.py")
                def __getitem__(self, name):
                    if name == "python3 reader.py":
                        nested.append(session.make("inner", makefile="nested.mk", variables=("VALUE",)))
                        return reader
                    return producer
            result = session.make("all", variables=("VALUE",), commands=Commands())
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "observed")
            self.assertEqual(nested[0].semantics["domains"]["VALUE"]["value"], "observed")
            self.assertFalse((session.tree / "nested.mk").exists())
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "Makefile is not an admitted"):
                session.make("inner", makefile="nested.mk")
            self.assertEqual(session.budget.runs, runs)
        self.fixture.assert_clean(session)

    def test_absent_and_empty_gitlink_namespaces_are_not_generated_output_authority(self):
        self.fixture.add("Makefile", "VALUE := $(shell python3 producer.py)\nall: ;\n")
        self.fixture.add("producer.py", "print('must not execute')\n")
        git = self.fixture.gitlink_git
        git(self.root, "init", "--quiet")
        git(self.root, "config", "user.name", "Owned Fixture")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "add", "Makefile", "producer.py")
        git(self.root, "update-index", "--add", "--cacheinfo", "160000," + "1"*40 + ",module")
        git(self.root, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "owned gitlink publication fixture")
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    (self.root / "module").mkdir()
                budget = foundation.ProbeBudget(foundation.Limits(seconds=30))
                entries = foundation.git_tree_entries(self.root, None, budget=budget)
                loader = foundation.AuthorityLoader(self.root, entries, budget=budget)
                with foundation.ProbeSession(loader, scratch_root=self.fixture.scratch, budget=budget) as session:
                    before = session.processes_used
                    with self.assertRaisesRegex(MakeProbeError, "conflicts with immutable source"):
                        session.make("all", commands={"python3 producer.py": Command(
                            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
                            outputs=("module/generated",),
                        )})
                    self.assertEqual(session.processes_used - before, 2)
                    self.assertFalse((session.tree / "module/generated").exists())
                self.fixture.assert_clean(session)
                if present:
                    self.assertEqual(list((self.root / "module").iterdir()), [])
                    (self.root / "module").rmdir()

    def test_unreachable_producer_is_not_executed_by_an_empty_speculative_pass(self):
        self.fixture.add("choice.py", "print('observed')\n")
        self.fixture.add("unreachable.py", (
            "from pathlib import Path\nimport sys\n"
            "(Path(sys.argv[1])/'unreachable.txt').write_text('executed')\n"
        ))
        self.fixture.add("Makefile", (
            "CHOICE := $(shell python3 choice.py)\nifeq ($(CHOICE),)\n"
            "UNUSED := $(shell python3 unreachable.py .)\nendif\n"
            "all:\n\t@printf '%s\\n' '$(CHOICE)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout, b"observed\n")
        self.assertFalse((self.root / "unreachable.txt").exists())
        calls = []
        command = Command(("/usr/bin/python3", "/repo/choice.py"), code=("choice.py",))
        registrations = {
            "python3 choice.py": command,
            "python3 unreachable.py .": Command(
                ("/usr/bin/python3", "/repo/unreachable.py", "/work"),
                code=("unreachable.py",), outputs=("unreachable.txt",),
            ),
        }
        class Commands:
            def __contains__(self, value):
                calls.append(value)
                return value in registrations
            def __getitem__(self, value):
                return registrations[value]
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", variables=("CHOICE",), commands=Commands())
            self.assertEqual(observed.semantics["domains"]["CHOICE"]["value"], "observed")
            self.assertEqual(calls, ["python3 choice.py"])
            self.assertEqual(len(observed.events), 1)
            self.assertFalse((session.tree / "unreachable.txt").exists())
        self.fixture.assert_clean(session)

    def test_invalid_request_rejects_before_any_producer_execution(self):
        command = self.producer_fixture()
        for defect in ("scope", "sequence", "completed", "hash", "count", "truncated"):
            with self.subTest(defect=defect):
                received = ProducerChannel.receive
                requests = []
                class Commands:
                    def __contains__(self, value):
                        requests.append(value)
                        return True
                    def __getitem__(self, value):
                        return command
                def corrupt(channel):
                    payload = received(channel)
                    if payload is None or channel.charge is None:
                        return payload
                    record = json.loads(payload)
                    if record["kind"] != "request":
                        return payload
                    if defect == "scope":
                        record["scope"] += "-foreign"
                    elif defect == "sequence":
                        record["sequence"] += 1
                    elif defect == "completed":
                        record["completed"] += 1
                    else:
                        frame = bytearray.fromhex(record["frame"])
                        if defect == "hash":
                            frame[8] ^= 1
                        elif defect == "count":
                            frame[4:8] = (1).to_bytes(4, "little")
                        else:
                            frame.pop()
                        record["frame"] = frame.hex()
                    return json.dumps(record).encode()
                with self.fixture.session(seconds=30) as session:
                    with patch.object(ProducerChannel, "receive", corrupt):
                        with self.assertRaises(MakeProbeError):
                            session.make("all", commands=Commands())
                    self.assertEqual(requests, [])
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_invalid_reply_never_retries_an_effectful_producer(self):
        command = self.producer_fixture()
        for defect in ("scope", "sequence", "slot", "owner", "stdout", "outputs"):
            with self.subTest(defect=defect):
                sent = ProducerChannel.send
                calls = []
                def corrupt(channel, payload):
                    record = json.loads(payload)
                    if channel.charge is not None and record.get("kind") == "result":
                        if defect == "scope":
                            record["scope"] += "-foreign"
                        elif defect in {"sequence", "slot"}:
                            record[defect] += 1
                        elif defect == "owner":
                            record["owner"] = "0"*64
                        elif defect == "stdout":
                            record["stdout_sha256"] = "0"*64
                        else:
                            record["outputs"] = ["Makefile"]
                        payload = json.dumps(record).encode()
                    return sent(channel, payload)
                with self.fixture.session(seconds=30) as session:
                    execute = session.command
                    def record(value):
                        calls.append(value)
                        return execute(value)
                    with patch.object(ProducerChannel, "send", corrupt), patch.object(session, "command", record):
                        with self.assertRaises(MakeProbeError):
                            session.make("all", commands={"python3 producer.py": command})
                    self.assertEqual(calls, [command])
                    self.assertFalse((session.tree / "generated.txt").exists())
                    self.assertIn("VALUE :=", (self.root / "Makefile").read_text())
                self.fixture.assert_clean(session)

    def test_duplicate_later_request_fails_after_one_real_effect(self):
        command = self.producer_fixture()
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 producer.py)\n"
            "SECOND := $(shell python3 producer.py)\nall: ;\n"
        ))
        received, calls = ProducerChannel.receive, []
        def duplicate(channel):
            payload = received(channel)
            if payload is not None and channel.charge is not None:
                record = json.loads(payload)
                if record.get("kind") == "request" and record["sequence"] == 2:
                    record["sequence"] = 1
                    return json.dumps(record).encode()
            return payload
        with self.fixture.session(seconds=30) as session:
            execute = session.command
            def record(value):
                calls.append(value)
                return execute(value)
            with patch.object(ProducerChannel, "receive", duplicate), patch.object(session, "command", record):
                with self.assertRaisesRegex(MakeProbeError, "out-of-order producer request"):
                    session.make("all", commands={"python3 producer.py": command})
            self.assertEqual(calls, [command])
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def late_reply_control(self, defect):
        command = self.producer_fixture()
        marker = self.fixture.directory / ("make-after-reply-" + defect)
        release = self.fixture.directory / ("reply-released-" + defect)
        proxy = self.fixture.directory / ("supervisor-observer-" + defect + ".py")
        proxy.write_text(
            "import sys,time\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(TRUSTED_ROOT)!r})\n"
            "import syscall_guard,sandbox_exec\noriginal=syscall_guard.Policy.entry\nseen=False\n"
            "def observe(self,pid,state,registers):\n"
            " global seen\n"
            " if self.producer_completed==1 and state.role=='make' and not seen:\n"
            f"  seen=True; Path({str(marker)!r}).write_text('native Make continued')\n"
            "  deadline=time.monotonic()+5\n"
            f"  while not Path({str(release)!r}).exists():\n"
            "   if time.monotonic()>=deadline: raise RuntimeError('owned duplicate control timed out')\n"
            "   time.sleep(0.001)\n"
            " return original(self,pid,state,registers)\n"
            "syscall_guard.Policy.entry=observe\nraise SystemExit(sandbox_exec.main())\n",
        )
        sent, executed, reports = ProducerChannel.send, [], []
        with self.fixture.session(seconds=30) as session:
            run, execute = session.budget.run, session.command
            def supervised(argv, **kwargs):
                if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                    config = json.loads(Path(argv[-1]).read_bytes())
                    if config["mode"] == "make":
                        argv = [*argv[:-2], str(proxy), argv[-1]]
                return run(argv, **kwargs)
            def producer(value):
                executed.append(value)
                return execute(value)
            def duplicate(channel, payload):
                sent(channel, payload)
                record = json.loads(payload)
                if channel.charge is not None and record.get("kind") == "result":
                    deadline = time.monotonic() + 5
                    while not marker.exists():
                        if time.monotonic() >= deadline:
                            raise AssertionError("real Make did not continue after its accepted reply")
                        time.sleep(0.001)
                    if defect == "partial":
                        channel.charge(1)
                        self.assertEqual(channel.connection.send(b"\x08"), 1)
                    elif defect != "positive":
                        if defect == "stale":
                            record["sequence"] = 0
                        elif defect == "foreign":
                            record["scope"] += "-foreign"
                        elif defect == "unknown":
                            record["kind"] = "unknown"
                        sent(channel, json.dumps(record).encode())
                    release.write_text("owned control released")
            with patch.object(session.budget, "run", supervised), patch.object(
                session, "command", producer,
            ), patch.object(ProducerChannel, "send", duplicate), self.capture_reports(session, reports):
                if defect == "positive":
                    observed = session.make("all", variables=("VALUE",), commands={"python3 producer.py": command})
                    self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "observed")
                else:
                    with self.assertRaisesRegex(MakeProbeError, "producer|rendezvous"):
                        session.make("all", commands={"python3 producer.py": command})
            self.assertEqual(executed, [command])
            self.assertTrue(marker.exists())
            self.assertTrue(release.exists())
            self.assert_settled_reports(session, reports)
        self.fixture.assert_clean(session)

    def test_separately_sent_final_reply_is_rejected_while_make_continues(self):
        self.late_reply_control("duplicate")

    def test_late_reply_family_rejects_partial_stale_foreign_and_unknown_messages(self):
        for defect in ("positive", "partial", "stale", "foreign", "unknown"):
            with self.subTest(defect=defect):
                self.late_reply_control(defect)

    def test_terminal_eof_barrier_rejects_a_reply_after_the_last_native_stop(self):
        command = self.producer_fixture()
        for defect in ("positive", "duplicate", "partial"):
            with self.subTest(defect=defect):
                sent, shutdown = ProducerChannel.send, ProducerChannel.shutdown_write
                replies, finished, executions, reports = [], [], [], []
                def record(channel, payload):
                    if channel.charge is not None and json.loads(payload).get("kind") == "result":
                        replies.append(payload)
                    return sent(channel, payload)
                def late(channel):
                    self.assertEqual(len(replies), 1)
                    finished.append(True)
                    if defect == "duplicate":
                        sent(channel, replies[0])
                    elif defect == "partial":
                        channel.charge(1)
                        self.assertEqual(channel.connection.send(b"\x08"), 1)
                    return shutdown(channel)
                with self.fixture.session(seconds=30) as session:
                    execute = session.command
                    def producer(value):
                        executions.append(value)
                        return execute(value)
                    with patch.object(ProducerChannel, "send", record), patch.object(
                        ProducerChannel, "shutdown_write", late,
                    ), patch.object(session, "command", producer), self.capture_reports(session, reports):
                        if defect == "positive":
                            observed = session.make(
                                "all", variables=("VALUE",), commands={"python3 producer.py": command},
                            )
                            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "observed")
                        else:
                            with self.assertRaisesRegex(MakeProbeError, "producer|rendezvous"):
                                session.make("all", commands={"python3 producer.py": command})
                    self.assertEqual(executions, [command])
                    self.assertEqual(finished, [True])
                    self.assertFalse((session.tree / "generated.txt").exists())
                    self.assert_settled_reports(session, reports)
                self.fixture.assert_clean(session)

    def test_final_notification_must_agree_with_the_actual_completed_transcript(self):
        command = self.producer_fixture()
        for defect in ("issued", "completed", "scope", "extra"):
            with self.subTest(defect=defect):
                received, executions, corrupted = ProducerChannel.receive, [], []
                def corrupt(channel):
                    payload = received(channel)
                    if payload is not None and channel.charge is not None:
                        record = json.loads(payload)
                        if record.get("kind") == "finished":
                            self.assertEqual((record["issued"], record["completed"]), (1, 1))
                            if defect == "issued":
                                record["issued"] += 1
                            elif defect == "completed":
                                record["completed"] = 0
                            elif defect == "scope":
                                record["scope"] += "-foreign"
                            else:
                                record["extra"] = True
                            payload = json.dumps(record).encode()
                            corrupted.append(True)
                    return payload
                with self.fixture.session(seconds=30) as session:
                    execute = session.command
                    def producer(value):
                        executions.append(value)
                        return execute(value)
                    with patch.object(ProducerChannel, "receive", corrupt), patch.object(
                        session, "command", producer,
                    ):
                        with self.assertRaisesRegex(MakeProbeError, "producer"):
                            session.make("all", commands={"python3 producer.py": command})
                    self.assertEqual(executions, [command])
                    self.assertEqual(corrupted, [True])
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_real_same_uid_sudo_keeps_static_make_and_live_remakes_channel_free(self):
        if not Path("/usr/bin/sudo").is_file():
            self.skipTest("the actual descriptor-closing control requires existing sudo")
        sudo = ["/usr/bin/sudo", "-n", "-u", "#" + str(os.getuid()), "--"]
        allowed = subprocess.run([*sudo, "/usr/bin/true"], capture_output=True, timeout=10)
        if allowed.returncode:
            self.skipTest("the existing sudo policy does not allow a same-UID control")
        self.fixture.add("input.mk", "SELECTED := observed\nobserved: ;\n")
        self.fixture.add("producer.py", (
            "import sys\n"
            "open(sys.argv[1],'wb').write(open('input.mk','rb').read())\n"
        ))
        self.fixture.add("plain.mk", "all: ;\n")
        self.fixture.add("Makefile", (
            "include generated.mk\n"
            "generated.mk: input.mk\n\t@python3 producer.py generated.mk\n"
            "all: $(SELECTED)\n"
        ))
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work/generated.mk"),
            code=("producer.py",), sources=("input.mk",), outputs=("generated.mk",),
        )
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertTrue((self.root / "generated.mk").is_file())
        (self.root / "generated.mk").unlink()
        values = []
        for through_sudo in (False, True):
            invocations = []
            with self.fixture.session(seconds=45) as session:
                if session.sudo_drop:
                    self.skipTest("the same-UID sudo control also requires the existing user-namespace route")
                with self.subTest(same_uid_sudo=through_sudo):
                    original = subprocess.Popen
                    def launch(argv, **kwargs):
                        if str(TRUSTED_ROOT / "sandbox_exec.py") in argv:
                            self.assertEqual(kwargs["pass_fds"], ())
                            self.assertTrue(kwargs["close_fds"])
                            self.assertNotIn("--producer-fd", argv)
                            invocations.append(argv)
                            if through_sudo:
                                argv = [*sudo, *argv]
                        return original(argv, **kwargs)
                    with patch("subprocess.Popen", launch):
                        plain = session.make("all", makefile="plain.mk")
                        self.assertEqual(plain.events, ())
                        observed = session.make(
                            "all", variables=("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS"),
                            commands={"python3 producer.py generated.mk": producer},
                        )
                    self.assertEqual(observed.stdout, ordinary.stdout)
                    self.assertEqual(observed.semantics["domains"]["SELECTED"]["value"], "observed")
                    self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                    self.assertEqual(observed.semantics["domains"]["MAKEFILE_LIST"]["value"], "Makefile generated.mk")
                    self.assertEqual(len(observed.events), 1)
                    self.assertGreaterEqual(len(invocations), 3)
                    values.append(observed.semantics)
            self.fixture.assert_clean(session)
        self.assertEqual(values[0], values[1])

    def test_missing_same_uid_namespace_skips_the_whole_comparison(self):
        from scripts.validation_ownership.make_probe import ProbeSession
        tools, routes = ProbeSession._tools, []
        def unavailable(session):
            tools(session)
            routes.append(session.sudo_drop)
            # Model only this optional comparison's missing prerequisite.
            # No capsule or credential transition is run under this value.
            session.sudo_drop = True
        case = ProducerTests("test_real_same_uid_sudo_keeps_static_make_and_live_remakes_channel_free")
        result = unittest.TestResult()
        with patch.object(ProbeSession, "_tools", unavailable):
            case.run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIs(result.skipped[0][0], case)
        if not routes:
            self.skipTest(result.skipped[0][1])
        self.assertEqual(len(routes), 1)
        self.assertEqual(
            result.skipped[0][1],
            "the same-UID sudo control also requires the existing user-namespace route",
        )

    def test_real_privileged_fallback_preserves_live_results_credentials_and_accounting(self):
        if os.getuid() == 0 or os.getgid() == 0 or not Path("/usr/bin/sudo").is_file():
            self.skipTest("the existing privileged route requires sudo and a non-root runner")
        _, command, producer = self.include_fixture()
        self.fixture.add("plain.mk", "all: ;\n")
        session = self.fixture.session(seconds=60)
        reports, invocations, peers, preflights = [], [], [], []
        try:
            available = session.budget.run(
                [*foundation.NAMESPACE_LAUNCHER, "/usr/bin/true"],
                env=ENVIRONMENT, privileged=True,
            )
            if available.returncode == 1 and (
                available.stderr.startswith((b"sudo:", b"unshare: unshare failed:"))
                or available.stderr.startswith(b"Sorry, user ") and b"not allowed" in available.stderr
            ):
                self.skipTest("existing privileged namespace route unavailable: " + available.stderr.decode("utf-8"))
            self.assertEqual(available.returncode, 0, available.stderr)
            run = session.budget.run
            def fallback(argv, **kwargs):
                result = run(argv, **kwargs)
                if argv[0] == "/usr/bin/unshare" and "--user" in argv and argv[-1] == "/usr/bin/true":
                    preflights.append(result.returncode)
                    if not result.returncode:
                        return subprocess.CompletedProcess(
                            argv, 1, result.stdout, b"test-only user-namespace preflight denial",
                        )
                return result
            launch, accept = subprocess.Popen, socket.socket.accept
            def capture_launch(argv, **kwargs):
                if str(TRUSTED_ROOT / "sandbox_exec.py") in argv:
                    self.assertEqual(argv[:3], ["/usr/bin/sudo", "-n", "--"])
                    self.assertNotIn("--user", argv)
                    self.assertEqual(kwargs["pass_fds"], ())
                    self.assertTrue(kwargs["close_fds"])
                    self.assertEqual(kwargs["stdin"], subprocess.PIPE)
                    config = json.loads(Path(argv[-1]).read_bytes())
                    self.assertTrue(config["sudo_drop"])
                    self.assertEqual((config["runner_uid"], config["runner_gid"]), (os.getuid(), os.getgid()))
                    invocations.append(config["mode"])
                return launch(argv, **kwargs)
            def capture_peer(listener):
                connection, address = accept(listener)
                peers.append(struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)))
                return connection, address
            with patch.object(session.budget, "run", fallback), session:
                self.assertTrue(session.sudo_drop)
                self.assertEqual(tuple(session.launcher), foundation.NAMESPACE_LAUNCHER)
                with self.capture_reports(session, reports), patch(
                    "subprocess.Popen", capture_launch,
                ), patch.object(socket.socket, "accept", capture_peer):
                    self.assertEqual(session.make("all", makefile="plain.mk").events, ())
                    observed = session.make(
                        "all", variables=("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS"),
                        commands={command: producer},
                    )
                    credentials = session.command(Command((
                        "/usr/bin/python3", "-c",
                        "import json,os;print(json.dumps([os.getresuid(),os.getresgid(),os.getgroups()]))",
                    )))
                self.assertEqual(observed.semantics["domains"]["SELECTED"]["value"], "observed")
                self.assertEqual(observed.semantics["domains"]["MAKEFILE_LIST"]["value"], "Makefile generated.mk")
                self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(len(observed.events), 1)
                self.assertEqual(json.loads(credentials.stdout), [
                    [os.getuid()]*3, [os.getgid()]*3, [],
                ])
                self.assertCountEqual(invocations, ["make", "make", "command", "command"])
                self.assertEqual(len(peers), 2)
                self.assertTrue(all(pid > 0 and pid != os.getpid() and uid == 0 for pid, uid, _ in peers))
                self.assertEqual(len(preflights), 1)
                self.assert_settled_reports(session, reports)
                self.assertFalse((session.tree / "generated.mk").exists())
                print("privileged route evidence:", json.dumps({
                    "actual_user_namespace_preflight": preflights[0],
                    "modeled_preflight_denial": preflights[0] == 0,
                    "peer_uids": [uid for _, uid, _ in peers],
                    "guest_credentials": json.loads(credentials.stdout),
                    "processes": session.processes_used, "live_peak": session.live_process_peak,
                    "memory_peak": session.memory_peak, "bytes": session.budget.bytes,
                }, sort_keys=True))
        finally:
            session.budget.close()
            self.fixture.assert_clean(session)

    def test_privileged_preflight_runtime_fault_is_not_a_permission_skip(self):
        if os.getuid() == 0 or os.getgid() == 0 or not Path("/usr/bin/sudo").is_file():
            self.skipTest("the privileged-route control requires sudo and a non-root runner")
        for status, diagnostic in (
            (125, b"namespace watchdog: [Errno 9] Bad file descriptor\n"),
            (1, b"Traceback: unexpected launcher failure\n"),
        ):
            with self.subTest(status=status):
                calls, run = [], foundation.ProbeBudget.run
                def failed_preflight(budget, argv, **kwargs):
                    if tuple(argv) == (*foundation.NAMESPACE_LAUNCHER, "/usr/bin/true"):
                        self.assertTrue(kwargs["privileged"])
                        calls.append(argv)
                        return subprocess.CompletedProcess(argv, status, b"", diagnostic)
                    return run(budget, argv, **kwargs)
                case = ProducerTests(
                    "test_real_privileged_fallback_preserves_live_results_credentials_and_accounting",
                )
                result = unittest.TestResult()
                with patch.object(foundation.ProbeBudget, "run", failed_preflight):
                    case.run(result)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result.skipped, [])
                self.assertEqual(result.errors, [])
                self.assertEqual(len(result.failures), 1)
                self.assertIn(diagnostic.decode("ascii").strip(), result.failures[0][1])

    def test_private_rendezvous_rejects_foreign_peer_before_producer_execution(self):
        command = self.producer_fixture()
        connections, executions, peers = [], [], []
        with self.fixture.session(seconds=30) as session:
            run, execute = session.budget.run, session.command
            accept = socket.socket.accept
            def capture_peer(listener):
                connection, address = accept(listener)
                peers.append(struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)))
                return connection, address
            def foreign_peer(argv, **kwargs):
                channel = kwargs.get("producer_channel")
                if channel is not None:
                    descriptor = os.open(channel.endpoint["directory"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        connections.append(connection)
                        connection.connect(f"/proc/self/fd/{descriptor}/peer.sock")
                    finally:
                        os.close(descriptor)
                return run(argv, **kwargs)
            def producer(value):
                executions.append(value)
                return execute(value)
            try:
                with patch.object(session.budget, "run", foreign_peer), patch.object(
                    session, "command", producer,
                ), patch.object(socket.socket, "accept", capture_peer):
                    expected = (
                        "foreign private producer peer credentials" if session.sudo_drop
                        else "foreign producer peer outside the owned launch"
                    )
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.make("all", commands={"python3 producer.py": command})
            finally:
                for connection in connections:
                    connection.close()
            self.assertEqual(len(connections), 1)
            self.assertEqual(peers, [(os.getpid(), os.getuid(), os.getgid())])
            self.assertEqual(executions, [])
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_same_uid_private_peer_must_belong_to_the_actual_live_launch(self):
        connector = (
            "import json,os,sys,time\nsys.path.insert(0,sys.argv[1])\n"
            "from scripts.validation_ownership.producer_channel import ProducerChannel\n"
            "peer=ProducerChannel.connect(json.loads(sys.argv[2]),owner_uid=int(sys.argv[3]),"
            "server_pid=int(sys.argv[4]),deadline=time.monotonic()+10,limit=4096)\n"
            "os.write(1,b'connected\\n');os.read(0,1);peer.close()\n"
        )
        for defect in ("launch", "credentials", "positive"):
            with self.subTest(defect=defect):
                directory = self.fixture.directory / ("same-uid-peer-" + defect)
                directory.mkdir(mode=0o700)
                listener = ProducerChannel.listen(directory, deadline=time.monotonic() + 10, limit=4096)
                peers, accept = [], socket.socket.accept
                def capture_peer(server):
                    connection, address = accept(server)
                    peers.append(struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)))
                    return connection, address
                try:
                    with self.fixture.owned_process([
                        "/usr/bin/python3", "-I", "-S", "-B", "-c", "import os;os.read(0,1)",
                    ]) as (other, _), self.fixture.owned_process([
                        "/usr/bin/python3", "-I", "-S", "-B", "-c", connector, str(foundation.ROOT),
                        json.dumps(listener.endpoint), str(os.getuid()), str(os.getpid()),
                    ]) as (child, _):
                        self.assertEqual(child.stdout.readline(), b"connected\n")
                        self.assertIsNone(other.poll())
                        self.assertIsNone(child.poll())
                        kwargs = {
                            "launcher_pid": other.pid if defect == "launch" else child.pid,
                            "peer_uid": os.getuid() + (defect == "credentials"), "ancestry_limit": 32,
                        }
                        with patch.object(socket.socket, "accept", capture_peer):
                            if defect == "positive":
                                listener.accept(**kwargs)
                                self.assertFalse(listener.listening)
                            else:
                                expected = (
                                    "foreign private producer peer credentials" if defect == "credentials"
                                    else "foreign producer peer outside the owned launch"
                                )
                                with self.assertRaisesRegex(ChannelError, expected):
                                    listener.accept(**kwargs)
                                self.assertTrue(listener.listening)
                        self.assertEqual(peers, [(child.pid, os.getuid(), os.getgid())])
                        child.stdin.write(b"x")
                        child.stdin.flush()
                        self.assertEqual(child.wait(timeout=5), 0, child.stderr.read())
                finally:
                    listener.close()
                    (directory / "peer.sock").unlink()
                    directory.rmdir()

    def test_private_rendezvous_binds_real_directory_socket_and_server_identity(self):
        for defect in ("positive", "directory", "mode", "socket", "symlink", "server"):
            with self.subTest(defect=defect):
                directory = self.fixture.directory / ("channel-" + defect)
                directory.mkdir(mode=0o700)
                listener = ProducerChannel.listen(directory, deadline=time.monotonic() + 5, limit=4096)
                endpoint = dict(listener.endpoint)
                peer = None
                try:
                    if defect == "directory":
                        endpoint["directory_inode"] += 1
                    elif defect == "mode":
                        directory.chmod(0o755)
                    elif defect == "socket":
                        (directory / "peer.sock").unlink()
                        (directory / "peer.sock").write_bytes(b"not a socket")
                    elif defect == "symlink":
                        (directory / "peer.sock").unlink()
                        (directory / "peer.sock").symlink_to(self.root)
                    kwargs = {
                        "owner_uid": os.getuid(), "server_pid": os.getpid() + (defect == "server"),
                        "deadline": time.monotonic() + 5, "limit": 4096,
                    }
                    if defect == "positive":
                        peer = ProducerChannel.connect(endpoint, **kwargs)
                        with listener.connection.accept()[0] as received:
                            peer.send(b"actual private bytes")
                            received.settimeout(5)
                            data = received.recv(64)
                            self.assertEqual(int.from_bytes(data[:4], "little"), len(data[4:]))
                            self.assertEqual(data[4:], b"actual private bytes")
                    else:
                        with self.assertRaisesRegex(ChannelError, "foreign|replaced"):
                            ProducerChannel.connect(endpoint, **kwargs)
                finally:
                    if peer is not None:
                        peer.close()
                    listener.close()
                    (directory / "peer.sock").unlink()
                    directory.rmdir()

    def test_parked_make_resources_are_not_granted_again_to_the_producer(self):
        command = self.producer_fixture()
        for limit in ({"processes": 2}, {"descendants": 2}):
            with self.subTest(limit=limit):
                reached = []
                class Commands:
                    def __contains__(self, value):
                        return True
                    def __getitem__(self, value):
                        reached.append(value)
                        return command
                with self.fixture.session(seconds=30, **limit) as session:
                    with self.assertRaisesRegex(MakeProbeError, "resource budget exhausted"):
                        session.make("all", commands=Commands())
                    self.assertEqual(reached, ["python3 producer.py"])
                    self.assertEqual(session.processes_used, 2)
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_all_funded_parked_vm_credits_remain_reserved(self):
        command = self.producer_fixture()
        self.fixture.add("producer.py", (
            "data=bytearray(16*1024*1024)\n"
            "open('/work/generated.txt','w').write('actual')\nprint('observed')\n"
        ))
        reached = []
        class Commands:
            def __contains__(self, value):
                return True
            def __getitem__(self, value):
                reached.append(value)
                return command
        with self.fixture.session(seconds=30, address_space_bytes=64*1024*1024) as session:
            self.assertEqual(session.command(command).stdout, b"observed\n")
            with self.assertRaisesRegex(MakeProbeError, "address-space"):
                session.make("all", commands=Commands())
            self.assertEqual(reached, ["python3 producer.py"])
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_reserved_absent_source_cannot_be_published(self):
        command = self.producer_fixture()
        self.fixture.add("reserved.txt", "admitted")
        (self.root / "reserved.txt").unlink()
        command = Command(command.argv, code=command.code, outputs=("reserved.txt",))
        with self.fixture.session(seconds=30) as session:
            self.assertIn("reserved.txt", session.snapshot.absent_paths)
            with self.assertRaisesRegex(MakeProbeError, "conflicts with immutable source"):
                session.make("all", commands={"python3 producer.py": command})
            self.assertFalse((session.tree / "reserved.txt").exists())
        self.fixture.assert_clean(session)

    def test_parked_helper_death_aborts_without_publication(self):
        command = self.producer_fixture()
        killed = []
        with self.fixture.session(seconds=30) as session:
            class Commands:
                def __contains__(self, value):
                    return True
                def __getitem__(self, value):
                    leader, = session.budget.children
                    current = leader.pid
                    descriptors = []
                    try:
                        for _ in range(16):
                            children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
                            if not children:
                                break
                            if len(children) != 1:
                                raise AssertionError("owned parked fixture is not one descendant chain")
                            current = int(children[0])
                            descriptors.append(os.pidfd_open(current))
                        else:
                            raise AssertionError("owned descendant chain exceeded its bound")
                        if len(descriptors) < 3:
                            raise AssertionError("owned native Make helper was not reached")
                        signal.pidfd_send_signal(descriptors[-1], signal.SIGKILL)
                        killed.append(current)
                    finally:
                        for descriptor in descriptors:
                            os.close(descriptor)
                    return command
            with self.assertRaises(MakeProbeError):
                session.make("all", commands=Commands())
            self.assertEqual(len(killed), 1)
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_producer_cannot_reach_callback_descriptors_or_private_channels(self):
        command = self.producer_fixture()
        for operation, denial in (
            ("os.fstat(3)", "unavailable inherited/unknown descriptor"),
            ("open('/control/map/count').read()", "supervisor channel denied"),
            ("open('/repo/Makefile','w').write('changed')", "write outside"),
            (f"ctypes.CDLL(None).syscall(39,ctypes.c_ulong({VO_PRODUCE}),1,20)", "unauthenticated"),
        ):
            with self.subTest(operation=operation):
                self.fixture.add("producer.py", (
                    "import ctypes,os\nprint('producer-started',flush=True)\n" + operation + "\n"
                ))
                outputs = []
                with self.fixture.session(seconds=30) as session:
                    original = session.budget.run
                    def record(argv, **kwargs):
                        result = original(argv, **kwargs)
                        if result.stdout:
                            outputs.append(result.stdout)
                        return result
                    with patch.object(session.budget, "run", record):
                        with self.assertRaisesRegex(MakeProbeError, denial):
                            session.make("all", commands={"python3 producer.py": command})
                    self.assertIn(b"producer-started\n", outputs)
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_parent_lifetime_loss_interrupts_nested_producer_and_cleans(self):
        command = self.producer_fixture()
        self.fixture.add("producer.py", (
            "import time\nprint('producer-started',flush=True)\n"
            "time.sleep(20)\nopen('/work/generated.txt','w').write('too late')\n"
        ))
        interrupted = []
        with self.fixture.session(seconds=30) as session:
            capsule, charge = session._sandbox_run, session.budget.charge
            inside = False
            def run(root, **kwargs):
                nonlocal inside
                producer = kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]
                previous = inside
                inside = producer or inside
                try:
                    return capsule(root, **kwargs)
                finally:
                    inside = previous
            def lose_lifetime(category, size):
                result = charge(category, size)
                if category == "output" and size and inside and not interrupted:
                    outer = next(iter(session.budget.children))
                    outer.stdin.close()
                    interrupted.append(outer.pid)
                return result
            with patch.object(session, "_sandbox_run", run), patch.object(session.budget, "charge", lose_lifetime):
                with self.assertRaisesRegex(MakeProbeError, "producer|rendezvous|lifetime"):
                    session.make("all", commands={"python3 producer.py": command})
            self.assertEqual(len(interrupted), 1)
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_missing_extra_and_failed_sources_never_publish(self):
        for program, sources, expected in (
            ("print('no output')\n", (), "missing declared generated"),
            ("open('/work/generated.txt','w').write('actual')\nopen('/work/extra','w').write('extra')\n",
             (), "undeclared or nonregular generated output"),
            ("open('/work/generated.txt','w').write('actual')\n", ("required.txt",), "declared/consumed"),
        ):
            with self.subTest(expected=expected):
                command = self.producer_fixture()
                self.fixture.add("required.txt", "required")
                self.fixture.add("producer.py", program)
                command = Command(command.argv, code=command.code, sources=sources, outputs=command.outputs)
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.make("all", commands={"python3 producer.py": command})
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_output_capture_rejects_real_nonregular_and_oversized_files(self):
        self.fixture.add("Makefile", "all: ;\n")
        root = self.fixture.directory / "captured-output"
        root.mkdir()
        target = root / "result"
        for kind in ("symlink", "fifo", "oversized"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    target.symlink_to(self.root)
                elif kind == "fifo":
                    os.mkfifo(target)
                else:
                    with target.open("wb") as stream:
                        stream.truncate(16*1024*1024 + 1)
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaises(MakeProbeError):
                        session._capture_outputs(root, ("result",))
                self.fixture.assert_clean(session)
                target.unlink()
        command = self.producer_fixture()
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "canonical and repository-relative"):
                session.make("all", commands={
                    "python3 producer.py": Command(command.argv, code=command.code, outputs=("../escape",)),
                })
        self.fixture.assert_clean(session)

    def test_real_repository_scaninc_native_registration_is_a_live_result(self):
        sources, headers = [], []
        for path in sorted((foundation.ROOT / "tools/scaninc").iterdir()):
            if path.suffix not in {".cpp", ".h"}:
                continue
            name = path.relative_to(foundation.ROOT).as_posix()
            self.fixture.add(name, path.read_bytes())
            (sources if path.suffix == ".cpp" else headers).append(name)
        self.fixture.add("proof.s", '.incbin "proof.bin"\n')
        actual = 'tools/scaninc/scaninc -I include -I "" proof.s'
        self.fixture.add("Makefile", f"INPUTS := $(shell {actual})\nall: $(INPUTS)\nproof.bin: ;\n")
        with self.fixture.session(seconds=45) as session:
            tool = session.compile_native(tuple(sources), headers=tuple(headers), cxx=True)
            registration = Command(
                ("/native/tool", "-I", "include", "-I", "", "proof.s"),
                sources=("proof.s",), native_tool=tool,
            )
            observed = session.make("all", variables=("INPUTS",), commands={actual: registration})
            self.assertEqual(observed.semantics["domains"]["INPUTS"]["value"], "proof.bin")
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "proof.bin", "order_only": False},
            ])
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertEqual(dynamic["command"]["native_tool"]["sha256"], tool.digest)
            self.assertEqual(dynamic["command"]["inputs"], session.snapshot.owners(("proof.s",)))
            self.assertFalse((session.tree / "native/tool").exists())
        self.fixture.assert_clean(session)

    def test_repeated_effects_execute_while_alias_identity_deduplicates(self):
        command = self.producer_fixture()
        alias = "python3 producer.py; printf ''"
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 producer.py)\n"
            f"SECOND := $(shell {alias})\nall: ;\n"
        ))
        with self.fixture.session(seconds=30) as session:
            run, executed = session._sandbox_run, []
            def count(root, **kwargs):
                if kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]:
                    executed.append(tuple(kwargs["argv"]))
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", count):
                result = session.make("all", commands={
                    "python3 producer.py": command, alias: command,
                })
            self.assertEqual(len(executed), 2)
            self.assertEqual(len(result.events), 2)
            self.assertEqual(len(result.semantics["dynamic_commands"]), 1)
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
                session.make("all", commands={
                    "python3 producer.py": command,
                    alias: Command((*command.argv, "different"), code=command.code, outputs=command.outputs),
                })
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_chained_outputs_have_two_authentic_native_restarts(self):
        names = ("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS")
        recipe = " ".join(f"'$({form}{name})'" for name in names for form in ("", "origin ", "flavor "))
        self.fixture.add("first.in", "include second.mk\n")
        self.fixture.add("second.in", "SELECTED := observed\nobserved: ;\n")
        self.fixture.add("producer.py", "import sys\nopen(sys.argv[2],'wb').write(open(sys.argv[1],'rb').read())\n")
        self.fixture.add("Makefile", (
            "include first.mk\nfirst.mk: first.in\n\t@python3 producer.py first.in first.mk\n"
            "second.mk: second.in\n\t@python3 producer.py second.in second.mk\n"
            f"all: $(SELECTED)\n\t@printf '%s\\n' '$^' {recipe}\n"
        ))
        ordinary = self.fixture.ordinary_assignment_context((), names)
        (self.root / "first.mk").unlink()
        (self.root / "second.mk").unlink()
        registrations = {
            f"python3 producer.py {name}.in {name}.mk": Command(
                ("/usr/bin/python3", "/repo/producer.py", name + ".in", "/work/" + name + ".mk"),
                code=("producer.py",), sources=(name + ".in",), outputs=(name + ".mk",),
            ) for name in ("first", "second")
        }
        with self.fixture.session(seconds=30) as session:
            result = session.make("all", variables=names, commands=registrations)
            self.assertEqual(result.semantics["domains"], ordinary[1])
            self.assertEqual(result.semantics["files"][0]["prerequisites"], ordinary[0])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "2")
            self.assertEqual(len(result.semantics["dynamic_commands"]), 2)
        self.fixture.assert_clean(session)

    def parallel_dispatch_fixture(self):
        self.fixture.add("producer.py", (
            "import sys\n"
            "open('/work/'+sys.argv[1]+'.txt','w').write(sys.argv[1])\n"
            "print(sys.argv[1])\n"
        ))
        self.fixture.add("Makefile", (
            "MAKEFLAGS += -j2\nall: one two\n"
            "one:\n\t+@python3 producer.py one\n"
            "two:\n\t+@python3 producer.py two\n"
        ))
        registrations = {
            "python3 producer.py " + name: Command(
                ("/usr/bin/python3", "/repo/producer.py", name),
                code=("producer.py",), outputs=(name + ".txt",),
            ) for name in ("one", "two")
        }
        return registrations

    def test_native_parallel_dispatch_keeps_distinct_request_receipts(self):
        registrations = self.parallel_dispatch_fixture()
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", commands=registrations)
            self.assertEqual(set(observed.stdout.splitlines()), {b"one", b"two"})
            self.assertEqual({event["match"] for event in observed.events}, {0, 1})
            self.assertEqual(len(observed.events), 2)
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 2)
            self.assertLessEqual(session.pending_commands_peak, session.budget.limits.pending)
            self.assertFalse((session.tree / "one.txt").exists())
            self.assertFalse((session.tree / "two.txt").exists())
        self.fixture.assert_clean(session)

    def test_native_parallel_events_follow_physical_append_order_not_exit_stop_order(self):
        registrations = self.parallel_dispatch_fixture()
        proxy = self.fixture.directory / "event-order-supervisor.py"
        proof = self.fixture.directory / "event-order-proof.json"
        proxy.write_text(
            "import ctypes,inspect,json,os,signal,sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(TRUSTED_ROOT)!r})\n"
            "import syscall_guard as guard,sandbox_exec\n"
            "config=json.loads(Path(sys.argv[1]).read_bytes())\n"
            "events=Path(config['root'])/'control/events'; wait=os.waitpid\n"
            "entry=None; entry_returned=False; blocked=None; held=None; release=[]\n"
            f"proof=Path({str(proof)!r}); record={{'leave_order':[]}}\n"
            "def save(): proof.write_text(json.dumps(record,sort_keys=True))\n"
            "def event_stop(values,child,status):\n"
            " state=values['processes'].get(child)\n"
            " if state is None or state.role!='helper' or not os.WIFSTOPPED(status) or "
            "os.WSTOPSIG(status)!=(signal.SIGTRAP|0x80): return None\n"
            " info=(ctypes.c_ubyte*128)(); guard.ptrace(0x420E,child,128,ctypes.byref(info))\n"
            " regs=guard.Registers(); guard.ptrace(guard.GETREGS,child,0,ctypes.byref(regs))\n"
            " if regs.orig_rax!=1: return None\n"
            " if info[0]==1 and state.fds.get(regs.rdi)=='/control/events':\n"
            "  return 'entry',state,guard.memory(child,regs.rsi,regs.rdx)\n"
            " if info[0]==2 and state.pending is not None and state.pending[0]=='event':\n"
            "  return 'exit',state,state.pending[1]\n"
            " return None\n"
            "def ordered_wait(pid,options):\n"
            " global entry,entry_returned,blocked,held\n"
            " caller=inspect.currentframe().f_back\n"
            " if caller.f_code.co_name not in ('supervise','fulfill_producer') or not options&os.WNOHANG:\n"
            "  return wait(pid,options)\n"
            " if release: return release.pop(0)[:2]\n"
            " while True:\n"
            "  child,status=wait(pid,options)\n"
            "  if not child: return child,status\n"
            "  item=event_stop(caller.f_locals,child,status)\n"
            "  if item is None: return child,status\n"
            "  phase,state,frame=item\n"
            "  if phase=='entry':\n"
            "   if entry is None: entry=(child,status,frame); continue\n"
            "   if child!=entry[0] and not entry_returned:\n"
            "    blocked=(child,status,frame); entry_returned=True; return entry[:2]\n"
            "   return child,status\n"
            "  raw=events.read_bytes()\n"
            "  if child==entry[0] and held is None:\n"
            "   if raw!=frame: raise RuntimeError('first physical append differs from its write')\n"
            "   held=(child,status,frame); record['physical_after_first']=raw.hex(); save()\n"
            "   if blocked is not None:\n"
            "    value=blocked; blocked=None; return value[:2]\n"
            "   continue\n"
            "  if held is not None and child!=held[0]:\n"
            "   if raw!=held[2]+frame: raise RuntimeError('physical append order changed')\n"
            "   record['physical']=raw.hex(); release.append(held); held=None; save(); return child,status\n"
            "  return child,status\n"
            "leave=guard.Policy.leave\n"
            "def recorded_leave(self,pid,state,regs):\n"
            " operation=state.pending[0] if state.pending is not None else None\n"
            " frame=state.pending[1] if operation=='event' else None\n"
            " result=leave(self,pid,state,regs)\n"
            " if operation=='event': record['leave_order'].append(frame.hex()); save()\n"
            " return result\n"
            "guard.os.waitpid=ordered_wait; guard.Policy.leave=recorded_leave\n"
            "raise SystemExit(sandbox_exec.main())\n",
        )
        original = foundation.ProbeBudget.run
        reports = []
        def supervise(budget, argv, **kwargs):
            if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                config = json.loads(Path(argv[-1]).read_bytes())
                if config["mode"] == "make":
                    argv = [*argv[:-2], str(proxy), argv[-1]]
            return original(budget, argv, **kwargs)
        with self.fixture.session(seconds=30) as session:
            with patch.object(foundation.ProbeBudget, "run", supervise), self.capture_reports(session, reports):
                observed = session.make("all", commands=registrations)
            record = json.loads(proof.read_bytes())
            physical_raw = bytes.fromhex(record["physical"])
            physical = list(_read_event_frames(physical_raw, expected_mapping_count=None))
            exits = [bytes.fromhex(frame) for frame in record["leave_order"]]
            self.assertEqual(Counter(frame for frame, _ in physical), Counter(exits))
            self.assertEqual([frame for frame, _ in physical], list(reversed(exits)))
            self.assertEqual(
                [_event_command(event) for _, event in physical],
                [_event_command(event) for event in observed.events],
            )
            self.assertEqual(set(observed.stdout.splitlines()), {b"one", b"two"})
            self.assertEqual({event["match"] for event in observed.events}, {0, 1})
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 2)
            self.assertEqual(session.budget.bytes["event"], len(physical_raw))
            self.assertEqual(reports[-1]["events"], record["leave_order"])
            self.assertGreaterEqual(reports[-1]["written_bytes"], len(physical_raw))
            self.assert_settled_reports(session, reports)
            self.assertFalse((session.tree / "one.txt").exists())
            self.assertFalse((session.tree / "two.txt").exists())
        self.fixture.assert_clean(session)

    def test_native_event_stream_and_write_observations_match_exact_bytes_with_multiplicity(self):
        registrations = self.parallel_dispatch_fixture()
        cases = (
            "observation-missing", "observation-extra", "observation-duplicate",
            "observation-bitflip", "physical-missing", "physical-extra",
            "physical-duplicate", "physical-bitflip", "physical-partial",
            "physical-trailing", "matched-duplicate", "wrong-slot",
            "wrong-mapping-count", "wrong-hash", "wrong-command",
        )
        for defect in cases:
            with self.subTest(defect=defect):
                target = {}
                with self.fixture.session(seconds=30) as session:
                    read = session.budget.read_bytes
                    def transform(frame):
                        data = bytearray(frame)
                        if defect == "wrong-slot":
                            slot = int.from_bytes(data[:4], "little", signed=True)
                            replacement = 1 - slot
                            data[:4] = replacement.to_bytes(4, "little", signed=True)
                            data[4:8] = (replacement + 1).to_bytes(4, "little")
                        elif defect == "wrong-mapping-count":
                            data[4:8] = (int.from_bytes(data[4:8], "little") + 1).to_bytes(4, "little")
                        elif defect == "wrong-hash":
                            data[8] ^= 1
                        elif defect == "wrong-command":
                            event = next(_read_event_frames(frame, expected_mapping_count=None))[1]
                            old = event["arguments"][-1]
                            new = "two" if old == "one" else "one"
                            data[-len(old):] = new.encode()
                            event["arguments"][-1] = new
                            data[8:16] = int(
                                _command_hash(_event_command(event)), 16,
                            ).to_bytes(8, "little")
                        return bytes(data)
                    def corrupt(path, category):
                        raw = read(path, category)
                        if path.parent == session.base and path.name.startswith("report-"):
                            report = json.loads(raw)
                            events = report["events"]
                            if events:
                                target["frame"] = bytes.fromhex(events[0])
                                if defect == "observation-missing":
                                    events.pop(0)
                                elif defect == "observation-extra":
                                    events.append(events[0])
                                elif defect == "observation-duplicate":
                                    events[1] = events[0]
                                elif defect == "matched-duplicate":
                                    events[1] = events[0]
                                elif defect == "observation-bitflip":
                                    frame = bytearray.fromhex(events[0]); frame[-1] ^= 1
                                    events[0] = frame.hex()
                                elif defect.startswith("wrong-"):
                                    events[0] = transform(target["frame"]).hex()
                            return json.dumps(report).encode()
                        if category != "event":
                            return raw
                        frames = [frame for frame, _ in _read_event_frames(
                            raw, expected_mapping_count=None,
                        )]
                        if defect == "physical-missing":
                            return b"".join(frames[1:])
                        if defect == "physical-extra":
                            return raw + frames[0]
                        if defect == "physical-duplicate":
                            return frames[0] + frames[0]
                        if defect == "matched-duplicate":
                            return target["frame"] + target["frame"]
                        if defect == "physical-bitflip":
                            data = bytearray(raw); data[-1] ^= 1; return bytes(data)
                        if defect == "physical-partial":
                            return raw[:-1]
                        if defect == "physical-trailing":
                            return raw + b"\0"
                        if defect.startswith("wrong-"):
                            replacement = transform(target["frame"])
                            return raw.replace(target["frame"], replacement, 1)
                        return raw
                    with patch.object(session.budget, "read_bytes", corrupt):
                        if defect == "matched-duplicate":
                            with self.assertRaisesRegex(
                                MakeProbeError, "unknown or repeated live producer completion",
                            ):
                                session.make("all", commands=registrations)
                        else:
                            with self.assertRaises(MakeProbeError):
                                session.make("all", commands=registrations)
                    self.assertTrue(session.budget.closed)
                    self.assertFalse(session.budget.children)
                    self.assertFalse((session.tree / "one.txt").exists())
                    self.assertFalse((session.tree / "two.txt").exists())
                self.fixture.assert_clean(session)

    def test_parallel_vfork_parent_can_park_while_its_child_waits_at_exec(self):
        proxy = self.fixture.directory / "ordered-supervisor.py"
        proof = self.fixture.directory / "vfork-interleaving.json"
        proxy.write_text(
            "import ctypes,inspect,json,os,signal,sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(TRUSTED_ROOT)!r})\n"
            "import syscall_guard as guard,sandbox_exec\n"
            "wait=os.waitpid; ready=None; held=None; ordered=False; execs=set()\n"
            "def arm(values):\n"
            " global ready,ordered\n"
            " parent=values['processes'][values['pid']]; child=values['processes'][held[0]]\n"
            f" Path({str(proof)!r}).write_text(json.dumps({{'parent_call':parent.kernel_call,"
            "'parent_parked':parent.parked,'child_call':59,"
            "'shared_group':child.memory_group==parent.memory_group}))\n"
            " ordered=True; value=ready; ready=None; return value\n"
            "def ordered_wait(pid,options):\n"
            " global ready,held,ordered\n"
            " frame=inspect.currentframe().f_back; values=frame.f_locals\n"
            " if frame.f_code.co_name not in ('supervise','fulfill_producer') or not options&os.WNOHANG:\n"
            "  return wait(pid,options)\n"
            " if held is not None and frame.f_code.co_name=='fulfill_producer':\n"
            "  value=held; held=None; return value\n"
            " while True:\n"
            "  child,status=wait(pid,options)\n"
            "  if not child or ordered: return child,status\n"
            "  state=values['processes'].get(child)\n"
            "  if state is None or not os.WIFSTOPPED(status) or os.WSTOPSIG(status)!=(signal.SIGTRAP|0x80):\n"
            "   return child,status\n"
            "  info=(ctypes.c_ubyte*128)(); guard.ptrace(0x420E,child,128,ctypes.byref(info))\n"
            "  if ready is None and state.pending is not None and state.pending[0]=='producer' and info[0]==2:\n"
            "   ready=(child,status)\n"
            "   if held is not None: return arm(values)\n"
            "   continue\n"
            "  if child!=values['pid'] and info[0]==1:\n"
            "   registers=guard.Registers(); guard.ptrace(guard.GETREGS,child,0,ctypes.byref(registers))\n"
            "   parent=values['processes'][values['pid']]\n"
            "   if registers.orig_rax==59 and state.memory_group==parent.memory_group and parent.kernel_call in (56,58,435):\n"
            "    execs.add(child)\n"
            "    if len(execs)==2:\n"
            "     held=(child,status)\n"
            "     if ready is not None: return arm(values)\n"
            "     continue\n"
            "  return child,status\n"
            "guard.os.waitpid=ordered_wait\nraise SystemExit(sandbox_exec.main())\n",
        )
        original = foundation.ProbeBudget.run
        reservations, funded = [], []
        def supervise(budget, argv, **kwargs):
            if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                config = json.loads(Path(argv[-1]).read_bytes())
                if config["mode"] == "make":
                    argv = [*argv[:-2], str(proxy), argv[-1]]
                    handler = kwargs["producer_handler"]
                    def capture(packet):
                        request = json.loads(packet)
                        if request.get("kind") == "request":
                            reservations.append(request["reserved"])
                        return handler(packet)
                    kwargs["producer_handler"] = capture
                elif "/repo/producer.py" in config["argv"]:
                    funded.append((config["process_limit"], config["memory_limit"], dict(reservations[-1])))
            return original(budget, argv, **kwargs)
        with patch.object(foundation.ProbeBudget, "run", supervise):
            try:
                self.test_native_parallel_dispatch_keeps_distinct_request_receipts()
            finally:
                if proof.exists():
                    print("owned vfork interleaving:", proof.read_text(), flush=True)
        observed = json.loads(proof.read_bytes())
        self.assertIn(observed["parent_call"], (56, 58, 435))
        self.assertFalse(observed["parent_parked"])
        self.assertEqual(observed["child_call"], 59)
        self.assertTrue(observed["shared_group"])
        self.assertEqual(len(funded), 2)
        self.assertEqual(reservations[0]["live"], 3)
        for processes, memory, parked in funded:
            self.assertEqual(processes + parked["processes"], foundation.Limits().processes)
            self.assertEqual(memory + parked["memory"], foundation.Limits().address_space_bytes)

    def test_generated_source_replacement_invalidates_a_reader_without_stat_calls(self):
        self.fixture.add("state/current", "original")
        self.fixture.add("writer.py", (
            "from pathlib import Path\nimport os,sys\n"
            "root=Path(sys.argv[1])/'generated'\nroot.mkdir(exist_ok=True)\n"
            "(root/'input').write_text(str(len(os.listdir('state'))))\n"
        ))
        self.fixture.add("marker.py", (
            "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])/'state'\n"
            "root.mkdir(exist_ok=True)\n(root/'new').write_text('new')\n"
        ))
        self.fixture.add("reader.py", (
            "import os,sys\nfd=os.open('generated/input',os.O_RDONLY)\n"
            "sys.stdout.write(os.read(fd,16).decode())\nos.close(fd)\n"
        ))
        self.fixture.add("Makefile", (
            "WRITE1 := $(shell python3 writer.py .)\n"
            "FIRST := $(shell python3 reader.py)\n"
            "UNCHANGED := $(shell python3 reader.py)\n"
            "MARK := $(shell python3 marker.py .)\n"
            "WRITE2 := $(shell python3 writer.py .)\n"
            "SECOND := $(shell python3 reader.py)\n"
            "all:\n\t@printf '%s\\n' '$(FIRST)' '$(SECOND)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.splitlines(), [b"1", b"2"])
        (self.root / "generated/input").unlink()
        (self.root / "generated").rmdir()
        (self.root / "state/new").unlink()
        registrations = {
            "python3 writer.py .": Command(
                ("/usr/bin/python3", "/repo/writer.py", "/work"),
                code=("writer.py",), directories=("state",), outputs=("generated/input",),
            ),
            "python3 marker.py .": Command(
                ("/usr/bin/python3", "/repo/marker.py", "/work"),
                code=("marker.py",), outputs=("state/new",),
            ),
            "python3 reader.py": Command(
                ("/usr/bin/python3", "/repo/reader.py"),
                code=("reader.py",), sources=("generated/input",),
            ),
        }
        with self.fixture.session(seconds=30) as session:
            execute, readers = session.command, []
            def record(command):
                result = execute(command)
                if command is registrations["python3 reader.py"]:
                    readers.append(result)
                return result
            with patch.object(session, "command", record):
                observed = session.make(
                    "all", variables=("FIRST", "UNCHANGED", "SECOND"), commands=registrations,
                )
            self.assertEqual(
                [observed.semantics["domains"][name]["value"] for name in ("FIRST", "UNCHANGED", "SECOND")],
                ["1", "1", "2"],
            )
            self.assertIs(readers[0], readers[1])
            self.assertIsNot(readers[1], readers[2])
            identities = [
                next(item for item in result.input_identities if item[0] == "generated/input")
                for result in readers
            ]
            self.assertEqual(identities, [
                ("generated/input", "100644", hashlib.sha256(value).hexdigest())
                for value in (b"1", b"1", b"2")
            ])
            records = [
                item for item in observed.semantics["dynamic_commands"]
                if item["command"]["argv"] == list(registrations["python3 reader.py"].argv)
            ]
            self.assertEqual(len(records), 2)
            self.assertEqual({
                (next(item[2] for item in record["command"]["inputs"] if item[0] == "generated/input"),
                 record["output_sha256"])
                for record in records
            }, {(hashlib.sha256(value).hexdigest(), hashlib.sha256(value).hexdigest()) for value in (b"1", b"2")})
        self.fixture.assert_clean(session)

    def test_generated_code_replacement_binds_bytes_mode_and_execution_provenance(self):
        for change in ("bytes", "mode"):
            with self.subTest(change=change):
                self.fixture.add("state/current", "original")
                code = "'print('+str(count)+')\\n'" if change == "bytes" else "'print(1)\\n'"
                self.fixture.add("writer.py", (
                    "from pathlib import Path\nimport os,sys\n"
                    "count=len(os.listdir('state'))\n"
                    "root=Path(sys.argv[1])/'generated'\nroot.mkdir(exist_ok=True)\n"
                    f"(root/'code.py').write_text({code})\n"
                    + (
                        "fd=os.open(root/'code.py',os.O_WRONLY)\n"
                        "os.fchmod(fd,0o644 if count==1 else 0o600); os.close(fd)\n"
                        if change == "mode" else ""
                    )
                ))
                self.fixture.add("marker.py", (
                    "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])/'state'\n"
                    "root.mkdir(exist_ok=True)\n(root/'new').write_text('new')\n"
                ))
                self.fixture.add("reader.py", (
                    "import os\nfd=os.open('generated/code.py',os.O_RDONLY)\n"
                    "code=os.read(fd,64)\nos.close(fd)\nexec(code)\n"
                ))
                self.fixture.add("Makefile", (
                    "WRITE1 := $(shell python3 writer.py .)\n"
                    "FIRST := $(shell python3 reader.py)\n"
                    "UNCHANGED := $(shell python3 reader.py)\n"
                    "MARK := $(shell python3 marker.py .)\n"
                    "WRITE2 := $(shell python3 writer.py .)\n"
                    "SECOND := $(shell python3 reader.py)\n"
                    "all:\n\t@printf '%s\\n' '$(FIRST)' '$(UNCHANGED)' '$(SECOND)'\n"
                ))
                ordinary = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
                    capture_output=True, check=True, timeout=10,
                )
                expected = ["1", "1", "2" if change == "bytes" else "1"]
                self.assertEqual(ordinary.stdout.decode().splitlines(), expected)
                (self.root / "generated/code.py").unlink()
                (self.root / "generated").rmdir()
                (self.root / "state/new").unlink()
                reader = Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "generated/code.py"),
                )
                registrations = {
                    "python3 writer.py .": Command(
                        ("/usr/bin/python3", "/repo/writer.py", "/work"),
                        code=("writer.py",), directories=("state",), outputs=("generated/code.py",),
                    ),
                    "python3 marker.py .": Command(
                        ("/usr/bin/python3", "/repo/marker.py", "/work"),
                        code=("marker.py",), outputs=("state/new",),
                    ),
                    "python3 reader.py": reader,
                }
                with self.fixture.session(seconds=30) as session:
                    execute, results = session.command, []
                    def record(command):
                        result = execute(command)
                        if command is reader:
                            results.append(result)
                        return result
                    with patch.object(session, "command", record):
                        observed = session.make(
                            "all", variables=("FIRST", "UNCHANGED", "SECOND"), commands=registrations,
                        )
                    self.assertEqual(
                        [observed.semantics["domains"][name]["value"] for name in ("FIRST", "UNCHANGED", "SECOND")],
                        expected,
                    )
                    self.assertIs(results[0], results[1])
                    self.assertIsNot(results[1], results[2])
                    for result in results:
                        self.assertIn("generated/code.py", result.code_consumed)
                        self.assertFalse(any(item[1] == "/repo/generated/code.py" for item in result.metadata))
                    identities = [
                        next(item for item in result.input_identities if item[0] == "generated/code.py")
                        for result in results
                    ]
                    self.assertEqual(identities, [
                        ("generated/code.py", "100600" if change == "mode" and index == 2 else "100644",
                         hashlib.sha256(("print(" + value + ")\n").encode()).hexdigest())
                        for index, value in enumerate(expected)
                    ])
                    records = [
                        item for item in observed.semantics["dynamic_commands"]
                        if item["command"]["argv"] == list(reader.argv)
                    ]
                    self.assertEqual(len(records), 2)
                    self.assertEqual({
                        (tuple(next(item for item in row["command"]["inputs"] if item[0] == "generated/code.py")),
                         row["output_sha256"]) for row in records
                    }, {
                        (identity, hashlib.sha256((value + "\n").encode()).hexdigest())
                        for identity, value in zip(identities, expected)
                    })
                    runs = session.budget.runs
                    with self.assertRaisesRegex(MakeProbeError, "unadmitted command code: generated/code.py"):
                        session.command(reader)
                    self.assertEqual(session.budget.runs, runs)
                self.fixture.assert_clean(session)

    def test_generated_glob_membership_binds_resolved_sources_and_preserves_unchanged_reuse(self):
        self.fixture.add("writer.py", (
            "from pathlib import Path\nimport sys\n"
            "root=Path(sys.argv[1])/'generated'\nroot.mkdir(exist_ok=True)\n"
            "(root/(sys.argv[2]+'.txt')).write_text(sys.argv[2])\n"
        ))
        self.fixture.add("reader.py", (
            "import os\nvalues=[]\n"
            "for name in sorted(os.listdir('generated')):\n"
            " if name.endswith('.txt'):\n"
            "  fd=os.open('generated/'+name,os.O_RDONLY)\n"
            "  values.append(os.read(fd,16).decode()); os.close(fd)\n"
            "print(','.join(values))\n"
        ))
        self.fixture.add("Makefile", (
            "WRITE1 := $(shell python3 writer.py . a)\n"
            "FIRST := $(shell python3 reader.py)\n"
            "UNCHANGED := $(shell python3 reader.py)\n"
            "WRITE2 := $(shell python3 writer.py . b)\n"
            "SECOND := $(shell python3 reader.py)\n"
            "all:\n\t@printf '%s\\n' '$(FIRST)' '$(UNCHANGED)' '$(SECOND)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.splitlines(), [b"a", b"a", b"a,b"])
        for name in ("a", "b"):
            (self.root / ("generated/" + name + ".txt")).unlink()
        (self.root / "generated").rmdir()
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",),
            sources=("generated/*.txt",), directories=("generated",),
        )
        registrations = {
            "python3 reader.py": reader,
            **{
                "python3 writer.py . " + name: Command(
                    ("/usr/bin/python3", "/repo/writer.py", "/work", name),
                    code=("writer.py",), outputs=("generated/" + name + ".txt",),
                ) for name in ("a", "b")
            },
        }
        with self.fixture.session(seconds=30) as session:
            execute, results = session.command, []
            def record(command):
                result = execute(command)
                if command is reader:
                    results.append(result)
                return result
            with patch.object(session, "command", record):
                observed = session.make(
                    "all", variables=("FIRST", "UNCHANGED", "SECOND"), commands=registrations,
                )
            self.assertEqual(
                [observed.semantics["domains"][name]["value"] for name in ("FIRST", "UNCHANGED", "SECOND")],
                ["a", "a", "a,b"],
            )
            self.assertIs(results[0], results[1])
            self.assertIsNot(results[1], results[2])
            self.assertEqual([result.consumed for result in results], [
                ("generated/a.txt",), ("generated/a.txt",), ("generated/a.txt", "generated/b.txt"),
            ])
            for result in results:
                self.assertEqual(
                    sorted(item[0] for item in result.input_identities),
                    sorted((*result.consumed, "reader.py")),
                )
            records = [
                item for item in observed.semantics["dynamic_commands"]
                if item["command"]["argv"] == list(reader.argv)
            ]
            self.assertEqual(len(records), 2)
            self.assertEqual(
                {tuple(item[0] for item in record["command"]["inputs"]) for record in records},
                {("generated/a.txt", "reader.py"), ("generated/a.txt", "generated/b.txt", "reader.py")},
            )
        self.fixture.assert_clean(session)

    def test_live_include_preserves_actual_metadata_and_residual_resources(self):
        add = self.fixture.add
        add("choice.txt", "observed")
        add("data/current", "original")
        add("producer.py", (
            "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])\n"
            "choice=Path('choice.txt').read_text()\n"
            "(root/'data/new').mkdir(parents=True)\n"
            "(root/'data/new/value').write_text(choice)\n"
            "(root/'generated.mk').write_text('SELECTED := '+choice+'\\n'"
            "+'VALUE := $(shell python3 reader.py)\\nobserved: ;\\n')\n"
        ))
        add("reader.py", (
            "import json,os\nvalue=os.stat('data')\n"
            "print(json.dumps({name:getattr(value,name) for name in "
            "('st_ino','st_nlink','st_mtime_ns','st_ctime_ns')},sort_keys=True))\n"
        ))
        names = ("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS")
        recipe = " ".join(f"'$({form}{name})'" for name in names for form in ("", "origin ", "flavor "))
        add("Makefile", (
            "include generated.mk\ngenerated.mk: choice.txt\n\t@python3 producer.py .\n"
            f"all: $(SELECTED)\n\t@printf '%s\\n' '$^' {recipe}\n"
        ))
        ordinary = self.fixture.ordinary_assignment_context((), names)
        (self.root / "generated.mk").unlink()
        (self.root / "data/new/value").unlink()
        (self.root / "data/new").rmdir()
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work"), code=("producer.py",),
            sources=("choice.txt",), outputs=("generated.mk", "data/new/value"),
        )
        reports, launches, final = [], [], []
        with self.fixture.session(seconds=45) as session:
            before = session.command(reader)
            self.assertEqual(json.loads(before.stdout)["st_nlink"], 2)
            original_run, original_capsule = session.budget.run, session._sandbox_run

            def record_launch(argv, **kwargs):
                if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                    config = json.loads(Path(argv[-1]).read_bytes())
                    launches.append((config, [dict(item) for item in session.parked_capsules]))
                return original_run(argv, **kwargs)

            def record_capsule(root, **kwargs):
                result, observed = original_capsule(root, **kwargs)
                reports.append(observed)
                if kwargs["mode"] == "make":
                    info = (session.tree / "data").stat()
                    final.append({name: getattr(info, name) for name in (
                        "st_ino", "st_nlink", "st_mtime_ns", "st_ctime_ns",
                    )})
                    self.assertEqual((session.tree / "data/new/value").read_text(), "observed")
                    self.assertTrue(stat.S_ISREG((session.tree / "generated.mk").stat().st_mode))
                return result, observed

            initial = {
                "processes": session.processes_used, "syscalls": session.syscalls_used,
                "observations": session.observations_used, "created": session.files_created,
                "sandbox": session.budget.bytes.get("sandbox", 0),
            }
            with patch.object(session.budget, "run", record_launch), patch.object(
                session, "_sandbox_run", record_capsule,
            ):
                result = session.make(
                    "all", variables=(*names, "VALUE"),
                    commands={"python3 producer.py .": producer, "python3 reader.py": reader},
                )
            self.assertEqual({name: result.semantics["domains"][name] for name in names}, ordinary[1])
            self.assertEqual(result.semantics["files"][0]["prerequisites"], ordinary[0])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(json.loads(result.semantics["domains"]["VALUE"]["value"]), final[-1])
            self.assertEqual(final[-1]["st_nlink"], 3)
            self.assertEqual(sum(config["mode"] == "make" for config, _ in launches), 1)
            nested = [(config, held) for config, held in launches if held]
            self.assertTrue(nested)
            for config, held in nested:
                self.assertGreaterEqual(sum(item["live"] for item in held), 2)
                self.assertEqual(
                    config["memory_limit"] + sum(item["memory"] for item in held),
                    session.budget.limits.address_space_bytes,
                )
                self.assertEqual(
                    config["process_limit"] + sum(item["processes"] for item in held),
                    session.budget.limits.processes,
                )
                if not config["metadata_validation"]:
                    self.assertFalse(any(item["target"] == "/control" for item in config["mounts"]))
                    self.assertTrue(any(
                        item["target"] == "/repo" and not item["writable"] and not item["executable"]
                        for item in config["mounts"]
                    ))
            self.assertEqual(session.processes_used - initial["processes"], sum(row["processes"] for row in reports))
            self.assertEqual(session.syscalls_used - initial["syscalls"], sum(row["syscalls"] for row in reports))
            self.assertEqual(session.observations_used - initial["observations"], sum(row["observations"] for row in reports))
            self.assertEqual(session.files_created - initial["created"], sum(row["created_files"] for row in reports))
            self.assertEqual(
                session.budget.bytes["sandbox"] - initial["sandbox"], sum(row["written_bytes"] for row in reports),
            )
            self.assertGreaterEqual(session.live_process_peak, 3)
            self.assertLessEqual(session.live_process_peak, session.budget.limits.processes)
            self.assertLessEqual(session.memory_peak, session.budget.limits.address_space_bytes)
            self.assertFalse((session.tree / "generated.mk").exists())
            self.assertFalse((session.tree / "data/new").exists())
        self.fixture.assert_clean(session)

    def test_python_command_tracks_only_the_actual_import_closure(self):
        for name, data in (
            ("scripts/generated_data/demo/helper.py", "VALUE = 7\n"),
            ("scripts/generated_data/demo/unrelated.py", "VALUE = 99\n"),
            ("scripts/generated_data/demo/tool.py",
             "from scripts.generated_data.demo.helper import VALUE as RESULT\n"),
        ):
            self.fixture.add(name, data)
        with self.fixture.session() as session:
            closure = python_code_closure(
                session,
                "from scripts.generated_data.demo.helper import VALUE\nprint(VALUE)\n",
                ("scripts/generated_data/demo/tool.py",),
            )
            self.assertIn("scripts/generated_data/demo/helper.py", closure)
            self.assertIn("scripts/generated_data/demo/tool.py", closure)
            self.assertNotIn("scripts/generated_data/demo/unrelated.py", closure)
            output = session.command(python_command(
                session,
                "from scripts.generated_data.demo.tool import RESULT\nprint(RESULT)",
                code=("scripts/generated_data/demo/tool.py",),
            ))
            self.assertEqual(output.stdout, b"7\n")
        self.fixture.assert_clean(session)

    def test_generated_dependency_command_publishes_all_three_depfiles_and_restarts_make(self):
        cases = self.add_generated_dependency_fixture()
        lines = [f"-include {case['depfile']}" for case in cases]
        lines.extend((".PHONY: all force", "ifeq ($(MAKE_RESTARTS),)"))
        for case in cases:
            lines.append(f"{case['depfile']}: force\n\t{case['command']}")
        lines.append("else")
        for case in cases:
            lines.append(f"{case['depfile']}: ;")
        lines.append("endif")
        for case in cases:
            lines.append(f"{case['make_target']}: ;")
        lines.append("all: " + " ".join(case["make_target"] for case in cases))
        lines.append("force: ;")
        self.fixture.add("Makefile", "\n".join(lines) + "\n")
        ordinary = {}
        for case in cases:
            completed = subprocess.run(
                ["/bin/sh", "-c", case["command"]],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.fixture.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ordinary[case["depfile"]] = (self.root / case["depfile"]).read_bytes().replace(
                os.fsencode(str(self.root)), b"/repo",
            )
        commands = {}
        for case in cases:
            with self.fixture.session(seconds=60) as session:
                registration = generated_dependency_command(
                    session,
                    case["module"],
                    option_values=case["options"],
                    make_target=case["make_target"],
                    depfile=case["depfile"],
                )
                commands[case["command"]] = registration
            self.fixture.assert_clean(session)
        with self.fixture.session(seconds=60) as session:
            observed = session.make("all", variables=("MAKE_RESTARTS",), commands=commands)
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(
                {
                    output[0]
                    for record in observed.semantics["dynamic_commands"]
                    for output in record["generated_outputs"]
                },
                {case["depfile"] for case in cases},
            )
            self.assertEqual(
                {
                    output[0]: output[2]
                    for record in observed.semantics["dynamic_commands"]
                    for output in record["generated_outputs"]
                },
                {
                    path: hashlib.sha256(data).hexdigest()
                    for path, data in ordinary.items()
                },
            )
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 3)
        self.fixture.assert_clean(session)

    def test_generated_dependency_command_reorders_named_options_and_rejects_key_drift(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        outputs = []
        for command in (cases["autoplaystrategies"]["command"], cases["autoplaystrategies"]["reordered_command"]):
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.fixture.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append((self.root / cases["autoplaystrategies"]["depfile"]).read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        with self.fixture.session(seconds=60) as session:
            canonical = generated_dependency_command(
                session,
                cases["autoplaystrategies"]["module"],
                option_values=cases["autoplaystrategies"]["options"],
                make_target=cases["autoplaystrategies"]["make_target"],
                depfile=cases["autoplaystrategies"]["depfile"],
            )
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=60) as session:
            reordered = generated_dependency_command(
                session,
                cases["autoplaystrategies"]["module"],
                option_values=cases["autoplaystrategies"]["reordered_options"],
                make_target=cases["autoplaystrategies"]["make_target"],
                depfile=cases["autoplaystrategies"]["depfile"],
            )
            self.assertEqual(canonical.sources, reordered.sources)
            self.assertEqual(canonical.directories, reordered.directories)
            self.assertEqual(json.loads(canonical.argv[7]), json.loads(reordered.argv[7]))
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=60) as session:
            with self.assertRaisesRegex(MakeProbeError, "missing --objectives-source"):
                generated_dependency_command(
                    session,
                    cases["autoplaystrategies"]["module"],
                    option_values={
                        "--source": "testdata/strategies/el_strategies.json",
                        "--bundle-source": "testdata/bundles/el_bundle.json",
                    },
                    make_target=cases["autoplaystrategies"]["make_target"],
                    depfile=cases["autoplaystrategies"]["depfile"],
                )
            with self.assertRaisesRegex(MakeProbeError, "extra --extra"):
                generated_dependency_command(
                    session,
                    cases["autoplaystrategies"]["module"],
                    option_values={
                        "--source": "testdata/strategies/el_strategies.json",
                        "--objectives-source": "testdata/objectives/el_objectives.json",
                        "--bundle-source": "testdata/bundles/el_bundle.json",
                        "--extra": "ignored",
                    },
                    make_target=cases["autoplaystrategies"]["make_target"],
                    depfile=cases["autoplaystrategies"]["depfile"],
                )
        self.fixture.assert_clean(session)

    def test_generated_dependency_command_keeps_nonselected_directory_files_ungranted(self):
        cases = {
            case["name"]: case for case in self.add_compact_generated_dependency_fixture()
        }
        for path in (
            "data/objectives/ignored.txt",
            "data/bundles/ignored.txt",
            "assets/tmx/ignored.txt",
            "graphics/map/layout/ignored.txt",
        ):
            self.fixture.add(path, "ignored\n")
        with self.fixture.session(seconds=60) as session:
            command = generated_dependency_command(
                session,
                cases["chapterobjectives"]["module"],
                option_values=cases["chapterobjectives"]["options"],
                make_target=cases["chapterobjectives"]["make_target"],
                depfile=cases["chapterobjectives"]["depfile"],
            )
            for path in (
                "data/objectives/ignored.txt",
                "data/bundles/ignored.txt",
                "assets/tmx/ignored.txt",
                "graphics/map/layout/ignored.txt",
            ):
                self.assertNotIn(path, command.sources)
            result = session.command(command)
            tracked = {row[0] for row in result.input_identities}
            self.assertIn("data/objectives/ch1_objectives.json", tracked)
            self.assertIn("data/bundles/ch1_bundle.json", tracked)
            self.assertIn("assets/tmx/Map.tmx", tracked)
            self.assertIn("graphics/map/layout/Map.json", tracked)
            self.assertNotIn("data/objectives/ignored.txt", tracked)
            self.assertNotIn("data/bundles/ignored.txt", tracked)
            self.assertNotIn("assets/tmx/ignored.txt", tracked)
            self.assertNotIn("graphics/map/layout/ignored.txt", tracked)
        self.fixture.assert_clean(session)

    def test_generated_dependency_repeated_output_registration_executes_and_publishes_twice(self):
        case = next(
            item for item in self.add_compact_generated_dependency_fixture()
            if item["name"] == "eventlists"
        )
        primary = case["command"]
        alias = primary + "; printf ''"
        self.fixture.add("Makefile", (
            "FIRST := $(shell " + primary + ")\n"
            "SECOND := $(shell " + alias + ")\n"
            "all: ;\n"
        ))
        with self.fixture.session(seconds=60) as session:
            command = generated_dependency_command(
                session,
                case["module"],
                option_values=case["options"],
                make_target=case["make_target"],
                depfile=case["depfile"],
            )
            run = session._sandbox_run
            executed = []

            def count(root, **kwargs):
                if kwargs["mode"] == "command" and tuple(kwargs["argv"][-7:]) == command.argv[-7:]:
                    executed.append(tuple(kwargs["argv"]))
                return run(root, **kwargs)

            with patch.object(session, "_sandbox_run", count):
                observed = session.make("all", commands={primary: command, alias: command})
            self.assertEqual(len(executed), 2)
            self.assertEqual(len(observed.events), 2)
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
        self.fixture.assert_clean(session)

    def test_generated_dependency_fusion_uses_one_fewer_capsule_for_all_three_modules(self):
        cases = {case["name"]: dict(case) for case in self.add_compact_generated_dependency_fixture()}
        expected_counts = {
            "chapterobjectives": (5, 4),
            "autoplaystrategies": (3, 2),
            "eventlists": (3, 2),
        }
        for name, case in cases.items():
            with self.subTest(module=name):
                with self.fixture.session(seconds=60) as session:
                    def produce_legacy():
                        command = self.legacy_generated_dependency_command(session, case)
                        return command, session.command(command)
                    (legacy_command, legacy_result), legacy_calls = self.capture_command_capsules(
                        session, produce_legacy,
                    )
                    self.assertEqual(len(legacy_result.generated), 1)
                    self.assertEqual(sum(mode == "command" for mode, _ in legacy_calls), expected_counts[name][0])
                self.fixture.assert_clean(session)
                with self.fixture.session(seconds=60) as session:
                    def produce():
                        command = generated_dependency_command(
                            session,
                            case["module"],
                            option_values=case["options"],
                            make_target=case["make_target"],
                            depfile=case["depfile"],
                        )
                        return command, session.command(command)
                    (command, result), calls = self.capture_command_capsules(session, produce)
                    self.assertEqual(len(result.generated), 1)
                    self.assertEqual(sum(mode == "command" for mode, _ in calls), expected_counts[name][1])
                    self.assertEqual(result.generated[0].data, legacy_result.generated[0].data)
                    self.assertEqual(command.sources, legacy_command.sources)
                    self.assertEqual(result.input_identities, legacy_result.input_identities)
                self.fixture.assert_clean(session)

    def test_generated_dependency_factory_follows_current_base_current_code_and_inputs(self):
        case = {
            "module": "scripts.generated_data.eventlists.deps",
            "options": {"--strategy-source": "data/strategy.json", "--bundle-source": "data/bundles/state.json"},
            "make_target": "build/generated/validated",
            "depfile": "build/generated/inputs.mk",
        }
        selector = (
            "import glob,os\n"
            "def source_paths(source):\n"
            " return sorted(glob.glob(source+'/*.json')) if os.path.isdir(source) else [source]\n"
        )
        for name in ("autoplaystrategies", "chapterbundle"):
            self.fixture.add("scripts/generated_data/" + name + "/schema.py", selector)
        self.fixture.add("data/strategy.json", '{"value":"strategy"}\n')
        bundle_path = "data/bundles/state.json"
        self.fixture.add(bundle_path, '{"value":"base"}\n')
        module_path = "scripts/generated_data/eventlists/deps.py"
        original_code = (
            "import json,os\n"
            "from ..autoplaystrategies.schema import source_paths as strategies\n"
            "from ..chapterbundle.schema import source_paths as bundles\n"
            "def collect_input_paths(strategy, bundle):\n"
            " paths=strategies(strategy)+bundles(bundle)\n"
            " for path in paths:\n"
            "  with open(path) as stream: json.load(stream)['value']\n"
            " return tuple(sorted(os.path.realpath(path) for path in paths))\n"
            "def render_depfile(target, inputs):\n"
            " return target+': '+' '.join(inputs)+'\\n'\n"
        )
        def renderer_code(version):
            return original_code + (
                "\n_original_renderer = render_depfile\n"
                "def render_depfile(target, inputs):\n"
                f"    return _original_renderer(target, inputs) + '# {version} renderer\\n'\n"
            )
        self.fixture.add(module_path, renderer_code("base"))
        budget = foundation.ProbeBudget()
        base_loader = self.capture_complete_loader(budget)
        self.fixture.add(bundle_path, '{"value":"current"}\n')
        self.fixture.add(module_path, renderer_code("current"))
        current_loader = self.capture_complete_loader(budget)

        def produce(session):
            command = generated_dependency_command(
                session, case["module"], option_values=case["options"],
                make_target=case["make_target"], depfile=case["depfile"],
            )
            result = session.command(command)
            self.assertEqual(len(result.generated), 1)
            return command.sources, result.input_identities, result.generated[0].data

        try:
            with foundation.ProbeSession(
                current_loader, scratch_root=self.fixture.scratch, budget=budget,
            ) as session:
                first = produce(session)
                with session.select_view(base_loader):
                    base = produce(session)
                restored = produce(session)
                self.assertEqual(first, restored)
                self.assertNotEqual(first, base)
                expected = (bundle_path, "data/strategy.json")
                self.assertEqual(first[0], expected)
                self.assertEqual(base[0], expected)
                current_identities = {row[0]: row[1:] for row in first[1]}
                base_identities = {row[0]: row[1:] for row in base[1]}
                self.assertNotEqual(
                    current_identities[bundle_path],
                    base_identities[bundle_path],
                )
                self.assertNotEqual(current_identities[module_path], base_identities[module_path])
                self.assertIn(b"# current renderer\n", first[2])
                self.assertIn(b"# base renderer\n", base[2])
                for path in expected:
                    self.assertIn(("/repo/" + path).encode(), first[2])
                    self.assertIn(("/repo/" + path).encode(), base[2])
            self.fixture.assert_clean(session)
        finally:
            budget.close()

    def test_generated_dependency_factory_support_modules_follow_current_base_current_inputs(self):
        cases = {
            case["name"]: case for case in self.add_compact_generated_dependency_fixture()
            if case["name"] in {"chapterobjectives", "autoplaystrategies"}
        }
        bundle_path = "data/bundles/ch1_bundle.json"
        dependency_module_path = "scripts/generated_data/units/schema.py"
        for name, case in cases.items():
            with self.subTest(module=name):
                if name == "chapterobjectives":
                    case = dict(case)
                    case["options"] = {
                        "--source": "data/objectives/ch1_objectives.json",
                        "--bundle-source": "data/bundles/ch1_bundle.json",
                    }
                module_path = case["module"].replace(".", "/") + ".py"
                original_bundle = json.loads((self.root / bundle_path).read_text(encoding="utf-8"))
                original_module = (self.root / module_path).read_text(encoding="utf-8")
                original_dependency = (self.root / dependency_module_path).read_text(encoding="utf-8")
                self.fixture.add(bundle_path, json.dumps(original_bundle, indent=4) + "\n")
                self.fixture.add(
                    module_path,
                    original_module
                    + "\n_original_renderer = render_depfile\n"
                    + "def render_depfile(target, inputs):\n"
                    + "    return _original_renderer(target, inputs) + '# base renderer\\n'\n",
                )
                self.fixture.add(dependency_module_path, original_dependency + "\n# base support\n")
                budget = foundation.ProbeBudget()
                base_loader = self.capture_complete_loader(budget)
                self.fixture.add(bundle_path, json.dumps(original_bundle, indent=2) + "\n")
                self.fixture.add(
                    module_path,
                    original_module
                    + "\n_original_renderer = render_depfile\n"
                    + "def render_depfile(target, inputs):\n"
                    + "    return _original_renderer(target, inputs) + '# current renderer\\n'\n",
                )
                self.fixture.add(dependency_module_path, original_dependency + "\n# current support\n")
                current_loader = self.capture_complete_loader(budget)
                restored_budget = foundation.ProbeBudget()
                restored_loader = self.capture_complete_loader(restored_budget)

                def produce(session):
                    command = generated_dependency_command(
                        session, case["module"], option_values=case["options"],
                        make_target=case["make_target"], depfile=case["depfile"],
                    )
                    result = session.command(command)
                    self.assertEqual(len(result.generated), 1)
                    return command, {row[0]: row[1:] for row in result.input_identities}, result.generated[0].data

                try:
                    with foundation.ProbeSession(
                        current_loader, scratch_root=self.fixture.scratch, budget=budget,
                    ) as session:
                        first = produce(session)
                        with session.select_view(base_loader):
                            base = produce(session)
                        self.assertNotEqual(first, base)
                        self.assertIn(b"# current renderer\n", first[2])
                        self.assertIn(b"# base renderer\n", base[2])
                        for path in (
                            bundle_path,
                            module_path,
                            dependency_module_path,
                        ):
                            self.assertIn(path, first[1])
                            self.assertIn(path, base[1])
                            self.assertNotEqual(first[1][path], base[1][path])
                    self.fixture.assert_clean(session)
                    with foundation.ProbeSession(
                        restored_loader, scratch_root=self.fixture.scratch, budget=restored_budget,
                    ) as session:
                        restored = produce(session)
                        self.assertEqual(first, restored)
                    self.fixture.assert_clean(session)
                finally:
                    budget.close()
                    restored_budget.close()

    def test_generated_dependency_factory_rejects_malformed_sources_without_publication(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        for name, path in (
            ("chapterobjectives", "testdata/objectives/el_objectives.json"),
            ("autoplaystrategies", "testdata/strategies/el_strategies.json"),
            ("eventlists", "testdata/bundles/el_bundle.json"),
        ):
            with self.subTest(module=name):
                case = cases[name]
                original = (self.root / path).read_bytes()
                self.fixture.add(path, b'{"malformed":')
                with self.fixture.session() as session:
                    with self.assertRaises(MakeProbeError):
                        session.command(generated_dependency_command(
                            session, case["module"], option_values=case["options"],
                            make_target=case["make_target"], depfile=case["depfile"],
                        ))
                    self.assertFalse(session.published_sources)
                    self.assertFalse((self.root / case["depfile"]).exists())
                self.fixture.assert_clean(session)
                self.fixture.add(path, original)

    def test_generated_dependency_command_rejects_collected_input_drift_before_publication(self):
        self.add_compact_generated_dependency_fixture()
        self.fixture.add("data/extra.json", '{"value":"extra"}\n')
        case = {
            "module": "scripts.generated_data.eventlists.deps",
            "options": {"--strategy-source": "data/strategies/ch1_strategies.json", "--bundle-source": "data/bundles/ch1_bundle.json"},
            "make_target": "build/generated/validated",
            "depfile": "build/generated/inputs.mk",
        }
        variants = {
            "omitted": (
                " return (os.path.realpath(strategy),)\n",
                "dependency collection differs from admitted selector/support evidence",
            ),
            "unexpected": (
                " return tuple(sorted({os.path.realpath(strategy), os.path.realpath(bundle), os.path.realpath('data/extra.json')}))\n",
                "undeclared source metadata: /repo/data/extra.json|dependency collection differs from admitted selector/support evidence",
            ),
            "duplicate": (
                " return (os.path.realpath(strategy), os.path.realpath(bundle), os.path.realpath(bundle))\n",
                "dependency collection must be canonical, unique and sorted",
            ),
            "escape": (
                " return (os.path.realpath(strategy), '/repo/../escape.json')\n",
                "dependency source must be repository-relative without parent components",
            ),
        }
        for name, (return_line, error) in variants.items():
            with self.subTest(variant=name):
                self.fixture.add(
                    "scripts/generated_data/eventlists/deps.py",
                    "import os\n"
                    "def collect_input_paths(strategy, bundle):\n"
                    + return_line
                    + "def render_depfile(target, inputs):\n"
                    + " return target+': '+' '.join(inputs)+'\\n'\n",
                )
                with self.fixture.session(seconds=60) as session:
                    command = generated_dependency_command(
                        session, case["module"], option_values=case["options"],
                        make_target=case["make_target"], depfile=case["depfile"],
                    )
                    with self.assertRaisesRegex(MakeProbeError, error):
                        session.command(command)
                    self.assertFalse(session.published_sources)
                    self.assertFalse((self.root / case["depfile"]).exists())
                self.fixture.assert_clean(session)

    def test_generated_dependency_command_rejects_missing_or_conflicting_outputs(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        with self.fixture.session(seconds=60) as session:
            with self.assertRaisesRegex(MakeProbeError, "generated output conflicts with immutable source"):
                session.command(generated_dependency_command(
                    session,
                    cases["eventlists"]["module"],
                    option_values=cases["eventlists"]["options"],
                    make_target=cases["eventlists"]["make_target"],
                    depfile="testdata/deps/deps_units.json",
                ))
        self.fixture.assert_clean(session)

        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        (self.root / "assets/manifest.json").unlink()
        del self.fixture.entries["assets/manifest.json"]
        with self.fixture.session(seconds=60) as session:
            with self.assertRaisesRegex(
                MakeProbeError,
                r"(missing declared owner input 'assets/manifest\.json'|source declaration resolves no regular inputs: assets/manifest\.json)",
            ):
                generated_dependency_command(
                    session,
                    cases["chapterobjectives"]["module"],
                    option_values=cases["chapterobjectives"]["options"],
                    make_target=cases["chapterobjectives"]["make_target"],
                    depfile=cases["chapterobjectives"]["depfile"],
                )
        self.fixture.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
