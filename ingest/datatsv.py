"""Read and write the tab-separated card files.

Format: a header line of column names, then one card per line, columns
separated by a tab, lines ending in a newline. In values, backslash, tab,
newline and carriage return are written as \\\\, \\t, \\n and \\r. An empty
value means missing. Booleans are 1 or 0. Lists are joined with |, and list
items never contain |. Column types come from the row schema.
"""

import re

_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}
_INT_RE = re.compile(r"-?[0-9]+")


class TsvError(Exception):
    pass


def escape(value):
    return "".join(_ESCAPES.get(ch, ch) for ch in value)


def unescape(value):
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            nxt = value[i + 1] if i + 1 < len(value) else ""
            if nxt not in _UNESCAPES:
                raise TsvError("bad escape \\%s" % nxt)
            out.append(_UNESCAPES[nxt])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def encode_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str) or "|" in item or item == "":
                raise TsvError("list item %r must be a non-empty string without |" % (item,))
        return "|".join(escape(item) for item in value)
    if isinstance(value, str):
        return escape(value)
    raise TsvError("cannot encode %r" % (value,))


def encode(rows, columns):
    """Return TSV text for a list of dicts."""
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join(encode_value(row.get(c)) for c in columns))
    return "\n".join(lines) + "\n"


def _types(prop):
    t = prop.get("type", [])
    return t if isinstance(t, list) else [t]


def decode_value(text, prop):
    types = _types(prop)
    if "array" in types:
        return [unescape(item) for item in text.split("|")] if text else []
    if text == "":
        return None
    if "integer" in types:
        if not _INT_RE.fullmatch(text):
            raise TsvError("%r is not an integer" % text)
        return int(text)
    if "boolean" in types:
        if text not in ("0", "1"):
            raise TsvError("%r is not 0 or 1" % text)
        return text == "1"
    return unescape(text)


def decode(text, schema):
    """Parse TSV text into a list of dicts, typed by the row schema.

    The schema's "columns" list gives the required header. Raises TsvError.
    """
    columns = schema["columns"]
    props = schema["properties"]
    if "\r" in text:
        raise TsvError("raw carriage return in file")
    if not text.endswith("\n"):
        raise TsvError("file does not end with a newline")
    lines = text[:-1].split("\n")
    header = lines[0].split("\t")
    if header != columns:
        raise TsvError("header %r, expected %r" % (header, columns))
    rows = []
    for num, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if len(fields) != len(columns):
            raise TsvError("line %d: %d fields, expected %d" % (num, len(fields), len(columns)))
        row = {}
        for col, field in zip(columns, fields):
            try:
                value = decode_value(field, props[col])
            except TsvError as e:
                raise TsvError("line %d, %s: %s" % (num, col, e))
            if value is not None:
                row[col] = value
        rows.append(row)
    return rows
