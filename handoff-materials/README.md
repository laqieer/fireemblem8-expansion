# Selected continuation records

Start with [the recovery handoff](../HANDOFF-2026-09-20.md), not every file here.
These are exact copies of 22 explicitly selected session records. Original
`F/<path>` references map to the same relative path below this directory.
They preserve design details and review findings that should not depend on
the crashed CLI session remaining available.

**Supersession matters:** GCC V2 and the shared receipt contract override
V1's two-FD/native-return-registry proposal. The bootstrap placement amendment
overrides the original host-child placement. The two implementation review
records identify unresolved code defects; the later correction scopes are
instructions for unfinished work, not evidence that fixes were implemented.
The first bootstrap handoff describes `204783`, not recovery WIP `3302f790e`.

No raw native report, private environment, SDK/source capture, cryptographic
key, token, ROM or crash dump is included. These files are recovery material,
not generated production input, release provenance or a new validation gate.
Do not merge the handoff branch as implementation.
