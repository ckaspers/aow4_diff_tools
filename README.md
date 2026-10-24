# AoW4 pack diff tools

Reports in the same interactive HTML viewer as the `update_…_to_….html` / `test_mod.html` files:

* **Mod vs live game** – every resource your mod overrides, compared field by field with the
  live game: what the mod changes, what the game changed since the copy was made, overrides
  whose target is gone, deletions.
* **Update report** – one game build against the next: removed / re-created / moved resources,
  new content, schema changes, systemic changes and every changed field. The page also has
  the *Check a mod (.acp)* button: drop your mod on it to see which overrides the update hit.

## In the browser (no install)

The mod report also runs in the browser: <https://ckaspers.github.io/aow4_diff_tools/>.
Pick the game folder (the one with `AOW4.exe`) and your mod's folder. The page runs this same Python code in the browser (Pyodide),
reading the files where they are. Nothing is uploaded, although the browser's prompt says
"upload": that is its standard wording for giving a page a folder. No snapshot is needed: the mod
report compares the mod with the game as installed now. Update reports, and the "Not merged"
section they feed, are desktop only.

The site is `web/` plus the files its worker loads, put together by `web/build_site.py` and
published by `.github/workflows/pages.yml` on every push to `main`. To try it locally:
`python web/build_site.py`, then `python -m http.server -d _site` and open <http://localhost:8000>.

## The routine after every game update

```
python aow4diff.py after-update
```
(or double-click `after_update.bat`)

1. rebuilds `schema_db.json` if `ResourceED.exe` changed (needs a disassembler, see below),
2. writes `reports/update_<old>_to_<new>.html` from the latest snapshot to the live game,
3. writes `reports/<mod>_vs_live_<build>.html` for every mod in your Mods folder,
4. snapshots the live game into `snapshots/<build>` so the *next* update can be compared.

For an aow4db-style website, once the patched game has made a new DUMPALL dump (with the mod loaded) and
it is copied into the site: double-click `update_site.bat`. It runs `alltexts.py`, `spawnsets.py`, `citytree.py`
and `magicmaterials.py` in that order (see below). It is a separate step because the dump only exists after the
game has run.

Steam updates the game in place, so the snapshot has to exist **before** an update arrives.
A baseline snapshot of the current build is already in `snapshots/`. Each one is ~430 MB;
delete old ones whenever you like.

Other commands:

