# Project CONTEXT.md initializer

You are writing the first version of `CONTEXT.md` at the project root. The framework holds this prompt; the user does not see it.

`CONTEXT.md` is a plan-free, codebase-facing handbook. Every future agent run on this project will load it at the start of the session, so it must read like a skim-friendly reference, not exhaustive documentation.

## What to do

1. Use `Read`, `Glob`, and `Grep` to explore the repository from the project root. Look at the top-level layout, the trunk files of each major component, and any standard metadata files (`README.md`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, etc.).
2. Synthesize a concise handbook that a new agent could skim in under a minute.
3. Use the `Write` tool to create `CONTEXT.md` at the project root. Do not write any other file.

## What CONTEXT.md must contain

- **What this project is.** One paragraph stating the project's purpose.
- **Repo shape.** A short outline of the top-level layout — frontend trunk, backend trunk, where tests live, where build configuration lives.
- **Conventions and guardrails.** Anything an agent should know before changing code: language/runtime versions, build/test commands, formatting rules, lint expectations, "do not touch X" warnings if visible in the repo.
- **Where to look for things.** Pointers to the entry points of each subsystem so a future agent can jump in without grepping the whole tree.

## What CONTEXT.md must NOT contain

- **No planspace state.** No active directions, no current STATUS, no in-flight goals.
- **No transient information.** No "current blocker", "recent incident", "what we're doing this week".
- **No TODO lists or plans.** Plans live in planspace notebooks, not here.
- **No exhaustive API surface.** A handbook, not a reference manual.

Keep CONTEXT.md tight — prefer one screen of content over five. If you find yourself describing what's currently in progress, stop and remove it.
