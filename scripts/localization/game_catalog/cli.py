"""CLI for deterministic full-game localized catalog generation."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.schema import SchemaInputs, parse_source_overrides
from scripts.localization.game_locales.authored import (
    AuthoredCatalogError,
    check_authored_catalogs,
)
from scripts.localization.game_locales.fixed_width_labels import ALIASES_PATH
from scripts.localization.game_locales.coverage import load_fe8u_target_ids
from scripts.localization.game_locales.mapping import MappingError
from scripts.localization.game_locales.width_contract import DEFAULT_WIDTH_REGISTRY_PATH

from .build import (
    DEFAULT_AUTHORED_PATHS,
    DEFAULT_ENGLISH_DEFINITIONS_PATH,
    DEFAULT_ENGLISH_TEXTS_PATH,
    DEFAULT_EU_MAPPING_PATH,
    DEFAULT_JA_INDEXED_PATH,
    DEFAULT_JA_RAW_PATH,
    DEFAULT_MAPPING_PATH,
    DEFAULT_TARGET_HEADER_PATH,
    DEFAULT_ZH_INDEXED_PATH,
    DEFAULT_ZH_RAW_PATH,
    GameCatalogError,
    build_game_catalog,
    write_build,
    _normalize_enabled_locales,
)
from .c_emitter import render_declarations
from .constants import CJK_LOCALE_IDS, LOCALE_IDS
from .control_streams import DEFAULT_EVENT_ROOT
from .leakage import (
    DEFAULT_RAW_CLOSURE_PATH,
    DEFAULT_REVIEW_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_SCRIPT_REVIEW_PATH,
    OUTPUT_REPORT_NAME,
    build_leakage_report,
    canonical_json_bytes,
    input_record,
    load_expansion_catalogs,
    load_raw_closure,
    load_review,
    load_script_review,
)


def _locale_path_map(values):
    if values is None:
        return None
    result = {}
    for value in values:
        locale, separator, path = value.partition("=")
        if not separator or not locale or not path:
            raise GameCatalogError(f"invalid locale mapping {value!r}; expected LOCALE=PATH")
        if locale in result:
            raise GameCatalogError(f"duplicate locale mapping for {locale!r}")
        result[locale] = Path(path)
    return result


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--english-texts", type=Path, default=DEFAULT_ENGLISH_TEXTS_PATH
    )
    parser.add_argument(
        "--english-definitions",
        type=Path,
        default=DEFAULT_ENGLISH_DEFINITIONS_PATH,
    )
    parser.add_argument("--ja-indexed", type=Path, default=DEFAULT_JA_INDEXED_PATH)
    parser.add_argument("--ja-raw", type=Path, default=DEFAULT_JA_RAW_PATH)
    parser.add_argument("--zh-indexed", type=Path, default=DEFAULT_ZH_INDEXED_PATH)
    parser.add_argument("--zh-raw", type=Path, default=DEFAULT_ZH_RAW_PATH)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--target-header", type=Path, default=DEFAULT_TARGET_HEADER_PATH)
    parser.add_argument("--source-override", action="append", metavar="TABLE=PATH")
    parser.add_argument("--event-root", type=Path, default=DEFAULT_EVENT_ROOT)
    parser.add_argument("--eu-mapping", type=Path, default=DEFAULT_EU_MAPPING_PATH)
    parser.add_argument("--eu-indexed", action="append", metavar="LOCALE=PATH")
    parser.add_argument("--eu-authored", action="append", metavar="LOCALE=PATH")
    parser.add_argument("--fixed-width-aliases", type=Path, default=ALIASES_PATH)
    parser.add_argument(
        "--width-registry",
        type=Path,
        default=DEFAULT_WIDTH_REGISTRY_PATH,
        help="typed UI/scene rendered-width registry",
    )
    parser.add_argument(
        "--authored",
        action="append",
        default=None,
        metavar="LOCALE=PATH",
        help="optional authored translation source per locale",
    )
    parser.add_argument(
        "--no-suffix-share",
        action="store_true",
        help="disable immediate-predecessor compressed suffix sharing",
    )
    parser.add_argument(
        "--enabled-locales",
        default="ja,zh-Hans",
        help="comma-separated game-catalog payload locales",
    )
    parser.add_argument(
        "--latin-span-review",
        type=Path,
        default=DEFAULT_REVIEW_PATH,
    )
    parser.add_argument(
        "--unicode-script-review",
        type=Path,
        default=DEFAULT_SCRIPT_REVIEW_PATH,
    )
    parser.add_argument(
        "--raw-closure",
        type=Path,
        default=DEFAULT_RAW_CLOSURE_PATH,
    )
    parser.add_argument(
        "--expansion-catalog-root",
        type=Path,
        default=Path("texts/expansion"),
    )
    parser.add_argument("--expansion-catalog", action="append", metavar="LOCALE=PATH")


def _suffix_share(args: argparse.Namespace) -> bool:
    return not args.no_suffix_share


def _build_summary(build) -> str:
    mapping = build.report["mapping_source_counts"]
    locale_counts = []
    for locale in LOCALE_IDS:
        report = build.report["locales"].get(locale)
        locale_counts.append(
            "{}.present={}".format(
                "zh" if locale == "zh-Hans" else locale,
                report["present_count"] if report is not None else "disabled",
            )
        )
    return (
        "targets={targets} indexed={indexed} raw={raw} authored={authored} "
        "fallback={fallback} unresolved={unresolved} "
        "en.present={english_present} {locale_counts}"
    ).format(
        targets=build.target_count,
        indexed=mapping["indexed"],
        raw=mapping["raw"],
        authored=mapping["authored"],
        fallback=mapping["english_fallback"],
        unresolved=mapping["unresolved"],
        english_present=build.report["shared_english"]["present_count"],
        locale_counts=" ".join(locale_counts),
    )


def _build_from_args(args: argparse.Namespace):
    try:
        sources = parse_source_overrides(args.source_override)
    except GeneratedDataError as error:
        raise GameCatalogError(str(error)) from error
    authored_paths = _locale_path_map(args.authored)
    inherited = tuple(
        locale for locale in CJK_LOCALE_IDS
        if authored_paths is None
        or authored_paths.get(locale, DEFAULT_AUTHORED_PATHS[locale]).resolve()
        == DEFAULT_AUTHORED_PATHS[locale].resolve()
    )
    if inherited:
        check_authored_catalogs(Path("."), locales=inherited)
    return build_game_catalog(
        english_texts_path=args.english_texts,
        english_definitions_path=args.english_definitions,
        ja_indexed_path=args.ja_indexed,
        ja_raw_path=args.ja_raw,
        zh_indexed_path=args.zh_indexed,
        zh_raw_path=args.zh_raw,
        mapping_path=args.mapping,
        eu_mapping_path=args.eu_mapping,
        eu_indexed_paths=_locale_path_map(args.eu_indexed),
        eu_authored_paths=_locale_path_map(args.eu_authored),
        target_header_path=args.target_header,
        event_root_path=args.event_root,
        width_registry_path=args.width_registry,
        fixed_width_aliases_path=args.fixed_width_aliases,
        schema_inputs=SchemaInputs(sources, message_header=str(args.target_header)),
        authored_paths=authored_paths,
        enabled_locales=args.enabled_locales,
        suffix_share=_suffix_share(args),
    )


def _enabled_locales(args: argparse.Namespace):
    return tuple(
        locale.strip()
        for locale in args.enabled_locales.split(",")
        if locale.strip()
    )


def _leakage_input_records(args: argparse.Namespace):
    enabled_locales = _enabled_locales(args)
    paths = {
        "english_definitions": args.english_definitions,
        "english_texts": args.english_texts,
        "fixed_width_display_aliases": args.fixed_width_aliases,
        "mapping": args.mapping,
        "raw_closure": args.raw_closure,
        "target_header": args.target_header,
        "unicode_script_review": args.unicode_script_review,
    }
    if "ja" in enabled_locales:
        paths["ja_indexed"] = args.ja_indexed
        paths["ja_raw"] = args.ja_raw
    if "zh-Hans" in enabled_locales:
        paths["zh_hans_indexed"] = args.zh_indexed
        paths["zh_hans_raw"] = args.zh_raw

    authored_paths = (
        _locale_path_map(args.authored)
        if args.authored is not None
        else DEFAULT_AUTHORED_PATHS
    )
    expansion_paths = _locale_path_map(args.expansion_catalog)
    for locale in enabled_locales:
        if locale not in CJK_LOCALE_IDS:
            continue
        paths[f"{locale}_authored"] = authored_paths[locale]
        paths[f"{locale}_expansion"] = (
            expansion_paths[locale] if expansion_paths is not None
            else args.expansion_catalog_root / f"catalog.{locale}.json")
    paths["en_expansion"] = (
        expansion_paths["en"] if expansion_paths is not None
        else args.expansion_catalog_root / "catalog.en.json")
    return {
        name: input_record(path)
        for name, path in sorted(paths.items())
    }


def _audit_from_args(args: argparse.Namespace, build):
    cjk_locales = tuple(
        locale for locale in build.enabled_locales
        if locale in CJK_LOCALE_IDS
    )
    if not cjk_locales:
        return {
            "summary": {
                "unapproved_span_count": 0,
                "game_payload_count": 0,
                "raw_surface_payload_count": 0,
                "approved_span_count": 0,
                "localized_span_decision_count": 0,
            }
        }
    review = load_review(args.latin_span_review)
    script_review = load_script_review(args.unicode_script_review)
    raw_closure = load_raw_closure(args.raw_closure)
    expansion_catalogs = load_expansion_catalogs(
        args.expansion_catalog_root,
        cjk_locales,
        catalog_paths=_locale_path_map(args.expansion_catalog),
    )
    audit_build = replace(
        build,
        locales=tuple(
            bundle
            for bundle in build.locales
            if bundle.locale in CJK_LOCALE_IDS
        ),
    )
    return build_leakage_report(
        audit_build,
        review=review,
        script_review=script_review,
        raw_closure=raw_closure,
        expansion_catalogs=expansion_catalogs,
        inputs=_leakage_input_records(args),
    )


def _write_bytes_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


def cmd_declare(args: argparse.Namespace) -> int:
    try:
        target_count = len(load_fe8u_target_ids(args.target_header))
    except MappingError as error:
        raise GameCatalogError(str(error)) from error
    locales = _normalize_enabled_locales(args.enabled_locales)
    _write_bytes_if_changed(
        args.output, render_declarations(target_count, locales).encode("utf-8"),
    )
    return 0


def cmd_describe_compile(args: argparse.Namespace) -> int:
    command = args.compiler_argv
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0]:
        raise GameCatalogError("compile descriptor requires the actual compiler argv")
    descriptor = {
        "schema_version": 1,
        "kind": "fe8-selected-text-compile",
        "object": str(args.object),
        "logical_source": str(args.logical_source),
        "selected_source": str(args.selected_source),
        "compiler_input": str(args.cc_input),
        "compiler_argv": command,
        "preprocessor_argv": args.preprocessor_arg,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes_if_changed(args.output, canonical_json_bytes(descriptor))
    return 0


def _native_compile_plan(args: argparse.Namespace, sources):
    command = args.make_argv[1:] if args.make_argv[:1] == ["--"] else args.make_argv
    if not command:
        raise GameCatalogError("compile descriptor requires the native Make plan command")
    command = [*command, *(argument for source in sources for argument in ("-o", source))]
    planned = subprocess.run(command, capture_output=True, text=True, check=False)
    if planned.returncode:
        raise GameCatalogError("native Make compile plan failed:\n" + planned.stdout + planned.stderr)
    lines = planned.stdout.replace("\\\n", " ").splitlines()

    def matching(program):
        prefixes = (program + " ", '"' + program + '" ', "'" + program + "' ")
        return (line.strip() for line in lines if line.lstrip().startswith(prefixes))

    compiler = {}
    for line in matching(args.compiler):
        try:
            argv = shlex.split(line)
        except ValueError as error:
            raise GameCatalogError("native Make emitted an invalid compiler command") from error
        if "-c" not in argv:
            continue
        if len(argv) < 5 or argv[-2] != "-o" or argv.count("-c") != 1:
            raise GameCatalogError("native Make emitted an unsupported object compile")
        if argv[-1] in compiler:
            raise GameCatalogError(f"native Make planned two compiles for {argv[-1]}")
        compiler[argv[-1]] = argv
    preprocessor = {}
    if args.preprocessor is not None:
        for line in matching(args.preprocessor):
            before, separator, after = line.rpartition(" > ")
            if not separator:
                continue
            try:
                target = shlex.split(after)
                invocation = shlex.split(before)
            except ValueError as error:
                raise GameCatalogError("native Make emitted an invalid preprocessor command") from error
            if not target:
                raise GameCatalogError("native Make data preprocessor has no output")
            if target[0] in preprocessor:
                raise GameCatalogError(f"native Make planned two preprocessors for {target[0]}")
            preprocessor[target[0]] = invocation
    return lines, compiler, preprocessor


def cmd_describe_make_compile(args: argparse.Namespace) -> int:
    _lines, compiler, preprocessors = _native_compile_plan(args, (str(args.selected_source),))
    argv = compiler.get(str(args.object))
    if argv is None or argv[argv.index("-c") + 1] != str(args.cc_input):
        raise GameCatalogError("native Make did not plan the selected compiler input")
    preprocessor = preprocessors.get(str(args.cc_input), [])
    if args.preprocessor is not None and (
        not preprocessor or preprocessor[-1] != str(args.selected_source)
    ):
        raise GameCatalogError("native Make did not plan the selected data preprocessor")
    args.compiler_argv = argv
    args.preprocessor_arg = preprocessor
    return cmd_describe_compile(args)


def cmd_describe_make_context(args: argparse.Namespace) -> int:
    sources = args.sources_file.read_text(encoding="utf-8").splitlines()
    if not sources or any(not source for source in sources):
        raise GameCatalogError("native Make source inventory is empty or malformed")
    lines, compiler, preprocessors = _native_compile_plan(args, sources)

    def inventory(marker):
        rows = [shlex.split(line) for line in lines if line.startswith(": " + marker + " ")]
        if len(rows) != 1:
            raise GameCatalogError(f"native Make must report exactly one {marker} inventory")
        return rows[0][2:]

    root, *objects = inventory("fe8-selected-text-objects")
    data_objects = set(inventory("fe8-selected-text-data"))
    if (not objects or len(objects) != len(set(objects))
            or set(compiler) != set(objects) or not data_objects <= set(objects)):
        raise GameCatalogError("native Make compile plan differs from the selected C inventory")
    units = []
    for object_path in objects:
        argv = compiler[object_path]
        compiler_input = argv[argv.index("-c") + 1]
        preprocessor = preprocessors.get(compiler_input, [])
        if (object_path in data_objects) != bool(preprocessor):
            raise GameCatalogError(f"native Make data preprocessing differs for {object_path}")
        try:
            logical = Path(object_path).relative_to(root).with_suffix(".c").as_posix()
        except ValueError as error:
            raise GameCatalogError(f"native Make object is outside its selected output: {object_path}") from error
        units.append({
            "schema_version": 1,
            "kind": "fe8-selected-text-compile",
            "object": object_path,
            "logical_source": logical,
            "selected_source": preprocessor[-1] if preprocessor else compiler_input,
            "compiler_input": compiler_input,
            "compiler_argv": argv,
            "preprocessor_argv": preprocessor,
        })
    _write_bytes_if_changed(args.output, canonical_json_bytes({
        "schema_version": 1, "kind": "fe8-selected-text-inputs",
        "source_root": str(Path.cwd().resolve()), "units": units,
    }))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    leakage = _audit_from_args(args, build)
    print(
        "validated full-game locale catalog inputs: "
        + _build_summary(build)
        + " leakage.unapproved_spans={}".format(
            leakage["summary"]["unapproved_span_count"]
        )
    )
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    leakage = _audit_from_args(args, build)
    write_build(build, output_dir=args.out_dir)
    _write_bytes_if_changed(
        args.out_dir / OUTPUT_REPORT_NAME,
        canonical_json_bytes(leakage),
    )
    print(
        "generated full-game locale catalog into {out_dir}: {summary}".format(
            out_dir=args.out_dir,
            summary=_build_summary(build),
        )
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    return cmd_generate(args)


def cmd_budget(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    leakage = _audit_from_args(args, build)
    write_build(build, output_dir=args.out_dir)
    _write_bytes_if_changed(
        args.out_dir / OUTPUT_REPORT_NAME,
        canonical_json_bytes(leakage),
    )
    print(json.dumps(build.budget, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_audit_leakage(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    leakage = _audit_from_args(args, build)
    _write_bytes_if_changed(args.report, canonical_json_bytes(leakage))
    print(
        "generated runtime locale leakage audit: "
        f"game={leakage['summary']['game_payload_count']} "
        f"raw={leakage['summary']['raw_surface_payload_count']} "
        f"approved_spans={leakage['summary']['approved_span_count']} "
        f"localized_spans={leakage['summary']['localized_span_decision_count']} "
        f"unapproved_spans={leakage['summary']['unapproved_span_count']}"
    )
    return 0


def cmd_check_leakage(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    leakage = _audit_from_args(args, build)
    expected = canonical_json_bytes(leakage)
    if not args.report.is_file() or args.report.read_bytes() != expected:
        raise GameCatalogError(
            f"{args.report}: committed leakage report differs from deterministic audit"
        )
    print(
        "runtime locale leakage audit matches committed bytes: "
        f"game={leakage['summary']['game_payload_count']} "
        f"raw={leakage['summary']['raw_surface_payload_count']} "
        "unapproved_spans=0"
    )
    return 0


def cmd_check_width(args: argparse.Namespace) -> int:
    build = _build_from_args(args)
    validation = build.report.get("width_validation")
    if not isinstance(validation, dict):
        raise GameCatalogError(
            "full-game rendered-width validation is unavailable for this target set"
        )
    summary = []
    for locale in sorted(validation):
        report = validation[locale]
        summary.append(
            "{}:targets={} lines={} inserted={} unclassified={}".format(
                locale,
                report["target_count"],
                report["line_count"],
                report["generated_line_break_count"],
                report["unclassified_target_count"],
            )
        )
    print("rendered-width contract passed: " + " ".join(summary))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    declare_p = sub.add_parser(
        "declare", help="emit source-known catalog types and counts without fonts or payloads",
    )
    declare_p.add_argument("--output", type=Path, required=True)
    declare_p.add_argument("--target-header", type=Path, required=True)
    declare_p.add_argument("--enabled-locales", default="ja,zh-Hans")
    declare_p.set_defaults(handler=cmd_declare)

    describe_p = sub.add_parser(
        "describe-compile", help="record selected compiler inputs without generating catalogs",
    )
    describe_p.add_argument("--output", type=Path, required=True)
    describe_p.add_argument("--object", type=Path, required=True)
    describe_p.add_argument("--logical-source", type=Path, required=True)
    describe_p.add_argument("--selected-source", type=Path, required=True)
    describe_p.add_argument("--cc-input", type=Path, required=True)
    describe_p.add_argument("--preprocessor-arg", action="append", default=[])
    describe_p.add_argument("compiler_argv", nargs=argparse.REMAINDER)
    describe_p.set_defaults(handler=cmd_describe_compile)

    plan_p = sub.add_parser(
        "describe-make-compile", help="record actual object-target argv from a native Make plan",
    )
    plan_p.add_argument("--output", type=Path, required=True)
    plan_p.add_argument("--object", type=Path, required=True)
    plan_p.add_argument("--logical-source", type=Path, required=True)
    plan_p.add_argument("--selected-source", type=Path, required=True)
    plan_p.add_argument("--cc-input", type=Path, required=True)
    plan_p.add_argument("--compiler", required=True)
    plan_p.add_argument("--preprocessor")
    plan_p.add_argument("make_argv", nargs=argparse.REMAINDER)
    plan_p.set_defaults(handler=cmd_describe_make_compile)

    context_p = sub.add_parser(
        "describe-make-context", help="record all selected C inputs from one native Make plan",
    )
    context_p.add_argument("--output", type=Path, required=True)
    context_p.add_argument("--sources-file", type=Path, required=True)
    context_p.add_argument("--compiler", required=True)
    context_p.add_argument("--preprocessor", required=True)
    context_p.add_argument("make_argv", nargs=argparse.REMAINDER)
    context_p.set_defaults(handler=cmd_describe_make_context)

    validate_p = sub.add_parser("validate", help="validate inputs and in-memory catalogs")
    _add_common_args(validate_p)
    validate_p.set_defaults(handler=cmd_validate)

    for command, handler, help_text in (
        ("generate", cmd_generate, "generate header/source/report/budget under --out-dir"),
        ("check", cmd_check, "CI-suitable alias for generate"),
        ("budget", cmd_budget, "generate outputs and print the budget JSON"),
    ):
        sub_parser = sub.add_parser(command, help=help_text)
        _add_common_args(sub_parser)
        sub_parser.add_argument("--out-dir", type=Path, required=True)
        sub_parser.set_defaults(handler=handler)

    for command, handler, help_text in (
        (
            "audit-leakage",
            cmd_audit_leakage,
            "write the final JA/ZH runtime Latin-span leakage audit",
        ),
        (
            "check-leakage",
            cmd_check_leakage,
            "verify the committed final runtime Latin-span leakage audit",
        ),
    ):
        sub_parser = sub.add_parser(command, help=help_text)
        _add_common_args(sub_parser)
        sub_parser.add_argument(
            "--report",
            type=Path,
            default=DEFAULT_REPORT_PATH,
        )
        sub_parser.set_defaults(handler=handler)

    width_p = sub.add_parser(
        "check-width",
        help="validate generated CJK payload lines against every classified UI context",
    )
    _add_common_args(width_p)
    width_p.set_defaults(handler=cmd_check_width)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (AuthoredCatalogError, GameCatalogError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
