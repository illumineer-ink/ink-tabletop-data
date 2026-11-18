"""Download LorcanaJSON files and Lorcast card ids. The only network code.

LorcanaJSON: for each language, metadata.json and allCards.json.zip, the zip
checked against its published .md5 file. Saved as <out>/<lang>/.

Lorcast: one request for the set list, then one request per set, with a
pause between requests as Lorcast asks. Lorcast is optional: on any Lorcast
error the file is not written, a warning is printed and the exit code stays 0.
Saved as a compact JSON file:
{"sets": {"<code>": [{"id": "crd_...", "number": "12", "tcgplayer": 123}]}}

Usage:
  python3 ingest/fetch.py --out <dir> [--langs en,fr,de,it] [--lorcast <file>]
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

LORCANAJSON = "https://lorcanajson.org/files/current/{lang}/{name}"
LORCAST = "https://api.lorcast.com/v0"
USER_AGENT = "ink-tabletop-data (+https://github.com/illumineer-ink/ink-tabletop-data)"
LORCAST_PAUSE = 0.1
TIMEOUT = 60
RETRIES = 3
MAX_BYTES = 64 * 1024 * 1024


class FetchError(Exception):
    pass


def http_get(url, accept="*/*"):
    """GET a URL, retrying network errors, 429 and 5xx three times with backoff."""
    last = None
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(2 ** attempt)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise FetchError("%s: response larger than %d bytes" % (url, MAX_BYTES))
                return body
        except urllib.error.HTTPError as e:
            last = "%s: HTTP %d" % (url, e.code)
            if e.code != 429 and e.code < 500:
                break
        except (urllib.error.URLError, http.client.HTTPException, ssl.SSLError, TimeoutError, ConnectionError, OSError) as e:
            last = "%s: %s" % (url, e)
    raise FetchError(last)


def parse_md5(text):
    """Return the md5 hex digest from a .md5 file body."""
    m = re.search(r"\b([0-9a-fA-F]{32})\b", text)
    if not m:
        raise FetchError("no md5 digest in %r" % text[:80])
    return m.group(1).lower()


def write_file(path, data):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def fetch_language(lang, out_dir):
    """Download one language's metadata and card zip, checking the md5."""
    lang_dir = os.path.join(out_dir, lang)
    os.makedirs(lang_dir, exist_ok=True)
    zip_bytes = http_get(LORCANAJSON.format(lang=lang, name="allCards.json.zip"))
    expected = parse_md5(http_get(LORCANAJSON.format(lang=lang, name="allCards.json.zip.md5")).decode("ascii", "replace"))
    actual = hashlib.md5(zip_bytes).hexdigest()
    if actual != expected:
        raise FetchError("%s/allCards.json.zip: md5 %s, expected %s" % (lang, actual, expected))
    metadata = http_get(LORCANAJSON.format(lang=lang, name="metadata.json"), "application/json")
    json.loads(metadata)
    write_file(os.path.join(lang_dir, "allCards.json.zip"), zip_bytes)
    write_file(os.path.join(lang_dir, "metadata.json"), metadata)
    print("%s: %d bytes, md5 ok" % (lang, len(zip_bytes)))


def lorcast_json(path):
    data = json.loads(http_get(LORCAST + path, "application/json"))
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        raise FetchError("lorcast %s: expected a list" % path)
    return data


def fetch_lorcast(path, pause=LORCAST_PAUSE):
    """Fetch every Lorcast set's card ids, one request per set."""
    sets = lorcast_json("/sets")
    if not sets:
        raise FetchError("lorcast: empty set list")
    result = {}
    for s in sets:
        code = s.get("code")
        if not isinstance(code, str) or not re.match(r"^[A-Za-z0-9]+$", code):
            raise FetchError("lorcast: bad set code %r" % (code,))
        time.sleep(pause)
        cards = lorcast_json("/sets/%s/cards" % code)
        rows = []
        for c in cards:
            cid, num = c.get("id"), c.get("collector_number")
            if not isinstance(cid, str) or not re.match(r"^crd_[0-9a-f]{32}$", cid) or not isinstance(num, str):
                raise FetchError("lorcast set %s: bad card %r" % (code, {"id": cid, "collector_number": num}))
            tcg = c.get("tcgplayer_id")
            rows.append({"id": cid, "number": num, "tcgplayer": tcg if isinstance(tcg, int) else None})
        rows.sort(key=lambda r: (r["number"], r["id"]))
        result[code] = rows
        print("lorcast %s: %d cards" % (code, len(rows)))
    write_file(path, json.dumps({"sets": result}, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="directory for LorcanaJSON files")
    ap.add_argument("--langs", default="en,fr,de,it")
    ap.add_argument("--lorcast", help="write Lorcast card ids to this file")
    args = ap.parse_args(argv)
    try:
        for lang in [l.strip() for l in args.langs.split(",") if l.strip()]:
            if not re.match(r"^[a-z]{2}$", lang):
                raise FetchError("bad language %r" % lang)
            fetch_language(lang, args.out)
    except (FetchError, ValueError) as e:
        print("fetch failed: %s" % e, file=sys.stderr)
        return 1
    if args.lorcast:
        try:
            fetch_lorcast(args.lorcast)
        except (FetchError, ValueError, AttributeError) as e:
            if os.path.exists(args.lorcast):
                os.remove(args.lorcast)
            print("::warning::Lorcast fetch failed, the build will reuse the last release's ids: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
