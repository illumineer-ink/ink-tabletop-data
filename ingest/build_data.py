"""Turn LorcanaJSON card files into the ink-tabletop data layout.

Reads <src>/<lang>/allCards.json (or allCards.json.zip) for every language
directory present, and writes:

  manifest.json             schema and data version, sets, languages
  index/<lang>.tsv          compact name index, the only file an import needs
  sets/<lang>/<set>.tsv     full card records
  aliases.json              known spelling drift between deck sites

Card files are tab-separated (see datatsv.py) because Tabletop Simulator's
JSON decoder is too slow for large files.

Everything is validated against ingest/schema before anything is written.
On any error nothing is written and the exit code is 1.

Usage:
  python3 ingest/build_data.py --src <dir> [--out <dir>] [--lorcast <file>]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.parse
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import datatsv
import validate

SCHEMA_VERSION = 1
SOURCE = {"name": "LorcanaJSON", "url": "https://lorcanajson.org"}
FILES = {"index": "index/{lang}.tsv", "sets": "sets/{lang}/{set}.tsv", "aliases": "aliases.json"}
INDEX_COLUMNS = validate.load_schema("index")["columns"]
CARD_COLUMNS = validate.load_schema("card")["columns"]
JSON_LIMIT = validate.JSON_LIMIT
LANG_ORDER = ["en", "fr", "de", "it"]

INKS = {
    "Amber": "Amber", "Amethyst": "Amethyst", "Emerald": "Emerald", "Ruby": "Ruby", "Sapphire": "Sapphire", "Steel": "Steel",
    "Ambre": "Amber", "Améthyste": "Amethyst", "Émeraude": "Emerald", "Rubis": "Ruby", "Saphir": "Sapphire", "Acier": "Steel",
    "Bernstein": "Amber", "Smaragd": "Emerald", "Rubin": "Ruby", "Stahl": "Steel",
    "Ambra": "Amber", "Ametista": "Amethyst", "Smeraldo": "Emerald", "Rubino": "Ruby", "Zaffiro": "Sapphire", "Acciaio": "Steel",
}
TYPES = {
    "Character": "Character", "Action": "Action", "Item": "Item", "Location": "Location",
    "Personnage": "Character", "Objet": "Item", "Lieu": "Location",
    "Charakter": "Character", "Aktion": "Action", "Gegenstand": "Item", "Ort": "Location",
    "Personaggio": "Character", "Azione": "Action", "Oggetto": "Item", "Luogo": "Location",
}
RARITIES = {
    "Common": "Common", "Uncommon": "Uncommon", "Rare": "Rare", "Super Rare": "Super Rare", "Legendary": "Legendary",
    "Epic": "Epic", "Enchanted": "Enchanted", "Iconic": "Iconic", "Special": "Special",
    "Commune": "Common", "Inhabituelle": "Uncommon", "Très Rare": "Super Rare", "Légendaire": "Legendary",
    "Épique": "Epic", "Enchantée": "Enchanted", "Iconique": "Iconic", "Spécial": "Special",
    "Gewöhnlich": "Common", "Ungewöhnlich": "Uncommon", "Selten": "Rare", "Episch": "Super Rare", "Legendär": "Legendary",
    "Mythisch": "Epic", "Verzaubert": "Enchanted", "Ikonisch": "Iconic", "Speziell": "Special",
    "Comune": "Common", "Non comune": "Uncommon", "Rara": "Rare", "Super Rara": "Super Rare", "Leggendaria": "Legendary",
    "Epica": "Epic", "Incantata": "Enchanted", "Iconica": "Iconic", "Speciale": "Special",
}
SET_KINDS = {"expansion": "expansion", "quest": "quest"}
LORCAST_SET_CODES = {"C1": "cp"}
IMAGE_HOST = "api.lorcana.ravensburger.com"
IMAGE_PATH_RE = re.compile(r"/[A-Za-z0-9/_.-]+\.(jpg|png)")

with open(os.path.join(HERE, "fold.json"), encoding="utf-8") as _f:
    FOLD = json.load(_f)

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_DASH_RE = re.compile(r"[ \t\n\r\f\v]*-[ \t\n\r\f\v]*")
_DROP_RE = re.compile(r"[.,!?:;\"()&/*]")
_SPACE_RE = re.compile(r"[ \t\n\r\f\v]+")


class BuildError(Exception):
    pass


def normalize(text):
    """Normalize a card name for lookup. See ingest/README.md for the spec."""
    s = unicodedata.normalize("NFC", text).translate(_ASCII_LOWER)
    s = "".join(FOLD.get(ch, ch) for ch in s)
    s = s.replace("'", "")
    if "-" in s:
        s = _DASH_RE.sub(" - ", s)
    else:
        s = s.replace(",", " - ", 1)
    s = _DROP_RE.sub("", s)
    return _SPACE_RE.sub(" ", s).strip()


MAX_CARDS_BYTES = 64 * 1024 * 1024


def _read_capped(f, what):
    raw = f.read(MAX_CARDS_BYTES + 1)
    if len(raw) > MAX_CARDS_BYTES:
        raise BuildError("%s: larger than %d bytes" % (what, MAX_CARDS_BYTES))
    return raw


def read_cards(lang_dir):
    """Load allCards.json from a language directory, unzipping in memory if needed."""
    plain = os.path.join(lang_dir, "allCards.json")
    packed = plain + ".zip"
    if os.path.exists(plain):
        with open(plain, "rb") as f:
            raw = _read_capped(f, plain)
    elif os.path.exists(packed):
        with zipfile.ZipFile(packed) as z:
            members = [i for i in z.infolist() if i.filename.rsplit("/", 1)[-1] == "allCards.json"]
            if len(members) != 1:
                raise BuildError("%s: expected one allCards.json in the zip" % packed)
            if members[0].file_size > MAX_CARDS_BYTES:
                raise BuildError("%s: allCards.json is %d bytes, limit %d" % (packed, members[0].file_size, MAX_CARDS_BYTES))
            with z.open(members[0]) as f:
                raw = _read_capped(f, packed)
    else:
        raise BuildError("%s: no allCards.json or allCards.json.zip" % lang_dir)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise BuildError("%s: %s" % (lang_dir, e))
    for key in ("metadata", "sets", "cards"):
        if key not in data:
            raise BuildError("%s: missing top-level %r, upstream format changed?" % (lang_dir, key))
    return data


SET_KEY_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")
LANG_RE = re.compile(r"^[a-z]{2}$")


def safe_set_key(value, card):
    if not isinstance(value, str) or not SET_KEY_RE.match(value):
        raise BuildError("card %s: bad set code %r" % (card.get("id"), value))
    return value


def set_key(card):
    """The printing's set: the promo grouping when there is one, else the set code."""
    safe_set_key(card["setCode"], card)
    return safe_set_key(card.get("promoGrouping") or card["setCode"], card)


