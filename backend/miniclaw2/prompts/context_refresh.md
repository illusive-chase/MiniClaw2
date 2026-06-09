# Project CONTEXT.md refresher

You are updating the existing `CONTEXT.md` at the project root. The framework holds this prompt; the user does not see it.

`CONTEXT.md` is a plan-free, codebase-facing handbook loaded at the start of every agent run. Treat this refresh as light-touch — every future run re-reads the repo anyway, so this file does not need to be exhaustive or perfectly current.

## What to do

1. Use the `Read` tool to read the current `CONTEXT.md`.
2. Use `Read`, `Glob`, and `Grep` to spot-check the repo for drift against what the file claims.
3. Decide whether anything is materially stale. If not, leave the file alone — an unchanged file is a legitimate outcome.
4. If updates are warranted, use the `Write` tool to write back the updated `CONTEXT.md`. Do not write any other file.

## Editing rules

- **Preserve user prose.** Any hand-written paragraph that is still accurate stays verbatim. Do not reword for style.
- **Update only stale facts.** If a section describes a directory that no longer exists, fix it. If a command in the conventions section is now wrong, fix it. Leave the rest.
- **No cosmetic rewrites.** Do not reorganize sections, renumber lists, or rephrase headings unless required by an actual change.
- **No scope creep.** Do not add new sections of plans, decisions, or TODOs. CONTEXT.md remains plan-free.

## What CONTEXT.md must NOT contain (still)

- No planspace state, active directions, in-flight work.
- No transient blockers, recent incidents, "what we're doing now".
- No TODO lists or plans.

If you decide nothing needs to change, simply do nothing — the framework treats a no-op refresh as success.
