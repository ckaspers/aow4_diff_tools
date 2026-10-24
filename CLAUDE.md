# AoW4 diff tools: notes for Claude

Tools that read Age of Wonders 4 game data (`.rpk` packs) and a mod's override file (`.acp`) and
write interactive HTML reports. Read `README.md` for usage. This file holds what is not obvious
from the code: the file formats, the editor's meaning of things, and decisions already made.

## Users and paths

- Modders edit their mod in Paradox's **ResourceED** editor. Explain findings in editor terms
  (field names as ResourceED shows them, "unmod / mod again", "Localized String Dialog").
- Folder paths are in `config.json` (not in Git). Notes about the maintainer's own mod are in
  `CLAUDE.local.md` (git-ignored): keep mod names and personal details out of committed files.
- After every game update, `after_update.bat`: schema rebuild if ResourceED.exe changed, update
  report (latest snapshot vs live), mod report, new snapshot. Keep at least the two newest
  snapshots: the previous one is the only way to rebuild the last update report.

## Never commit game data

`snapshots/`, `reports/`, `cache/` hold or are built from Paradox's files and are git-ignored.
`schema_db.json` is committed and published by the web version (field names read out of
ResourceED.exe, needed to run; the maintainer decided that is fine). The web version serves the
committed copy, so a rebuilt schema reaches it only once it is committed and pushed.

## File formats

- **Record**: `[count byte][(fid, off) pairs][bodies]`. High bit in count: a u32 count of
  (u32 fid, u32 off) pairs follows. A body of one `\0` byte is an empty record/list.
  Polymorphic object = `[u32 class id][record]`. Class scopes are nested records keyed by class id
  (fid > 0xffff). **Fields at their default value are not written at all.**
- **.rpk**: header 0xb0; top record field 13 = categories `[cid]{50: FID, 51: lib}`; lib field 21 →
  `{1: {i: item}}`. Resource head fields: 20 FName, 21 FUserName, 24 FID (lo, hi → id hex
  `hi8 + lo8`). Strings are u32 length + UTF-8.
- **.acp**: `CPR\0` + 12 bytes; TOP at 0x24; TOP field 38 = pack blocks (cid 0x401204) each
  `{1: pack name, 2: {1: overrides}}`; override (cid 0x401206) `{1: raw copy, 2: category,
  3: '[#ID]cat\row'}`. **No base copy and no timestamp**: nothing says when or from which build an
  override was copied.
- **Text**: game `Language/EN/EN.MO`; mod `.po` (read the .po, it is what the user edits).
  A mod may hold a `.po` named after a game language (`EN.po`): a modder's own copy of the game's
  EN.MO for viewing and copying strings. The game does not load it and it goes stale after an
  update, so `tooltips.load_texts` skips any .po named after a game language folder.
- **.clb** (content library, ContentEd; `clbicons.py`): `CRL\0`, header u32 [3] string table size,
  [4] records size. Strings start at 0x20, records follow, the data area starts at the next 16-byte
  boundary. A string is referenced as (u32 length, u32 offset from 0x20). A texture is a record with
  byte (fid, off) pairs: 0x14 width, 0x15 height, 0x19 depth, 0x17 format, 0x18 flags, 0x16 data
  (u32 offset in the data area, u32 storage 2 = LZ4 block / 0 = raw, u32 size, u32 stored size).
  An image's own texture is nested after its name; its smaller copies follow as a list (class id
  0x410024 before each). Icon images are format 83, 8-bit RGBA (all 6587 in the game's Icons
  libraries); copies and other textures are block-compressed (191 is BC7 sRGB per ContentEd).
  An atlas is assembled at load time; its only data of its own is a 1-bit mask.

## The schema (`extract_schema.py` → `schema_db.json`)

Read from ResourceED.exe via RTTI vtables: vt[2] save, vt[4] editor props, vt[6] cid, vt[7] parent,
vt[12] name. Props give display names, offsets, enum ids; constructors give member classes; the
trigger registry gives script function names; enum tables give enum value names. Per class:
`f` {fid: [name, kind, off, default?]}, `d` {off: [display, type, enum?]}, `m` member types,
`e` enums. Fields the scan cannot name show as `field_N`; `decode.FIELD_LABELS` names a few by hand
(confirmed with the user from editor screenshots: TCombatDefenseBlock 1 Armor 2 Resistance;
TCombatDamageBlock 1..6 Physical, Blight, Shock, Fire, Spirit, Frost).

## Editor and game semantics (confirmed by the user)

- A missing field means its **default**, which is not always 0: compare against the schema default
  (shown as "(default)"). This bit us once.
- **-1 is not "empty"** in general: Armor/Resistance -1 subtracts; only some fields (e.g. Mind
  Control duration) use -1 for infinite.
- **Tooltips** (`TELocalizedStringLink`: msgid + key/value list). A `{placeholder}` is filled from a
  typed-in value, else from a **data store**: the DataStoreTag of the resource or of resources it
  links to (`{TAG>Field}`). Keys should only be typed in when a value can't be resolved or is a
  manual one (`{amount}`, `{tier}`). `{GLOBAL.…}`/`{LOC.…}` always resolve. The Localized String
  Dialog rebuilds the key list from the text: **clicking Ok + save drops leftover keys**, and it
  does not show leftover keys at all. Text inside trigger scripts (predictions etc.) is filled by
  the script's own Text Variables: skip it.
- Keys left over in the list are harmless (the game ships 170+ itself).
- **Unmod, mod again, re-apply edits** refreshes an override to the current game. It is the fix for
  stale copies (e.g. a Lost Tomb event copy still carried a prediction the game had removed).
- Removing the last entry of a list saves it as an empty list (`\0` body), not as a missing field.

## City buildings (`citytree.py`)

