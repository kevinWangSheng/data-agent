"""The YAML subset dbt project files use, read without a YAML dependency (the repo reads YML by
line scan elsewhere, `verifier_tools.yml_columns`): block mappings, block sequences, flow lists of
scalars, quoted and plain scalars, comments. Anchors, multi-document files, flow mappings with
content and block scalars (`|`, `>`) are not supported; a file that uses them, or that does not
parse, reads as `{}` so the caller treats it as declaring nothing.
"""

import re
from dataclasses import dataclass
from typing import Any

# A comment starts at a `#` that begins the line or follows whitespace, outside quotes.
COMMENT_RE = re.compile(r"""(^|\s)#.*$""")
KEY_RE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s:'"\[\]{}][^:]*?)\s*:(?:\s+|$)""")
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


class YamlLiteError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    indent: int
    text: str  # stripped of indent, comment and trailing space


def load(text: str) -> Any:
    """The document as nested dicts, lists and scalars; `{}` when it is empty or unsupported."""
    try:
        lines = _lines(text)
        if not lines:
            return {}
        value, index = _block(lines, 0, lines[0].indent)
        if index != len(lines):
            raise YamlLiteError(f"unexpected content at line {index}")
        return value
    except (YamlLiteError, IndexError):
        return {}


def _lines(text: str) -> list[Line]:
    lines: list[Line] = []
    for raw in text.splitlines():
        if raw.strip() in ("---", "..."):
            continue
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        if "\t" in stripped[: len(stripped) - len(stripped.lstrip())]:
            raise YamlLiteError("tab indentation")
        lines.append(Line(len(stripped) - len(stripped.lstrip()), stripped.lstrip()))
    return lines


def _strip_comment(raw: str) -> str:
    quote: str | None = None
    for i, char in enumerate(raw):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i]
    return raw


def _block(lines: list[Line], index: int, indent: int) -> tuple[Any, int]:
    if _is_item(lines[index].text):
        return _sequence(lines, index, indent)
    return _mapping(lines, index, indent)


def _is_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _mapping(lines: list[Line], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if _is_item(line.text):
            break
        match = KEY_RE.match(line.text)
        if not match:
            raise YamlLiteError(f"not a key: {line.text!r}")
        key = _scalar(match.group(1))
        rest = line.text[match.end() :].strip()
        index += 1
        if rest:
            result[str(key)] = _scalar(rest)
        elif index < len(lines) and (
            lines[index].indent > indent
            # YAML lets a key's sequence start at the key's own indent.
            or (lines[index].indent == indent and _is_item(lines[index].text))
        ):
            value, index = _block(lines, index, lines[index].indent)
            result[str(key)] = value
        else:
            result[str(key)] = None
    if index < len(lines) and lines[index].indent > indent:
        raise YamlLiteError(f"bad indentation: {lines[index].text!r}")
    return result, index


def _sequence(lines: list[Line], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not _is_item(line.text):
            break
        rest = line.text[1:].strip()
        if not rest:
            index += 1
            if index < len(lines) and lines[index].indent > indent:
                value, index = _block(lines, index, lines[index].indent)
                result.append(value)
            else:
                result.append(None)
        elif KEY_RE.match(rest) and not rest.startswith(("[", "{", '"', "'")):
            # `- name: x` starts a mapping whose keys line up with `name`.
            item_indent = indent + (len(line.text) - len(rest))
            lines[index] = Line(item_indent, rest)
            value, index = _mapping(lines, index, item_indent)
            result.append(value)
        else:
            result.append(_scalar(rest))
            index += 1
    return result, index


def _scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(item) for item in _split_flow(inner)] if inner else []
    if text == "{}":
        return {}
    if text.startswith(("{", "[", "&", "*", "|", ">")):
        raise YamlLiteError(f"unsupported scalar: {text!r}")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        return body.replace("''", "'") if text[0] == "'" else body.replace('\\"', '"')
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", ""):
        return None
    if INT_RE.match(text):
        return int(text)
    if FLOAT_RE.match(text):
        return float(text)
    return text


def _split_flow(inner: str) -> list[str]:
    items: list[str] = []
    current = ""
    quote: str | None = None
    for char in inner:
        if quote:
            current += char
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            current += char
        elif char == ",":
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)
    return [item for item in items if item.strip()]
