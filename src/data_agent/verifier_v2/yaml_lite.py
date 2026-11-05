"""The YAML subset dbt project files use, read without a YAML dependency (the repo reads YML by
line scan elsewhere, `verifier_tools.yml_columns`): block mappings, block sequences, flow lists
and flow mappings (also spanning lines), block scalars (`|`, `>`, with `-`/`+` chomping), quoted
and plain scalars (also continued on more-indented lines), comments. Anchors, aliases, tags,
explicit block indentation indicators and tab indentation are not supported: `load` raises
`YamlLiteError` for them and for text that does not parse, so a caller can say the file could not
be read instead of treating it as declaring nothing.
"""

import re
from dataclasses import dataclass
from typing import Any

# A comment starts at a `#` that begins the line or follows whitespace, outside quotes.
KEY_RE = re.compile(
    r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s:'"\[\]{}&*!|>][^:]*?)\s*:(?:\s+|$)"""
)
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
BLOCK_INDICATOR_RE = re.compile(r"^([|>])([-+]?)$")


class YamlLiteError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    indent: int
    text: str  # stripped of indent, comment and trailing space
    number: int  # 1-based source line, for messages
    block: str | None = None  # the block scalar's text when `text` ends with `|` or `>`


def load(text: str) -> Any:
    """The document as nested dicts, lists and scalars; `{}` when it is empty. Raises
    YamlLiteError when the text uses YAML outside the subset or does not parse."""
    try:
        lines = _lines(text)
        if not lines:
            return {}
        value, index = _block(lines, 0, lines[0].indent)
        if index != len(lines):
            raise YamlLiteError(f"line {lines[index].number}: unexpected content")
        return value
    except IndexError as exc:
        raise YamlLiteError("unexpected end of document") from exc


def _lines(text: str) -> list[Line]:
    raw_lines = text.splitlines()
    lines: list[Line] = []
    index = 0
    while index < len(raw_lines):
        raw = raw_lines[index]
        number = index + 1
        index += 1
        if raw.strip() in ("---", "..."):
            continue
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        if "\t" in stripped[: len(stripped) - len(stripped.lstrip())]:
            raise YamlLiteError(f"line {number}: tab indentation")
        indent = len(stripped) - len(stripped.lstrip())
        text_ = stripped.lstrip()
        inner_indent, value = _innermost_value(indent, text_)
        if value is not None and BLOCK_INDICATOR_RE.match(value):
            block, index = _block_scalar(raw_lines, index, inner_indent, value)
            lines.append(Line(indent, text_, number, block))
            continue
        if value is not None and value[0] in "[{" and _flow_depth(value) > 0:
            # A flow collection continued on the next lines, up to the closing bracket.
            while _flow_depth(value) > 0 and index < len(raw_lines):
                more = _strip_comment(raw_lines[index]).strip()
                index += 1
                if more:
                    value += " " + more
                    text_ += " " + more
            if _flow_depth(value) > 0:
                raise YamlLiteError(f"line {number}: unclosed flow collection")
        lines.append(Line(indent, text_, number))
    return lines


def _innermost_value(indent: int, text: str) -> tuple[int, str | None]:
    """(indent of the innermost key or item, its inline value) of a line such as `- a: b` or
    `key: |`; the value is None for a line that opens a block (`key:` alone, `-` alone)."""
    tail = text
    inner = indent
    while True:
        if _is_item(tail):
            rest = tail[1:].lstrip()
            inner += len(tail) - len(rest)
            tail = rest
            if not tail:
                return inner, None
            continue
        match = KEY_RE.match(tail)
        if match:
            tail = tail[match.end() :].strip()
            if not tail:
                return inner, None
            continue
        return inner, tail


def _block_scalar(
    raw_lines: list[str], index: int, parent_indent: int, indicator: str
) -> tuple[str, int]:
    """The text of a `|` or `>` block scalar whose content lines follow `index`, and the index of
    the first line after it. Content lines are more indented than the key (blank lines are
    content too); comments inside are text."""
    content: list[str] = []
    while index < len(raw_lines):
        raw = raw_lines[index]
        if raw.strip() and len(raw) - len(raw.lstrip(" ")) <= parent_indent:
            break
        content.append(raw)
        index += 1
    block_indent = next(
        (len(line) - len(line.lstrip(" ")) for line in content if line.strip()), parent_indent + 1
    )
    body = [line[block_indent:] if line.strip() else "" for line in content]
    style, chomp = indicator[0], indicator[1:]
    text = "\n".join(body) if style == "|" else _fold(body)
    stripped = text.rstrip("\n")
    if chomp == "-":
        return stripped, index
    if chomp == "+":
        return text + "\n" if content else text, index
    return stripped + "\n" if stripped else "", index


