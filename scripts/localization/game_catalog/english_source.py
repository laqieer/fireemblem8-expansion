"""Parser and encoder for the committed FE8U English message sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

from scripts.texttools import textprocess

from .control_streams import ControlStreamError, tokenize_payload
from .model import EnglishSourceEntry, GameCatalogError


_DEFINITION_RE = re.compile(r"^\[([^\]]+)\]\s*=\s*(.+?)\s*$")
_TOKEN_RE = re.compile(r"\[([^\[\]\r\n]+)\]")

_PRINTABLE_REPLACEMENTS = {
    "DashedLine": b"-",
    "TAB": "\u3000".encode("utf-8"),
    "LQuote": b'"',
    "RQuote": b'"',
    "AccentedE": b"e",
}


def _strict_utf8_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise GameCatalogError(f"{path}: file is not strict UTF-8") from error


def _strip_comments(text: str, *, source_name: str) -> str:
    output: List[str] = []
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            if newline < 0:
                break
            output.append("\n")
            index = newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise GameCatalogError(f"{source_name}: unterminated block comment")
            output.extend("\n" for char in text[index:end + 2] if char == "\n")
            index = end + 2
            continue
        output.append(text[index])
        index += 1
    return "".join(output)


def load_english_definitions(path: Path) -> Dict[str, Tuple[int, ...]]:
    text = _strip_comments(_strict_utf8_text(path), source_name=str(path))
    definitions: Dict[str, Tuple[int, ...]] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = _DEFINITION_RE.match(line.strip())
        if match is None:
            continue
        name = match.group(1)
        if name in definitions:
            raise GameCatalogError(
                f"{path}:{line_number}: duplicate text definition {name!r}"
            )
        try:
            values = tuple(
                int(value.strip(), 0) for value in match.group(2).split(",")
            )
        except ValueError as error:
            raise GameCatalogError(
                f"{path}:{line_number}: invalid byte list for {name!r}"
            ) from error
        if not values or any(value < 0 or value > 0xFF for value in values):
            raise GameCatalogError(
                f"{path}:{line_number}: {name!r} must define one or more bytes"
            )
        definitions[name] = values

    if definitions.get("X") != (0,):
        raise GameCatalogError(f"{path}: [X] must be the standalone NUL byte")
    return definitions


def _encode_named_token(
    name: str,
    definitions: Mapping[str, Tuple[int, ...]],
    *,
    source_name: str,
) -> bytes:
    replacement = _PRINTABLE_REPLACEMENTS.get(name)
    if replacement is not None:
        return replacement
    if name not in definitions:
        raise GameCatalogError(f"{source_name}: unknown text token [{name}]")

    values = definitions[name]
    if any(value >= 0x7F for value in values):
        is_control = values[0] == 0x80
        is_face_payload = name.startswith("FID_")
        if not is_control and not is_face_payload:
            raise GameCatalogError(
                f"{source_name}: non-UTF-8 printable token [{name}] has no "
                "modern replacement"
            )
    return bytes(values)


def encode_english_source_text(
    text: str,
    definitions: Mapping[str, Tuple[int, ...]],
    *,
    source_name: str,
) -> bytes:
    return _encode_preprocessed_english_text(
        _strip_comments(text, source_name=source_name),
        definitions,
        source_name=source_name,
    )


def _encode_preprocessed_english_text(
    text: str,
    definitions: Mapping[str, Tuple[int, ...]],
    *,
    source_name: str,
) -> bytes:
    text = text.replace("\r", "").replace("\n", "")
    payload = bytearray()
    position = 0
    for match in _TOKEN_RE.finditer(text):
        literal = text[position:match.start()]
        if "[" in literal or "]" in literal:
            raise GameCatalogError(f"{source_name}: malformed text token")
        if "\x00" in literal:
            raise GameCatalogError(f"{source_name}: literal text contains an embedded NUL")
        payload.extend(literal.encode("utf-8"))
        payload.extend(
            _encode_named_token(
                match.group(1), definitions, source_name=source_name
            )
        )
        position = match.end()

    literal = text[position:]
    if "[" in literal or "]" in literal:
        raise GameCatalogError(f"{source_name}: malformed text token")
    if "\x00" in literal:
        raise GameCatalogError(f"{source_name}: literal text contains an embedded NUL")
    payload.extend(literal.encode("utf-8"))

    data = bytes(payload)
    if not data.endswith(b"\x00"):
        raise GameCatalogError(f"{source_name}: message must end with [X]")
    try:
        tokenize_payload(data, source_name=source_name)
    except ControlStreamError as error:
        raise GameCatalogError(str(error)) from error
    return data


def load_english_source_entries(
    texts_path: Path,
    definitions_path: Path,
    *,
    target_count: int,
) -> Tuple[EnglishSourceEntry, ...]:
    definitions = load_english_definitions(definitions_path)
    try:
        parsed = textprocess.order_messages(
            textprocess.parse_source_messages(texts_path, strict_directives=True),
            str(texts_path),
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise GameCatalogError(f"{texts_path}: {error}") from error

    by_id: Dict[int, EnglishSourceEntry] = {}
    for message in parsed:
        source_name = (
            f"{message.file_path}:{message.line_number}: "
            f"message 0x{message.idx:04X}"
        )
        by_id[message.idx] = EnglishSourceEntry(
            target_id=message.idx,
            definition=message.definition,
            source_text=message.source_text,
            message_text=message.text,
            encoded_bytes=_encode_preprocessed_english_text(
                message.text, definitions, source_name=source_name
            ),
        )

    expected = set(range(target_count))
    actual = set(by_id)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(
                "missing " + ", ".join(f"0x{value:04X}" for value in missing[:8])
            )
        if extra:
            detail.append(
                "extra " + ", ".join(f"0x{value:04X}" for value in extra[:8])
            )
        raise GameCatalogError(
            f"{texts_path}: English source IDs do not match target range: "
            + "; ".join(detail)
        )

    return tuple(by_id[target_id] for target_id in range(target_count))
