"""CLI for deterministic full-game localized catalog generation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from scripts.localization.game_locales.authored import (
    AuthoredCatalogError,
    check_authored_catalogs,
)
from scripts.localization.game_locales.fixed_width_labels import ALIASES_PATH
from scripts.localization.game_locales.width_contract import DEFAULT_WIDTH_REGISTRY_PATH

from .build import (
    DEFAULT_AUTHORED_PATHS,
    DEFAULT_ENGLISH_DEFINITIONS_PATH,
    DEFAULT_ENGLISH_TEXTS_PATH,
    DEFAULT_JA_INDEXED_PATH,
    DEFAULT_JA_RAW_PATH,
    DEFAULT_MAPPING_PATH,
    DEFAULT_TARGET_HEADER_PATH,
    DEFAULT_ZH_INDEXED_PATH,
    DEFAULT_ZH_RAW_PATH,
    GameCatalogError,
    build_game_catalog,
    write_build,
)
from .constants import CJK_LOCALE_IDS, LOCALE_IDS
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
    if args.authored is None:
        check_authored_catalogs(Path("."))
    return build_game_catalog(
        english_texts_path=args.english_texts,
        english_definitions_path=args.english_definitions,
        ja_indexed_path=args.ja_indexed,
        ja_raw_path=args.ja_raw,
        zh_indexed_path=args.zh_indexed,
        zh_raw_path=args.zh_raw,
        mapping_path=args.mapping,
        target_header_path=args.target_header,
        width_registry_path=args.width_registry,
        authored_paths=_locale_path_map(args.authored),
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
        "fixed_width_display_aliases": ALIASES_PATH,
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
    for locale in enabled_locales:
        if locale not in CJK_LOCALE_IDS:
            continue
        paths[f"{locale}_authored"] = authored_paths[locale]
        paths[f"{locale}_expansion"] = (
            args.expansion_catalog_root / f"catalog.{locale}.json"
        )
    paths["en_expansion"] = args.expansion_catalog_root / "catalog.en.json"
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
            }
        }
    review = load_review(args.latin_span_review)
    script_review = load_script_review(args.unicode_script_review)
    raw_closure = load_raw_closure(args.raw_closure)
    expansion_catalogs = load_expansion_catalogs(
        args.expansion_catalog_root,
        cjk_locales,
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
