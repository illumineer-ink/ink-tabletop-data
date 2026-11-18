# ink-tabletop data

Card data for ink-tabletop, an unofficial and free Disney Lorcana table for Tabletop Simulator. The mod reads these files to import decks and spawn cards.

## Where the data comes from

- Card data is built from [LorcanaJSON](https://lorcanajson.org). Thank you to its maintainers. If they ask us to stop mirroring their data, we will stop.
- Lorcast card ids, used as a backup image source, come from the [Lorcast API](https://lorcast.com/docs/api). Thank you to Lorcast.
- No card images or art are stored here. Each card record only holds links to images hosted elsewhere.
- Disney Lorcana is a trademark of Disney and Ravensburger. This project is not affiliated with or endorsed by either.

## Using the data

Every data change is a git tag named `data-<date>-<commit>`. Files are served through jsDelivr, for example:

```
https://cdn.jsdelivr.net/gh/illumineer-ink/ink-tabletop-data@<tag>/manifest.json
```

Tag URLs never change, so they can be cached forever.

## How it updates

A GitHub Action (`.github/workflows/data.yml`) runs every six hours and on demand. It has two jobs.

The build job has read-only access to the repository:

1. `ingest/fetch.py` downloads LorcanaJSON's card zip for each language and checks it against the published md5 file. It then reads the Lorcast set lists, one request per set with a short pause between requests.
2. `ingest/build_data.py` turns the cards into the files below and validates them against `ingest/schema/`. It compares card counts with the last published `manifest.json`: a language that drops below 95% of its previous count, or disappears, fails the build. The first publish needs at least 3000 English cards. Every card id in another language must also exist in English.
3. The output is uploaded as a workflow artifact.

The publish job downloads that artifact, validates it again, and if the files changed, commits them and pushes a new tag. If nothing changed, it does nothing.

Any LorcanaJSON download, format, count or validation error fails the build job, so nothing is committed and the last good tag stays in place. Lorcast is optional: if it cannot be reached, the build reuses the Lorcast ids from the last published index and the job log shows a warning.

To keep your own copy, fork this repository and enable Actions on the fork. Everything runs on GitHub; no server is needed.

To build locally (Python 3, no packages needed):

```
python3 ingest/fetch.py --out src --lorcast lorcast.json
python3 ingest/build_data.py --src src --lorcast lorcast.json --previous . --out out
python3 ingest/validate.py out
```

## Layout

| File | Contents |
| --- | --- |
| `manifest.json` | Schema version, data version (LorcanaJSON's `generatedOn`), source, languages with card counts, sets, and the file name patterns below. |
| `index/<lang>.tsv` | One row per printing. This is the only file a deck import needs. |
| `sets/<lang>/<set>.tsv` | Full card records for one set: the index fields plus rarity, move cost, deck limit, format legality, keywords and more. |
| `aliases.json` | Normalized old or misspelled names mapped to the normalized card name. |

`manifest.json` and `aliases.json` are JSON and stay under 16 KB each. The card files are tab-separated, because Tabletop Simulator's JSON decoder slows down sharply on large files.

### TSV format

- UTF-8, lines end with a single newline (no carriage returns), the file ends with a newline.
- The first line holds the column names. Each following line is one card, with exactly one value per column, separated by tabs.
- In values, backslash, tab, newline and carriage return are written as `\\`, `\t`, `\n` and `\r`.
- An empty value means the field is missing.
- Booleans are `1` or `0`. Lists are joined with `|`; list items never contain `|`.
- Readers should match columns by name from the header, so new columns can be added at the end.

The schemas in `ingest/schema/` describe one decoded row: `index.schema.json` for the index (its `columns` list gives the column order) and `card.schema.json` for set files. `manifest.schema.json` describes the manifest.

### Index columns

| Column | Meaning |
| --- | --- |
| `id` | LorcanaJSON card id. The same printing has the same id in every language. |
| `n` | Normalized `Name - Version` (or just the name when there is no version). See below. |
| `name`, `version` | As printed, in the file's language. `version` is empty for actions and items. |
| `cost` | Ink cost. |
| `ink` | Empty, or one or two of `Amber`, `Amethyst`, `Emerald`, `Ruby`, `Sapphire`, `Steel` joined with `\|`, in English for every language. |
| `type` | `Character`, `Action`, `Item` or `Location`, in English for every language. |
| `img` | Official card image: https on `api.lorcana.ravensburger.com`, ending in `.jpg` or `.png`. |
| `set3`, `num3` | Set and collector number for image hosts that use them. Numeric values are padded to three digits (`001`); promo and quest sets keep their code (`P1`, `Q1`). The set is the promo group when the printing has one, otherwise the set code. `num3` is empty when the card has no number. |
| `lorcast` | Lorcast card id (`crd_...`), or empty when no Lorcast card matched. |
| `landscape` | `1` for Locations, which are printed sideways. |
| `strength`, `willpower`, `lore` | Character stats, empty when the card has none. |
| `text` | Rules text, for the card's hover description. |

Set files add `rarity`, `moveCost`, `maxCopiesInDeck`, `formats`, `keywords`, `fullName`, `inkwell`, `set`, `setCode`, `number`, `variant`, `thumb`, `subtypes` and `baseId`. Each `formats` item reads `Name:allowed:until:bannedSince`, for example `Core:0:2025-09-05:`.

Several rows can share the same `n`: a base card, its enchanted version and its promos are separate printings. The index lists main sets in number order first, then promo and quest sets, and within a set by collector number, so the first row for a name is its default printing.

## Name normalization

Deck lists from different sites spell names differently. Both the data build and the mod's importer reduce names to the same key with these steps, in this order:

1. Lowercase ASCII letters `A` to `Z`. Other letters are handled by the fold table.
2. Replace each character found in `ingest/fold.json` with its replacement. The table folds accented Latin letters, uppercase or lowercase, to plain lowercase letters (`é` and `É` to `e`, `ā` to `a`, `œ` to `oe`, `ß` to `ss`), typographic apostrophes (U+2018, U+2019, U+201A, U+201B, U+2032, U+02BC, U+00B4) and the backtick to `'`, typographic double quotes (U+201C to U+201F, U+2033, U+00AB, U+00BB) to `"`, the dash family (U+2010 to U+2015, U+2212, U+FE58, U+FE63, U+FF0D) to `-`, non-breaking and other special spaces to a space, and the ellipsis U+2026 to `...`. It also removes combining accent marks (U+0300 to U+036F), so a decomposed `e` plus accent becomes `e`. Characters not in the table are kept. The build applies Unicode NFC to its input first. Card names in the data are already NFC, so an implementation without NFC gets the same keys for them.
3. Remove every `'`.
4. If the text contains a `-`: replace every `-` together with any spaces, tabs or line breaks directly around it by ` - `. Otherwise replace the first `,` by ` - `.
5. Remove each of `. , ! ? : ; " ( ) & / *`.
6. Replace each run of spaces, tabs and line breaks by one space, and trim both ends.

The index key `n` is the normalized `fullName`, which LorcanaJSON writes as `Name - Version`.

Examples:

| Input | Key |
| --- | --- |
| `Goons, Maleficent's Minions` | `goons - maleficents minions` |
| `Te Kā - The Burning One` | `te ka - the burning one` |
| `Mr. Smee - Bumbling Mate` | `mr smee - bumbling mate` |
| `Maurice - World-Famous Inventor` | `maurice - world - famous inventor` |
| `Flotsam & Jetsam - Entangling Eels` | `flotsam jetsam - entangling eels` |

`ingest/normalize_vectors.json` lists inputs and expected keys. Any other implementation should pass all of them.

## Aliases

`ingest/aliases.json` maps known misspellings or old names to the name LorcanaJSON uses. The build normalizes both sides, drops entries that normalize to the same key, and fails when a target is not a real English card name. Aliases apply to English names.