def _fold(body: list[str]) -> str:
    """`>`: lines of a paragraph join with a space; a blank line is a line break."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in body:
        if line.strip():
            current.append(line.strip())
        else:
            paragraphs.append(" ".join(current))
            current = []
    paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


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


def _flow_depth(text: str) -> int:
    """Bracket depth at the end of `text`, quotes respected; 0 when the collection is closed."""
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
    return depth


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
            raise YamlLiteError(f"line {line.number}: not a key: {line.text!r}")
        key = _scalar(match.group(1), line)
        rest = line.text[match.end() :].strip()
        index += 1
        if rest:
            result[str(key)], index = _inline_value(lines, index, indent, rest, line)
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
        raise YamlLiteError(f"line {lines[index].number}: bad indentation: {lines[index].text!r}")
    return result, index


def _sequence(lines: list[Line], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not _is_item(line.text):
            break
        rest = line.text[1:].strip()
        if _is_item(rest):
            raise YamlLiteError(
                f"line {line.number}: nested block sequence on one line: {line.text!r}"
            )
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
            lines[index] = Line(item_indent, rest, line.number, line.block)
            value, index = _mapping(lines, index, item_indent)
            result.append(value)
        else:
            index += 1
            value, index = _inline_value(lines, index, indent, rest, line)
            result.append(value)
    return result, index


def _inline_value(
    lines: list[Line], index: int, indent: int, rest: str, line: Line
) -> tuple[Any, int]:
    """The value written on the key's or item's own line: a block scalar, or a scalar that may
    continue on more-indented lines (a plain multi-line scalar)."""
    if BLOCK_INDICATOR_RE.match(rest):
        if line.block is None:
            raise YamlLiteError(f"line {line.number}: block scalar without content")
        return line.block, index
    while index < len(lines) and lines[index].indent > indent:
        rest += " " + lines[index].text
        index += 1
    return _scalar(rest, line), index


def _scalar(text: str, line: Line | None = None) -> Any:
    text = text.strip()
    where = f"line {line.number}: " if line else ""
    if text.startswith(("[", "{")):
        try:
            return _Flow(text).parse()
        except YamlLiteError as exc:
            raise YamlLiteError(f"{where}{exc}") from None
    if text.startswith(("&", "*", "!", "|", ">")):
        raise YamlLiteError(f"{where}unsupported YAML (anchor, alias, tag or indicator): {text!r}")
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


class _Flow:
    """A flow collection (`[a, {b: c}]`) read left to right."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def parse(self) -> Any:
        value = self._value(closers="")
        self._skip()
        if self.pos != len(self.text):
            raise YamlLiteError(f"trailing text in flow collection: {self.text[self.pos :]!r}")
        return value

    def _skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _value(self, closers: str) -> Any:
        self._skip()
        char = self._peek()
        if char == "[":
            return self._list()
        if char == "{":
            return self._mapping()
        return _scalar(self._plain(closers))

    def _list(self) -> list[Any]:
        self.pos += 1
        items: list[Any] = []
        while True:
            self._skip()
            if self._peek() == "]":
                self.pos += 1
                return items
            items.append(self._value(closers=",]"))
            self._skip()
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "]":
                raise YamlLiteError("unclosed flow list")

    def _mapping(self) -> dict[str, Any]:
        self.pos += 1
        result: dict[str, Any] = {}
        while True:
            self._skip()
            if self._peek() == "}":
                self.pos += 1
                return result
            key = _scalar(self._plain(closers=":,}"))
            self._skip()
            if self._peek() == ":":
                self.pos += 1
                result[str(key)] = self._value(closers=",}")
            else:
                result[str(key)] = None
            self._skip()
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "}":
                raise YamlLiteError("unclosed flow mapping")

    def _plain(self, closers: str) -> str:
        """A scalar's text up to one of `closers` outside quotes (a `:` only when followed by
        a space, a closer or the end)."""
        start = self.pos
        quote: str | None = None
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'" and self.pos == start:
                quote = char
            elif char in closers and (
                char != ":" or self.pos + 1 >= len(self.text) or self.text[self.pos + 1] in " ,}]"
            ):
                break
            self.pos += 1
        if quote:
            raise YamlLiteError("unclosed quote in flow collection")
        return self.text[start : self.pos].strip()
