# GBA playtest feature-seam evidence

Issue [#157](https://github.com/laqieer/fireemblem8-expansion/issues/157)
replaces sixteen issue-99 `gba-playtest-runtime` source-text audit records.
It changes no feature default, gameplay behavior, save format, generated game
data, localization payload, ROM scenario, or target-runtime contract.

`TC-TEST-QUALITY-001` governs this migration: behavior-preserving refactors
remain green, while dispatch, gate, provider, probe, and configuration
mutations fail the replacement evidence.

| Issue #157 audit IDs | Replacement evidence | Positive control | Adversarial control |
| --- | --- | --- | --- |
| `I157-AOE-001` through `I157-AOE-004` | Real enabled/disabled AoE host drivers; ARM object section/symbol checks; ELF relocation records for every production dispatch caller; parsed config identity and generated metadata JSON. | The reference provider handles a routed action, each production caller retains its required `ExpansionAoE_DispatchItem` relocation, and enabled metadata records the AoE flag. | Disabled reference omits its probe/effect symbol; invalid flag `2`, archival ABI, invalid shape/filter, route reentry, and capacity overflow fail. |
| `I157-DANGER-001` through `I157-DANGER-007` | Default/enabled compiled menu and player-phase objects, symbol/section evidence, parsed configuration identity, and existing danger-overlay probe procedures. | Enabled menu adds one bounded row, defines the wrapper, relocates to the original danger-zone effect, and emits the 20-byte modern probe. | Default object omits wrapper references, default probe data is zero, legacy-like object emits no EWRAM probe, and invalid flag `2` fails parsing. |
| `I157-MECHANICS-001` through `I157-MECHANICS-005` | Real registry/sample/disabled host drivers, enabled/default battle-object symbol boundaries, parsed configuration identity, and ARM C89 compilation. | Registered hooks alter the real battle-defense field and enabled battle code relocates only to the generic mechanics seam. | Disabled registry/probe stays inert, registration during apply fails, invalid hook/sample profiles fail parsing, and battle code has no starter-content relocation. |

The existing feature procedures remain authoritative:
`TC-GAMEPLAY-001` through `TC-GAMEPLAY-003` and
`TC-GAMEPLAY-006`. Their focused host suites are the mapped automation; no
new tester action or ROM scenario is required for this evidence-only change.

## Rollback

Revert this issue's test and evidence-map commit to restore the prior checks.
That rollback changes no ROM, save, generated game-data output, configuration
identity, or runtime scenario.
