# Copilot Repository Instructions

## Mandatory Project Rules
- `old/` is legacy reference only. Do not modify it.
- Implement all new features in new files/modules outside `old/`.
- If behavior from `old/` is needed, port by re-implementation, not by editing legacy files.

## Current Initiative
- Implement OracleDB-based data update pipeline.
- Existing file-release flow is reference behavior only.

## Working Pattern
1. Read `plan.md` and continue from the latest status.
2. Keep changes isolated and incremental.
3. Add or update tests for each meaningful unit.
4. Summarize outcomes with file paths and validation steps.

## Persistence Strategy Across Agents
- Use `plan.md` as the single source of truth for progress and handoff.
- Keep decisions and assumptions explicit in `plan.md`.
- Before ending a session, update the handoff section in `plan.md`.
