"""Command-line tools for importing and auditing full-game locale sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .authored import (
    AuthoredCatalogError,
    check_authored_catalogs,
    refresh_authored_terminology_provenance,
    write_authored_catalogs,
)
from .combined_coverage import (
    CombinedCoverageError,
    build_combined_coverage_report,
)
from .coverage import build_coverage_report, load_fe8u_target_ids
from .crosswalk import (
    build_crosswalk_coverage_report,
    build_release_mapping,
    canonical_json_bytes,
    harvest_structural_evidence,
)
from .febuilder import (
    FeBuilderEvidenceError,
    build_febuilder_alignment_evidence,
    canonical_json_bytes as febuilder_json_bytes,
    import_febuilder_source,
)
from .final_mapping import (
    FinalMappingError,
    build_final_mapping_artifacts,
    canonical_artifacts,
    recover_original_rows,
    require_no_fallback,
)
from .importer import (
    check_vendored_locale_sources,
    import_locale_sources,
    regenerate_vendored_locale_sources,
)
from .mapping import MappingError, validate_mapping_document
from .parsers import LocaleSourceError
from .raw_closure import (
    build_raw_surface_closure,
    canonical_json_bytes as closure_json_bytes,
)
from .raw_providers import (
    RawProviderError,
    verify_ja_raw_provider_git_source,
)
from .structural_completion import (
    build_structural_completion_evidence,
    check_structural_completion_evidence,
    refresh_structural_completion_protected_inputs,
)


def _load_mapping(path: Path, target_count: int, *, repo_root: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MappingError(f"{path}: invalid JSON: {error}") from error
    return validate_mapping_document(
        data,
        target_count=target_count,
        repo_root=repo_root,
    )


def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MappingError(f"{path}: invalid JSON: {error}") from error


def _cmd_import(args: argparse.Namespace) -> int:
    written = import_locale_sources(
        jp_text_path=args.jp_text,
        jp_controls_path=args.jp_controls,
        cn_text_path=args.cn_text,
        mapping_seed_path=args.mapping_seed,
        output_dir=args.out_dir,
        override_path=args.overrides,
    )
    manifest = json.loads(written["manifest.json"].read_text(encoding="utf-8"))
    print(
        "imported "
        f"JP={manifest['locales']['ja']['indexed']['message_count']} "
        f"CN-indexed={manifest['locales']['zh-Hans']['indexed']['message_count']} "
        f"CN-raw={manifest['locales']['zh-Hans']['raw']['record_count']}/"
        f"{manifest['locales']['zh-Hans']['raw']['unique_import_count']} "
        f"into {args.out_dir}"
    )
    return 0


def _cmd_regenerate(args: argparse.Namespace) -> int:
    written = regenerate_vendored_locale_sources(
        source_dir=args.source_dir,
        output_dir=args.out_dir,
        override_path=args.overrides,
    )
    manifest = json.loads(written["manifest.json"].read_text(encoding="utf-8"))
    print(
        "regenerated committed locale artifacts "
        f"JP={manifest['locales']['ja']['indexed']['message_count']} "
        f"CN={manifest['locales']['zh-Hans']['indexed']['message_count']} "
        f"raw={manifest['locales']['zh-Hans']['raw']['record_count']}/"
        f"{manifest['locales']['zh-Hans']['raw']['unique_import_count']}"
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    artifacts = check_vendored_locale_sources(
        source_dir=args.source_dir,
        output_dir=args.out_dir,
        override_path=args.overrides,
    )
    manifest = json.loads(artifacts["manifest.json"].decode("utf-8"))
    print(
        "locale artifacts match vendored raw snapshots byte-for-byte "
        f"JP={manifest['locales']['ja']['indexed']['message_count']} "
        f"CN={manifest['locales']['zh-Hans']['indexed']['message_count']} "
        f"raw={manifest['locales']['zh-Hans']['raw']['record_count']}/"
        f"{manifest['locales']['zh-Hans']['raw']['unique_import_count']}"
    )
    return 0


def _cmd_build_authored_catalogs(args: argparse.Namespace) -> int:
    written = write_authored_catalogs(
        args.repo_root,
        manifest_path=args.authored_manifest,
    )
    print(
        "built canonical authored catalogs: "
        + " ".join(
            f"{locale}={path}" for locale, path in sorted(written.items())
        )
    )
    return 0


def _cmd_check_authored_catalogs(args: argparse.Namespace) -> int:
    checked = check_authored_catalogs(
        args.repo_root,
        manifest_path=args.authored_manifest,
    )
    print(
        "authored catalogs match deterministic shard merge: "
        + " ".join(
            f"{locale}={path}" for locale, path in sorted(checked.items())
        )
    )
    return 0


def _cmd_refresh_authored_provenance(args: argparse.Namespace) -> int:
    written = refresh_authored_terminology_provenance(
        args.repo_root,
        manifest_path=args.authored_manifest,
    )
    print(
        "refreshed authored terminology provenance: "
        f"files={len(written)}"
    )
    return 0


def _cmd_validate_mapping(args: argparse.Namespace) -> int:
    target_ids = load_fe8u_target_ids(args.target_header)
    mapping = _load_mapping(
        args.mapping,
        len(target_ids),
        repo_root=args.repo_root,
    )
    print(
        f"valid {mapping.authority} mapping: {len(mapping.rows)} rows, "
        f"locale_ids={','.join(mapping.locale_ids)}"
    )
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    target_ids = load_fe8u_target_ids(args.target_header)
    mapping = _load_mapping(
        args.mapping,
        len(target_ids),
        repo_root=args.repo_root,
    )
    report = build_coverage_report(mapping, target_ids, locale=args.locale)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _build_crosswalk_artifacts(args: argparse.Namespace):
    target_count = len(load_fe8u_target_ids(args.target_header))
    evidence = _load_json(args.evidence)
    candidates = _load_json(args.candidates)
    mapping = build_release_mapping(
        evidence,
        target_count=target_count,
        candidate_data=candidates,
        repo_root=args.repo_root,
    )
    report = build_crosswalk_coverage_report(
        mapping,
        target_count=target_count,
        repo_root=args.repo_root,
    )
    return {
        args.mapping: canonical_json_bytes(mapping),
        args.report: canonical_json_bytes(report),
    }


def _cmd_harvest_crosswalk(args: argparse.Namespace) -> int:
    target_count = len(load_fe8u_target_ids(args.target_header))
    evidence = harvest_structural_evidence(
        fe8u_root=args.fe8u_root,
        fe8j_root=args.fe8j_root,
        raw_path=args.raw_source,
        target_count=target_count,
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_bytes(canonical_json_bytes(evidence))
    print(
        f"harvested {len(evidence['records'])} structural evidence slots "
        f"with {len(evidence['gaps'])} explicit gaps"
    )
    return 0


def _cmd_build_crosswalk(args: argparse.Namespace) -> int:
    if args.mapping.is_file():
        current = _load_json(args.mapping)
        if any(
            row.get("verification", {})
            .get("promotion", {})
            .get("pipeline")
            == "fe8u-final-mapping-v1"
            for row in current.get("rows", [])
        ):
            print(
                "final promotion metadata is present; preserving the final map "
                "and checking its structural base"
            )
            return _cmd_check_crosswalk(args)
    artifacts = _build_crosswalk_artifacts(args)
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    mapping = json.loads(artifacts[args.mapping].decode("utf-8"))
    report = json.loads(artifacts[args.report].decode("utf-8"))
    print(
        f"built {len(mapping['rows'])} FE8U target decisions: "
        f"translated={report['translation_coverage']['count']} "
        f"fallback={report['explicit_fallback_coverage']['count']} "
        f"unresolved={report['unresolved_count']}"
    )
    return 0


def _cmd_check_crosswalk(args: argparse.Namespace) -> int:
    artifacts = _build_crosswalk_artifacts(args)
    committed_mapping = _load_json(args.mapping)
    rebuilt_base = json.loads(artifacts[args.mapping].decode("utf-8"))
    if recover_original_rows(committed_mapping) != rebuilt_base["rows"]:
        raise MappingError(
            "final target map does not preserve the deterministic structural base"
        )
    target_count = len(load_fe8u_target_ids(args.target_header))
    report = build_crosswalk_coverage_report(
        committed_mapping,
        target_count=target_count,
        repo_root=args.repo_root,
    )
    if not args.report.is_file() or args.report.read_bytes() != canonical_json_bytes(
        report
    ):
        raise MappingError(
            f"{args.report}: final coverage differs from deterministic rebuild"
        )
    print(
        "structural base is preserved by final artifacts: "
        f"decisions={len(committed_mapping['rows'])} "
        f"translated={report['translation_coverage']['count']} "
        f"fallback={report['explicit_fallback_coverage']['count']} "
        f"unresolved={report['unresolved_count']}"
    )
    return 0


def _build_febuilder_evidence(args: argparse.Namespace):
    return build_febuilder_alignment_evidence(
        source_path=args.febuilder_source,
        ja_indexed_path=args.ja_indexed,
        zh_indexed_path=args.zh_hans_indexed,
        raw_path=args.zh_hans_raw,
        structural_path=args.structural_evidence,
        target_header_path=args.target_header,
        repo_root=args.repo_root,
    )


def _cmd_import_febuilder_evidence(args: argparse.Namespace) -> int:
    import_febuilder_source(args.source, args.febuilder_source)
    evidence = _build_febuilder_evidence(args)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_bytes(febuilder_json_bytes(evidence))
    summary = evidence["summary"]
    print(
        "imported pinned FEBuilder map and built evidence: "
        f"targets={summary['target_candidate_count']} "
        f"conflicts={summary['structural_conflict_count']} "
        f"collisions={summary['unresolved_differing_payload_collision_count']}"
    )
    return 0


def _build_structural_completion(args: argparse.Namespace):
    target_count = len(load_fe8u_target_ids(args.target_header))
    return build_structural_completion_evidence(
        repo_root=args.repo_root,
        fe8u_root=args.fe8u_root,
        fe8j_root=args.fe8j_root,
        reference_map_path=args.reference_map,
        region_map_path=args.region_map,
        target_count=target_count,
    )


def _cmd_harvest_structural_completion(args: argparse.Namespace) -> int:
    evidence = _build_structural_completion(args)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_bytes(canonical_json_bytes(evidence))
    summary = evidence["summary"]
    print(
        "harvested structural completion evidence: "
        f"proposed={summary['proposed_target_count']} "
        f"context={summary['context_required_count']} "
        f"residual={summary['unmapped_residual_count']}"
    )
    return 0


def _cmd_build_febuilder_evidence(args: argparse.Namespace) -> int:
    evidence = _build_febuilder_evidence(args)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_bytes(febuilder_json_bytes(evidence))
    summary = evidence["summary"]
    print(
        "built FEBuilder alignment evidence: "
        f"targets={summary['target_candidate_count']} "
        f"conflicts={summary['structural_conflict_count']} "
        f"collisions={summary['unresolved_differing_payload_collision_count']}"
    )
    return 0


def _cmd_check_febuilder_evidence(args: argparse.Namespace) -> int:
    evidence = _build_febuilder_evidence(args)
    expected = febuilder_json_bytes(evidence)
    if not args.evidence.is_file() or args.evidence.read_bytes() != expected:
        raise FeBuilderEvidenceError(
            f"{args.evidence}: FEBuilder evidence differs from deterministic rebuild"
        )
    summary = evidence["summary"]
    print(
        "FEBuilder alignment evidence matches committed bytes: "
        f"targets={summary['target_candidate_count']} "
        f"conflicts={summary['structural_conflict_count']} "
        f"collisions={summary['unresolved_differing_payload_collision_count']}"
    )
    return 0


def _cmd_check_structural_completion(args: argparse.Namespace) -> int:
    target_count = len(load_fe8u_target_ids(args.target_header))
    evidence = check_structural_completion_evidence(
        args.evidence,
        repo_root=args.repo_root,
        target_count=target_count,
    )
    if args.rebuild:
        missing = [
            name
            for name in ("fe8u_root", "fe8j_root", "reference_map", "region_map")
            if getattr(args, name) is None
        ]
        if missing:
            raise MappingError(
                "--rebuild requires --fe8u-root, --fe8j-root, "
                "--reference-map, and --region-map"
            )
        rebuilt = _build_structural_completion(args)
        if args.evidence.read_bytes() != canonical_json_bytes(rebuilt):
            raise MappingError(
                f"{args.evidence}: differs from deterministic structural rebuild"
            )
    summary = evidence["summary"]
    print(
        "valid structural completion evidence: "
        f"proposed={summary['proposed_target_count']} "
        f"context={summary['context_required_count']} "
        f"residual={summary['unmapped_residual_count']}"
        + (" rebuilt=match" if args.rebuild else "")
    )
    return 0


def _build_combined_coverage(args: argparse.Namespace):
    target_count = len(load_fe8u_target_ids(args.target_header))
    return build_combined_coverage_report(
        repo_root=args.repo_root,
        target_count=target_count,
        mapping_path=args.mapping,
        coverage_path=args.coverage,
        structural_crosswalk_path=args.structural_evidence,
        febuilder_path=args.febuilder_evidence,
        structural_completion_path=args.structural_completion,
    )


def _cmd_build_combined_coverage(args: argparse.Namespace) -> int:
    report = _build_combined_coverage(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical_json_bytes(report))
    summary = report["summary"]
    print(
        "built combined fallback coverage: "
        f"translated={summary['translated_target_count']} "
        f"candidates={summary['combined_unblocked_candidate_target_count']} "
        f"blocked={summary['combined_blocked_target_count']} "
        f"residual={summary['residual_target_count']}"
    )
    return 0


def _cmd_check_combined_coverage(args: argparse.Namespace) -> int:
    report = _build_combined_coverage(args)
    expected = canonical_json_bytes(report)
    if not args.report.is_file() or args.report.read_bytes() != expected:
        raise CombinedCoverageError(
            f"{args.report}: combined coverage differs from deterministic rebuild"
        )
    summary = report["summary"]
    print(
        "combined fallback coverage matches committed bytes: "
        f"translated={summary['translated_target_count']} "
        f"candidates={summary['combined_unblocked_candidate_target_count']} "
        f"blocked={summary['combined_blocked_target_count']} "
        f"residual={summary['residual_target_count']}"
    )
    return 0


def _build_raw_closure(args: argparse.Namespace):
    return build_raw_surface_closure(
        raw_data=_load_json(args.raw_source),
        mapping_data=_load_json(args.mapping),
        decisions_data=_load_json(args.decisions),
        ja_raw_provider_data=_load_json(args.ja_raw),
        registry_data=_load_json(args.registry),
        catalog_data={
            "en": _load_json(args.catalog_en),
            "ja": _load_json(args.catalog_ja),
            "zh-Hans": _load_json(args.catalog_zh_hans),
        },
        repo_root=args.repo_root,
    )


def _cmd_build_raw_closure(args: argparse.Namespace) -> int:
    closure = _build_raw_closure(args)
    args.closure.parent.mkdir(parents=True, exist_ok=True)
    args.closure.write_bytes(closure_json_bytes(closure))
    summary = closure["summary"]
    print(
        f"built raw closure: {summary['total_count']}/"
        f"{summary['total_count']} decisions, "
        f"game={summary['game_message_count']} "
        f"expansion={summary['expansion_message_count']} "
        f"excluded={summary['non_user_facing_exclusion_count'] + summary['diagnostic_exclusion_count']} "
        f"fallback={summary['english_fallback_count']} "
        f"unresolved={summary['unresolved_count']}"
    )
    return 0


def _cmd_check_raw_closure(args: argparse.Namespace) -> int:
    closure = _build_raw_closure(args)
    expected = closure_json_bytes(closure)
    if not args.closure.is_file() or args.closure.read_bytes() != expected:
        raise MappingError(
            f"{args.closure}: raw closure differs from deterministic rebuild"
        )
    summary = closure["summary"]
    print(
        f"raw closure matches committed bytes: decisions={summary['total_count']} "
        f"game={summary['game_message_count']} "
        f"expansion={summary['expansion_message_count']} "
        f"excluded={summary['non_user_facing_exclusion_count'] + summary['diagnostic_exclusion_count']} "
        f"fallback={summary['english_fallback_count']} "
        f"unresolved={summary['unresolved_count']}"
    )
    return 0


def _cmd_check_ja_raw_origin(args: argparse.Namespace) -> int:
    data = _load_json(args.ja_raw)
    verify_ja_raw_provider_git_source(
        data,
        source_root=args.ja_raw.parent,
        repository=args.repository,
    )
    print(
        "Japanese raw provider Git origin matches pinned commit and source blobs"
    )
    return 0


def _build_final_mapping(args: argparse.Namespace):
    target_count = len(load_fe8u_target_ids(args.target_header))
    return build_final_mapping_artifacts(
        repo_root=args.repo_root,
        target_count=target_count,
        mapping_data=_load_json(args.mapping),
        febuilder_data=_load_json(args.febuilder_evidence),
        structural_data=_load_json(args.structural_completion),
        english_texts_path=args.english_texts,
        english_definitions_path=args.english_definitions,
        ja_indexed_text=args.ja_indexed.read_text(encoding="utf-8"),
        zh_indexed_text=args.zh_hans_indexed.read_text(encoding="utf-8"),
        zh_raw_data=_load_json(args.zh_hans_raw),
        ja_raw_data=_load_json(args.ja_raw),
        authored_catalogs={
            "en": _load_json(args.catalog_en),
            "ja": _load_json(args.catalog_ja),
            "zh-Hans": _load_json(args.catalog_zh_hans),
        },
        runtime_authored_catalogs={
            "ja": _load_json(args.runtime_authored_ja),
            "zh-Hans": _load_json(args.runtime_authored_zh_hans),
        },
        authored_queue_data=_load_json(args.queue),
    )


def _final_mapping_paths(args: argparse.Namespace):
    return {
        "coverage": args.coverage,
        "ending_metrics": args.ending_metrics,
        "fixed_width_metrics": args.fixed_width_metrics,
        "mapping": args.mapping,
        "queue": args.queue,
        "report": args.final_report,
    }


def _cmd_build_final_mapping(args: argparse.Namespace) -> int:
    target_count = len(load_fe8u_target_ids(args.target_header))
    refresh_structural_completion_protected_inputs(
        args.structural_completion,
        repo_root=args.repo_root,
        target_count=target_count,
    )
    artifacts = _build_final_mapping(args)
    encoded = canonical_artifacts(artifacts)
    for name, path in _final_mapping_paths(args).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded[name])
    refresh_structural_completion_protected_inputs(
        args.structural_completion,
        repo_root=args.repo_root,
        target_count=target_count,
    )
    artifacts = _build_final_mapping(args)
    encoded = canonical_artifacts(artifacts)
    for name, path in _final_mapping_paths(args).items():
        path.write_bytes(encoded[name])
    summary = artifacts["report"]["summary"]
    print(
        "built final FE8U mapping: "
        f"translated={summary['translated_target_count']} "
        f"fallback={summary['fallback_target_count']} "
        f"queue={summary['authored_queue_target_count']} "
        f"promoted={summary['promoted_target_count']}"
    )
    return 0


def _cmd_check_final_mapping(args: argparse.Namespace) -> int:
    artifacts = _build_final_mapping(args)
    encoded = canonical_artifacts(artifacts)
    mismatches = [
        str(path)
        for name, path in _final_mapping_paths(args).items()
        if not path.is_file() or path.read_bytes() != encoded[name]
    ]
    if mismatches:
        raise FinalMappingError(
            "final mapping artifacts differ from deterministic rebuild: "
            + ", ".join(mismatches)
        )
    if args.require_no_fallback:
        require_no_fallback(artifacts["queue"], mapping=artifacts["mapping"])
    summary = artifacts["report"]["summary"]
    print(
        "final FE8U mapping artifacts match committed bytes: "
        f"translated={summary['translated_target_count']} "
        f"fallback={summary['fallback_target_count']} "
        f"queue={summary['authored_queue_target_count']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, handler, help_text in (
        (
            "build-authored-catalogs",
            _cmd_build_authored_catalogs,
            "merge pinned authored shards into canonical runtime catalogs",
        ),
        (
            "check-authored-catalogs",
            _cmd_check_authored_catalogs,
            "verify canonical authored catalogs against pinned shards",
        ),
        (
            "refresh-authored-provenance",
            _cmd_refresh_authored_provenance,
            "refresh terminology-source hashes without changing translations",
        ),
    ):
        authored_parser = subparsers.add_parser(command, help=help_text)
        authored_parser.add_argument(
            "--authored-manifest",
            type=Path,
            default=Path("texts/locales/authored/manifest.json"),
        )
        authored_parser.add_argument("--repo-root", type=Path, default=Path("."))
        authored_parser.set_defaults(handler=handler)

    import_parser = subparsers.add_parser(
        "import",
        help="import the four pinned locale inputs into deterministic artifacts",
    )
    import_parser.add_argument("--jp-text", type=Path, required=True)
    import_parser.add_argument("--jp-controls", type=Path, required=True)
    import_parser.add_argument("--cn-text", type=Path, required=True)
    import_parser.add_argument("--mapping-seed", type=Path, required=True)
    import_parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("texts/locales/indexed_overrides.json"),
    )
    import_parser.add_argument("--out-dir", type=Path, required=True)
    import_parser.set_defaults(handler=_cmd_import)

    for command, help_text, handler in (
        (
            "regenerate",
            "regenerate artifacts from the committed raw input snapshots",
            _cmd_regenerate,
        ),
        (
            "check",
            "compare regenerated artifacts with committed bytes",
            _cmd_check,
        ),
    ):
        source_parser = subparsers.add_parser(command, help=help_text)
        source_parser.add_argument(
            "--source-dir",
            type=Path,
            default=Path("texts/locales/source"),
        )
        source_parser.add_argument(
            "--out-dir",
            type=Path,
            default=Path("texts/locales"),
        )
        source_parser.add_argument(
            "--overrides",
            type=Path,
            default=Path("texts/locales/indexed_overrides.json"),
        )
        source_parser.set_defaults(handler=handler)

    validate_parser = subparsers.add_parser(
        "validate-mapping",
        help="validate sparse mapping syntax and candidate/verified authority semantics",
    )
    validate_parser.add_argument("--mapping", type=Path, required=True)
    validate_parser.add_argument(
        "--target-header",
        type=Path,
        default=Path("include/constants/msg.h"),
    )
    validate_parser.add_argument("--repo-root", type=Path, default=Path("."))
    validate_parser.set_defaults(handler=_cmd_validate_mapping)

    coverage_parser = subparsers.add_parser(
        "coverage",
        help="classify every FE8U target using an authority-gated sparse mapping",
    )
    coverage_parser.add_argument("--mapping", type=Path, required=True)
    coverage_parser.add_argument("--locale", choices=("ja", "zh-Hans"), required=True)
    coverage_parser.add_argument(
        "--target-header",
        type=Path,
        default=Path("include/constants/msg.h"),
    )
    coverage_parser.add_argument("--repo-root", type=Path, default=Path("."))
    coverage_parser.set_defaults(handler=_cmd_coverage)

    harvest_parser = subparsers.add_parser(
        "harvest-crosswalk",
        help="harvest structural FE8U/FE8J evidence from authorized reference trees",
    )
    harvest_parser.add_argument("--fe8u-root", type=Path, required=True)
    harvest_parser.add_argument("--fe8j-root", type=Path, required=True)
    harvest_parser.add_argument(
        "--raw-source",
        type=Path,
        default=Path("texts/locales/zh-Hans/raw.json"),
    )
    harvest_parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("texts/locales/mapping/fe8u_structural_evidence.json"),
    )
    harvest_parser.add_argument(
        "--target-header",
        type=Path,
        default=Path("include/constants/msg.h"),
    )
    harvest_parser.set_defaults(handler=_cmd_harvest_crosswalk)

    for command, help_text, handler in (
        (
            "build-crosswalk",
            "build the authoritative map and coverage report from committed evidence",
            _cmd_build_crosswalk,
        ),
        (
            "check-crosswalk",
            "compare committed crosswalk artifacts with a deterministic rebuild",
            _cmd_check_crosswalk,
        ),
    ):
        crosswalk_parser = subparsers.add_parser(command, help=help_text)
        crosswalk_parser.add_argument(
            "--evidence",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_structural_evidence.json"),
        )
        crosswalk_parser.add_argument(
            "--candidates",
            type=Path,
            default=Path("texts/locales/mapping/fe8j_to_fe8u.candidates.json"),
        )
        crosswalk_parser.add_argument(
            "--mapping",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.json"),
        )
        crosswalk_parser.add_argument(
            "--report",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.coverage.json"),
        )
        crosswalk_parser.add_argument(
            "--target-header",
            type=Path,
            default=Path("include/constants/msg.h"),
        )
        crosswalk_parser.add_argument("--repo-root", type=Path, default=Path("."))
        crosswalk_parser.set_defaults(handler=handler)

    def add_febuilder_inputs(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--febuilder-source",
            type=Path,
            default=Path(
                "texts/locales/source/febuilder/translate_textid_FE8.txt"
            ),
        )
        command_parser.add_argument(
            "--ja-indexed",
            type=Path,
            default=Path("texts/locales/ja/indexed.txt"),
        )
        command_parser.add_argument(
            "--zh-hans-indexed",
            type=Path,
            default=Path("texts/locales/zh-Hans/indexed.txt"),
        )
        command_parser.add_argument(
            "--zh-hans-raw",
            type=Path,
            default=Path("texts/locales/zh-Hans/raw.json"),
        )
        command_parser.add_argument(
            "--structural-evidence",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_structural_evidence.json"),
        )
        command_parser.add_argument(
            "--target-header",
            type=Path,
            default=Path("include/constants/msg.h"),
        )
        command_parser.add_argument(
            "--evidence",
            type=Path,
            default=Path(
                "texts/locales/mapping/febuilder_alignment_evidence.json"
            ),
        )
        command_parser.add_argument("--repo-root", type=Path, default=Path("."))

    import_febuilder_parser = subparsers.add_parser(
        "import-febuilder-evidence",
        help="vendor the pinned FEBuilder map and build its evidence ledger",
    )
    import_febuilder_parser.add_argument("--source", type=Path, required=True)
    add_febuilder_inputs(import_febuilder_parser)
    import_febuilder_parser.set_defaults(handler=_cmd_import_febuilder_evidence)

    for command, help_text, handler in (
        (
            "build-febuilder-evidence",
            "build non-authoritative FEBuilder alignment evidence",
            _cmd_build_febuilder_evidence,
        ),
        (
            "check-febuilder-evidence",
            "compare FEBuilder evidence with a deterministic rebuild",
            _cmd_check_febuilder_evidence,
        ),
    ):
        febuilder_parser = subparsers.add_parser(command, help=help_text)
        add_febuilder_inputs(febuilder_parser)
        febuilder_parser.set_defaults(handler=handler)

    completion_parser = subparsers.add_parser(
        "harvest-structural-completion",
        help="harvest evidence-only completion proposals from authorized references",
    )
    completion_parser.add_argument("--fe8u-root", type=Path, required=True)
    completion_parser.add_argument("--fe8j-root", type=Path, required=True)
    completion_parser.add_argument("--reference-map", type=Path, required=True)
    completion_parser.add_argument("--region-map", type=Path, required=True)
    completion_parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(
            "texts/locales/mapping/structural_completion_evidence.json"
        ),
    )
    completion_parser.add_argument(
        "--target-header",
        type=Path,
        default=Path("include/constants/msg.h"),
    )
    completion_parser.add_argument("--repo-root", type=Path, default=Path("."))
    completion_parser.set_defaults(handler=_cmd_harvest_structural_completion)

    completion_check_parser = subparsers.add_parser(
        "check-structural-completion",
        help="validate the committed completion artifact, optionally rebuilding it",
    )
    completion_check_parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(
            "texts/locales/mapping/structural_completion_evidence.json"
        ),
    )
    completion_check_parser.add_argument(
        "--target-header",
        type=Path,
        default=Path("include/constants/msg.h"),
    )
    completion_check_parser.add_argument("--repo-root", type=Path, default=Path("."))
    completion_check_parser.add_argument("--rebuild", action="store_true")
    completion_check_parser.add_argument("--fe8u-root", type=Path)
    completion_check_parser.add_argument("--fe8j-root", type=Path)
    completion_check_parser.add_argument("--reference-map", type=Path)
    completion_check_parser.add_argument("--region-map", type=Path)
    completion_check_parser.set_defaults(handler=_cmd_check_structural_completion)

    def add_combined_coverage_inputs(
        command_parser: argparse.ArgumentParser,
    ) -> None:
        command_parser.add_argument(
            "--mapping",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.json"),
        )
        command_parser.add_argument(
            "--coverage",
            type=Path,
            default=Path(
                "texts/locales/mapping/fe8u_target_map.coverage.json"
            ),
        )
        command_parser.add_argument(
            "--structural-evidence",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_structural_evidence.json"),
        )
        command_parser.add_argument(
            "--febuilder-evidence",
            type=Path,
            default=Path(
                "texts/locales/mapping/febuilder_alignment_evidence.json"
            ),
        )
        command_parser.add_argument(
            "--structural-completion",
            type=Path,
            default=Path(
                "texts/locales/mapping/structural_completion_evidence.json"
            ),
        )
        command_parser.add_argument(
            "--report",
            type=Path,
            default=Path(
                "texts/locales/mapping/combined_fallback_coverage.json"
            ),
        )
        command_parser.add_argument(
            "--target-header",
            type=Path,
            default=Path("include/constants/msg.h"),
        )
        command_parser.add_argument("--repo-root", type=Path, default=Path("."))

    for command, help_text, handler in (
        (
            "build-combined-coverage",
            "build an evidence-only handoff report for current fallback targets",
            _cmd_build_combined_coverage,
        ),
        (
            "check-combined-coverage",
            "compare the combined fallback report with a deterministic rebuild",
            _cmd_check_combined_coverage,
        ),
    ):
        combined_parser = subparsers.add_parser(command, help=help_text)
        add_combined_coverage_inputs(combined_parser)
        combined_parser.set_defaults(handler=handler)

    for command, help_text, handler in (
        (
            "build-raw-closure",
            "build the 143-record raw-surface closure manifest",
            _cmd_build_raw_closure,
        ),
        (
            "check-raw-closure",
            "compare the raw-surface closure manifest with a deterministic rebuild",
            _cmd_check_raw_closure,
        ),
    ):
        closure_parser = subparsers.add_parser(command, help=help_text)
        closure_parser.add_argument(
            "--raw-source",
            type=Path,
            default=Path("texts/locales/zh-Hans/raw.json"),
        )
        closure_parser.add_argument(
            "--ja-raw",
            type=Path,
            default=Path("texts/locales/ja/raw.json"),
        )
        closure_parser.add_argument(
            "--mapping",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.json"),
        )
        closure_parser.add_argument(
            "--decisions",
            type=Path,
            default=Path("texts/locales/mapping/raw_surface_decisions.json"),
        )
        closure_parser.add_argument(
            "--closure",
            type=Path,
            default=Path("texts/locales/mapping/raw_surface_closure.json"),
        )
        closure_parser.add_argument(
            "--registry",
            type=Path,
            default=Path("texts/expansion/registry.json"),
        )
        closure_parser.add_argument(
            "--catalog-en",
            type=Path,
            default=Path("texts/expansion/catalog.en.json"),
        )
        closure_parser.add_argument(
            "--catalog-ja",
            type=Path,
            default=Path("texts/expansion/catalog.ja.json"),
        )
        closure_parser.add_argument(
            "--catalog-zh-hans",
            type=Path,
            default=Path("texts/expansion/catalog.zh-Hans.json"),
        )
        closure_parser.add_argument(
            "--repo-root",
            type=Path,
            default=Path("."),
        )
        closure_parser.set_defaults(handler=handler)

    origin_parser = subparsers.add_parser(
        "check-ja-raw-origin",
        help="verify Japanese raw provider metadata against a local Git source",
    )
    origin_parser.add_argument(
        "--ja-raw",
        type=Path,
        default=Path("texts/locales/ja/raw.json"),
    )
    origin_parser.add_argument(
        "--repository",
        type=Path,
        required=True,
        help="local checkout/object database for the pinned FE8J repository",
    )
    origin_parser.set_defaults(handler=_cmd_check_ja_raw_origin)

    def add_final_mapping_inputs(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--mapping",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.json"),
        )
        command_parser.add_argument(
            "--coverage",
            type=Path,
            default=Path("texts/locales/mapping/fe8u_target_map.coverage.json"),
        )
        command_parser.add_argument(
            "--febuilder-evidence",
            type=Path,
            default=Path(
                "texts/locales/mapping/febuilder_alignment_evidence.json"
            ),
        )
        command_parser.add_argument(
            "--structural-completion",
            type=Path,
            default=Path(
                "texts/locales/mapping/structural_completion_evidence.json"
            ),
        )
        command_parser.add_argument(
            "--queue",
            type=Path,
            default=Path(
                "texts/locales/mapping/authored_translation_queue.json"
            ),
        )
        command_parser.add_argument(
            "--final-report",
            type=Path,
            default=Path("texts/locales/mapping/final_mapping_report.json"),
        )
        command_parser.add_argument(
            "--ending-metrics",
            type=Path,
            default=Path(
                "texts/locales/mapping/ending_layout_metrics.json"
            ),
        )
        command_parser.add_argument(
            "--fixed-width-metrics",
            type=Path,
            default=Path(
                "texts/locales/mapping/fixed_width_label_metrics.json"
            ),
        )
        command_parser.add_argument(
            "--english-texts",
            type=Path,
            default=Path("texts/texts.txt"),
        )
        command_parser.add_argument(
            "--english-definitions",
            type=Path,
            default=Path("texts/textdefs.txt"),
        )
        command_parser.add_argument(
            "--ja-indexed",
            type=Path,
            default=Path("texts/locales/ja/indexed.txt"),
        )
        command_parser.add_argument(
            "--ja-raw",
            type=Path,
            default=Path("texts/locales/ja/raw.json"),
        )
        command_parser.add_argument(
            "--zh-hans-indexed",
            type=Path,
            default=Path("texts/locales/zh-Hans/indexed.txt"),
        )
        command_parser.add_argument(
            "--zh-hans-raw",
            type=Path,
            default=Path("texts/locales/zh-Hans/raw.json"),
        )
        command_parser.add_argument(
            "--catalog-en",
            type=Path,
            default=Path("texts/expansion/catalog.en.json"),
        )
        command_parser.add_argument(
            "--catalog-ja",
            type=Path,
            default=Path("texts/expansion/catalog.ja.json"),
        )
        command_parser.add_argument(
            "--catalog-zh-hans",
            type=Path,
            default=Path("texts/expansion/catalog.zh-Hans.json"),
        )
        command_parser.add_argument(
            "--runtime-authored-ja",
            type=Path,
            default=Path("texts/locales/authored/catalog.ja.json"),
        )
        command_parser.add_argument(
            "--runtime-authored-zh-hans",
            type=Path,
            default=Path("texts/locales/authored/catalog.zh-Hans.json"),
        )
        command_parser.add_argument(
            "--target-header",
            type=Path,
            default=Path("include/constants/msg.h"),
        )
        command_parser.add_argument("--repo-root", type=Path, default=Path("."))

    final_build_parser = subparsers.add_parser(
        "build-final-mapping",
        help="promote final evidence and generate the exact authored queue",
    )
    add_final_mapping_inputs(final_build_parser)
    final_build_parser.set_defaults(handler=_cmd_build_final_mapping)

    final_check_parser = subparsers.add_parser(
        "check-final-mapping",
        help="compare final map, coverage, queue, and report with a rebuild",
    )
    add_final_mapping_inputs(final_check_parser)
    final_check_parser.add_argument(
        "--require-no-fallback",
        action="store_true",
        help="final-delivery gate: reject any remaining authored queue target",
    )
    final_check_parser.set_defaults(handler=_cmd_check_final_mapping)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (
        AuthoredCatalogError,
        CombinedCoverageError,
        FeBuilderEvidenceError,
        FinalMappingError,
        LocaleSourceError,
        MappingError,
        OSError,
        RawProviderError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
