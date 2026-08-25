"""Static, no-execution-required proof for issue #2 slice 2's global
save-compatibility gate (src/savemenu.c's StartSaveMenu()) and its dedicated
compatibility proc (src/save_compat_menu.c).

Two properties are proven purely by scanning the real, shipped .c/.h files
(no ARM/GBA execution environment is required for these tests -- runtime
behavior is separately proven by tools/gba-playtest scenarios):

1. StartSaveMenu() is the *only* directly-coupled entry point into the
   normal save menu (ProcScr_SaveMenu) reachable from a proc script, and it
   unconditionally classifies SRAM via ClassifySramSaveCompat() and diverts
   every non-CURRENT state to StartSaveCompatMenu() before ever calling
   Proc_StartBlocking(ProcScr_SaveMenu, ...). No other call site in src/
   bypasses this gate to reach ProcScr_SaveMenu directly.

2. The compatibility proc itself (src/save_compat_menu.c) never references
   any slot/block/current-struct accessor -- IsSaveValid, ReadSaveBlockInfo,
   ReadGameSave, ReadGameSavePlaySt, InvalidateGameSave, or the
   struct SaveBlockInfo type -- anywhere in its compiled code (comments are
   stripped first, since the file's own documentation prose legitimately
   names these functions to explain what must never be called).
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SAVEMENU_C = ROOT / "src" / "savemenu.c"
SAVE_COMPAT_MENU_C = ROOT / "src" / "save_compat_menu.c"
SCENARIOS_DIR = ROOT / "tools" / "gba-playtest" / "scenarios"
FINGERPRINTS_DIR = ROOT / "tools" / "gba-playtest" / "fingerprints"
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump")
INCLUDE_FLAGS = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]

_FORBIDDEN_IDENTIFIERS = (
    "IsSaveValid",
    "ReadSaveBlockInfo",
    "ReadGameSave",
    "ReadGameSavePlaySt",
    "ReadGameSaveCoreGfx",
    "InvalidateGameSave",
)
_FORBIDDEN_SAVE_BLOCK_TYPE = "SaveBlockInfo"
_FORBIDDEN_XMAP_TREE_TOKENS = (
    "SaveBlocks",
    "ExtraMapSaveHead",
    "xmap",
    "xmap_magic",
)
_SAVE_INTERNAL_APIS = (
    "WriteSuspendSave",
    "ReadSuspendSave",
    "WriteGameSave",
    "ReadGameSave",
)
_SAVE_API_COUNTS = {
    "WriteSuspendSave": {
        "src/bm.c": 1, "src/bmarena.c": 1, "src/bmbattle.c": 1,
        "src/bmdebug.c": 1, "src/bmtrap.c": 1, "src/cp_decide.c": 1,
        "src/playerphase.c": 2, "src/uiarena.c": 1,
    },
    "ReadSuspendSave": {"src/bmdebug.c": 2, "src/savemenu.c": 1},
    "WriteGameSave": {
        "src/bmdebug.c": 3, "src/bonusclaim.c": 1, "src/savemenu.c": 1,
    },
    "ReadGameSave": {"src/savemenu.c": 5, "src/sio_term.c": 1},
}
def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text
def _compile_arm(work: Path, source: Path, name: str, defines=(), extra_includes=()) -> Path:
    obj = work / name
    completed = subprocess.run(
        [
            ARM_CC,
            "-mcpu=arm7tdmi",
            "-mthumb",
            "-mthumb-interwork",
            "-mabi=aapcs",
            "-std=gnu89",
            "-ffreestanding",
            "-fno-builtin",
            "-w",
            *INCLUDE_FLAGS,
            *(value for path in extra_includes for value in ("-I", str(path))),
            *defines,
            "-c",
            str(source),
            "-o",
            str(obj),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0: raise AssertionError(completed.stdout + completed.stderr)
    return obj

def _generate_message_ids(work: Path) -> Path:
    generated = work / "generated"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.localization.cli",
            "generate",
            "--out-dir",
            str(generated),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0: raise AssertionError(completed.stdout + completed.stderr)
    return generated
def _undefined_symbols(obj: Path) -> set[str]:
    completed = subprocess.run(
        [ARM_NM, "--undefined-only", str(obj)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return {line.split()[-1] for line in completed.stdout.splitlines() if line.split()}
def _gcc_tree(work: Path, source: Path, name: str, dump: str, defines=(), extra_includes=()) -> str:
    output = work / name
    tree = work / (name + "." + dump)
    completed = subprocess.run(
        [
            ARM_CC,
            "-mcpu=arm7tdmi",
            "-mthumb",
            "-mthumb-interwork",
            "-mabi=aapcs",
            "-std=gnu89",
            "-ffreestanding",
            "-fno-builtin",
            "-w",
            *INCLUDE_FLAGS,
            *(value for path in extra_includes for value in ("-I", str(path))),
            *defines,
            "-fdump-tree-" + dump + "=" + str(tree),
            "-c",
            str(source),
            "-o",
            str(output),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return tree.read_text(encoding="utf-8")
def _gcc_original_tree(work, source, name, defines=(), extra_includes=()) -> str:
    return _gcc_tree(work, source, name, "original", defines, extra_includes)
def _gcc_cfg_tree(work, source, name, defines=(), extra_includes=()) -> str:
    return _gcc_tree(work, source, name, "cfg", defines, extra_includes)
def _function_cfg(tree: str, name: str) -> str:
    marker = ";; Function " + name + " ("
    start = tree.find(marker)
    if start < 0: raise AssertionError("%s CFG function not found" % name)
    end = tree.find("\n;; Function ", start + len(marker))
    return tree[start:] if end < 0 else tree[start:end]


def _enum_value(work: Path, defines=(), extra_includes=(), expression="SAVE_COMPAT_CURRENT", declaration="") -> int:
    source = work / "save_compat_current_probe.c"
    source.write_text('#include "global.h"\n#include "save_format.h"\n' + declaration + '\nint Probe(void)\n{\n    return ' + expression + ';\n}\n', encoding="utf-8")
    tree = _gcc_original_tree(work, source, "save_compat_current_probe.o", defines, extra_includes)
    match = re.search(r"return (\d+);", tree)
    if match is None: raise AssertionError("SaveCompatState enum representation not found")
    return int(match.group(1))


def _gate_fixture(work: Path, name: str, body: str, declaration="") -> Path:
    source = work / name
    source.write_text('#include "global.h"\n#include "savemenu.h"\n#include "save_format.h"\n#include "save_compat_menu.h"\nextern struct ProcCmd ProcScr_SaveMenu[];\n' + declaration + '\nvoid StartSaveMenu(void *parent)\n{\n    ' + body + '\n}\n', encoding="utf-8")
    return source


def _has_start_save_gate_cfg(cfg: str, current_value: int) -> bool:
    successors = {
        match.group(1): set(match.group(2).split())
        for match in re.finditer(r"^;; (\d+) succs \{([^}]*)\}", cfg, re.MULTILINE)
    }
    block_matches = list(
        re.finditer(r"^\s*<bb (\d+)> :\n(.*?)(?=^\s*<bb |\Z)", cfg, re.MULTILINE | re.DOTALL)
    )
    blocks = {match.group(1): match.group(2) for match in block_matches}
    compat_blocks = {
        number for number, body in blocks.items()
        if "StartSaveCompatMenu" in body
    }
    normal_blocks = {
        number for number, body in blocks.items()
        if "Proc_StartBlocking" in body and "ProcScr_SaveMenu" in body
    }
    return_blocks = {
        number for number, body in blocks.items() if re.search(r"\breturn;", body)
    }
    entry = min(successors, key=int)
    predecessors = {number: set() for number in successors}
    for number, targets in successors.items():
        for target in targets:
            predecessors.setdefault(target, set()).add(number)
    dominators = {number: {entry} if number == entry else set(successors) for number in successors}
    changed = True
    while changed:
        changed = False
        for number in successors:
            if number == entry or not predecessors.get(number):
                continue
            value = {number}.union(set.intersection(*(dominators[parent] for parent in predecessors[number])))
            if value != dominators[number]:
                dominators[number], changed = value, True

    def reaches(start: str, targets: set[str], blocked=()) -> bool:
        pending = [start]
        seen = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in blocked:
                continue
            if current in targets:
                return True
            pending.extend(successors.get(current, ()))
        return False

    for branch, branch_successors in successors.items():
        if len(branch_successors) != 2:
            continue
        branch_body = blocks.get(branch, "")
        condition = re.search(r"if \((\w+) != " + str(current_value) + r"\)", branch_body)
        if condition is None:
            continue
        ordered_successors = re.findall(r"goto <bb (\d+)>", branch_body)
        if len(ordered_successors) < 2:
            continue
        value = condition.group(1)
        classifier_blocks = {
            number for number, body in blocks.items()
            if re.search(r"\b" + re.escape(value) + r" = ClassifySramSaveCompat", body)
        }
        if not classifier_blocks or not any(reaches(classifier, {branch}) for classifier in classifier_blocks):
            continue
        for compat in compat_blocks & branch_successors:
            normal = next(iter(branch_successors - {compat}), None)
            if normal not in normal_blocks or ordered_successors[:2] != [compat, normal]:
                continue
            if branch not in dominators.get(normal, set()):
                continue
            if reaches(compat, normal_blocks):
                continue
            if reaches(compat, return_blocks):
                return True
    return False


def _production_sources() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(
        ROOT / path
        for path in completed.stdout.splitlines()
        if path.startswith("src/") and path.endswith(".c")
    )


def _included_header_text(source: Path) -> str:
    pending = [source]
    seen = set()
    text = []
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        content = path.read_text(encoding="utf-8", errors="replace")
        text.append(content)
        for include in re.findall(r'^\s*#include\s+"([^"]+)"', content, re.MULTILINE):
            for parent in (path.parent, ROOT / "include"):
                candidate = parent / include
                if candidate.is_file():
                    pending.append(candidate)
                    break
    return re.sub(r"\\\n", "", "\n".join(text))


def _candidate_sources(sources: tuple[Path, ...], symbols: tuple[str, ...]) -> tuple[Path, ...]:
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, symbols)) + r")\b")
    return tuple(
        source
        for source in sources
        if pattern.search(_strip_comments(source.read_text(encoding="utf-8", errors="replace")))
        or re.search(
            r"^\s*#define\b.*" + pattern.pattern,
            _included_header_text(source),
            re.MULTILINE,
        )
    )


def _object_relocation_edges(obj: Path, symbols: tuple[str, ...]) -> list[tuple[str, str]]:
    completed = subprocess.run(
        [ARM_OBJDUMP, "-dr", str(obj)],
        capture_output=True,
        text=True,
        check=True,
    )
    edges = []
    function = None
    symbol_pattern = re.compile(
        r"R_ARM_[A-Z0-9_]+\s+(" + "|".join(map(re.escape, symbols)) + r")\b"
    )
    for line in completed.stdout.splitlines():
        match = re.match(r"^[0-9a-fA-F]+ <([^>]+)>:", line)
        if match:
            function = match.group(1)
        match = symbol_pattern.search(line)
        if match and function is not None:
            edges.append((function, match.group(1)))
    return edges


def _compiled_census_edges(
    work: Path,
    sources: tuple[Path, ...],
    symbols: tuple[str, ...],
    defines=(),
    extra_includes=(),
) -> list[tuple[str, str, str]]:
    edges = []
    for index, source in enumerate(_candidate_sources(sources, symbols)):
        object_name = "census-%03d.o" % index
        obj = _compile_arm(work, source, object_name, defines, extra_includes)
        relative = source.relative_to(ROOT).as_posix()
        for function, symbol in _object_relocation_edges(obj, symbols):
            edges.append((relative, function, symbol))
    return edges


def _boundary_modes(work: Path):
    yield "default", (), ()
    yield "modern", ("-DMODERN=1",), (_generate_message_ids(work),)
def _assert_no_save_block_or_xmap_access(test: unittest.TestCase, tree: str):
    test.assertNotRegex(
        tree,
        r"\b(?:struct )?" + _FORBIDDEN_SAVE_BLOCK_TYPE + r"\b",
        "compatibility proc must not declare or dereference SaveBlockInfo",
    )
    for token in _FORBIDDEN_XMAP_TREE_TOKENS:
        test.assertNotRegex(
            tree,
            r"\b" + re.escape(token) + r"\b",
            "compatibility proc must not access XMAP through %s" % token,
        )


def _xmap_access_tree(work: Path, defines=(), extra_includes=(), body=None) -> str:
    source = work / "xmap_access_negative.c"
    if body is None:
        body = "return blocks->xmap.xmap_magic == XMAP_MAGIC;"
    source.write_text(
        '#include "global.h"\n'
        '#include "bmsave.h"\n'
        'u32 SaveCompatXmapNegative(const struct SaveBlocks *blocks)\n'
        '{\n'
        '    ' + body + '\n'
        '}\n',
        encoding="utf-8",
    )
    return _gcc_original_tree(
        work,
        source,
        "xmap_access_negative.o",
        defines,
        extra_includes,
    )


def _has_xmap_member_use(tree: str) -> bool:
    return bool(
        re.search(
            r"\bblocks->xmap\.xmap_magic\s*==\s*\d+\b",
            tree,
        )
    )
class SaveCompatDialogBackSemanticTests(unittest.TestCase):
    def test_every_noncurrent_back_fixture_preserves_sram(self):
        scenario = json.loads(
            (SCENARIOS_DIR / "savecompat-dialog-back.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [checkpoint["name"] for checkpoint in scenario["checkpoints"]],
            ["dialog-shown", "after-dismiss", "back-returned"],
        )
        self.assertTrue(
            all(checkpoint["sram_hash"] for checkpoint in scenario["checkpoints"])
        )

        fingerprints = sorted(
            FINGERPRINTS_DIR.glob("savecompat-dialog-back-*.json")
        )
        self.assertTrue(fingerprints)
        for path in fingerprints:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["scenario"], "savecompat-dialog-back")
            hashes = [checkpoint["sram_hash"] for checkpoint in data["checkpoints"]]
            self.assertEqual(
                len(set(hashes)),
                1,
                "%s must keep SRAM unchanged through default Back" % path.name,
            )


@unittest.skipIf(ARM_CC is None or ARM_NM is None or ARM_OBJDUMP is None, "no arm-none-eabi compiler/binutils")
class SaveCompatCompiledBoundaryTests(unittest.TestCase):
    def test_compat_proc_has_no_forbidden_save_api_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for mode, defines, includes in _boundary_modes(work):
                with self.subTest(mode=mode):
                    refs = _undefined_symbols(
                        _compile_arm(
                            work,
                            SAVE_COMPAT_MENU_C,
                            mode + "-save_compat_menu.o",
                            defines,
                            includes,
                        )
                    )
                    self.assertFalse(
                        refs & set(_FORBIDDEN_IDENTIFIERS),
                        "compatibility proc must classify globally before "
                        "any slot/block API: %r"
                        % sorted(refs & set(_FORBIDDEN_IDENTIFIERS)),
                    )

    def test_compat_proc_parsed_tree_has_no_save_block_type_or_field_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for mode, defines, includes in _boundary_modes(work):
                with self.subTest(mode=mode):
                    tree = _gcc_original_tree(
                        work,
                        SAVE_COMPAT_MENU_C,
                        mode + "-save_compat_menu.ast",
                        defines,
                        includes,
                    )
                    _assert_no_save_block_or_xmap_access(self, tree)

                    negative = _xmap_access_tree(work, defines, includes)
                    self.assertTrue(
                        _has_xmap_member_use(negative),
                        "parsed negative control must expose the nested "
                        "XMAP member access",
                    )
                    removed = _xmap_access_tree(
                        work,
                        defines,
                        includes,
                        "return 0;",
                    )
                    self.assertFalse(
                        _has_xmap_member_use(removed),
                        "removing the XMAP member-use body must fail the "
                        "negative control",
                    )

    def test_startsavemenu_cfg_diverts_noncurrent_before_normal_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for mode, defines, includes in _boundary_modes(work):
                with self.subTest(mode=mode):
                    current = _enum_value(work, defines, includes)
                    cfg = _function_cfg(
                        _gcc_cfg_tree(
                            work,
                            SAVEMENU_C,
                            mode + "-savemenu.o",
                            defines,
                            includes,
                        ),
                        "StartSaveMenu",
                    )
                    self.assertTrue(
                        _has_start_save_gate_cfg(cfg, current),
                        "StartSaveMenu CFG must branch non-CURRENT to the "
                        "compatibility return path before normal menu start",
                    )

                    mutations = {
                        "unrelated": 'int other = 0;\n    enum SaveCompatState state = ClassifySramSaveCompat();\n    if (other != SAVE_COMPAT_CURRENT)\n        StartSaveCompatMenu(parent, state);\n    else\n        Proc_StartBlocking(ProcScr_SaveMenu, parent);',
                        "early": 'Proc_StartBlocking(ProcScr_SaveMenu, parent);\n    enum SaveCompatState state = ClassifySramSaveCompat();\n    if (state != SAVE_COMPAT_CURRENT)\n        StartSaveCompatMenu(parent, state);',
                        "reversed": 'enum SaveCompatState state = ClassifySramSaveCompat();\n    if (state != SAVE_COMPAT_CURRENT)\n        Proc_StartBlocking(ProcScr_SaveMenu, parent);\n    else\n        StartSaveCompatMenu(parent, state);',
                    }
                    for suffix, body in mutations.items():
                        mutation = _gate_fixture(
                            work,
                            mode + "-" + suffix + "_save_menu.c",
                            body,
                        )
                        self.assertFalse(
                            _has_start_save_gate_cfg(
                                _function_cfg(
                                    _gcc_cfg_tree(
                                        work,
                                        mutation,
                                        mode + "-" + suffix + "_save_menu.o",
                                        defines,
                                        includes,
                                    ),
                                    "StartSaveMenu",
                                ),
                                current,
                            ),
                            suffix + " gate mutation must fail the CFG check",
                        )

                    declaration = "enum { TEST_CURRENT = 9 };"
                    reordered = _gate_fixture(
                        work,
                        mode + "-reordered_save_menu.c",
                        'enum SaveCompatState state = ClassifySramSaveCompat();\n    if (state != TEST_CURRENT)\n        StartSaveCompatMenu(parent, state);\n    else\n        Proc_StartBlocking(ProcScr_SaveMenu, parent);',
                        declaration,
                    )
                    reordered_cfg = _function_cfg(
                        _gcc_cfg_tree(work, reordered, mode + "-reordered_save_menu.o", defines, includes),
                        "StartSaveMenu",
                    )
                    reordered_current = _enum_value(
                        work, defines, includes, "TEST_CURRENT", declaration
                    )
                    self.assertTrue(_has_start_save_gate_cfg(reordered_cfg, reordered_current))
                    literal = _gate_fixture(
                        work,
                        mode + "-literal_save_menu.c",
                        'enum SaveCompatState state = ClassifySramSaveCompat();\n    if (state != 4)\n        StartSaveCompatMenu(parent, state);\n    else\n        Proc_StartBlocking(ProcScr_SaveMenu, parent);',
                        declaration,
                    )
                    literal_cfg = _function_cfg(
                        _gcc_cfg_tree(
                            work,
                            literal,
                            mode + "-literal_save_menu.o",
                            defines,
                            includes,
                        ),
                        "StartSaveMenu",
                    )
                    self.assertFalse(
                        _has_start_save_gate_cfg(literal_cfg, reordered_current)
                    )

    def test_complete_production_reverse_reference_census(self):
        sources = _production_sources()
        self.assertGreater(len(sources), 400)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            extra_caller = work / "extra_save_caller.c"
            extra_caller.write_text(
                '#include "global.h"\n#include "bmsave.h"\nvoid SaveCompatUnexpectedSaveHook(void)\n{\n    WriteGameSave(0);\n}\n',
                encoding="utf-8",
            )
            hidden_header = work / "hidden_save_hook.h"
            hidden_header.write_text(
                '#define SAVE_COMPAT_HIDDEN_WRITE() \\\n    WriteGameSave(0)\n',
                encoding="utf-8",
            )
            hidden_caller = work / "hidden_save_caller.c"
            hidden_caller.write_text(
                '#include "global.h"\n#include "bmsave.h"\n#include "hidden_save_hook.h"\nvoid SaveCompatHiddenSaveHook(void)\n{\n    SAVE_COMPAT_HIDDEN_WRITE();\n}\n',
                encoding="utf-8",
            )
            bypass = work / "extra_save_menu_bypass.c"
            bypass.write_text(
                '#include "global.h"\n#include "savemenu.h"\nextern struct ProcCmd ProcScr_SaveMenu[];\nvoid SaveCompatUnexpectedMenuBypass(ProcPtr parent)\n{\n    Proc_StartBlocking(ProcScr_SaveMenu, parent);\n}\n',
                encoding="utf-8",
            )
            modern_only = work / "modern_only_save_caller.c"
            modern_only.write_text(
                '#include "global.h"\n#include "bmsave.h"\nvoid SaveCompatModernOnlyHook(void)\n{\n#ifdef MODERN\n    WriteGameSave(0);\n#endif\n}\n',
                encoding="utf-8",
            )

            for mode, defines, includes in _boundary_modes(work):
                with self.subTest(mode=mode):
                    edges = _compiled_census_edges(
                        work, sources, _SAVE_INTERNAL_APIS, defines, includes
                    )
                    by_symbol = {
                        symbol: dict(
                            Counter(
                                path for path, _, edge_symbol in edges
                                if edge_symbol == symbol
                            )
                        )
                        for symbol in _SAVE_INTERNAL_APIS
                    }
                    self.assertEqual(by_symbol, _SAVE_API_COUNTS)
                    menu_symbols = ("ProcScr_SaveMenu", "Proc_StartBlocking", "Proc_Find")
                    menu_edges = _object_relocation_edges(
                        _compile_arm(work, SAVEMENU_C, mode + "-savemenu-menu.o", defines, includes),
                        menu_symbols,
                    )
                    expected_menu_edges = {
                        ("StartSaveMenu", "ProcScr_SaveMenu"),
                        ("StartSaveMenu", "Proc_StartBlocking"),
                        ("SaveMenu_SetDifficultyChoice", "ProcScr_SaveMenu"),
                        ("SaveMenu_SetDifficultyChoice", "Proc_Find"),
                    }
                    self.assertEqual(
                        {edge for edge in menu_edges if edge[0] in {"StartSaveMenu", "SaveMenu_SetDifficultyChoice"}},
                        expected_menu_edges,
                    )
                    self.assertEqual(
                        _candidate_sources((hidden_caller,), _SAVE_INTERNAL_APIS),
                        (hidden_caller,),
                    )
                    self.assertEqual(
                        Counter(
                            symbol
                            for _, symbol in _object_relocation_edges(
                                _compile_arm(
                                    work,
                                    extra_caller,
                                    mode + "-extra_save_caller.o",
                                    defines,
                                    includes,
                                ),
                                _SAVE_INTERNAL_APIS,
                            )
                        ),
                        Counter({"WriteGameSave": 1}),
                    )
                    self.assertEqual(
                        Counter(
                            symbol
                            for _, symbol in _object_relocation_edges(
                                _compile_arm(
                                    work,
                                    hidden_caller,
                                    mode + "-hidden_save_caller.o",
                                    defines,
                                    (work, *includes),
                                ),
                                _SAVE_INTERNAL_APIS,
                            )
                        ),
                        Counter({"WriteGameSave": 1}),
                    )
                    self.assertEqual(
                        Counter(
                            symbol
                            for _, symbol in _object_relocation_edges(
                                _compile_arm(
                                    work,
                                    bypass,
                                    mode + "-extra_save_menu_bypass.o",
                                    defines,
                                    includes,
                                ),
                                ("ProcScr_SaveMenu",),
                            )
                        ),
                        Counter({"ProcScr_SaveMenu": 1}),
                    )
                    same_file = work / "savemenu.c"
                    same_file.write_text(
                        '#include "global.h"\n#include "savemenu.h"\nextern struct ProcCmd ProcScr_SaveMenu[];\nvoid StartSaveMenu(void *p)\n{\n    Proc_StartBlocking(ProcScr_SaveMenu, p);\n}\nvoid SaveMenu_SetDifficultyChoice(int a, int b)\n{\n    Proc_StartBlocking(ProcScr_SaveMenu, 0);\n}\n',
                        encoding="utf-8",
                    )
                    self.assertNotEqual(
                        set(_object_relocation_edges(
                            _compile_arm(work, same_file, mode + "-same_savemenu.o", defines, includes),
                            menu_symbols,
                        )),
                        expected_menu_edges,
                    )
                    modern_edges = _object_relocation_edges(
                        _compile_arm(
                            work,
                            modern_only,
                            mode + "-modern_only_save_caller.o",
                            defines,
                            includes,
                        ),
                        _SAVE_INTERNAL_APIS,
                    )
                    self.assertEqual(
                        Counter(symbol for _, symbol in modern_edges),
                        Counter({"WriteGameSave": 1}) if mode == "modern" else Counter(),
                    )

    def test_public_diagnostic_probe_declarations_link_for_a_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for mode, defines, includes in _boundary_modes(work):
                consumer = work / (mode + "-save_compat_probe_consumer.c")
                consumer.write_text(
                    '#include "global.h"\n'
                    '#include "save_compat_menu.h"\n'
                    'u8 ReadSaveCompatProbe(void)\n'
                    '{\n'
                    '    return gSaveCompatMenuActive | gSaveCompatMenuLastState;\n'
                    '}\n',
                    encoding="utf-8",
                )
                refs = _undefined_symbols(
                    _compile_arm(
                        work,
                        consumer,
                        mode + "-consumer.o",
                        defines,
                        includes,
                    )
                )
                self.assertTrue(
                    {
                        "gSaveCompatMenuActive",
                        "gSaveCompatMenuLastState",
                    }.issubset(refs),
                    "public header must declare both diagnostic probes for "
                    "external consumers",
                )

class EraseConfirmWarningActiveTests(unittest.TestCase):
    """Proves the authored irreversible-erase warning
    (MSG_SAVE_COMPAT_ERASE_CONFIRM) is actually rendered before the
    destructive erase can execute -- not merely generated-but-dead code.

    Structural proof (source order + call-site presence), since this file
    has no execution environment available to it: the message must be
    referenced by a StartHelpBoxExt_Unk(...) call inside
    SaveCompatMenu_ShowEraseConfirm(), which must itself appear, in the
    gProcScr_SaveCompatMenu script array, strictly before
    PL_SAVECOMPAT_DO_ERASE / SaveCompatMenu_DoErase (the only
    InitGlobalSaveInfodata() call site). Runtime evidence that the warning
    is actually visible on screen is separately captured by the
    savecompat-erase scenario's "confirm-shown" framebuffer_hash
    checkpoint differing from a build without this fix (see
    tools/gba-playtest/fingerprints/savecompat-erase-*.json).
    """

    def setUp(self):
        self.text = SAVE_COMPAT_MENU_C.read_text(encoding="utf-8")
        self.stripped = _strip_comments(self.text)

    def test_erase_confirm_message_referenced_in_show_erase_confirm(self):
        match = re.search(
            r"static void SaveCompatMenu_ShowEraseConfirm\([^)]*\)\s*\{(.*?)\n\}",
            self.stripped, re.DOTALL,
        )
        self.assertIsNotNone(
            match, "SaveCompatMenu_ShowEraseConfirm() not found"
        )
        body = match.group(1)
        self.assertIn(
            "MSG_SAVE_COMPAT_ERASE_CONFIRM", body,
            "SaveCompatMenu_ShowEraseConfirm() must render "
            "MSG_SAVE_COMPAT_ERASE_CONFIRM -- it must not be dead code",
        )
        self.assertRegex(
            body, r"StartHelpBoxExt_Unk\s*\([^)]*MSG_SAVE_COMPAT_ERASE_CONFIRM",
            "MSG_SAVE_COMPAT_ERASE_CONFIRM must be passed to "
            "StartHelpBoxExt_Unk(...) (the existing HelpBox-display "
            "pattern), not merely referenced in a comment",
        )

    def test_erase_confirm_message_precedes_destructive_erase_in_script_order(self):
        show_confirm_index = self.text.find("PROC_CALL(SaveCompatMenu_ShowEraseConfirm)")
        do_erase_label_index = self.text.find("PROC_LABEL(PL_SAVECOMPAT_DO_ERASE)")
        self.assertNotEqual(show_confirm_index, -1)
        self.assertNotEqual(do_erase_label_index, -1)
        self.assertLess(
            show_confirm_index, do_erase_label_index,
            "SaveCompatMenu_ShowEraseConfirm() (which renders the warning) "
            "must run before PL_SAVECOMPAT_DO_ERASE (the destructive step) "
            "in gProcScr_SaveCompatMenu's script order",
        )

    def test_erase_confirm_message_referenced_exactly_once(self):
        # Exactly one call site (inside ShowEraseConfirm) plus the
        # explanatory comment above it -- never referenced a second time
        # elsewhere, and never omitted.
        occurrences = self.text.count("MSG_SAVE_COMPAT_ERASE_CONFIRM")
        self.assertGreaterEqual(
            occurrences, 1,
            "MSG_SAVE_COMPAT_ERASE_CONFIRM must be referenced in "
            "src/save_compat_menu.c",
        )

    def test_helpbox_closed_before_do_erase_runs(self):
        """The warning HelpBox opened by ShowEraseConfirm must be closed
        (CloseHelpBox()) before SaveCompatMenu_DoErase() ever runs, so the
        destructive action never leaves a stray graphical HelpBox
        resource acquired without a matching release."""
        route_match = re.search(
            r"static void SaveCompatMenu_RouteEraseChoice\([^)]*\)\s*\{(.*?)\n\}",
            self.stripped, re.DOTALL,
        )
        self.assertIsNotNone(route_match)
        self.assertIn(
            "CloseHelpBox()", route_match.group(1),
            "SaveCompatMenu_RouteEraseChoice() must close the erase-confirm "
            "HelpBox unconditionally (both Yes and No outcomes)",
        )


class DiagnosticProbeGlobalsTests(unittest.TestCase):
    """Proves the read-only diagnostic probe globals (requirement 4) are
    declared and are the only new EWRAM globals this feature adds."""

    def test_probe_globals_defined_exactly_once(self):
        text = SAVE_COMPAT_MENU_C.read_text(encoding="utf-8")
        self.assertEqual(text.count("EWRAM_DATA u8 gSaveCompatMenuActive"), 1)
        self.assertEqual(text.count("EWRAM_DATA u8 gSaveCompatMenuLastState"), 1)


if __name__ == "__main__":
    unittest.main()
