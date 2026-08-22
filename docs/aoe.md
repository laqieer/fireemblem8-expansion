# Typed area-of-effect framework (issue #42)

Issue #42 is a **framework capability** plus one **optional reusable reference
module**. The core is modern-build infrastructure for bounded target
selection, effect execution, and item routing. The bundled reference is a
default-off, project-neutral radius heal exposed only through its public test
entry point and runtime probe.

This is not a skill catalog, campaign spell, copied patch, item-content pack,
or general scripting language. It introduces no item, text, graphics, event,
animation, generated-data record, localization ID, or save field.

## Tester-facing cases

[`TC-GAMEPLAY-006`](test-cases/optional-gameplay.md#tc-gameplay-006-typed-aoe-reference-heals-bounded-allied-targets)
provides the complete enabled-reference and disabled-reference procedure,
including source profiles, exact target/effect values, cleanup, and the mapped
host/ROM gates. [`TC-GAMEPLAY-007`](test-cases/optional-gameplay.md#tc-gameplay-007-optional-gameplay-profiles-compose-without-changing-saves)
covers the AoE reference's independent composition with the other optional
gameplay modules. Both remain indexed in the tester-case catalog and preserve
the default-off configuration.

## Configuration

| Autoconf | Make | C | Default |
| --- | --- | --- | --- |
| `--enable-aoe-reference` | `EXPANSION_AOE_REFERENCE` | `FE8_EXPANSION_AOE_REFERENCE` | `0` |

The flag controls only `ExpansionAoEReference_Apply` and its booted semantic
probe. The probe symbol and its 100 bytes of EWRAM exist only when the
reference is enabled. The core API and empty item-route seam remain available
to all modern framework builds. Values other than exactly `0` or `1` fail
configuration and C compilation.

Dependencies: **none**. Conflicts: **none**. The reference does not require the
starter mechanics registry, expanded item IDs, starter content, localization,
or debug tools. Its enabled state participates in
`FE8_EXPANSION_CONFIG_FINGERPRINT`; it does not change
`EXPANSION_SAVE_COMPAT_EPOCH`.

```bash
./configure --enable-aoe-reference
make

# One-off equivalent
make expansion-modern-rom EXPANSION_AOE_REFERENCE=1
```

## Public targeting API

Include `include/expansion_aoe.h`.

`ExpansionAoE_BuildTargetSet` accepts:

* a validated diamond, square, or cross shape with inclusive minimum/maximum
  range and `EXPANSION_AOE_MAX_RADIUS == 5`;
* an explicit map position or a stable source-unit ID as origin;
* source/ally/enemy/any relation filters, damaged/status predicates, explicit
  inclusion flags for hidden/dead/undeployed/rescued units, and an optional
  typed predicate;
* optional mirroring into the existing `gBmMapRange` and selection-target
  list through `EXPANSION_AOE_BUILD_RANGE_MAP` and
  `EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST`.

Discovery iterates stable unit-ID slots rather than `gBmMapUnit`, so an actor
hidden by `UnitBeginAction` and valid hidden/rescued units are not lost. Each
stable unit ID is considered once; rescued units use their carrier's validated
on-map position and are excluded when that carrier link is invalid. The
hidden and rescued inclusion flags are independent, so a rescued unit requires
both flags because the engine marks it hidden as well.

The result holds at most `EXPANSION_AOE_MAX_TARGETS == 16`. Entries contain
only unit IDs, coordinates, and distance, never `struct Unit *` or proc
pointers. Ordering is deterministic: distance, then Y, X, and unit ID. The
builder records the complete count; if more than 16 units match, it returns
`EXPANSION_AOE_ERR_CAPACITY`, marks the retained diagnostic set incomplete,
and `ExpansionAoE_Execute` refuses to run it. Unknown shape, filter, condition,
or build-flag values fail closed.

## Effect execution contract

`ExpansionAoE_Execute` walks the immutable target set synchronously in public
order. Each callback returns applied, skipped, or failed.

| Policy | Contract |
| --- | --- |
| Partial failure | `CONTINUE` attempts later targets; `STOP` preserves earlier effects and stops. There is no rollback. Final outcome and counts are available to the batch event callback. |
| EXP | None, once, or per-applied; aggregated into one award callback and capped by `expCap`. Failed/skipped targets award nothing. |
| Animation | None, one callback per applied target in target order, or one batch callback after effects. |
| Event | None, once after any applied target, or once only after a complete no-failure traversal. |

Callbacks are call-local and synchronous. A project that needs proc-driven
animations or events starts them from the typed callbacks and owns their proc
lifetime; the AoE framework does not retain callback, target, unit, or proc
pointers after the call.

## Shared item/action/AI seam

Downstream projects author at most eight exact `ItemId` routes as a `const
struct ExpansionAoEItemRoute[]` plus a `const struct
ExpansionAoEItemRouteTable`, then provide one strong
`ExpansionAoE_GetItemRouteTable()` override. The framework's weak default
returns an empty table. The table, keys, policies, and callbacks stay in ROM;
the framework does not copy them into persistent EWRAM.

`ExpansionAoE_ValidateItemRouteTable` deterministically rejects an oversized
table, null entries, empty/overlong keys, item ID zero, item IDs above
`ITEM_ID_CONFIGURED_CAP`, unsupported policies, and duplicate item IDs or keys.
Dispatch validates the active table before using it, and table order is the
authored deterministic order.

The one dispatcher is called from:

* `CanUnitUseItem` and `DoItemUse`;
* `ActionStaffDoorChestUseItem`;
* staff and special-item AI selection.

An unregistered item returns `EXPANSION_AOE_ITEM_NOT_HANDLED` and continues
through the unchanged vanilla switch/LUT path. A registered handler owns all
four phases (`CAN_USE`, `BEGIN_USE`, `EXECUTE`, `AI_SELECT`) and must return
`HANDLED`, `REJECTED`, or `ERROR`; returning `NOT_HANDLED` after an exact route
matched is treated as a route error.

AI behavior is explicit per route:

* `EXPANSION_AOE_AI_NEVER` rejects AI selection without invoking the handler;
* `EXPANSION_AOE_AI_CALLBACK` invokes the handler, which must populate the
  ordinary engine AI decision state and return `HANDLED`.

The existing battle-stat mechanics registry is intentionally not reused: its
callback mutates one `BattleUnit` during stat calculation, while AoE routing
selects map units and executes item actions. Reusing it would couple
incompatible lifetimes and contracts. AoE instead reuses the established map,
target-list, action, and AI pipelines.

## Save and resume

The only supported route policy is
`EXPANSION_AOE_SAVE_ATOMIC_REBUILD`. Selection and execution complete
synchronously inside the ordinary action call. No transient target set,
callback pointer, `struct Unit *`, or proc pointer is serialized. After a
suspend/resume, a not-yet-executed action rebuilds targets from scalar action
state; a completed action is already represented by ordinary unit/item state.

No save struct, packed field, version, or migration changes. The config
fingerprint records the optional reference choice diagnostically, but save
compatibility remains governed only by `EXPANSION_SAVE_COMPAT_EPOCH`.

## Bundled reference

Include `include/expansion_aoe_reference.h`. When enabled,
`ExpansionAoEReference_Apply(sourceUnitId, ...)`:

* selects the source and damaged allies in a radius-2 diamond;
* heals each by exactly 3 HP in deterministic order;
* continues after an individual target failure;
* awards no EXP, starts no animation, invokes no event, and is never selected
  by AI.

It registers no item and changes no vanilla item behavior. The boot probe runs
once at a blue-phase start, temporarily damages two eligible allies, calls the
public reference function, records bounded scalar results, restores every
original HP value, and clears its temporary range/target-list output.
`EXPANSION_AOE_REFERENCE=0` compiles out both the effect callback and
`gExpansionAoEReferenceProbe`; the negative runtime control requires that
symbol to be absent rather than spending EWRAM on a zero-filled diagnostic.

## Resource bounds and compatibility

* Default/reference-disabled fixed EWRAM: at most 4 bytes for the dispatch
  reentrancy guard. Item routes and keys are ROM-authored and consume no
  persistent EWRAM.
* Reference-enabled fixed EWRAM: the same dispatch guard plus the 100-byte
  semantic probe, guarded together by a 128-byte object budget.
* Compared with the failing debug layout, the default/reference-disabled
  contract regains 420 bytes: 256 from the removed route array, 100 from the
  disabled probe, and 64 from the Sound Room transient-bitset change described
  in `docs/save_format.md`. The failing `en,ja` profile moved from 304 bytes
  overflow to 116 bytes free. Fresh successful debug profile reports record
  116 bytes free for `en,ja`, 116 for `en,zh-Hans`, 80 for
  `en,ja,zh-Hans`, and 44 for `en,ja,zh-Hans,qps-ploc`.
* Target-set stack object: at most 72 bytes; no dynamic allocation.
* Core plus enabled reference ARM `.text`: guarded by an 8 KiB object budget.
* No new save, SRAM, generated-data, localization, VRAM, palette, tile, OAM,
  animation, event-slot, or item-ID allocation.
* Modern debug and release AAPCS builds are supported. The archival agbcc
  linker does not include the new objects and retains its existing behavior.

## Validation

```bash
TMPDIR="$PWD/build/issue42-tmp" \
  python3 -m unittest tools.gba-playtest.tests.test_expansion_aoe -v

make expansion-modern-aoe-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-aoe-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

The host suite covers validation, all shapes/filters, deterministic ordering,
capacity refusal, partial failures, EXP caps, animation/event sequencing,
ROM-table errors, explicit AI non-use, enabled reference behavior, disabled
stubs, AAPCS compilation, and budgets. Each ROM gate runs the same real-map
route against an enabled profile and the ordinary default ROM, resolving the
enabled probe address from its ELF: enabled must heal and restore two units;
the disabled ELF must omit the probe symbol entirely.
