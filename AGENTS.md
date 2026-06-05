# Agent Working Agreement

## Scope
This repository contains the active implementation and a legacy reference in `old/`.

## Non-Negotiable Rules
- Treat `old/` as read-only reference.
- Never edit, move, or delete files under `old/`.
- New implementation must be created outside `old/`.
- Reuse ideas and interfaces from `old/`, but re-implement code in new modules.

## Build Direction
- Primary goal: replace file-release pipeline with OracleDB update pipeline.
- Keep provider abstraction and data standardization concepts.
- Enforce provider encapsulation: use only `CompositeProvider` at external entry points.
- Hide `pykrx`, `korea_investment`, and `fdr` behind `CompositeProvider` internals.
- Separate pipeline stages clearly:
  1. data fetch
  2. normalization/features
  3. database upsert

## Agent Startup Checklist
1. Read `plan.md` first.
2. Read this file (`AGENTS.md`).
3. Confirm no edits are planned under `old/`.
4. Continue from latest handoff section in `plan.md`.

## Handoff Protocol (append to plan.md)
When finishing a session, append:
- Completed
- In progress
- Next 3 concrete tasks
- Risks/blockers
- Commands used for verification

## Editing Guardrails
- Prefer small, reviewable changes.
- Preserve existing public interfaces unless migration requires explicit changes.
- Add tests for new behavior where practical.
