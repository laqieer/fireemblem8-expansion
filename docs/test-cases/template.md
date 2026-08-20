# Tester-facing case template

Copy this template for a new stable case. Replace every bracketed value,
retain every section, add the record to `registry.json`, and update the
feature's authoritative reference document to link here.

## TC-AREA-001: Short observable title

- **Feature / originating issue:** [feature ID] / [issue URL].
- **Supported configuration or artifact:** [documented source profile; an
  optional artifact may be named but cannot be required].
- **Prerequisites and clean starting state:** [requirements].

### Actions

1. [Exact input or action.]
2. [Exact input or action.]

### Expected result

[Observable result.]

### Negative control

[Default/disabled or pre-fix control and its observable result. If genuinely
inapplicable, state why.]

### Interactions and save compatibility

[Dependencies, conflicts, feature interactions, and save expectations.]

### Automation

[Exact deterministic command/scenario/test and what it proves. Name a precise
manual-only visual, audio, or UX criterion only when automation cannot assert
it. In `registry.json`, omit or leave `automation` empty only with a
non-placeholder `manual_only_reason` that names the criterion and why a human
judgment is necessary.]

### Cleanup and limitations

[Reset/cleanup, known limitations, and unsupported configurations.]
