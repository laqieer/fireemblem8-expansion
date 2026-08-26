# Presentation, audio, and utility cases

These procedures backfill the reusable, shipped seams owned by
[issue #58](https://github.com/laqieer/fireemblem8-expansion/issues/58).
They use bounded source fixtures where the committed registry deliberately
contains no project-authored mappings. A fixture demonstrates the public
resolver or validator, not campaign content; never retain it in a production
registry.

## TC-PORTRAIT-001: Resolve ordered portrait and minimug rules

- **Feature / originating issue:** `portrait-minimug-resolver` /
  [issue #35](https://github.com/laqieer/fireemblem8-expansion/issues/35).
- **Supported configuration or artifact:** default modern source checkout
  with a host C compiler; no portrait asset replacement, ROM, or save.
- **Prerequisites and clean starting state:** start with the committed empty
  `gExpansionPortraitRules` registry. The automated probe creates its
  three-rule fixture under `build/test-artifacts/portrait-resolver-native/`.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_issue34_35_resolvers.PortraitResolverContractTests -v`.
2. Inspect the fixture result: the first matching character rule wins over a
   later class/chapter/flag-specific rule; a required flag selects its rule;
   a forbidden flag excludes it.

### Expected result

The extracted production resolver returns the expected full portrait and
minimug IDs, honoring declaration order and all character/class/chapter/flag
selectors. The test process removes its fixture directory.

### Negative control

A required/forbidden flag conflict does not match, and an invalid full-portrait
ID makes the registry invalid rather than selecting an out-of-range asset.

### Interactions and save compatibility

This has no dependencies or conflicts with optional profiles, changes no
save bytes, and only resolves existing portrait IDs. It does not add graphics
or alter the chapter-specific legacy substitution.

### Automation

The named unittest compiles and runs extracted production resolver functions
against a disposable fixture. It also checks consumer routing and the default
empty-registry contract; no subjective visual judgment is claimed.

### Cleanup and limitations

The probe deletes `build/test-artifacts/portrait-resolver-native/`. Do not
copy fixture rows into `src/expansion_portraits.c`; production remains empty.

## TC-PORTRAIT-002: Fall back for empty or invalid portrait registries

- **Feature / originating issue:** `portrait-minimug-resolver` /
  [issue #35](https://github.com/laqieer/fireemblem8-expansion/issues/35).
- **Supported configuration or artifact:** default modern source checkout
  with a host C compiler; no ROM or saved state.
- **Prerequisites and clean starting state:** use the stock empty registry
  or the temporary invalid row constructed by `TC-PORTRAIT-001`.

### Actions

1. Run the command in `TC-PORTRAIT-001`.
2. Confirm the fixture makes a full ID `0xAD` invalid and then resolves the
   legacy minimug; confirm its chapter `0x22` unit context substitutes legacy
   full portrait `0x4A` with `0x46`.

### Expected result

Empty and invalid registries safely use legacy character/class/minimug
behavior, while valid minimugs remain within `0x7F00..0x7F07`.

### Negative control

An invalid registry must not partially apply its earlier rows or bypass the
legacy fallback.

### Interactions and save compatibility

The resolver is read-only and preserves all existing chapter substitutions;
there is no save, localization, or resource-budget impact.

### Automation

`TC-PORTRAIT-001`'s host probe performs the invalid-bound and substitution
assertions against the production resolver.

### Cleanup and limitations

The fixture is deleted by the test. It proves resolver semantics only; stock
campaign portrait art remains outside this content-free framework case.

## TC-SOUND-001: Persist expanded sound-room unlocks safely

- **Feature / originating issue:** `expanded-sound-room` /
  [issue #36](https://github.com/laqieer/fireemblem8-expansion/issues/36).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3; no ROM or real SRAM image.
- **Prerequisites and clean starting state:** no save file is required. The
  test constructs all `0x24`-byte sound-room records in memory.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_issue36_soundroom -v`.
2. Verify the synthetic legacy record unlocks IDs `0`, `127`, `128`, and
   `255`, then migrates only its representation marker after a permitted
   write.

### Expected result

IDs through `255` are valid, visibility remains distinct from playability,
the legacy checksum domain and all unlock bits survive migration, and a
current record is idempotent.

### Negative control

IDs below `0` or above `255`, a bad checksum, and unknown marker `0x0802`
are rejected without a guessed migration.

### Interactions and save compatibility

The record remains at SRAM offset `0x7224`, size `0x24`; locale, casual-mode,
and `ExpansionUserPrefs` records are untouched. No song or catalog content is
added.

### Automation

The named unittest exercises byte-level state, visibility/playability
bookkeeping, migration, and corruption rejection through synthetic memory
only; it never writes real SRAM.

### Cleanup and limitations

All fixture bytes are in memory and disappear when the test exits. The
framework does not claim a stock campaign condition for a custom visible
locked entry.

## TC-BGM-001: Keep an empty BGM registry inert and honor explicit requests

- **Feature / originating issue:** `typed-bgm-routing` /
  [issue #37](https://github.com/laqieer/fireemblem8-expansion/issues/37).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3 and a host C compiler; `preserve` continuation policy.
- **Prerequisites and clean starting state:** keep
  `src/data/bgm_registry.json` empty. The native test owns a temporary
  in-process fixture and leaves the source JSON unchanged.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_issue37_40_bgm -v`.
2. Run `make bgm-registry-check`.
3. Confirm the native fixture resolves an ordinary matching context, then
   returns the explicit song when `hasExplicitSong` is set.

### Expected result

The committed empty registry validates and leaves ordinary legacy songs
unchanged; explicit event/battle-animation requests remain authoritative and
`SONG_NONE` remains a stop request.

### Negative control

An explicit request must not be replaced by a matching fixture variant, and
an empty registry must not introduce a new song.

### Interactions and save compatibility

The BGM router has no sound-room persistence dependency and no save impact.
It shares its continuation configuration with `TC-BGM-004`; there are no
other conflicts.

### Automation

The named unittest compiles and runs extracted production routing functions,
validates the empty authored JSON, and verifies generated output freshness.

### Cleanup and limitations

The native artifact directory is deleted by the test. The fixture is not
campaign content and cannot be used to judge music quality.

## TC-BGM-002: Select chapter and flag variants deterministically

- **Feature / originating issue:** `typed-bgm-routing` /
  [issues #37](https://github.com/laqieer/fireemblem8-expansion/issues/37)
  and [#38](https://github.com/laqieer/fireemblem8-expansion/issues/38).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3 and a host C compiler.
- **Prerequisites and clean starting state:** use the test's disposable
  variant fixture; do not edit the committed empty registry.

### Actions

1. Run the unittest command in `TC-BGM-001`.
2. Observe its chapter-7 generic row, two equal-priority chapter/flag rows,
   and `CheckFlag(5)` transition.

### Expected result

The flag-specific higher-priority row wins when flag `5` is set; equal
priority rows retain declaration order. Wildcards use explicit masks, so
legal zero-valued IDs do not collide with “any”.

### Negative control

The validator rejects flags `0`, `41`, `100`, and `301`, out-of-range
chapters, unknown contexts, and unknown songs before generation.

### Interactions and save compatibility

Variant lookup precedes only ordinary fallback routing; it never overrides an
explicit request and has no save-format effect.

### Automation

`test_issue37_40_bgm` runs the native priority/source-order probe and the
Python validator’s legal-boundary and invalid-row cases.

### Cleanup and limitations

The test cleans its generated probe. No generic stock-game route exposes a
project-authored chapter/flag mapping while the production registry is empty.

## TC-BGM-003: Resolve dancer and staff selectors by specificity

- **Feature / originating issue:** `typed-bgm-routing` /
  [issues #37](https://github.com/laqieer/fireemblem8-expansion/issues/37)
  and [#40](https://github.com/laqieer/fireemblem8-expansion/issues/40).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3 and a host C compiler.
- **Prerequisites and clean starting state:** use the native selector fixture
  from `TC-BGM-001`; no save or registry edit is needed.

### Actions

1. Run the unittest command in `TC-BGM-001`.
2. Confirm the character/class dancer selector beats the generic dancer row,
   equal-specificity ties retain source order, and the higher-priority
   staff-kind/item selector wins.

### Expected result

Selector priority, then specificity, then declaration order decide the song;
an absent staff kind falls back to the legacy song.

### Negative control

An invalid action or a staff selector with `STAFF_NONE` cannot claim the
action; unknown staff kinds and selector IDs fail validation.

### Interactions and save compatibility

Selectors are independent of sound-room persistence and of BGM continuation.
They use the ordinary action fallback and do not mutate game state or saves.

### Automation

The host-native extracted-source fixture and validator tests in
`test_issue37_40_bgm` exercise both positive selection and the fallback.

### Cleanup and limitations

The test removes its artifact. It proves routing choices, not audio playback
or a subjective assessment of any song.

## TC-BGM-004: Apply continuation policy and preserve stop requests

- **Feature / originating issue:** `typed-bgm-routing` /
  [issue #39](https://github.com/laqieer/fireemblem8-expansion/issues/39).
- **Supported configuration or artifact:** modern debug source profile with
  each of `preserve`, `resume`, and `restart`; no save is needed.
- **Prerequisites and clean starting state:** configure one policy at a time
  with `./configure --with-bgm-continuation-policy=<policy>` or use the
  corresponding `EXPANSION_BGM_CONTINUATION_POLICY` make override.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_issue37_40_bgm -v`.
2. For each supported policy, run
   `python3 scripts/modernize/expansion_config.py resolve --config debug --abi aapcs --rom-size 16M --bgm-continuation-policy <policy>`.

### Expected result

`preserve` leaves silence or an already resolved song alone, `resume` starts
when silent and changes only when needed, and `restart` fades the resolved
song even when current. `SONG_NONE` always takes the stop path.

### Negative control

An invalid policy fails configuration resolution; `SONG_NONE` is not passed
through variant or selector routing.

### Interactions and save compatibility

The permanent policy changes configuration identity only, not save
compatibility. It composes with ordinary BGM variants and selectors.

### Automation

The named unittest checks the distinct production continuation branches and
`SONG_NONE` callers; the configuration resolver proves accepted values change
identity and rejects invalid values.

### Cleanup and limitations

Remove any generated `config.autotools.mk`/`GNUmakefile` only if local
configuration was run. The case asserts call behavior, not human preference
for a fade or transition.

## TC-BANIM-001: Apply standard, reduced, and off battle presentation

- **Feature / originating issue:** `battle-animation-presentation` /
  [issue #41](https://github.com/laqieer/fireemblem8-expansion/issues/41).
- **Supported configuration or artifact:** default modern source checkout
  with a host C compiler; no save, ROM, or optional feature profile.
- **Prerequisites and clean starting state:** no fixture data or game state
  is required; the test compiles extracted policy functions under
  `build/test-artifacts/banim-policy-runtime/`.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_banim_policy_native -v`.
2. Inspect the standard, reduced, and off semantic outputs in the native
   probe: standard retains all numbers/effects, reduced leaves damage only,
   and off suppresses every number and hit effect.

### Expected result

Reduced uses bounded duration scaling and removes hit/crit numbers and palette
flash; off suppresses hit effects and all combat numbers without assets.

### Negative control

The default Animation option maps to the existing standard policy; unsupported
animation values and invalid policy fields do not select a new effect.

### Interactions and save compatibility

The existing Animation setting remains authoritative and persists normally.
The optional utility preference in `TC-UTILITY-001` can select a policy but
does not change locale, sound-room, or casual records.

### Automation

The native probe executes the real policy functions. No manual visual/audio
criterion is needed because the case asserts the semantic number/effect state.

### Cleanup and limitations

The test removes its probe directory. It does not assert that a player likes
the visual style.

## TC-BANIM-002: Reject invalid presentation resources and persist selections

- **Feature / originating issue:** `battle-animation-presentation` /
  [issue #41](https://github.com/laqieer/fireemblem8-expansion/issues/41).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3 and a host C compiler.
- **Prerequisites and clean starting state:** no SRAM image is needed; the
  preference matrix uses synthetic 12-byte records.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_banim_policy_native scripts.modernize.tests.test_ui_registry_contract scripts.modernize.tests.test_expansion_user_prefs_native -v`.
2. Confirm the probe rejects VRAM above `0x8000`, palette slots above `16`,
   OAM above `128`, effect speed above `8`, and invalid timing ranges.

### Expected result

Only bounded policies validate; a selected valid policy round-trips through
the existing `ExpansionUserPrefs` selection bytes and saved Animation option.

### Negative control

Unknown policy IDs, unsupported extension bytes, and out-of-mask utility
bits classify as invalid rather than being silently applied.

### Interactions and save compatibility

This reuses the fixed 12-byte preferences record and its checksum; it changes
no offset, epoch, locale semantics, sound-room record, or casual marker.

### Automation

The native policy and user-preferences matrices execute real C functions,
while the UI contract test checks persistence dispatch.

### Cleanup and limitations

Probe artifacts are removed by the tests. The resource fields are validation
metadata, not a reservation of palette, OAM, or VRAM.

## TC-MANIFEST-001: Resolve localized presentation manifest fallbacks safely

- **Feature / originating issue:** `ui-presentation-manifests` /
  [issue #43](https://github.com/laqieer/fireemblem8-expansion/issues/43).
- **Supported configuration or artifact:** default modern source checkout
  with Python 3 and a host C compiler; no custom screen assets or save.
- **Prerequisites and clean starting state:** use committed
  `src/data/ui_presentation.json`. Schema tests clone it in memory and write
  only removable generated artifacts under `build/test-artifacts/`.

### Actions

1. Run `python3 -m unittest scripts.generated_data.tests.test_ui_presentation -v`.
2. Run `make generated-data-check`.
3. Confirm the default chapter-title and screen entries resolve localized
   keys with static UTF-8 fallback text and no asset requirement.

### Expected result

Fallback strings emit deterministically with escaped controls, quotes,
backslashes, and UTF-8 bytes. Optional missing assets use text fallback, and
the manifest remains within its 32-record cap.

### Negative control

Required resources without an asset ID, unknown localization keys, NUL text,
resource-cap overflow, and a 33rd record fail validation.

### Interactions and save compatibility

Manifests are generated presentation data; they do not change locale
persistence, save layout, or default static rendering unless a project calls
the existing resolver seam.

### Automation

The schema test compiles generated fixture C and covers output escaping,
bounds, localization-key validation, and capacity failures.

### Cleanup and limitations

The test deletes its artifacts. It proves data and resolver inputs, not a
project-specific screen or asset package.

## TC-UTILITY-001: Persist available utility preferences without record interference

- **Feature / originating issue:** `unified-utility-preferences` /
  [issue #44](https://github.com/laqieer/fireemblem8-expansion/issues/44).
- **Supported configuration or artifact:** default modern source checkout
  and the optional `EXPANSION_DANGER_OVERLAY_MENU=1` profile for Threat
  Range; Python 3 and a host C compiler.
- **Prerequisites and clean starting state:** use synthetic preference records
  only. Start default profile with Danger unavailable, then test the
  opt-in profile separately.

### Actions

1. Run `python3 -m unittest scripts.modernize.tests.test_ui_registry_contract scripts.modernize.tests.test_expansion_user_prefs_native -v`.
2. Run `python3 scripts/modernize/expansion_config.py resolve --config debug --abi aapcs --rom-size 16M --danger-overlay-menu 0`, then repeat with `--danger-overlay-menu 1`.
3. Verify the native matrix accepts policy `2`, utility bit `1`, and schema
   `1`, while older/schema-zero and unknown/corrupt values default safely.

### Expected result

Battle presentation is always available; Danger is unavailable by
default and becomes selectable only in its existing opt-in profile. Valid
selections persist through `ExpansionUserPrefs` without changing other
records.

### Negative control

Unknown policy IDs, utility bits outside the mask, newer selection schemas,
and unavailable Danger are rejected or safely defaulted.

### Interactions and save compatibility

The single descriptor registry has no competing settings screen. It preserves
locale selection/checksum behavior and does not reuse sound-room or casual
save bytes.

### Automation

The UI registry contract and native preference matrix cover descriptor bounds,
unavailable behavior, current/legacy/unknown records, and checksum-preserving
selection writes; the config resolver validates both utility profiles.

### Cleanup and limitations

No persistent fixture or save is created. The case does not enable complete
catalog coverage and does not claim a subjective UX preference as pass/fail.