- A `TStructureUpgradeR` upgrades from whatever grants its `RequiredStructureProperties`; its
  `MustHaveStructureProperties` are further conditions (mostly a Town Hall tier). Buildings grant
  "Tier N - Gold"-style properties in `Structure Properties`, and "<building> - ID" ones in
  `Colony Properties`. Some property names end in a space: strip them.
- "Tier 1 - Town Hall" / "Town Hall Tier 1 - …" and "Throne City ID" / "Transfer With Throne ID
  Property" are granted by no building: they are the two roots (Town Hall I, the Throne).
- A property nothing grants ("Wizard Tower - Unreachable Player Property") is how a building is
  switched off; mods use it too.
- Culture swaps: each `TCityCultureSettingsR` (City Settings) has `StructureUpgradeReplacementList`
  (generic → culture building). Cultures with sub-cultures keep the swaps on the sub-culture's
  settings; Mystic keeps them on the culture's. Culture keys come from the faction property giver's
  `TelemetryName` (left out at its default: Industrious), which matches the site's icon names.
- `TPropertyUnlockSettings` unlocks buildings for a player property: tome skills, sub-cultures
  (Mystic schools, Oathsworn oaths). Primal's animal sub-cultures have no City Settings, only these.
- The dump's structure `id` is not unique (Keep, Castle and Commune share one): key by `resid`.
- Culture, sub-culture and Throne icons are not in the DUMPALL export. `TGameIconR` points into a
  texture atlas in `Content/Title/Libraries/Icons/*.clb` (KTX/BC7 entries named after the source
  .png); nothing reads those yet.

## Code map

- `aow4lib.py` record parsing, `Schema`. `decode.py` `Decoder` → nodes: `('o', cls, [(label,
  owner, node)])`, `('l', items)`, `('v', text[, default])`, `('r', name, id)`, `('t', tags)`,
  `('f', tagfilter)`, `('x', bytes)`. `render()` prints a node tree.
- `differ.py` compares node trees (keyed list matching, defaults, cosmetic labels). `script.py`
  renders trigger scripts as pseudo-code and diffs them.
- `acpread.py` reads overrides. `aow4diff.py` commands, `Model`, `cmd_mod`, `cmd_update`,
  `mark_unmerged` (labels mod differences that undo a change listed in an update report in
  `reports/`).
- `tooltips.py` tooltip key checks. `stale.py` dead links + "entries only your copy has".
- `citytree.py` standalone: city tree links for the website (`Game` = game + mod packs +
  overrides, `build` → roots, cultures, structures). Imports the modules above, changes none.
- `alltexts.py` standalone: the website's `Data/EN/all.json` from the live game with the mod (`Texts`: GLOBAL
  names from the tooltip libraries, typed values from the links that show a text, `{TAG>Field}` only when
  one resource is meant). `update_site.bat` runs it, then `spawnsets.py`, `citytree.py` and `magicmaterials.py`.
- `spawnsets.py` standalone: the website's `Data/GEN/all_spawnsets.json`, every `TUnitCategory` with its sets'
  unit names (a set is a `TUnitSet` by SetName, or a unit by its DataStoreTag).
- `clbicons.py` standalone: list, find and export images of the game's `.clb` content libraries
  (standard library only: its own LZ4 block decoder and PNG writer).
- `magicmaterials.py` standalone: magic material sets and materials for the website (`Texts` fills a
  text link from its own replacements, `{TAG>ADD>Field}` from the DataStoreTag resource; references
  are followed by id, as a renamed resource keeps its old name in links).
- `template.html` the viewer; the report data is injected as JSON at `__DATA__`. Mod-report
  sections: Not merged, Tooltip keys, Possibly old content, Deleted, No target, Layout-only,
  Repeated, Overrides.
- `web/` the browser version of the mod report (GitHub Pages). `index.html` takes the folders
  through `<input webkitdirectory>`. `worker.js` runs Pyodide, mounts them with WORKERFS (lazy
  reads, read-only) at `/game` and `/mod`, and calls `cmd_mod` unchanged with no history; the caches live
  in IDBFS at `/app/cache`. The worker's `APP` list is what gets served (`build_site.py` reads it).
  Why not `showDirectoryPicker`: it is Chromium-only and is expected to refuse Program Files and
  %APPDATA% (Steam's default and the Mods folder). WORKERFS gives blobs "now" as their date, so the
  worker sets `node.timestamp` from `File.lastModified` (build label and cache keys need it).
  Python code that runs there must not write next to its inputs (pass `out`).

## Working on it

- Check a resource with `python aow4diff.py dump "<name>" [--acp "<mod.acp>"]`; diff the two dumps.
- After a change, regenerate with `python aow4diff.py mod` and open the report; check the browser
  console for errors. A whole mod report takes ~30-45 s.
- Web version: `python web/build_site.py`, then `python -m http.server -d _site` and open
  http://localhost:8000 with Ctrl+F5 (the browser caches the worker). Rebuild after changing `web/`
  or any file the worker loads. Its report should match `aow4diff.py mod` apart from "Not merged".
- An older copy of a mod's `.acp` is a useful regression input (`--acp ... --out _dev/bak.html`).
- `_dev/` is git-ignored scratch (test scripts, backups).
- Reports must make sense to the user without code knowledge: plain section text, editor names.

## Decided against

- Old builds via Steam depot downloads (to date each override and list game additions a copy
  lacks): too much disk space, not needed. History is built going forward from the user's own
  snapshots; they prune old ones by hand when space runs short. Don't suggest it again.
- Update reports in the web version (a drop-in box, or publishing the update data with the site):
  only the maintainer can make them, and publishing them means decoded game data in a public repo.
  The web version stays a plain mod-vs-live comparison; "Not merged" is desktop only.
