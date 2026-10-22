# AoW4 pack diff tools

Reports in the same interactive HTML viewer as the `update_…_to_….html` / `test_mod.html` files:

* **Mod vs live game** – every resource your mod overrides, compared field by field with the
  live game: what the mod changes, what the game changed since the copy was made, overrides
  whose target is gone, deletions.
* **Update report** – one game build against the next: removed / re-created / moved resources,
  new content, schema changes, systemic changes and every changed field. The page also has
  the *Check a mod (.acp)* button: drop your mod on it to see which overrides the update hit.

## The routine after every game update

```
python aow4diff.py after-update
```
(or double-click `after_update.bat`)

1. rebuilds `schema_db.json` if `ResourceED.exe` changed (needs a disassembler, see below),
2. writes `reports/update_<old>_to_<new>.html` from the latest snapshot to the live game,
3. writes `reports/<mod>_vs_live_<build>.html` for every mod in your Mods folder,
4. snapshots the live game into `snapshots/<build>` so the *next* update can be compared.

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

Paths live in `config.json` (game folder, Mods folder, snapshots, reports).

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

## Known limits

* Trigger scripts are rendered from the registry as `Type[Function, args]` pseudo-code; a few
  constructs (variables, some operators) read rougher than in the editor.
* Tag-filter condition modes are labelled from the editor's strings by best guess.
* Builds are labelled by the exe date (`exe of 2026-10-16`), so two game updates on the same day
  share a label: the second one's mod report replaces the first, and `after-update` sees no new build.
* Byte-level changes in assets (textures, sounds, FX data) show as "undecoded".

`template.html` is the viewer page from the reports you were sent, with the data swapped in.
