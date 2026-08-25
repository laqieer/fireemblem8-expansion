# Generated-data authoring tutorial (Issue #5)

This is the **contributor-facing walkthrough** for the deterministic
generated-data platform. It shows, with runnable commands and real source
paths, how to add or modify every supported input type without ever
hand-editing generated C. For the full design/reference (per-field
semantics, every validation rule, linking mechanics), see
`docs/generated_data.md`; for the discoverable registry of every table and
its record count, see `reports/generated_data_manifest.md`.

Everything here is stdlib-only Python. There is no per-table dispatch: the
same three verbs drive every table.

## The core loop (applies to every table)

```sh
# 1. Edit the JSON source under src/data/ (see each section below).
# 2. Validate: reports every diagnostic with file:line:column, not just the first.
python3 -m scripts.generated_data validate --table <table>

# 3. Regenerate C89 output (build/generated/data/) + the committed inventory.
python3 -m scripts.generated_data generate --table <table>

# 4. Drift gate (CI-safe: writes nothing committed, non-zero exit on drift).
python3 -m scripts.generated_data check --table <table>
```

Whole-platform equivalents (all tables at once) are Make targets:

```sh
make generated-data-validate     # validate every registered table
make generated-data-generate     # regenerate every table's C + inventory + manifest
make generated-data-check        # CI drift gate: per-table + aggregate manifest
make generated-data-test         # the Python unittest suite
make generated-data-manifest     # (re)write reports/generated_data_manifest.md
```