def pad3(key):
    """Pad numeric set keys and collector numbers to three digits."""
    return key.zfill(3) if key.isdigit() else key


LORCAST_ID_RE = re.compile(r"crd_[0-9a-f]{32}")


def warn(message):
    """Print a warning that GitHub Actions shows in the job summary."""
    print("::warning::" + message, file=sys.stderr)


class Lorcast:
    """Lorcast ids, either fresh from fetch.py or reused from the last release."""

    def __init__(self, by_number=None, by_tcg=None, by_card=None):
        self.by_number, self.by_tcg, self.by_card = by_number or {}, by_tcg or {}, by_card


def load_lorcast(path):
    """Index a fetch.py Lorcast file by (set, number) and by TCGplayer id.

    Returns None, with a warning, when the file is missing, unreadable or empty.
    """
    if not path:
        return None
    by_number, by_tcg = {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for code, rows in data["sets"].items():
            for row in rows:
                if not LORCAST_ID_RE.fullmatch(row["id"]):
                    raise ValueError("bad id %r" % row["id"])
                by_number[(code.lower(), row["number"].lower())] = row["id"]
                if isinstance(row.get("tcgplayer"), int):
                    by_tcg.setdefault(row["tcgplayer"], row["id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        warn("Lorcast data unavailable (%s: %s)" % (path, e))
        return None
    if not by_number:
        warn("Lorcast data is empty (%s)" % path)
        return None
    return Lorcast(by_number, by_tcg)


def previous_lorcast(previous):
    """Card id to Lorcast id from the last release's English index, or None."""
    path = os.path.join(previous, FILES["index"].format(lang="en"))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8", newline="") as f:
            rows = datatsv.decode(f.read(), validate.load_schema("index"))
    except (OSError, ValueError, datatsv.TsvError) as e:
        warn("cannot read previous Lorcast ids from %s: %s" % (path, e))
        return None
    return Lorcast(by_card={r["id"]: r["lorcast"] for r in rows if r.get("lorcast")})


def match_lorcast(card, lorcast):
    if lorcast is None:
        return None
    if lorcast.by_card is not None:
        return lorcast.by_card.get(card["id"])
    by_number, by_tcg = lorcast.by_number, lorcast.by_tcg
    key = set_key(card)
    code = LORCAST_SET_CODES.get(key, key).lower()
    number = card.get("number")
    if number is not None:
        variant = (card.get("variant") or "").lower()
        hit = by_number.get((code, str(number) + variant))
        if hit is None and not variant:
            hit = by_number.get((code, str(number)))
        if hit:
            return hit
    tcg = (card.get("externalLinks") or {}).get("tcgPlayerId")
    return by_tcg.get(tcg) if tcg else None


def _canon(table, value, what, card):
    if value not in table:
        raise BuildError("card %s: unknown %s %r" % (card.get("id"), what, value))
    return table[value]


def inks(card):
    colors = card.get("colors")
    if not colors:
        colors = [c for c in card.get("color", "").split("-") if c]
    result = [_canon(INKS, c, "ink", card) for c in colors]
    if len(result) > 2:
        raise BuildError("card %s: more than two inks %r" % (card.get("id"), result))
    return result


def keywords(card):
    out = []
    for a in card.get("abilities") or []:
        if a.get("type") == "keyword" and a.get("keyword"):
            value = a.get("keywordValue")
            out.append(a["keyword"] + (" " + value if value else ""))
    return out or list(card.get("keywordAbilities") or [])


def formats(card):
    """Encode format legality as "Name:allowed:until:bannedSince" strings."""
    out = []
    for name, f in sorted((card.get("allowedInFormats") or {}).items()):
        if not re.fullmatch(r"[A-Za-z0-9]+", name):
            raise BuildError("card %s: bad format name %r" % (card.get("id"), name))
        out.append("%s:%d:%s:%s" % (name, 1 if f.get("allowed") else 0, f.get("allowedUntilDate") or "", f.get("bannedSinceDate") or ""))
    return out


def image_url_ok(url):
    """True for an https URL on the official image host with a plain .jpg or .png path."""
    if not isinstance(url, str) or not re.fullmatch(r"[\x21-\x7e]+", url):
        return False
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    return (u.scheme == "https" and u.netloc == IMAGE_HOST and u.hostname == IMAGE_HOST and port is None
            and u.username is None and u.password is None and not u.query and not u.fragment
            and "?" not in url and "#" not in url and IMAGE_PATH_RE.fullmatch(u.path) is not None)


def check_image(url, card, what):
    if not image_url_ok(url):
        raise BuildError("card %s: %s image URL %r is not an https .jpg or .png on %s" % (card.get("id"), what, url, IMAGE_HOST))
    return url


def card_record(card, lorcast):
    """Build the full record for one LorcanaJSON card."""
    images = card.get("images") or {}
    key = set_key(card)
    number = card.get("number")
    full_name = card.get("fullName") or card["name"]
    ctype = _canon(TYPES, card.get("type"), "type", card)
    return {
        "id": card["id"],
        "n": normalize(full_name),
        "name": card["name"],
        "version": card.get("version") or None,
        "fullName": full_name,
        "type": ctype,
        "cost": card["cost"],
        "inkwell": bool(card.get("inkwell")),
        "ink": inks(card),
        "rarity": _canon(RARITIES, card.get("rarity"), "rarity", card),
        "set": key,
        "setCode": card["setCode"],
        "number": number,
        "variant": (card.get("variant") or "").lower() or None,
        "set3": pad3(key),
        "num3": pad3(str(number)) if number is not None else None,
        "img": check_image(images.get("full"), card, "full"),
        "thumb": check_image(images["thumbnail"], card, "thumbnail") if images.get("thumbnail") else None,
        "lorcast": match_lorcast(card, lorcast),
        "landscape": ctype == "Location",
        "subtypes": list(card.get("subtypes") or []),
        "keywords": keywords(card),
        "text": card.get("fullText") or None,
        "strength": card.get("strength"),
        "willpower": card.get("willpower"),
        "lore": card.get("lore"),
        "moveCost": card.get("moveCost"),
        "maxCopiesInDeck": card.get("maxCopiesInDeck"),
        "formats": formats(card),
        "baseId": card.get("baseId"),
    }


def index_entry(record):
    return {k: record[k] for k in INDEX_COLUMNS}


def _number_key(rec):
    num = rec["number"]
    return (num is None, num if num is not None else 0, rec["variant"] or "", rec["id"])


def printing_order(rec):
    """Main sets in numeric order first, then letter sets; by number within a set.

    The first index row for a given n is the default printing.
    """
    return _set_sort_key(rec["set"]) + _number_key(rec)


def load_aliases(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("aliases", data)


def build_aliases(seed, known_names):
    """Normalize the alias seed. Every target must be a real card name."""
    out = {}
    for src, dst in seed.items():
        ns, nd = normalize(src), normalize(dst)
        if nd not in known_names:
            raise BuildError("alias target %r is not a card name" % dst)
        if ns == nd:
            continue
        if ns in known_names:
            raise BuildError("alias source %r is itself a card name" % src)
        out[ns] = nd
    return {"schemaVersion": SCHEMA_VERSION, "aliases": dict(sorted(out.items()))}


def build(src, lorcast_path=None, aliases_path=None, previous=None):
    """Build every output document in memory. Returns {relative path: object}."""
    langs = [l for l in sorted(os.listdir(src)) if os.path.isdir(os.path.join(src, l)) and LANG_RE.match(l)]
    langs.sort(key=lambda l: (LANG_ORDER.index(l) if l in LANG_ORDER else len(LANG_ORDER), l))
    if "en" not in langs:
        raise BuildError("%s: no en directory" % src)
    lorcast = load_lorcast(lorcast_path)
    if lorcast is None and previous is not None:
        lorcast = previous_lorcast(previous)
        if lorcast is not None:
            warn("reusing %d Lorcast ids from the last release" % len(lorcast.by_card))
    if lorcast is None and lorcast_path:
        warn("publishing without Lorcast ids")
    docs, lang_info, set_meta, format_version, versions = {}, [], {}, None, []
    en_names, ids_by_lang = set(), {}
    for lang in langs:
        data = read_cards(os.path.join(src, lang))
        meta = data["metadata"]
        generated = meta.get("generatedOn")
        if not isinstance(generated, str):
            raise BuildError("%s: metadata.generatedOn missing" % lang)
        versions.append(generated)
        fv = meta.get("formatVersion")
        if not isinstance(fv, str) or not re.match(r"^2\.\d+(\.\d+)*$", fv):
            raise BuildError("%s: unsupported formatVersion %r" % (lang, fv))
        if format_version is not None and fv != format_version:
            raise BuildError("%s: formatVersion %s differs from %s" % (lang, fv, format_version))
        format_version = fv
        if lang == "en":
            for code, s in data["sets"].items():
                set_meta[code] = {"name": s.get("name"), "releaseDate": s.get("releaseDate"), "type": s.get("type")}
        by_set, records, seen = {}, [], set()
        for card in data["cards"]:
            try:
                rec = card_record(card, lorcast)
            except (KeyError, TypeError) as e:
                raise BuildError("%s card %s: missing or bad field %s" % (lang, card.get("id"), e))
            if rec["id"] in seen:
                raise BuildError("%s: duplicate card id %s" % (lang, rec["id"]))
            seen.add(rec["id"])
            by_set.setdefault(rec["set"], []).append(rec)
            records.append(rec)
            if lang == "en":
                en_names.add(rec["n"])
        records.sort(key=printing_order)
        docs[FILES["index"].format(lang=lang)] = Tsv([index_entry(r) for r in records], INDEX_COLUMNS)
        for key, cards in by_set.items():
            cards.sort(key=_number_key)
            docs[FILES["sets"].format(lang=lang, set=key)] = Tsv(cards, CARD_COLUMNS)
        lang_info.append({"code": lang, "cards": len(records), "generatedOn": generated})
        ids_by_lang[lang] = seen
    all_keys = sorted({p.split("/")[-1][:-4] for p in docs if p.startswith("sets/")}, key=_set_sort_key)
    sets = []
    for key in all_keys:
        meta = set_meta.get(key)
        if meta:
            kind = SET_KINDS.get(meta["type"])
            if kind is None:
                raise BuildError("set %s: unknown set type %r" % (key, meta["type"]))
        else:
            kind, meta = "promo", {"name": None, "releaseDate": None}
        sets.append({"key": key, "set3": pad3(key), "kind": kind, "name": meta["name"], "releaseDate": meta["releaseDate"]})
    check_languages(ids_by_lang)
    if previous is not None:
        check_counts(lang_info, previous)
    seed = load_aliases(aliases_path or os.path.join(HERE, "aliases.json"))
    docs[FILES["aliases"]] = build_aliases(seed, en_names)
    docs["manifest.json"] = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": max(versions),
        "formatVersion": format_version,
        "source": SOURCE,
        "languages": lang_info,
        "sets": sets,
        "files": FILES,
    }
    return docs


MIN_SHARE = 0.95
MIN_FIRST_EN = 3000


def check_languages(ids_by_lang):
    """Every card id in another language must also exist in English."""
    en = ids_by_lang["en"]
    for lang, ids in ids_by_lang.items():
        missing = sorted(ids - en)
        if missing:
            raise BuildError("%s: %d card ids not in en, e.g. %s" % (lang, len(missing), missing[:5]))


def read_previous_manifest(previous):
    path = os.path.join(previous, "manifest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        return {l["code"]: int(l["cards"]) for l in manifest["languages"]}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BuildError("%s: cannot read previous manifest: %s" % (path, e))


def check_counts(lang_info, previous):
    """Fail when a language shrinks below 95% of the last published count.

    Without a previous manifest (first publish), English needs at least 3000 cards.
    """
    counts = {l["code"]: l["cards"] for l in lang_info}
    before = read_previous_manifest(previous)
    if before is None:
        if counts.get("en", 0) < MIN_FIRST_EN:
            raise BuildError("en: %d cards, a first publish needs at least %d" % (counts.get("en", 0), MIN_FIRST_EN))
        return
    for lang, old in sorted(before.items()):
        new = counts.get(lang)
        if new is None:
            raise BuildError("%s: language was published before and is now missing" % lang)
        if new < old * MIN_SHARE:
            raise BuildError("%s: %d cards, down from %d (more than %d%% fewer)" % (lang, new, old, round((1 - MIN_SHARE) * 100)))


def _set_sort_key(key):
    return (0, int(key), "") if key.isdigit() else (1, 0, key)


class Tsv:
    """Rows to be written as a TSV file with the given columns."""

    def __init__(self, rows, columns):
        self.rows, self.columns = rows, columns


def dump(rel, obj):
    if isinstance(obj, Tsv):
        try:
            return datatsv.encode(obj.rows, obj.columns)
        except datatsv.TsvError as e:
            raise BuildError("%s: %s" % (rel, e))
    text = json.dumps(obj, ensure_ascii=False, sort_keys=False, separators=(",", ":")) + "\n"
    if len(text.encode("utf-8")) > JSON_LIMIT:
        raise BuildError("%s: larger than %d bytes" % (rel, JSON_LIMIT))
    return text


MARKER = ".ink-tabletop-data"


def check_out_dir(out):
    """Refuse to replace anything but an empty dir or an earlier build's output."""
    real = os.path.realpath(out)
    protected = {os.path.realpath(p) for p in (os.getcwd(), os.path.expanduser("~"), os.sep, os.path.dirname(HERE))}
    if real in protected:
        raise BuildError("refusing to use %s as the output directory" % out)
    if not os.path.lexists(out):
        return
    if not os.path.isdir(out) or os.path.islink(out):
        raise BuildError("%s exists and is not a directory" % out)
    entries = os.listdir(out)
    if entries and (MARKER not in entries or ".git" in entries):
        raise BuildError("%s is not empty and was not written by this build (no %s); not replacing it" % (out, MARKER))


def write_tree(docs, out):
    """Write all documents to a temp dir, validate it, then swap it into place."""
    check_out_dir(out)
    parent = os.path.dirname(os.path.abspath(out)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".build-", dir=parent)
    try:
        root = os.path.realpath(tmp) + os.sep
        for rel, obj in docs.items():
            path = os.path.join(tmp, rel)
            if not os.path.realpath(path).startswith(root):
                raise BuildError("refusing to write outside the output directory: %r" % rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(dump(rel, obj))
        with open(os.path.join(tmp, MARKER), "w", encoding="utf-8") as f:
            f.write("Output of ingest/build_data.py. The build may delete and replace this directory.\n")
        errors = validate.validate_tree(tmp)
        if errors:
            raise BuildError("validation failed:\n  " + "\n  ".join(errors[:50]) + ("\n  ..." if len(errors) > 50 else ""))
        if os.path.exists(out):
            shutil.rmtree(out)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            shutil.rmtree(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="directory with one LorcanaJSON folder per language")
    ap.add_argument("--out", default="out", help="output directory, replaced on success")
    ap.add_argument("--lorcast", help="Lorcast card ids written by fetch.py")
    ap.add_argument("--aliases", help="alias seed file (default ingest/aliases.json)")
    ap.add_argument("--previous", help="last published data (for example the data repo root): guards card counts and supplies Lorcast ids when they are unavailable")
    args = ap.parse_args(argv)
    try:
        docs = build(args.src, args.lorcast, args.aliases, args.previous)
        write_tree(docs, args.out)
    except (BuildError, OSError, ValueError, zipfile.BadZipFile) as e:
        print("build failed: %s" % e, file=sys.stderr)
        return 1
    m = docs["manifest.json"]
    print("built %s: %s" % (args.out, ", ".join("%s %d cards" % (l["code"], l["cards"]) for l in m["languages"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
