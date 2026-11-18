#!/usr/bin/env python3
"""Fail on any file in a public tree that breaks the publishing rules.

Usage: python3 .github/check_public.py <dir>

Each hit prints as path:line: [rule] text and the exit status is 1.
"""

import io
import json
import os
import re
import sys
import tokenize
import unicodedata

I = re.I

WORDS = [
    ("guard", re.compile(r"p[l]ans?\s*#?\s*\d+|c[l]aude|(?<![A-Za-z0-9])A[I]s?(?![A-Za-z0-9])|\bA\.[I]\.|c[o]ding assistant|T[O]DO\(plan", I)),
    ("tooling", re.compile(
        r"a[n]thropic|o[p]enai|c[h]atgpt|c[o]pilot|g[e]mini|c[o]dex|c[o]degraph|\bL[L]Ms?\b|(?<![-\w])a[g]ents?\b"
        r"|\bs[u]b-?a[g]ents?\b|\ba[g]entic\b|r[o]admap|\bS[t]ep \d+\b|i[n]k-tabletop-dev|\bw[i]ndsurf\b|\ba[i]der\b"
        r"|\bc[l]ine\b|l[a]nguage models?|c[h]atbots?|c[o]-authored", I)),
    ("internal path", re.compile(
        r"d[o]cs/research|C[H]ANGELOG\.internal|R[O]ADMAP\.md|C[L]AUDE\.md|A[G]ENTS\.md|G[E]MINI\.md|t[o]ols/dev\b"
        r"|(^|[^\w.])\.(c[l]aude|a[g]ents|g[r]ok|c[o]dex|c[u]rsor|c[o]degraph|r[o]admap)\b", I)),
    ("tooling", re.compile(r"\bG[P]T\b|\bO[p]us\b|\bS[o]nnet\b|\bH[a]iku\b|\bC[u]rsor\b")),
]

class FieldWord:
    def __init__(self, pattern):
        self.rx = re.compile(pattern, I)

    def search(self, text):
        for m in self.rx.finditer(text):
            before, after = text[:m.start()], text[m.end():]
            quoted = before[-1:] in ("\x22", "\x27", "\x60") and after[:1] == before[-1:]
            if quoted and re.match(r"\s*([:,|\])}]|$)", after[1:]):
                continue
            if re.search(r"(^|[^\w.])[a-z_]\w*\.$", before) and not re.match(r"\w", after):
                continue
            if re.search(r"(^|[{(,])\s*$", before) and re.match(r"\s*=", after):
                continue
            return m
        return None


TEXT_RULES = WORDS + [
    ("style 10", re.compile(r"<m[a]rk[\s>]", I)),
    ("style 11", re.compile(
        r"n[o]t just .{1,40}\bit.?s\b|\b(s[a]y goodbye to|m[e]et your new|s[u]percharge|u[n]lock the power of"
        r"|i[n] seconds,? not)\b|\b(b[l]azing[- ]fast|e[f]fortless(ly)?|s[e]amless(ly)?|g[a]me[- ]?changer"
        r"|n[e]xt[- ]level)\b", I)),
    ("style 12", re.compile(
        "[\u00a9\u00ae\u203c\u2049\u2122\u2139\u2194-\u2199\u21a9\u21aa\u231a\u231b\u2328\u23cf\u23e9-\u23f3"
        "\u23f8-\u23fa\u24c2\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2600-\u27bf\u2934\u2935\u2b05-\u2b07\u2b1b"
        "\u2b1c\u2b50\u2b55\u3030\u303d\u3297\u3299\U0001f000-\U0001faff]")),
    ("style 23", re.compile("[\u2554\u2557\u255a\u255d\u2551\u2550\u2593\u2592\u2591]")),
    ("wording 3", re.compile(r", (h[i]ghlighting|e[n]suring|r[e]flecting|s[h]owcasing|f[o]stering)\b", I)),
    ("wording 5", re.compile(r"\b(e[x]perts believe|i[n]dustry reports|s[o]me critics)\b", I)),
    ("wording 7", re.compile(
        r"\b(a[d]ditionally|c[r]ucial|d[e]lve|e[n]during|e[n]hance[sd]?|f[o]stering|g[a]rner|i[n]terplay"
        r"|i[n]tricate|p[i]votal|s[h]owcas\w*|t[a]pestry|t[e]stament|u[n]derscor\w*|v[i]brant)\b", I)),
    ("wording 7", FieldWord(r"\bl[a]ndscape\b")),
    ("wording 8", re.compile(r"\b(s[e]rves as|s[t]ands as|b[o]asts)\b", I)),
    ("wording 9", re.compile(r"\bn[o]t just\b.{1,60}\bbut\b", I)),
    ("wording 13", re.compile("[\u2014\u2013]")),
    ("wording 19", re.compile("[\u201c\u201d\u2018\u2019]")),
    ("wording 20", re.compile(
        r"i h[o]pe this helps|l[e]t me know if|\bc[e]rtainly!|o[f] course!|s[m]oking gun|g[r]eat question"
        r"|a[b]solutely right", I)),
    ("wording 23", re.compile(
        r"i[n] order to|d[u]e to the fact|i[t] is important to note|c[o]uld potentially|t[h]e future looks bright", I)),
    ("wording 26", re.compile(r"\b(s[u]bstrate|n[o]rth star|f[l]ywheel|p[a]radigm|b[e]drock|n[e]xus|l[o]cus|m[o]dality)\b", I)),
    ("wording 31", re.compile(r"\b(u[t]iliz\w*|l[e]verag\w*|f[a]cilitat\w*|n[u]merous|i[n] the event that)\b", I)),
]

