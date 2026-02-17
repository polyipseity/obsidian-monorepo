import { FILE_GLOB as MD_FILE_GLOB } from "./.markdownlint-cli2.mjs";

/**
 * Convert `FILE_GLOB`, which uses globby/fast-glob style brace expansion,
 * into a micromatch-compatible glob pattern that matches the same set of files.
 */
const MD_GLOB_KEY = MD_FILE_GLOB;

/**
 * Lint-staged configuration.
 *
 * Note: lint-staged appends the list of staged file paths to the command it runs.
 * For commands that must receive that file list (formatters or linters that
 * operate on provided files), prefer invoking the underlying CLI directly so
 * lint-staged's file arguments are forwarded. Examples: `prettier --write`,
 * `markdownlint-cli2 --fix --no-globs`, `python -m scripts.format`,
 * `python -m ruff check --fix`. Avoid `pnpm run <script>` for commands that must
 * operate on the staged file list because `pnpm run` may not reliably forward
 * arbitrary file arguments.
 *
 * Prefer invoking `uv run --locked` for reproducible, locked Python CLI runs when
 * those tools are installed via `uv sync`.
 *
 * @type {import('lint-staged').Configuration}
 */
export default {
  [MD_GLOB_KEY]: ["markdownlint-cli2 --fix --no-globs"],
  "**/*.{astro,cjs,css,csv,gql,graphql,hbs,html,js,jsx,json,json5,jsonc,jsonl,less,mjs,pcss,sass,scss,svelte,styl,ts,tsx,vue,xml,yaml,yml}":
    ["prettier --write"],
  "**/*.{py,pyi,pyw,pyx}": [
    // Run pyright and each Python formatter as its own command so lint-staged appends
    // the staged file list to each invocation (pyright, ruff).
    "pyright",
    "uv run --locked ruff check --fix",
    "uv run --locked ruff format",
  ],
};