`--table <name>` accepts any registered table. Discover the full set (and
each table's live record count / capacity / dependency order) from
`reports/generated_data_manifest.md`, or at compile time from the
generated `build/generated/data/generated_data_manifest.h`
(`GENERATED_DATA_<TABLE>_RECORD_COUNT`, `GENERATED_DATA_TABLE_COUNT`).

Diagnostics are actionable. A duplicate/invalid/out-of-range/dangling
reference is reported as `src/data/<file>.json:LINE:COL: <message> (at
<breadcrumb>)`, and a run reports *every* problem it finds, so you fix a
batch per iteration instead of one-at-a-time.

---

## Global tables

### Add or modify a **character** (`--table characters`)

Source: `src/data/characters.json` (the `characters` array, one record per
`CHARACTER_*`). Modify a field in place, e.g. bump Eirika's luck growth:

```json
{
  "character": "CHARACTER_EIRIKA",
  "defaultClass": "CLASS_EIRIKA_LORD",
  "growth": { "hp": 70, "pow": 40, "skl": 60, "spd": 60, "def": 30, "res": 30, "lck": 65 },
  "supportData": "SupportData_Eirika"
}
```

`characters` is a hard **256-slot** array (`gCharacterData[]`). The manifest
reports usage as `256/256` and the `manifest` command *fails* if a source
ever authors more than 256 records (record-budget diagnostic). Cross-table
validation checks `defaultClass` against `classes` and `supportData`
against `supports`, so an edit that dangles either is rejected with a
`file:line:column` diagnostic.

```sh
python3 -m scripts.generated_data validate --table characters
python3 -m scripts.generated_data generate --table characters   # -> build/generated/data/data_characters.c
```

### Add or modify a **class** (`--table classes`)

Source: `src/data/classes.json` (the `classes` array, one record per
`CLASS_*`). Records carry `base`/`max`/`growth`/`promotionGain` stat
blocks plus terrain-lookup fields cross-validated against the
`terrainstats` and `movecost` tables. Same validate/generate/check loop
(output: `data_classes.c`).

### Add or modify an **item** (`--table items`)

Source: `src/data/items.json` (the `items` array, one record per
`ITEM_*`). Example: raise the Iron Sword's might:

```json
{
  "item": "ITEM_SWORD_IRON",
  "weaponType": "ITYPE_SWORD",
  "attributes": ["IA_WEAPON"],
  "might": 6,
  "range": { "min": 1, "max": 1 }
}
```

`nameTextId`/`descTextId`/`useDescTextId` are range-checked against the
live `MSG_COUNT` bound; `attributes`/`requiredWexp`/`weaponType` are
resolved symbolically. Output: `data_items.c`.

Text IDs may also be authored **symbolically** as `MSG_*` names resolved
against `include/constants/msg.h`:

```json
{ "item": "ITEM_SWORD_STEEL", "nameTextId": "MSG_SAVE_COMPAT_BACK" }
```

(any `MSG_*` constant the live header defines; the one above is only a
spelling example). An unknown symbol fails the data build with an actionable
diagnostic instead of silently repointing the item at whatever text later
lands on that number.
The 206 vanilla records keep the plain-integer form and still round-trip
byte-for-byte.

**Framework-authored (expansion) records must not add a message at all.**
`texts/texts.txt` feeds one shared, Huffman-compressed blob, so appending a
message re-encodes the text of every build, default ones included. Leave the
text IDs unset on such a record and author its display text through the
config-gated content path instead -- see `docs/starter_features.md` for the
bundled worked example.


For the merged typed example, add the symbolic ID to
`include/constants/items_expansion.h`, author `ITEM_EXPANSION_CE` in
`src/data/items_expansion.json`, and use `authoringName`/
`authoringDescription`/`authoringUseDescription`. With
`EXPANSION_STARTER_CONTENT=1`, `python3 -m scripts.generated_data
content-text` produces the build-local typed name table and audit catalog;
with the flag off it removes stale outputs and writes nothing. The profile
also requires mechanics hooks and an active item cap reaching `0xCE`; see
[`starter_features.md`](starter_features.md) for the exact matrix and
in-game-description boundary.

### Author expansion-localized UI strings

Expansion framework UI text is independent of item `MSG_*` IDs. Add an
append-only message record to `texts/expansion/registry.json`, add matching
English text to `texts/expansion/catalog.en.json`, then run:

```sh
make localization-validate
make localization-generate
make localization-test
```

`qps-ploc` is derived from English and never hand-authored. Reserved locale
slots have no translation content today; enabling a future real locale also
requires its complete catalog and equivalent host/runtime matrix. See
[`localization.md`](localization.md).

### Add or modify a **support** (`--table supports`)

Source: `src/data/supports.json`. A record is one owner and its parallel
`characters[]`/`supportExpBase[]`/`supportExpGrowth[]` arrays (all must be
the same length, `<= UNIT_SUPPORT_MAX_COUNT`, and `supportCount` must
match). Add a partner to Eirika:

```json
{
  "owner": "CHARACTER_EIRIKA",
  "symbol": "SupportData_Eirika",
  "characters": ["CHARACTER_EPHRAIM", "CHARACTER_SETH"],
  "supportExpBase": [30, 25],
  "supportExpGrowth": [4, 3],
  "supportCount": 2
}
```

Supports are **reciprocal**: if Eirika lists Ephraim with a given
`(base, growth)`, Ephraim's record must list Eirika back with the same
pair -- edit both directions together. Mismatched parallel-array
lengths, an out-of-capacity `supportCount`, a duplicate owner, a
one-directional/asymmetric support, or an unknown `CHARACTER_*` are
each reported with a `file:line:column` location. Output:
`data_supports.c`.

---

## Chapter 2 vertical slice

### Add or modify a **unit group** (`--table units`)

Source: `src/data/ch2_units.json` (the `groups` array; each `symbol` is a
`UnitDef_*` list of placed units with `charIndex`/`classIndex`/
`allegiance`/position/`items[]`/`redas[]` reinforcement records). Every
`charIndex`/`classIndex`/`items` entry resolves symbolically; item lists
and `redas` are capacity-checked. Output: `data_ch2_units.c`.

### Add or modify a **shop** (`--table shops`)

Source: `src/data/ch2_shops.json` (the `shops` array; each `symbol` is a
`ShopList_*` with an `items[]` list). Add a stock item:

```json
{
  "symbol": "ShopList_Event_Ch2Armory",
  "items": ["ITEM_SWORD_SLIM", "ITEM_SWORD_IRON", "ITEM_LANCE_IRON"]
}
```

Output: `data_ch2_shops.c`.

### Add or modify a **trap** (`--table traps`)

Source: `src/data/ch2_traps.json` (the `traps` array; each `symbol` is a
`TrapData_*` with an `entries[]` list, capacity-checked against
`TRAP_MAX_COUNT`). Output: `data_ch2_traps.c`.

### Add or modify an **event symbol** (`--table eventscripts`)

Source: `src/data/ch2_eventscripts.json` (the `scripts` array). This is a
metadata-only table (no generated C): it declares each hand-written
`EventScr_*`/`EventListScr_*` symbol, its `owner` category
(`turn_based`, `character_based`, `location_based`, `misc_based`,
`tutorial`, ...), `kind`, and the header that declares it. Adding an entry
here is what lets an `eventlists` list reference that symbol.

### Add or modify an **event-list** (`--table eventlists`)

Source: `src/data/ch2_eventlists.json` (the `lists` array + the 30-entry
`tutorial` list + the `Ch2Events` manifest). Each list entry is a
`{ "macro": ..., "args": [...] }` event macro call. String args that name
a symbol (e.g. `"EventScr_Ch2_Turn1Player"`) are resolved against the
`eventscripts` table; unknown symbols, an out-of-range tutorial list, or a
missing manifest field are each reported with a location. Output:
`data_ch2_eventlists.c`.

`eventlists` declares `dependency_tables()` (`units`, `shops`, `traps`,
`eventscripts`), so `validate` loads those tables automatically to resolve
cross-references. Override a dependency's source for local iteration with
`--dep-source NAME=PATH`.

#### Typed event-script helpers

The optional `helperScripts` array adds bounded, typed operations without
introducing another event router or bytecode language. Each entry has a
`symbol` and `entries`, where each entry is a
`{"helper": FAMILY, "operation": NAME, "args": [...]}` object; the generator
lowers it to one established `EAstdlib.h`/`eventscript.h` macro and appends
`ENDA`. An optional `owner` (`turn_based`, `character_based`,
`location_based`, `misc_based`, or `tutorial`) lets existing list/tutorial entries reference the
generated script with no separate eventscript declaration.
Helpers are also accepted as entries in the existing event-list arrays, where
they lower to `TURN`, `AFEV`, `AREA`, `Armory`, `Vendor`, or `SecretShop`.

Supported script families are:

* `flag.set` / `flag.clear` -> `ENUT` / `ENUF`;
* `unit.spawn_ally`, `unit.spawn_npc`, `unit.spawn_enemy` -> the matching
  `SPAWN_*` macro, and `unit.load1`..`unit.load4` -> `LOAD1`..`LOAD4`;
* `bgm.start`, `bgm.fade_in`, `bgm.override`, `bgm.restore` -> `MUSC`,
  `EvtBgmFadeIn`, `MUSS`, and `MURE`;
* `recovery.set_hp` -> `SET_HP` (the established event-slot-1 HP contract);
* `escape.warp_out` -> `WARP_OUT`.

List helpers are `shop.armory`/`shop.vendor`/`shop.secret_shop`,
`turn.event`, `flag.event`, and `escape.area`. IDs resolve against the live
character, song, event-flag, faction, event-script, shop, and unit tables;
coordinates and command widths are range checked. Unknown families,
operations, references, arity, types, or ranges report the source
`file:line:column` and JSON breadcrumb. The default Chapter 2 source has no
`helperScripts`, so its generated C remains unchanged.

### Compose the whole chapter: the **chapter bundle** (`--table chapterbundle`)

Source: `src/data/ch2_bundle.json`. This is the coherence layer: it names
the chapter (`CHAPTER_L_2`, its `chapterSettingsIndex`, etc.), the
`Ch2Events` manifest, and every `units`/`shops`/`traps`/`eventscripts`
symbol the chapter uses, and cross-checks that the whole slice is
internally consistent as one bundle (every referenced symbol exists and is
reachable, support owners are reciprocal, referenced character/class/item
IDs exist). It is metadata-only (no generated C) -- its job is the
whole-bundle drift gate `make generated-data-ch2-check`.

### Add typed **chapter objectives and AI groups** (`--table chapterobjectives`)

Source: `src/data/chapter_objectives.json`. This modern-only generated table
is empty by default: existing chapters have no objective/group record and keep
their behavior unchanged. A future chapter adds one bundle with a stable
symbol, uppercase machine IDs, bounded `aiGroups`, and bounded `objectives`:

```json
{
  "chapter": "CHAPTER_L_2",
  "symbol": "ChapterObjectives_ProjectChapter2",
  "aiGroups": [{
    "id": "AI_GROUP_PROJECT_ESCORT",
    "members": [{
      "character": "CHARACTER_EIRIKA",
      "unitGroup": "UnitDef_Event_Ch2Ally"
    }]
  }],
  "objectives": [{
    "id": "OBJECTIVE_PROJECT_REACH",
    "kind": "reach_area",
    "group": "AI_GROUP_PROJECT_ESCORT",
    "area": { "xMin": 4, "yMin": 2, "xMax": 6, "yMax": 4 }
  }],
  "dependencies": {
    "characters": ["CHARACTER_EIRIKA"],
    "eventFlags": [],
    "unitGroups": ["UnitDef_Event_Ch2Ally"]
  }
}
```

The only initial kinds are `protect` (a character plus another objective),
`reach_area`, `defeat_group`, `event_flag`, and `hold_until_turn`. Each
chapter is limited to eight objectives, eight AI groups, and 16 members per
group. A group member names both a `CHARACTER_*` and a `UnitDef_*` symbol;
the objective schema resolves that symbol through the owning chapter bundle's
`tables.units.symbols`, so dangling, mismatched, and cross-chapter references
fail with the member's source location and JSON breadcrumb. The required
`dependencies` lists are exact declarations: missing, duplicate, stale,
empty, over-capacity, contradictory, cyclic, invalid-area, or unknown
references fail with a source location and JSON breadcrumb.

`activationFlag` and `deactivationFlag` use existing `EVFLAG_*` state. Set
or clear those values only through the existing `helperScripts` `flag.set` /
`flag.clear` operations or established event scripts; objectives introduce no
event language, chapter manifest, router, or hidden runtime activation bit.
`protect` requires distinct existing `failureFlag` and `completionFlag`
values. It latches a protected member's death, missing, or rescued state only
while its completion objective remains pending; its first completion success
latches `completionFlag`, so success stays terminal even if a dynamic
completion condition later regresses. `hold_until_turn` requires its own
`failureFlag`; once its deadline arrives without that latch it is terminal
success, including after later departure. Both reconstruct through
Suspend/Resume without new save data. State-mutating evaluation starts only
after the engine's beginning events complete, so beginning-event unit loads
cannot create setup-time failure latches.
For example, a continuous hold uses
`"failureFlag": "EVFLAG_PROJECT_ESCORT_FAILED"` alongside its `group`,
`area`, and `untilTurn`; the flag must be a project-defined existing
`EVFLAG_*` value and cannot be shared with that objective's activation,
deactivation, or event flag.
Every authored `src/data/*_bundle.json` is loaded and indexed by chapter
identity. Its `chapterObjectives` declaration is the ownership declaration
for that chapter's symbols; a matching owner must exist exactly once, and its
`tables.units.symbols` is the only unit-group set the objectives may use. Each
bundle's `units`, `eventlists`, and related table dependencies are loaded from
that bundle's own declared `TableRef.source` paths, not from another chapter's
default table. The canonical objective source path loaded by the generator
must exactly equal `chapterObjectives.source`; a same-named record from an
unrelated file is rejected. Directory inputs load `*_objectives.json` and
`*_bundle.json` in sorted order while retaining each record's source identity.
Keep the declaration empty when the chapter has no authored records.

The modern objective hook is enabled only when that same canonical objective
loader finds at least one record. A nonempty file and a nonempty sorted
directory therefore compile the map/phase telemetry hooks identically; an
empty record set omits them. Malformed or unreadable sources stop Make with
the loader diagnostic instead of silently selecting the disabled path.

`reach_area` and `hold_until_turn` rectangles must fit the owning chapter's
actual map dimensions. Validation resolves the chapter's `mainLayerId` through
`gChapterDataAssetTable`, then reads the map's authored TMX or layout metadata;
coordinates at the last valid tile are accepted, while a partially or wholly
off-map rectangle is rejected with the offending coordinate's JSON breadcrumb.
The build discovers these objective/bundle sources, every per-bundle table
source, chapter settings, asset table, map manifest, and authored TMX/layout
metadata deterministically, so changing any live validation input reruns
generation rather than reusing stale output.

```sh
python3 -m scripts.generated_data validate --table chapterobjectives
python3 -m scripts.generated_data generate --table chapterobjectives
python3 -m scripts.generated_data check --table chapterobjectives
make generated-data-ch2-check
```

Generated C is linked only by the modern framework. It emits a 12-byte bundle
record per authored chapter, 12 bytes per AI group plus one byte per member,
and 28 bytes per objective; the default empty table contains only its
12-byte sentinel. Runtime state is one 16-byte EWRAM telemetry record, never
save data. Each authored telemetry refresh uses a 1 KiB stack unit index and
scans the 255 unit slots once, replacing per-member character scans while
remaining within the 4 KiB stack bound. IDs are source-owned uppercase machine
IDs; their checked FNV-1a value is telemetry-only and needs no localization.

---

## Custom C symbols / callbacks (escape hatch)

When a field must reference existing hand-written C (a callback pointer, a
shared constant/table) rather than a value the generator invents, the
schema declares it as a `CSymbolRefField` bound to a specific header. The
validator rejects malformed identifiers and any symbol not *declared* in
that header (an allowlist by construction), and the generator emits the
name as a bare C token (unquoted) so it compiles as a real reference. This
is how you point generated data at hand-owned code without editing any
generated output. See `scripts/generated_data/escape_hatch.py` and its
tests for the end-to-end contract.

---

## What "done" looks like

```sh
make generated-data-check   # per-table drift + aggregate manifest/budget gate, all OK
make generated-data-test    # full unittest suite green
```

CI runs `make generated-data-check` on every push (`.github/workflows/build.yml`),
so a stale inventory, a broken reference, a capacity/budget overflow, or a
manifest drift fails fast with an actionable diagnostic before the slower
ROM linker gate runs.

## Tester-facing procedures

[`TC-CORE-004`](test-cases/core-framework.md#tc-core-004-generated-data-loop-reports-diagnostics)
covers the disposable authoring validate/generate/check/test loop and its
cross-table diagnostic control. [`TC-CORE-010`](test-cases/core-framework.md#tc-core-010-typed-authoring-lowers-through-existing-routes)
covers the typed class, unit, event-list, and helper lowering routes without
changing the default Chapter 2 source behavior.