DENY_NAMES = re.compile(
    r"C[L]AUDE\.md|A[G]ENTS\.md|G[E]MINI\.md|R[O]ADMAP\.md|C[H]ANGELOG\.internal\.md|t[e]sts", I)
DENY_PREFIXES = ("d" + "ocs/research", "t" + "ools/dev")
ASSET_EXT = {".png", ".jpg", ".jpeg", ".webp", ".txt", ".json", ".obj", ".ogg"}
TEXT_EXT = {".lua", ".ttslua", ".xml", ".html", ".svg", ".md", ".txt", ".json", ".yml", ".yaml", ".sh", ".bash",
            ".py", ".css", ".js", ".cfg", ".toml", ".ini", ".csv"}
CONFUSABLE = str.maketrans(
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0406\u0408\u0405"
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7\u03bf\u03b9",
    "ABEKMHOPCTXIJSaeopcyxijsABEZHIKMNOPTYXoi")
LONG_BRACKET = re.compile(r"\[(=*)\[")
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
HASH = re.compile(r"(^|\s)#")
SHELL_BANG = re.compile(r"#!\S*\b(env\s+)?(ba|da|k|z)?sh\b")
PY_BANG = re.compile(r"#!\S*\b(env\s+)?python")
SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")
FOLD_TABLE = "scripts/lib/fold.lua"
FOLD_DATA = ("ingest/fold.json", "ingest/normalize_vectors.json")
FOLD_EXEMPT = {"wording 13", "wording 19", "unicode"}


class Report:
    def __init__(self):
        self.hits = 0

    def hit(self, path, line, rule, text):
        text = re.sub(r"[\x00-\x1f\x7f]", "?", text.strip())[:200]
        print("%s:%d: [%s] %s" % (path, line, rule, text))
        self.hits += 1


def safe(path):
    return re.sub(r"[\x00-\x1f\x7f]", "?", path)


def clean(text):
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Mn", "Me"))
    return unicodedata.normalize("NFKC", text).translate(CONFUSABLE)


def format_char(text):
    for ch in text:
        if unicodedata.category(ch) == "Cf":
            return "U+%04X" % ord(ch)
    return None


def mixed_script(text):
    if text.isascii():
        return None
    for word in re.findall(r"[^\W\d_]{2,}", text):
        if word.isascii():
            continue
        seen = set()
        for ch in word:
            name = unicodedata.name(ch, "").split(" ")[0]
            if name in SCRIPTS:
                seen.add(name)
        if "LATIN" in seen and len(seen) > 1:
            return word
    return None


