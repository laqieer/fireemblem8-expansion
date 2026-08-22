# Typed BGM routing

Issues #37-#40 share the public `include/expansion_bgm.h` seam. Callers use
`ExpansionBgm_Start`/`ExpansionBgm_Change` for ordinary contexts and
`ExpansionBgm_StartExplicit`/`ExpansionBgm_ChangeExplicit` or
`ExpansionBgm_Override` for explicit event or battle-animation overrides.
Explicit requests are authoritative; the chapter/flag registry is consulted
only when a caller supplies a fallback song.

`src/data/bgm_registry.json` is the narrow, validated authoring surface. It
accepts chapter/context/flag variants and typed dancer/staff selectors. The
committed registry is empty, so default behavior is inert and retains the
legacy map, menu, battle, dance, staff, event, and world-map songs. Validate
or regenerate it with:

```bash
python3 scripts/modernize/bgm_registry.py validate
python3 scripts/modernize/bgm_registry.py check
make bgm-registry-check
```

Variants select the highest priority matching row; ties retain source order.
Omitted `chapter` and `flag` fields are represented by explicit match masks,
not sentinel IDs, so legal zero-valued IDs and character/class boundary IDs
cannot collide with wildcards. Flag references must be in the runtime
`CheckFlag` spaces: chapter flags `1..40` or permanent flags `101..300`;
`0` and `100` are non-stored sentinels and are rejected.
Selectors select highest priority, then the most specific unit/class/item row,
then retain source order. Invalid context, flag, chapter, selector, or song
IDs fail before a build can use the registry.

`EXPANSION_BGM_CONTINUATION_POLICY` is a permanent configuration choice:
`preserve` (default) returns when the player is silent or already playing the
resolved song, and transitions when a different song is active; `resume`
starts the resolved song when silent and otherwise transitions only when the
active song differs; and `restart` forcibly fades in the resolved song even
when it is already current. `SONG_NONE` is always a stop request and is never
passed through variant or selector routing. The choice
participates in configuration identity, not save compatibility. Use
`./configure --with-bgm-continuation-policy=...` or a one-off
`make EXPANSION_BGM_CONTINUATION_POLICY=...`.

Issue #36's expanded sound-room persistence is intentionally not included.
These APIs leave room for that future catalog to provide unlock metadata.

## Tester-facing cases

[`TC-BGM-001` through `TC-BGM-004`](test-cases/presentation-audio-utility.md#tc-bgm-001-keep-an-empty-bgm-registry-inert-and-honor-explicit-requests)
cover the disposable router/variant/selector fixtures, the intentionally
empty committed registry, explicit requests, continuation policies, and stop
requests. They validate selection semantics rather than subjective audio.
