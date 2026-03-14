# Copilot / Assistant instructions — workspace-level

## Summary

- Follow `AGENTS.md` and the per-package `AGENTS.md` files first. This file provides a short, non-interactive assistant template and guardrails for automated agents and Copilot-like assistants.
- Do NOT add a top-level `.github/copilot-instructions.md` in this repository; use `.agents/instructions/copilot.instructions.md` or update `AGENTS.md` instead.

## Required behavior (short)

- Always prefer non-interactive CLI forms and package-scoped commands.
- Run or recommend these exact commands when suggesting tests/builds.
- Short, factual replies only — include changed files, tests, and required commands.

## Response template (copy/paste)

- Summary: 1–2 lines describing the change
- Changed files: list of `path/to/file` entries
- Tests: which tests were added/updated
- Commands to run locally: `bun run test -- --run` (examples below)
- Risk / next steps: 1 line

## Non-interactive commands (use these exactly)

- Install workspace: `bun install`
- Run all tests (workspace): `bun run test`
- Run package tests: `bun run test -- --run` (from the package directory)
- Run unit tests only: `bun x vitest run "**/*.spec.*" --run`
- Run integration tests only: `bun x vitest run "**/*.test.*" --run`
- Check & format: `bun run check` / `bun run format`
- Python tests: `uv run --locked pytest`

## Guardrails / repository rules

- Do not run interactive/watch commands (no `vitest` without `--run`).
- Follow Conventional Commits; run `bun run commitlint` before committing.
- When adding strings, add them to `assets/locales/en/translation.json` first; preserve `{{...}}` and `$t(...)` tokens.
- Every Python module must declare a top-level `__all__` tuple immediately after imports.
- Do not add or suggest a top-level `.github/copilot-instructions.md` for this repo.

## Pre-PR checklist (short)

1. Add or update tests that exercise behavior changes. ✅
2. Run `bun run check` and `bun run format`. ✅
3. Update `assets/locales/en/translation.json` for UI strings. ✅
4. Ensure Python `__all__` is present if modifying Python modules. ✅
5. Add a changeset for public API changes. ✅
6. Use a Conventional Commit message (run `bun run commitlint`). ✅

## Example prompts & ideal short replies (2 examples)

1) Prompt: "Add `maxItems` validation to `src/settings.ts` and tests."
   Ideal reply (short):
   - Summary: Add `maxItems` validation and unit tests.
   - Changed files: `src/settings.ts`, `tests/settings.spec.ts`
   - Tests: `tests/settings.spec.ts` (new)
   - Commands to run: `bun run test -- --run` (from the package directory)
   - Risk: Low — small validation logic only.

2) Prompt: "Localize new button label `Sync now`."
   Ideal reply (short):
   - Summary: Add `syncNow` i18n key and update component.
   - Changed files: `assets/locales/en/translation.json`, `src/components/SyncButton.tsx`
   - Tests: `tests/i18n.spec.ts` (updated)
   - Commands to run: `bun run test`
   - Risk: Low — update translation only.

## Suggested next agent-customizations (short)

- `create-prompt:add-localization-key` — small prompt for adding i18n keys and tests.
- `create-skill:python-exports-checker` — automated skill to add/fix `__all__` entries and unit tests.
- `create-agent:ci-fix` — agent to diagnose & fix failing CI `bun run test` runs (non-interactive suggestions).

## Where to edit vs where to add files

- Small assistant guidance: update `AGENTS.md`.
- Reusable assistant templates and rules: add/edit `.agents/instructions/*.instructions.md` (this file).
