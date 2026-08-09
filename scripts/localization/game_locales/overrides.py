"""Hash-pinned corrections applied after raw locale-source normalization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .controls import ControlSyntaxError, expand_canonical_text, validate_canonical_text
from .parsers import IndexedMessage, LocaleSourceError

OVERRIDE_SCHEMA_VERSION = 1
OVERRIDE_KIND = "indexed-locale-source-overrides"
_MESSAGE_ID_RE = re.compile(r"0x[0-9A-F]{4}")
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")


@dataclass(frozen=True)
class IndexedOverride:
    message_id: int
    expected_text: Optional[str]
    expected_text_sha256: str
    replacement_text: Optional[str]
    replacements: Tuple[Tuple[str, str], ...]
    reason: str
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class SourceOverrides:
    locale_id: str
    source_id: str
    source_sha256: str
    entries: Mapping[int, IndexedOverride]


@dataclass(frozen=True)
class OverrideCatalog:
    path: str
    sha256: str
    byte_count: int
    sources: Mapping[str, SourceOverrides]

    @property
    def entry_count(self) -> int:
        return sum(len(source.entries) for source in self.sources.values())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload_structure(text: str) -> Tuple[Tuple[str, int], ...]:
    structure = []
    for unit in expand_canonical_text(text):
        if isinstance(unit, int):
            structure.append(("control", unit))
            continue
        pieces = unit.split("\n")
        for index, piece in enumerate(pieces):
            if piece:
                structure.append(("text", 0))
            if index + 1 < len(pieces):
                structure.append(("newline", 0))
    return tuple(structure)


def _apply_replacements(
    text: str,
    replacements: Tuple[Tuple[str, str], ...],
    *,
    source_id: str,
    message_id: str,
) -> str:
    result = text
    for expected, replacement in replacements:
        count = result.count(expected)
        if count != 1:
            raise LocaleSourceError(
                f"{source_id}:{message_id}: replacement expected fragment "
                f"must occur exactly once, found {count}"
            )
        result = result.replace(expected, replacement, 1)
    return result


def _load_entry(source_id: str, message_id: str, data: Any) -> IndexedOverride:
    if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
        raise LocaleSourceError(
            f"{source_id}: override key {message_id!r} must use canonical 0xNNNN form"
        )
    if not isinstance(data, dict):
        raise LocaleSourceError(f"{source_id}:{message_id}: override must be an object")

    expected_text = data.get("expected_text")
    expected_text_sha256 = data.get("expected_text_sha256")
    replacement_text = data.get("replacement_text")
    raw_replacements = data.get("replacements")
    reason = data.get("reason")
    provenance = data.get("provenance")
    if expected_text is None and expected_text_sha256 is None:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: expected_text or expected_text_sha256 is required"
        )
    if expected_text is not None and expected_text_sha256 is not None:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: expected_text and expected_text_sha256 "
            "are mutually exclusive"
        )
    if expected_text is not None and (
        not isinstance(expected_text, str) or not expected_text
    ):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: expected_text must be non-empty"
        )
    if expected_text_sha256 is not None and (
        not isinstance(expected_text_sha256, str)
        or len(expected_text_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_text_sha256)
    ):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: expected_text_sha256 must be a lowercase SHA-256"
        )
    if replacement_text is None and raw_replacements is None:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement_text or replacements is required"
        )
    if replacement_text is not None and raw_replacements is not None:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement_text and replacements "
            "are mutually exclusive"
        )
    if replacement_text is not None and (
        not isinstance(replacement_text, str) or not replacement_text
    ):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement_text must be non-empty"
        )
    replacements = ()
    if raw_replacements is not None:
        if not isinstance(raw_replacements, list) or not raw_replacements:
            raise LocaleSourceError(
                f"{source_id}:{message_id}: replacements must be a non-empty array"
            )
        parsed_replacements = []
        for index, replacement in enumerate(raw_replacements):
            if not isinstance(replacement, dict) or set(replacement) != {
                "expected",
                "replacement",
            }:
                raise LocaleSourceError(
                    f"{source_id}:{message_id}: replacements[{index}] must contain "
                    "expected and replacement"
                )
            expected_fragment = replacement["expected"]
            replacement_fragment = replacement["replacement"]
            if (
                not isinstance(expected_fragment, str)
                or not expected_fragment
                or not isinstance(replacement_fragment, str)
                or not replacement_fragment
                or expected_fragment == replacement_fragment
            ):
                raise LocaleSourceError(
                    f"{source_id}:{message_id}: replacements[{index}] fragments "
                    "must be distinct non-empty strings"
                )
            parsed_replacements.append(
                (expected_fragment, replacement_fragment)
            )
        replacements = tuple(parsed_replacements)

    resolved_replacement = replacement_text
    if expected_text is not None and replacements:
        resolved_replacement = _apply_replacements(
            expected_text,
            replacements,
            source_id=source_id,
            message_id=message_id,
        )
    if expected_text is not None and resolved_replacement == expected_text:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement must change the payload"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise LocaleSourceError(f"{source_id}:{message_id}: reason must be non-empty")
    if not isinstance(provenance, dict):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: provenance must be an object"
        )
    audit = provenance.get("audit")
    context = provenance.get("context")
    target_ids = provenance.get("target_ids")
    if not isinstance(audit, str) or not audit:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: provenance.audit must be non-empty"
        )
    if not isinstance(context, str) or not context:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: provenance.context must be non-empty"
        )
    if (
        not isinstance(target_ids, list)
        or not target_ids
        or any(
            not isinstance(target_id, str)
            or not _TARGET_ID_RE.fullmatch(target_id)
            for target_id in target_ids
        )
        or len(set(target_ids)) != len(target_ids)
    ):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: provenance.target_ids must be unique 0xNNNN IDs"
        )
    try:
        if expected_text is not None:
            validate_canonical_text(expected_text)
        if resolved_replacement is not None:
            validate_canonical_text(resolved_replacement)
    except ControlSyntaxError as error:
        raise LocaleSourceError(f"{source_id}:{message_id}: {error}") from error
    if (
        expected_text is not None
        and resolved_replacement is not None
        and _payload_structure(expected_text) != _payload_structure(resolved_replacement)
    ):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement must preserve controls, "
            "newlines, placeholders, and their placement"
        )

    return IndexedOverride(
        message_id=int(message_id, 16),
        expected_text=expected_text,
        expected_text_sha256=(
            expected_text_sha256
            if expected_text_sha256 is not None
            else _sha256_bytes(expected_text.encode("utf-8"))
        ),
        replacement_text=replacement_text,
        replacements=replacements,
        reason=reason,
        provenance=provenance,
    )


def load_override_catalog(
    path: Path,
    *,
    expected_source_hashes: Mapping[str, str],
) -> OverrideCatalog:
    path = Path(path)
    data_bytes = path.read_bytes()
    try:
        data = json.loads(data_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocaleSourceError(f"{path}: override catalog must be valid UTF-8 JSON") from error
    if not isinstance(data, dict):
        raise LocaleSourceError(f"{path}: override catalog root must be an object")
    if data.get("schema_version") != OVERRIDE_SCHEMA_VERSION:
        raise LocaleSourceError(
            f"{path}: override schema_version must be {OVERRIDE_SCHEMA_VERSION}"
        )
    if data.get("kind") != OVERRIDE_KIND:
        raise LocaleSourceError(f"{path}: override kind must be {OVERRIDE_KIND!r}")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, dict) or not raw_sources:
        raise LocaleSourceError(f"{path}: override sources must be a non-empty object")
    for source_id, raw_source in raw_sources.items():
        if (
            not isinstance(raw_source, dict)
            or not isinstance(raw_source.get("entries"), dict)
        ):
            raise LocaleSourceError(
                f"{path}: override source {source_id!r} entries must be an object"
            )
    merged_sources = {
        source_id: {
            **raw_source,
            "entries": dict(raw_source["entries"]),
        }
        for source_id, raw_source in raw_sources.items()
    }
    catalog_bytes = [data_bytes]
    supplements = data.get("supplements", [])
    if not isinstance(supplements, list):
        raise LocaleSourceError(f"{path}: override supplements must be an array")
    for index, descriptor in enumerate(supplements):
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "path",
            "sha256",
        }:
            raise LocaleSourceError(
                f"{path}: supplements[{index}] must contain path and sha256"
            )
        if (
            not isinstance(descriptor["path"], str)
            or not descriptor["path"]
            or not isinstance(descriptor["sha256"], str)
            or len(descriptor["sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in descriptor["sha256"]
            )
        ):
            raise LocaleSourceError(
                f"{path}: supplements[{index}] path or sha256 is invalid"
            )
        supplement_path = Path(descriptor["path"])
        if not supplement_path.is_absolute():
            repo_path = (
                path.parents[2] / supplement_path
                if len(path.parents) > 2
                else supplement_path
            )
            cwd_path = Path.cwd() / supplement_path
            supplement_path = (
                repo_path
                if repo_path.is_file()
                else cwd_path
                if cwd_path.is_file()
                else path.parent / supplement_path
            )
        supplement_bytes = supplement_path.read_bytes()
        if _sha256_bytes(supplement_bytes) != descriptor["sha256"]:
            raise LocaleSourceError(
                f"{supplement_path}: override supplement SHA-256 drift"
            )
        try:
            supplement = json.loads(supplement_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocaleSourceError(
                f"{supplement_path}: override supplement must be valid UTF-8 JSON"
            ) from error
        if (
            not isinstance(supplement, dict)
            or supplement.get("schema_version") != 1
            or supplement.get("kind")
            != "indexed-locale-source-override-supplement"
            or set(supplement) != {"kind", "schema_version", "sources"}
        ):
            raise LocaleSourceError(
                f"{supplement_path}: override supplement schema is invalid"
            )
        supplement_sources = supplement["sources"]
        if not isinstance(supplement_sources, dict):
            raise LocaleSourceError(
                f"{supplement_path}: override supplement sources must be an object"
            )
        for source_id, supplement_source in supplement_sources.items():
            if source_id not in merged_sources:
                raise LocaleSourceError(
                    f"{supplement_path}: unsupported override source {source_id!r}"
                )
            if (
                not isinstance(supplement_source, dict)
                or set(supplement_source) != {"entries"}
                or not isinstance(supplement_source["entries"], dict)
            ):
                raise LocaleSourceError(
                    f"{supplement_path}: source {source_id!r} is malformed"
                )
            duplicate_ids = set(merged_sources[source_id]["entries"]) & set(
                supplement_source["entries"]
            )
            if duplicate_ids:
                raise LocaleSourceError(
                    f"{supplement_path}: duplicate override IDs for {source_id}: "
                    + ", ".join(sorted(duplicate_ids))
                )
            merged_sources[source_id]["entries"].update(
                supplement_source["entries"]
            )
        catalog_bytes.append(supplement_bytes)

    sources: Dict[str, SourceOverrides] = {}
    for source_id, raw_source in sorted(merged_sources.items()):
        if source_id not in expected_source_hashes:
            raise LocaleSourceError(f"{path}: unsupported override source {source_id!r}")
        if not isinstance(raw_source, dict):
            raise LocaleSourceError(f"{path}: override source {source_id!r} must be an object")
        locale_id = raw_source.get("locale_id")
        source_sha256 = raw_source.get("source_sha256")
        raw_entries = raw_source.get("entries")
        if locale_id not in ("ja", "zh-Hans"):
            raise LocaleSourceError(
                f"{path}: override source {source_id!r} has invalid locale_id"
            )
        if source_sha256 != expected_source_hashes[source_id]:
            raise LocaleSourceError(
                f"{path}: override source {source_id!r} is not pinned to the "
                "active raw-source SHA-256"
            )
        if not isinstance(raw_entries, dict) or not raw_entries:
            raise LocaleSourceError(
                f"{path}: override source {source_id!r} entries must be non-empty"
            )
        entries = {
            int(message_id, 16): _load_entry(source_id, message_id, entry)
            for message_id, entry in sorted(raw_entries.items())
        }
        if len(entries) != len(raw_entries):
            raise LocaleSourceError(f"{path}: duplicate override IDs for {source_id}")
        sources[source_id] = SourceOverrides(
            locale_id=locale_id,
            source_id=source_id,
            source_sha256=source_sha256,
            entries=entries,
        )

    return OverrideCatalog(
        path=path.as_posix(),
        sha256=_sha256_bytes(b"\0".join(catalog_bytes)),
        byte_count=sum(len(data) for data in catalog_bytes),
        sources=sources,
    )


def apply_indexed_overrides(
    messages: Iterable[IndexedMessage],
    *,
    source: SourceOverrides,
) -> Tuple[Tuple[IndexedMessage, ...], Tuple[IndexedOverride, ...]]:
    message_by_id = {message.id: message for message in messages}
    missing = sorted(set(source.entries) - set(message_by_id))
    if missing:
        formatted = ", ".join(f"0x{message_id:04X}" for message_id in missing)
        raise LocaleSourceError(
            f"{source.source_id}: override IDs are absent from normalized source: {formatted}"
        )

    applied = []
    result = []
    for message in message_by_id.values():
        override = source.entries.get(message.id)
        if override is None:
            result.append(message)
            continue
        actual_sha256 = _sha256_bytes(message.text.encode("utf-8"))
        if actual_sha256 != override.expected_text_sha256:
            raise LocaleSourceError(
                f"{source.source_id}:0x{message.id:04X}: normalized source payload "
                "does not match override expected text hash"
            )
        replacement_text = override.replacement_text
        if replacement_text is None:
            replacement_text = _apply_replacements(
                message.text,
                override.replacements,
                source_id=source.source_id,
                message_id=f"0x{message.id:04X}",
            )
        try:
            validate_canonical_text(replacement_text)
        except ControlSyntaxError as error:
            raise LocaleSourceError(
                f"{source.source_id}:0x{message.id:04X}: {error}"
            ) from error
        if _payload_structure(message.text) != _payload_structure(replacement_text):
            raise LocaleSourceError(
                f"{source.source_id}:0x{message.id:04X}: replacement must preserve "
                "controls, newlines, placeholders, and their placement"
            )
        if message.text == replacement_text:
            raise LocaleSourceError(
                f"{source.source_id}:0x{message.id:04X}: replacement does not "
                "change the normalized source payload"
            )
        result.append(
            IndexedMessage(
                message.id,
                replacement_text,
                message.marker_line,
            )
        )
        applied.append(override)
    return tuple(result), tuple(applied)