| command | what it does |
|---|---|
| `python aow4diff.py mod` | mod reports only (all mods, or `--acp path\to\mod.acp`) |
| `python aow4diff.py update [--old snapshots\<build>]` | update report only |
| `python aow4diff.py snapshot [--label NAME]` | snapshot only |
| `python aow4diff.py dump "<resource name>" [--acp mod.acp]` | print how one resource decodes |
| `python extract_schema.py --exe "<game>\ResourceED.exe"` | rebuild the schema by hand |
| `python alltexts.py --site PATH [--acp mod.acp \| --no-mod] [--dry-run]` | all of the game's texts for an aow4db-style website (see below) |
| `python spawnsets.py --site PATH [--out FILE] [--acp mod.acp \| --no-mod]` | unit categories (summon spells' spawn chances) for the same website (see below) |
| `python citytree.py --site PATH [--out FILE] [--acp mod.acp \| --no-mod]` | city tree links for the same website (see below) |
| `python magicmaterials.py --site PATH [--out FILE] [--acp mod.acp \| --no-mod]` | magic material sets, texts and income for the same website (see below) |
| `python clbicons.py find PATTERN` / `list LIBRARY [PATTERN]` / `export LIBRARY NAME OUT.png [--crop]` / `export-all LIBRARY FOLDER [PATTERN]` | icons from the game's content libraries (see below) |

Paths live in `config.json` (game folder, Mods folder, snapshots, reports, and `"site"` for
`alltexts.py`, `spawnsets.py`, `citytree.py` and `magicmaterials.py`).

## Setup on Windows

* Python 3.8 or newer (python.org, tick "Add to PATH").
* For the schema step only: `pip install capstone`. Without it the reports still work, using
  the schema that is here; field names may lag behind a game update until it is rebuilt.
* Copy `config.example.json` to `config.json` and set your game and Mods folders in it (the first
  run writes one with the defaults if it is missing). `config.json` stays out of Git, as do
  `snapshots`, `reports` and `cache`: those hold or are built from Paradox's game files.

## How it works

* `.rpk` packs and the `.acp` mod file are trees of records: `[count][(field id, offset)…][bodies]`.
  The files hold only numeric field ids.
* `extract_schema.py` reads `ResourceED.exe` (the mod editor, which is not DRM-wrapped like
  `AOW4.exe`): class ids and names from its vtables, each class's save routine for field
  ids / names / kinds, its editor property list for display names ("Screen Description"), its
  constructors for the class of every embedded member, and the trigger-script registry for
  script function names. That is what turns `field 9 / 11 / 2` into
  `IncomeFlat / Entries [Gold] / Value: 10 -> 5`.
* Resources are matched by id and compared after decoding, so moving list items, re-stamped
  script node ids or a reference's display name drifting are not reported as changes
  (they are counted as cosmetic).

## Tooltip keys (mod reports)

The mod report checks every tooltip in your overrides against its text (your .po, else the game's EN.MO;
a `.po` named after a game language, such as `EN.po`, is a reference copy of the game's text and is skipped):

- **Placeholders nothing fills**: a `{placeholder}` with no typed-in value and no data store of that
  name anywhere in the game or the mod. Data stores that exist are trusted: the check does not follow
  run-time links. `{GLOBAL.…}` and `{LOC.…}` are skipped.
- **Leftover keys**: keys in the list that the text never uses. Harmless; Ok in the Localized String
  Dialog plus a save drops them.

Findings the game's own version has too are folded away under "The same in the game's own version".
Code: `tooltips.py`.

## Possibly old content (mod reports)

Signs that an override is an old copy holding content the game has since removed:

- **Links to resources that no longer exist**: ids neither the game nor the mod has (the game's own
  dead links are left out).
- **Entries only your copy has**: list entries, script actions and things added inside an action
  (such as a prediction) that the game's current version lacks. Some are your own additions; the
  rest is old game content. Re-making the override clears it.

Code: `stale.py`.

## All texts for a website

`alltexts.py` writes the site's `Data/EN/all.json`: every text of the game's EN.MO with the mod's `.po`
on top, filled in with the values of the resources that show it, in the site's markup. A website that
started from upstream's copy keeps its entries and order; texts the game added are appended, and texts
it no longer has are kept, as pages may still look them up.

- Concept links (`{GLOBAL.GoldConcept.Hyperlink}`) are the concept's own Hyperlink text; the names come
  from the game's tooltip libraries (`TEDataStoreResource`, "Tooltip Global Library" and the like).
  `{GLOBAL.PROPERTIES.TAG}`, `UNITS`, `STRUCTURE_UPGRADES`... are the resource with that DataStoreTag,
  by its Screen Name.
- Typed-in values come from the text links that show the text; where they differ, the values most of
  them have. A text no resource shows keeps the site's wording.
- `{TAG>Field}` is filled only when it is clear: one resource holds the tag, or one of those the text's
  resources link to, and the field is written with a whole number. Several resources can share a tag
  (three hold `STUNNED_EFFECT_DESCRIPTION`, one with 6 turns); a field left at its default is not
  written. Anything unfilled stays `<eventColor>{key}</eventColor>`, as the site shows placeholders.

It also writes `Data/EN/concept_tooltips.json`: every concept the libraries name, with its Title,
icon, the wording of its Hyperlink and its Description (links in it as plain text, as the site shows no
tooltip inside a tooltip). Concepts the game no longer has are kept.

`--dry-run` reports what would change and writes nothing. It takes about a minute.

## Unit categories for a website

`spawnsets.py` writes the site's `Data/GEN/all_spawnsets.json`: every `TUnitCategory` (CategoryName, its
Sets, repeats kept as they weigh the chances) with the names of each set's units. A category names a
`TUnitSet` by its SetName or a unit by its DataStoreTag. Summon spells such as Call Greater Animal show
their spawn chances from it, so they follow the mod: upstream's copy has the vanilla game's lists.

## City tree for a website

`citytree.py` writes `Data/GEN/CityTreeLinks.json` for an aow4db-style website: which city building
follows which, which culture, sub-culture or tome it belongs to, and the Town Hall tier it needs, read
from the live game with the mod on top. It holds only links (resource ids, culture keys and text ids);
the site's own `StructureUpgrades.json`, `Tomes.json` and `all.json` supply names, icons and texts.

Run it after copying a new DUMPALL dump into the site. Give the site's folder with `--site`, or set
`"site"` in `config.json`. It reads the first mod in your Mods folder on top of the game; `--acp`
picks another, `--no-mod` reads the game alone.

## Magic materials for a website

`magicmaterials.py` writes `Data/GEN/MagicMaterials.json`: each magic material set with its bonus and
how many materials it needs (a field the decoder hides at its default of 3, different ones unless the
set turns that off), and each material's name, global effect, lore, local income and combat
enchantment. Texts are the game's or the mod's own, filled in with the values of the resource that
shows them, so they follow game updates and the mod. Concept links are written the way the site's
`all.json` writes them, icons included, so run it after `alltexts.py`, with the same `--site`, `--acp` and
`--no-mod` as `citytree.py`.

## Icons from the content libraries

The game's DUMPALL exports only some icon folders. `clbicons.py` reads the rest straight from the
game's content libraries (`.clb`, the files ContentEd opens) and saves them as PNG:

    python clbicons.py find UmbralRamblings
    python clbicons.py list Gameplay_Icons_EldritchRealms CombatEnchantment
    python clbicons.py export Gameplay_Icons_EldritchRealms CombatEnchantment_UmbralRamblings umbral_ramblings.png --crop
    python clbicons.py export-all Gameplay_Icons_EldritchRealms out CombatEnchantment --crop

A library is a path or its name. An atlas that ContentEd shows (CombatEnchantments) is assembled from
its images when the game loads; the library keeps each image separately, so export the images.
`--crop` trims the transparent border, which gives the size of the hand-made icons on the website
(218x248 for a combat enchantment). Icon images are 8-bit RGBA and export as is; other textures
(block-compressed) are listed but skipped.

## Known limits

* Trigger scripts are rendered from the registry as `Type[Function, args]` pseudo-code; a few
  constructs (variables, some operators) read rougher than in the editor.
* Tag-filter condition modes are labelled from the editor's strings by best guess.
* Builds are labelled by the exe date (`exe of 2026-10-16`), so two game updates on the same day
  share a label: the second one's mod report replaces the first, and `after-update` sees no new build.
* Byte-level changes in assets (textures, sounds, FX data) show as "undecoded".

`template.html` is the viewer page from the reports you were sent, with the data swapped in.