def lua_comment_lines(src):
    i, n, line = 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif src.startswith("--", i):
            yield line
            m = LONG_BRACKET.match(src, i + 2)
            if m:
                end = src.find("]" + m.group(1) + "]", m.end())
                end = n if end < 0 else end + len(m.group(1)) + 2
            else:
                end = src.find("\n", i)
                end = n if end < 0 else end
            line += src.count("\n", i, end)
            i = end
        elif c == "[" and LONG_BRACKET.match(src, i):
            m = LONG_BRACKET.match(src, i)
            end = src.find("]" + m.group(1) + "]", m.end())
            end = n if end < 0 else end + len(m.group(1)) + 2
            line += src.count("\n", i, end)
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            line += src.count("\n", i, min(j, n))
            i = j + 1
        else:
            i += 1


def py_comment_lines(src):
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and not (tok.start[0] == 1 and tok.string.startswith("#!")):
                yield tok.start[0]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        yield -1


def kind(rel, first_line):
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".lua", ".ttslua"):
        return "lua"
    if ext in (".xml", ".html", ".svg"):
        return "xml"
    if ext in (".yml", ".yaml"):
        return "yaml"
    if ext in (".sh", ".bash") or SHELL_BANG.match(first_line):
        return "shell"
    if ext == ".py" or PY_BANG.match(first_line):
        return "python"
    return None


def check_line(rep, rel, n, line, license_file):
    norm = normalize(line)
    plain = clean(line)
    for rule, rx in TEXT_RULES:
        if rule == "style 12" and license_file:
            continue
        if rule in FOLD_EXEMPT and rel == FOLD_TABLE:
            continue
        if rx.search(line) or (norm != line and rx.search(norm)):
            rep.hit(rel, n, rule, line)
    if rel == FOLD_TABLE:
        return
    check_unicode(rep, rel, n, line, plain)


def check_unicode(rep, rel, n, line, plain, label=""):
    code = format_char(line)
    if code:
        rep.hit(rel, n, "unicode", label + "invisible format character " + code)
    word = mixed_script(plain)
    if word:
        rep.hit(rel, n, "unicode", label + "mixed-script word " + word)


def check_comments(rep, rel, text, k, label="", base=0):
    lines = text.split("\n")
    if k == "lua":
        for n in lua_comment_lines(text):
            rep.hit(rel, base or n, "comment", label + lines[n - 1])
    elif k == "python":
        for n in py_comment_lines(text):
            rep.hit(rel, base or max(n, 0), "comment", label + (lines[n - 1] if n > 0 else "does not tokenize"))
    elif k == "xml":
        for n, line in enumerate(lines, 1):
            if "<!--" in line:
                rep.hit(rel, base or n, "comment", label + line)
    elif k == "yaml":
        for n, line in enumerate(lines, 1):
            if yaml_comment(line):
                rep.hit(rel, base or n, "comment", label + line)
    elif k == "shell":
        for n, line in enumerate(lines, 1):
            if n == 1 and line.startswith("#!"):
                continue
            if HASH.search(QUOTED.sub("", line)):
                rep.hit(rel, base or n, "comment", label + line)


def yaml_comment(line, start=0):
    quote, i, n, opened = None, start, len(line), 0
    while i < n:
        c = line[i]
        if quote == "'":
            if c == "'" and line[i + 1:i + 2] == "'":
                i += 1
            elif c == "'":
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif c in "'\"" and (i == 0 or line[i - 1] in " \t:-[{,=("):
            quote, opened = c, i
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return True
        i += 1
    if quote:
        return yaml_comment(line[:opened] + " " + line[opened + 1:], opened + 1)
    return False


class Pairs(list):
    pass


