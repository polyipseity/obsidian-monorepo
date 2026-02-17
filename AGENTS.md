# AGENTS.md — Workspace AI Agent Guide

This file is the workspace-level instructions for AI coding agents working across the obsidian-monorepo. It adapts the original `obsidian-plugin-template` guidance to a multi-package monorepo and points to package-specific `AGENTS.md` files where package-level rules differ.

## What changed

- This document is now the *workspace* guide (root-level). Package-specific rules remain in each package's `AGENTS.md` (see list below).  
- Keep package-level patterns (build, test, localization) but prefer workspace-wide commands and CI conventions.

## 1. Workspace overview

- Monorepo containing multiple Obsidian-related packages (examples: `obsidian-modules`, `obsidian-plugin-library`, `obsidian-plugin-template`, `obsidian-show-hidden-files`, `obsidian-terminal`).
- Each package keeps its own `AGENTS.md` when package-specific conventions are required — this root file covers workspace-wide policies and cross-package workflows.

## 2. Developer workflows (workspace-level)

- **Preferred package manager:** `pnpm` (workspace-aware). Use `npm` only if `pnpm` is unavailable.
- **Workspace install:** `pnpm install` (run at repository root).
- **Build:** run package-local build scripts (e.g., `pnpm --filter <package> build`) or the workspace helper `pnpm -w build` where provided.
- **Testing:** run tests per-package (recommended) or the full suite from root with `pnpm -w test`.
- **CI:** use `pnpm install --frozen-lockfile` in CI for deterministic installs.

Notes:

- Prefer package-scoped commands (filter by package) when working on a single package to reduce iteration time.
- When making workspace-wide infra changes (tooling, CI, lint rules), update this root `AGENTS.md` and the affected package `AGENTS.md` files.

---

## Scripts & common commands 🔧

- Use `pnpm -w` for workspace-level operations and `pnpm --filter <pkg>` for package-scoped runs.
- Common package scripts (found in package `package.json` files): `build`, `dev`, `test`, `check`, `format`, `obsidian:install` (package-specific).
- Lint & format: `pnpm -w run check` / `pnpm -w run format`.

> Tip: Use `pnpm --filter <pkg> test -- <pattern>` for iterative test runs in a single package.

## Testing conventions (workspace)

- **Runner:** Vitest across packages.
- **File conventions:** `*.spec.*` = unit; `*.test.*` = integration. Keep the semantic distinction.
- **Per-package tests:** Prefer running tests inside the package (use `pnpm --filter <pkg> test`).
- **Agent requirement:** Never run `vitest` in watch mode in automated agents — use `vitest run` or `--run`.

---

## 3. Coding conventions (applies across workspace)

- TypeScript rules: avoid `any`, avoid `as` casts, prefer `interface` for object shapes, and add runtime type guards when needed.
- Commit messages: follow Conventional Commits; run `npm run commitlint` or `pnpm -w run commitlint` before pushing.
- Python modules & `__all__`:
  - Every Python module must declare a top-level `__all__` tuple (even if empty). Use a `tuple` (not a `list`) and place the assignment immediately after top-level imports.
  - `__all__` must list the public API (functions, classes, constants). Internal helpers should remain named with a leading underscore or omitted from `__all__`.
  - Do **not** avoid exporting names by aliasing imports with leading underscores — remove such aliasing and rely on `__all__` to control exports. Update all references and type annotations accordingly.
  - When changing a module's public API, add or update tests (see `tests/test_module_exports.py`) to assert the expected exports.

## 4. Integration points & shared packages

- `ext.obsidian-api/` — Obsidian type definitions and helpers used by multiple packages.
- `obsidian-plugin-library` — shared UI, i18n, and helpers used by plugin packages.
- When modifying shared packages, add or update integration tests in dependent packages.

---

## 5. Package-specific AGENTS.md (where to look)

- `obsidian-modules/AGENTS.md`
- `obsidian-plugin-library/AGENTS.md`
- `obsidian-plugin-template/AGENTS.md`
- `obsidian-show-hidden-files/AGENTS.md`
- `obsidian-terminal/AGENTS.md`

Open the package `AGENTS.md` for package-level conventions and examples (tests, build scripts, i18n keys, plugin lifecycle patterns).

---

## 6. When to update this file vs package `AGENTS.md`

- Update this root `AGENTS.md` for workspace-wide policies (tooling, CI, dependency manager, scripts that affect all packages).
- Update package `AGENTS.md` for package-local patterns, tests, or lifecycle specifics.

---

## 7. For AI Coding Agents — quick checklist 🤖

1. Read this root `AGENTS.md` first for workspace-wide rules.  
2. Open the target package's `AGENTS.md` next for package-specific guidance.  
3. Add tests first for behavioral changes and follow the one-test-file-per-source-file convention.  
4. Run package-scoped tests with `pnpm --filter <pkg> test` during development.  
5. Use `pnpm -w test` for the full workspace run in CI.

---

## 8. Copilot / Chat assistant examples 💬

- Location: primary guidance is in `.github/instructions/copilot.instructions.md`; package-level `copilot.instructions.md` files may add repo-specific examples.
- Response template (copy/paste): Summary; Changed files; Tests; Commands to run; Risk / Next steps.
- Example — add failing unit test + fix:
  - Summary: Add failing test and minimal fix for the bug.
  - Changed files: `src/foo.ts`, `tests/foo.spec.ts`
  - Commands: `pnpm --filter <pkg> test -- --run`
- Example — add i18n key + UI change:
  - Summary: Add `syncNow` key and update UI to use it.
  - Changed files: `assets/locales/en/translation.json`, `src/components/SyncButton.tsx`
  - Commands: `pnpm -w test`
- Refusal text (exact): `Sorry, I can't assist with that.`
- When to ask clarifying questions: ambiguous requirements, multiple valid approaches, or public API/settings changes.

---

## 9. Linked instructions & resources

- `.github/instructions/` — coding rules and per-topic instructions (TypeScript, localization, commit messages).
- Package `AGENTS.md` files listed above for package-level guidance.

---

If you want, I can also:

- add or sync missing package `AGENTS.md` files, or
- add a short summary table listing each package and its purpose.

Which of those should I do next?
