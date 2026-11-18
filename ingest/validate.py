"""Validate built data against the JSON schemas in ingest/schema.

Supports the subset of JSON Schema the schemas use: type, const, enum,
pattern, minLength, minimum, minItems, maxItems, required, properties,
additionalProperties, items, oneOf and local $ref.

Card files are TSV (see datatsv.py); each decoded row is checked against
index.schema.json or card.schema.json. The manifest and aliases stay JSON and
must each be at most 16 KB.

Usage: python3 ingest/validate.py <data-dir>
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datatsv

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema(name):
    """Load a schema file from ingest/schema by base name, e.g. 'card'."""
    with open(os.path.join(SCHEMA_DIR, name + ".schema.json"), encoding="utf-8") as f:
        return json.load(f)


def _resolve(root, ref):
    if not ref.startswith("#/"):
        raise ValueError("only local $ref is supported: " + ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _anchor_end(pattern):
    """Make a trailing $ match only at the very end, never before a final newline."""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def validate(value, schema, root=None, path="$"):
    """Return a list of error strings; empty means valid."""
    root = schema if root is None else root
    errors = []
    if "$ref" in schema:
        return validate(value, _resolve(root, schema["$ref"]), root, path)
    if "oneOf" in schema:
        matches = sum(1 for s in schema["oneOf"] if not validate(value, s, root, path))
        if matches != 1:
            errors.append("%s: matches %d of oneOf, expected 1" % (path, matches))
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append("%s: expected %r, got %r" % (path, schema["const"], value))
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r not in %r" % (path, value, schema["enum"]))
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            errors.append("%s: expected type %s, got %s" % (path, "/".join(types), type(value).__name__))
            return errors
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: shorter than %d" % (path, schema["minLength"]))
        if "pattern" in schema and not re.search(_anchor_end(schema["pattern"]), value):
            errors.append("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if _TYPES["number"](value) and "minimum" in schema and value < schema["minimum"]:
        errors.append("%s: %r below minimum %r" % (path, value, schema["minimum"]))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: fewer than %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: more than %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"], root, "%s[%d]" % (path, i)))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing %r" % (path, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            sub = "%s.%s" % (path, key)
            if key in props:
                errors.extend(validate(item, props[key], root, sub))
            elif extra is False:
                errors.append("%s: unexpected property" % sub)
            elif isinstance(extra, dict):
                errors.extend(validate(item, extra, root, sub))
    return errors


JSON_LIMIT = 16 * 1024


def _check_aliases(data, path):
    errors = []
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("aliases"), dict):
        return ["%s: expected {schemaVersion: 1, aliases: {...}}" % path]
    for src, dst in data["aliases"].items():
        if not isinstance(src, str) or not isinstance(dst, str) or not src or not dst:
            errors.append("%s: bad alias %r -> %r" % (path, src, dst))
    return errors


def read_rows(path, schema):
    """Decode one TSV file with a row schema. Returns (rows, errors)."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        rows = datatsv.decode(text, schema)
    except (OSError, ValueError, datatsv.TsvError) as e:
        return [], ["%s: %s" % (path, e)]
    errors = []
    for i, row in enumerate(rows):
        errors.extend(validate(row, schema, path="%s:%d" % (path, i + 2)))
    if not rows:
        errors.append("%s: no cards" % path)
    return rows, errors


def validate_tree(data_dir):
    """Validate a built data directory; return a list of error strings."""
    errors = []
    schemas = {name: load_schema(name) for name in ("manifest", "index", "card")}

    def read_json(rel):
        path = os.path.join(data_dir, rel)
        size = os.path.getsize(path)
        if size > JSON_LIMIT:
            raise ValueError("%d bytes, limit %d" % (size, JSON_LIMIT))
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    try:
        manifest = read_json("manifest.json")
    except (OSError, ValueError) as e:
        return ["manifest.json: %s" % e]
    errors.extend(validate(manifest, schemas["manifest"], path="manifest.json"))
    if errors:
        return errors
    set_keys = [s["key"] for s in manifest["sets"]]
    en_ids = None
    for lang in manifest["languages"]:
        code = lang["code"]
        rel = manifest["files"]["index"].replace("{lang}", code)
        rows, errs = read_rows(os.path.join(data_dir, rel), schemas["index"])
        errors.extend(errs)
        index_ids = [r.get("id") for r in rows]
        if len(index_ids) != lang["cards"]:
            errors.append("%s: %d cards, manifest says %d" % (rel, len(index_ids), lang["cards"]))
        if len(set(index_ids)) != len(index_ids):
            errors.append("%s: duplicate card ids" % rel)
        if code == "en":
            en_ids = set(index_ids)
        elif en_ids is not None and not set(index_ids) <= en_ids:
            errors.append("%s: %d card ids not in en" % (rel, len(set(index_ids) - en_ids)))
        set_ids = []
        for key in set_keys:
            rel_set = manifest["files"]["sets"].replace("{lang}", code).replace("{set}", key)
            if not os.path.exists(os.path.join(data_dir, rel_set)):
                continue
            set_rows, errs = read_rows(os.path.join(data_dir, rel_set), schemas["card"])
            errors.extend(errs)
            for r in set_rows:
                if r.get("set") != key:
                    errors.append("%s: card %s has set %r" % (rel_set, r.get("id"), r.get("set")))
            set_ids.extend(r.get("id") for r in set_rows)
        if sorted(set_ids) != sorted(index_ids):
            errors.append("sets/%s: card ids differ from %s" % (code, rel))
    rel = manifest["files"]["aliases"]
    try:
        errors.extend(_check_aliases(read_json(rel), rel))
    except (OSError, ValueError) as e:
        errors.append("%s: %s" % (rel, e))
    return errors


def main(argv):
    if len(argv) != 2:
        print("usage: validate.py <data-dir>", file=sys.stderr)
        return 2
    errors = validate_tree(argv[1])
    for e in errors[:200]:
        print(e, file=sys.stderr)
    if errors:
        print("validation failed: %d error(s)" % len(errors), file=sys.stderr)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
