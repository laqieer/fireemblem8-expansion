# Portrait and minimug resolver (issue #35)

All framework portrait consumers use the typed API in
`include/expansion_portraits.h`:

* `ExpansionPortrait_ResolveUnit` resolves a unit context.
* `ExpansionPortrait_ResolveUnitWithFlags` resolves a unit with caller-owned
  rule flags.
* `ExpansionPortrait_ResolveCharacter` resolves character-only consumers.
* `ExpansionPortrait_Resolve` accepts explicit character, class, chapter, and
  flag selectors.

`src/expansion_portraits.c` owns the project-editable static rule registry.
Rules are evaluated in declaration order; a matching nonzero full-portrait or
minimug ID wins. Use `EXPANSION_PORTRAIT_MATCH_ANY` and
`EXPANSION_PORTRAIT_CHAPTER_ANY` for wildcards, and required/forbidden flag
masks for deterministic flag rules. `ExpansionPortrait_ValidateRegistry`
rejects zero character/class selectors, overlapping flag masks, reserved bytes,
and IDs outside the current full-portrait (`1..0xAC`) or generic minimug
(`0x7F00..0x7F07`) ranges.

An empty or invalid registry falls back to the legacy character portrait,
class default portrait, and character minimug behavior; unit contexts also
retain the existing chapter-specific substitution. The resolver changes no
save data and adds no graphics assets. It has no dependency or conflict with
the optional gameplay/config flags. Project rules should be added to the
registry rather than introducing consumer-specific conditionals.

## Tester-facing cases

[`TC-PORTRAIT-001` and `TC-PORTRAIT-002`](test-cases/presentation-audio-utility.md#tc-portrait-001-resolve-ordered-portrait-and-minimug-rules)
exercise ordered fixture resolution, bounds, the empty/invalid-registry
fallback, and chapter substitution without adding portrait content.
