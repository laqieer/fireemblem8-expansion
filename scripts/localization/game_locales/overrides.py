"""Hash-pinned corrections applied after raw locale-source normalization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from .controls import ControlSyntaxError, expand_canonical_text, validate_canonical_text
from .parsers import IndexedMessage, LocaleSourceError

OVERRIDE_SCHEMA_VERSION = 1
OVERRIDE_KIND = "indexed-locale-source-overrides"
_MESSAGE_ID_RE = re.compile(r"0x[0-9A-F]{4}")
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")


@dataclass(frozen=True)
class IndexedOverride:
    message_id: int
    expected_text: str
    replacement_text: str
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


def _load_entry(source_id: str, message_id: str, data: Any) -> IndexedOverride:
    if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
        raise LocaleSourceError(
            f"{source_id}: override key {message_id!r} must use canonical 0xNNNN form"
        )
    if not isinstance(data, dict):
        raise LocaleSourceError(f"{source_id}:{message_id}: override must be an object")

    expected_text = data.get("expected_text")
    replacement_text = data.get("replacement_text")
    reason = data.get("reason")
    provenance = data.get("provenance")
    if not isinstance(expected_text, str) or not expected_text:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: expected_text must be non-empty"
        )
    if not isinstance(replacement_text, str) or not replacement_text:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement_text must be non-empty"
        )
    if replacement_text == expected_text:
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement_text must change the payload"
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
        validate_canonical_text(expected_text)
        validate_canonical_text(replacement_text)
    except ControlSyntaxError as error:
        raise LocaleSourceError(f"{source_id}:{message_id}: {error}") from error
    if _payload_structure(expected_text) != _payload_structure(replacement_text):
        raise LocaleSourceError(
            f"{source_id}:{message_id}: replacement must preserve controls, "
            "newlines, placeholders, and their placement"
        )

    return IndexedOverride(
        message_id=int(message_id, 16),
        expected_text=expected_text,
        replacement_text=replacement_text,
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

    sources: Dict[str, SourceOverrides] = {}
    for source_id, raw_source in sorted(raw_sources.items()):
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
        sha256=_sha256_bytes(data_bytes),
        byte_count=len(data_bytes),
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
        if message.text != override.expected_text:
            raise LocaleSourceError(
                f"{source.source_id}:0x{message.id:04X}: normalized source payload "
                "does not match override expected_text"
            )
        result.append(
            IndexedMessage(
                message.id,
                override.replacement_text,
                message.marker_line,
            )
        )
        applied.append(override)
    return tuple(result), tuple(applied)