def json_strings(value, key=None):
    if isinstance(value, Pairs):
        for k, v in value:
            yield None, k
            yield from json_strings(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from json_strings(v, key)
    elif isinstance(value, str):
        yield key, value


def check_json(rep, rel, text, depth=0):
    try:
        data = json.loads(text, object_pairs_hook=Pairs)
    except ValueError:
        return
    escaped = "\\u" in text or depth > 0
    for key, s in json_strings(data):
        if escaped:
            for part in s.split("\n"):
                norm = normalize(part)
                for rule, rx in TEXT_RULES:
                    if rule in FOLD_EXEMPT and rel in FOLD_DATA:
                        continue
                    if rx.search(norm):
                        rep.hit(rel, 0, rule, "escaped text: " + part)
                if rel not in FOLD_DATA:
                    check_unicode(rep, rel, 0, part, clean(part), "escaped text: ")
        if depth < 32 and s.lstrip()[:1] in ("{", "["):
            check_json(rep, rel, s, depth + 1)
        if key == "LuaScript":
            check_comments(rep, rel, s, "lua", "LuaScript: ")
        elif key == "XmlUI":
            check_comments(rep, rel, s, "xml", "XmlUI: ")


def check_binary(rep, rel, data):
    seen = set()
    for raw in (data, data.replace(b"\x00", b"")):
        text = normalize(raw.decode("utf-8", "replace"))
        for rule, rx in WORDS:
            for m in rx.finditer(text):
                if (rule, m.group(0).lower()) not in seen:
                    seen.add((rule, m.group(0).lower()))
                    rep.hit(rel, 0, rule, "in binary data: " + m.group(0))


def check_file(rep, rel, path):
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(rel)[1].lower()
    license_file = re.search(r"(^|/)LICENSE[^/]*$", rel) is not None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    is_text = text is not None and (ext in TEXT_EXT or b"\x00" not in data[:8192])
    if ext in TEXT_EXT and text is None:
        rep.hit(rel, 0, "unicode", "not valid UTF-8")
    if not is_text:
        check_binary(rep, rel, data)
        return
    if "\x00" in text:
        check_binary(rep, rel, data)
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    for n, line in enumerate(lines, 1):
        check_line(rep, rel, n, line, license_file)
    check_comments(rep, rel, text, kind(rel, lines[0]))
    if ext == ".json":
        check_json(rep, rel, text)


def check_path(rep, rel, is_dir):
    parts = rel.split("/")
    top = len(parts) == 1
    name = parts[-1]
    if re.search(r"[\x00-\x1f\x7f]", rel):
        rep.hit(safe(rel), 0, "path", "control character in name")
        return False
    if name == ".git":
        rep.hit(rel, 0, "path", "nested repository")
        return False
    if name.startswith(".") and not (top and ((is_dir and name == ".github") or (not is_dir and name == ".gitignore"))):
        rep.hit(rel, 0, "path", "dot file or folder")
        return False
    if DENY_NAMES.fullmatch(name) or rel.lower().startswith(DENY_PREFIXES) or "c" + "o-authored" in rel.lower():
        rep.hit(rel, 0, "path", "internal file in the public tree")
        return False
    norm = normalize(rel)
    for rule, rx in TEXT_RULES:
        if rx.search(rel) or rx.search(norm):
            rep.hit(rel, 0, "path name", "%s matches %s" % (rel, rule))
            break
    if not is_dir and parts[0] == "assets" and os.path.splitext(name)[1].lower() not in ASSET_EXT:
        rep.hit(rel, 0, "asset", "file type not allowed under assets/")
    return True


def scrub(root):
    rep = Report()
    count = 0
    broot = os.fsencode(root)
    for dirpath, dirnames, filenames in os.walk(broot):
        reldir = os.path.relpath(dirpath, broot)
        keep = []
        for d in sorted(dirnames):
            relb = os.path.normpath(os.path.join(reldir, d))
            if relb == b".git" and not os.path.islink(os.path.join(dirpath, d)):
                continue
            rel = decode(rep, relb)
            if rel is None:
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                rep.hit(rel, 0, "path", "symbolic link")
                continue
            if check_path(rep, rel, True):
                keep.append(d)
        dirnames[:] = keep
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            relb = os.path.normpath(os.path.join(reldir, fname))
            rel = decode(rep, relb)
            count += 1
            if rel is None:
                continue
            if os.path.islink(full):
                rep.hit(rel, 0, "path", "symbolic link")
                continue
            if relb == b".git":
                continue
            if check_path(rep, rel, False):
                check_file(rep, rel, full)
    print("scrub: %d hit(s) in %d files" % (rep.hits, count))
    return 1 if rep.hits else 0


def decode(rep, relb):
    try:
        return relb.decode("utf-8").replace(os.sep, "/")
    except UnicodeDecodeError:
        rep.hit(safe(relb.decode("utf-8", "replace")), 0, "path", "name is not UTF-8")
        return None


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: python3 .github/check_public.py <dir>", file=sys.stderr)
        return 2
    return scrub(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
