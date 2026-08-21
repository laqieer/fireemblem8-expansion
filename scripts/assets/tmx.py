"""Fail-closed conversion for the documented tmx-safe-v1 map subset."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree


TMX_VERSION = "1.10"
TILESET_NAME = "fe8-metatiles-16px-4096"
TILESET_TILE_COUNT = 4096
TMX_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>'
TILED_VERSION_RE = re.compile(r"1\.10\.[0-9]+")
DECIMAL_RE = re.compile(r"[0-9]+")
ASCII_WHITESPACE = " \t\r\n"
MAX_TMX_BYTES = 512 * 1024
MAX_XML_MARKUP_TOKENS = 16
MAX_GID_DECIMAL_DIGITS = 10


class TmxError(ValueError):
    """A source-level TMX contract violation."""


def _exact_attributes(element, expected, context):
    actual = set(element.attrib)
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    if missing:
        raise TmxError("{} is missing required attribute '{}'".format(context, missing[0]))
    if extra:
        raise TmxError("{} has unsupported attribute '{}'".format(context, extra[0]))
    for key, value in expected.items():
        if value is not None and element.attrib[key] != value:
            raise TmxError(
                "{} attribute '{}' must be '{}', not '{}'".format(
                    context, key, value, element.attrib[key]
                )
            )


def _positive_dimension(value, name):
    if not DECIMAL_RE.fullmatch(value):
        raise TmxError("{} must be an ASCII decimal integer".format(name))
    parsed = int(value)
    if not 1 <= parsed <= 255:
        raise TmxError("{} must be in 1..255".format(name))
    return parsed


def _ascii_strip(value):
    return value.strip(ASCII_WHITESPACE)


def _require_whitespace(value, context):
    if value is not None and _ascii_strip(value):
        raise TmxError("{} must not contain text".format(context))


def _parse_csv(text, expected_cells):
    if text is None:
        raise TmxError("layer data must contain CSV text")
    if any(character not in "0123456789," + ASCII_WHITESPACE for character in text):
        raise TmxError("layer data must contain only ASCII decimal CSV tokens")
    parts = _ascii_strip(text).split(",")
    if len(parts) != expected_cells:
        raise TmxError(
            "CSV contains {} cells; expected {}".format(len(parts), expected_cells)
        )
    values = []
    for index, part in enumerate(parts):
        token = _ascii_strip(part)
        if not DECIMAL_RE.fullmatch(token):
            raise TmxError("CSV cell {} must be an ASCII decimal GID".format(index))
        if len(token) > MAX_GID_DECIMAL_DIGITS:
            raise TmxError(
                "CSV cell {} exceeds the {}-digit GID limit".format(
                    index, MAX_GID_DECIMAL_DIGITS
                )
            )
        gid = int(token)
        if gid & 0xF0000000:
            raise TmxError("CSV cell {} uses unsupported Tiled flip/rotation flags".format(index))
        if not 1 <= gid <= TILESET_TILE_COUNT:
            raise TmxError(
                "CSV cell {} GID {} must be in 1..{}".format(index, gid, TILESET_TILE_COUNT)
            )
        values.append(gid - 1)
    return values


def parse_tmx(path):
    """Return the exact canonical map dimensions and zero-based FE8 metatile IDs."""

    with open(path, "rb") as handle:
        raw = handle.read(MAX_TMX_BYTES + 1)
    if len(raw) > MAX_TMX_BYTES:
        raise TmxError("TMX exceeds the {}-byte source limit".format(MAX_TMX_BYTES))
    if not raw.startswith(TMX_XML_DECLARATION):
        raise TmxError('TMX must begin with <?xml version="1.0" encoding="UTF-8"?>')
    if b"<!" in raw or b"&" in raw:
        raise TmxError("TMX DTD/entity syntax is unsupported")
    if raw.count(b"<") > MAX_XML_MARKUP_TOKENS:
        raise TmxError("TMX contains too many XML markup constructs")
    try:
        text = raw.decode("utf-8")
        root = ElementTree.fromstring(text)
    except (UnicodeDecodeError, ElementTree.ParseError) as exc:
        raise TmxError("malformed UTF-8 XML: {}".format(exc))

    _exact_attributes(
        root,
        {
            "version": TMX_VERSION,
            "tiledversion": None,
            "orientation": "orthogonal",
            "renderorder": "right-down",
            "width": None,
            "height": None,
            "tilewidth": "16",
            "tileheight": "16",
            "infinite": "0",
        },
        "map",
    )
    if root.tag != "map":
        raise TmxError("root element must be map")
    if not TILED_VERSION_RE.fullmatch(root.attrib["tiledversion"]):
        raise TmxError("map tiledversion must be a Tiled 1.10.x version")
    width = _positive_dimension(root.attrib["width"], "map width")
    height = _positive_dimension(root.attrib["height"], "map height")

    children = list(root)
    if [child.tag for child in children] != ["tileset", "layer"]:
        raise TmxError("map must contain exactly one inline tileset followed by one layer")
    tileset, layer = children
    _require_whitespace(root.text, "map")
    _require_whitespace(tileset.tail, "map")
    _require_whitespace(layer.tail, "map")
    _exact_attributes(
        tileset,
        {
            "firstgid": "1",
            "name": TILESET_NAME,
            "tilewidth": "16",
            "tileheight": "16",
            "tilecount": str(TILESET_TILE_COUNT),
            "columns": "64",
        },
        "tileset",
    )
    if list(tileset):
        raise TmxError("tileset children, external sources, images, transforms, and properties are unsupported")
    _require_whitespace(tileset.text, "tileset")

    _exact_attributes(
        layer,
        {
            "id": "1",
            "name": "Main",
            "width": str(width),
            "height": str(height),
        },
        "layer",
    )
    layer_children = list(layer)
    if len(layer_children) != 1 or layer_children[0].tag != "data":
        raise TmxError("layer must contain exactly one CSV data element")
    data = layer_children[0]
    _require_whitespace(layer.text, "layer")
    _require_whitespace(data.tail, "layer")
    _exact_attributes(data, {"encoding": "csv"}, "layer data")
    if list(data):
        raise TmxError("layer data chunks and child elements are unsupported")
    values = _parse_csv(data.text, width * height)
    return width, height, values


def render_mar(values):
    output = bytearray()
    for value in values:
        output.extend((value << 3).to_bytes(2, byteorder="little"))
    return bytes(output)


def render_metadata(symbol, width, height):
    return (json.dumps(
        {"id": symbol, "width": width, "height": height},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
