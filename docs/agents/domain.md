# Domain docs

This repository uses a **single-context** layout. Engineering skills consume
domain documentation from root `GLOSSARY.md` and `docs/adr/`.

## Before exploring

Read root `GLOSSARY.md` and the ADRs in `docs/adr/` relevant to the area being
explored. If a root `GLOSSARY-MAP.md` is introduced later, follow its pointers
to the relevant context glossaries and context-scoped ADRs instead.

When these files are absent, proceed silently. `/domain-modeling` creates them
lazily when terms or decisions are resolved; setup does not create empty
glossaries or ADRs.

## Vocabulary

Use the glossary's terms in issue titles, proposals, hypotheses, and tests.
If a needed concept is missing, reconsider invented terminology or note the
gap for `/domain-modeling`.

## ADR conflicts

Surface any contradiction with an existing ADR explicitly, naming the ADR and
why reopening the decision is warranted.

## Maintaining the configuration

Edit `docs/agents/*.md` directly to change these conventions. Re-run the setup
skill only to switch issue trackers or restart configuration from scratch.
