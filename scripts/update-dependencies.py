"""Command-line helper to update dependencies across repositories.

This module provides an asynchronous CLI that runs `npm-check-updates` (`ncu`),
performs `npm`/`pnpm` dedupe operations, normalises `package-lock.json`, and
creates signed git commits/tags for each repository supplied on the command
line.

Usage: run the included `parser()`/`invoke` entry point from a script
invocation; see `main()` for the high-level workflow.
"""

import subprocess
from argparse import ONE_OR_MORE, ArgumentParser, Namespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from logging import INFO, basicConfig, error, info
from os import cpu_count
from sys import argv, exit
from typing import Any, final

from aioshutil import which
from anyio import CapacityLimiter, Path, run_process
from asyncer import SoonValue, create_task_group, runnify

__all__ = ("Arguments", "parser", "main")

_GIT_FILES = "package-lock.json", "package.json", "pnpm-lock.yaml"
_GIT_MESSAGE = "Update dependencies"
_GIT_TAG = "rolling"
# Bound the number of concurrently running subprocesses
_SUBPROCESS_SEMAPHORE = CapacityLimiter(cpu_count() or 4)


@final
@dataclass(
    init=True,
    repr=True,
    eq=True,
    order=False,
    unsafe_hash=False,
    frozen=True,
    match_args=True,
    kw_only=True,
    slots=True,
)
class Arguments:
    """Immutable container for this script's command-line arguments.

    Instances of `Arguments` are a frozen dataclass representing the parsed
    command-line options used by `main()`.

    Attributes
    ----------
    filter:
        Optional string passed to `npm-check-updates` (`ncu --filter`). When
        `None` the update runs against all dependency types.
    inputs:
        Sequence of repository `Path` objects to operate on. The sequence is
        converted to an immutable `tuple` in `__post_init__` so the dataclass
        remains hashable and cannot be mutated after construction.
    """

    filter: str | None
    inputs: Sequence[Path]

    def __post_init__(self):
        """Coerce the `inputs` iterable into an immutable `tuple`.

        The dataclass is declared `frozen=True`, therefore `object.__setattr__`
        is used to replace the `inputs` attribute with a `tuple` to ensure
        stable hashing and to prevent accidental mutation by callers.
        """
        object.__setattr__(self, "inputs", tuple(self.inputs))


@wraps(which)
async def _which2(cmd: str):
    """Locate `cmd` in PATH and raise if it cannot be found.

    This is a thin async wrapper around `aioshutil.which()` that converts a
    ``None`` result into a `FileNotFoundError` so callers can handle a missing
    executable with normal exception semantics.
    """
    ret = await which(cmd)
    if ret is None:
        raise FileNotFoundError(cmd)
    return ret


# we now use anyio.run_process directly (async); no wrapper needed.


@wraps(which)
async def _exec(*args: Any, **kwargs: Any):
    """Run a subprocess with bounded concurrency, log output, and raise on error.

    ``anyio.run_process`` provides a native async implementation and returns a
    ``subprocess.CompletedProcess`` similar to the old blocking API.  We hold
    the semaphore while the process runs to limit concurrency.
    """
    async with _SUBPROCESS_SEMAPHORE:
        cp = await run_process(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            **kwargs,
        )
    stdout, stderr = (
        cp.stdout.decode(errors="ignore").strip(),
        cp.stderr.decode(errors="ignore").strip(),
    )
    if stdout:
        info(stdout)
    if stderr:
        error(stderr)
    if cp.returncode:
        raise ChildProcessError(cp.returncode, stderr)
    # expose the CompletedProcess result to callers.
    return cp


async def main(args: Arguments):
    """Update dependencies for each repository in `args.inputs`.

    The function ensures required tooling is present (`git`, `npm`, `pnpm`, and
    `ncu` / `npm-check-updates`). For each supplied repository path it runs the
    following sequence:

    - `ncu --upgrade` (optionally with `--filter`)
    - `npm dedupe --package-lock-only` and `pnpm dedupe`
    - normalise `package-lock.json` whitespace (trim and rewrite if needed)
    - `git add` then `git commit --gpg-sign` and create/force a signed tag

    Any subprocess failures raise `ChildProcessError`. If multiple repository
    operations fail, a `BaseExceptionGroup` is raised containing all errors.

    Parameters
    ----------
    args:
        An `Arguments` instance with `filter` and `inputs` fields.

    Raises
    ------
    BaseExceptionGroup
        When one or more repository update tasks failed.
    """
    # resolve required executables concurrently using asyncer.soonify
    soon_git: SoonValue[str] | None = None
    soon_ncu: SoonValue[str | None] | None = None
    soon_npm: SoonValue[str] | None = None
    soon_pnpm: SoonValue[str] | None = None
    async with create_task_group() as tg:
        soon_git = tg.soonify(_which2)("git")
        soon_ncu = tg.soonify(which)("ncu")
        soon_npm = tg.soonify(_which2)("npm")
        soon_pnpm = tg.soonify(_which2)("pnpm")

    # the values are available once the task group exits
    git = soon_git.value  # type: ignore[assignment]
    ncu = soon_ncu.value  # type: ignore[assignment]
    npm = soon_npm.value  # type: ignore[assignment]
    pnpm = soon_pnpm.value  # type: ignore[assignment]
    if ncu is None:
        await _exec(npm, "install", "--global", "npm-check-updates")
        ncu = await _which2("ncu")

    async def exec(path: Path):
        """Perform the dependency update workflow for a single `path`.

        The coroutine runs `ncu --upgrade` (respecting `args.filter`), runs
        package-lock/pnpm dedupe operations, normalises the `package-lock.json`
        file by trimming surrounding whitespace, and then stages/commits/tags
        the changes in `git` with a signed commit and tag.

        Parameters
        ----------
        path:
            Repository `Path` to update.

        Raises
        ------
        ChildProcessError
            If any subprocess invoked by the workflow exits with a non-zero
            return code.
        """
        await _exec(
            ncu,
            *() if args.filter is None else ("--filter", args.filter),
            "--upgrade",
            cwd=path,
        )
        # run the two dedupe subprocesses at the same time using asyncer
        async with create_task_group() as tg:
            tg.soonify(_exec)(npm, "dedupe", "--package-lock-only", cwd=path)
            tg.soonify(_exec)(pnpm, "dedupe", cwd=path)
        async with await (path / "package-lock.json").open(
            "r+t", encoding="UTF-8", errors="strict", newline=None
        ) as packageLock:
            read = await packageLock.read()
            # if trimming is needed, seek back to start and write
            if (text := read.strip()) != read:
                await packageLock.seek(0)
                await packageLock.write(text)
                await packageLock.truncate()
        await _exec(git, "add", *_GIT_FILES, cwd=path)
        await _exec(
            git,
            "commit",
            "--gpg-sign",
            "--message",
            _GIT_MESSAGE,
            cwd=path,
        )
        await _exec(
            git, "tag", "--force", "--message", _GIT_TAG, "--sign", _GIT_TAG, cwd=path
        )

    # launch all repository operations concurrently and collect any errors
    soon_list: list[SoonValue[Any]] = []
    async with create_task_group() as tg:
        for path in args.inputs:
            soon_list.append(tg.soonify(exec)(path))

    errors = tuple(sv.value for sv in soon_list if isinstance(sv.value, BaseException))
    if errors:
        raise BaseExceptionGroup("", errors)

    exit(0)


def parser(parent: Callable[..., ArgumentParser] | None = None):
    """Create and return an `ArgumentParser` for the script.

    The returned parser defines the CLI for `update-dependencies.py` and sets a
    coroutine `invoke` as the parser default so callers can `await entry.invoke(entry)`.

    Parameters
    ----------
    parent:
        Optional parent parser class/factory to use instead of `argparse.ArgumentParser`.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser with an `invoke` coroutine set as the default handler.
    """
    prog = argv[0]

    parser = (ArgumentParser if parent is None else parent)(
        prog=prog,
        description="update dependencies",
        add_help=True,
        allow_abbrev=False,
        exit_on_error=False,
    )
    parser.add_argument(
        "-f",
        "--filter",
        action="store",
        default=None,
        dest="filter",
        help="package filter",
        type=str,
    )
    parser.add_argument(
        "inputs",
        action="store",
        help="sequence of repository(s)",
        nargs=ONE_OR_MORE,
        type=Path,
    )

    @wraps(main)
    async def invoke(entry: Namespace):
        """Argument parser `invoke` wrapper — resolve paths and call `main()`.

        This coroutine is assigned as the parser's `invoke` default. It resolves
        provided `inputs` to absolute `Path` objects (strict existence check)
        and constructs an `Arguments` instance that is forwarded to `main()`.
        """
        # concurrently resolve the supplied input paths
        soon_list: list[SoonValue[Path]] = []
        async with create_task_group() as tg:
            for p in entry.inputs:
                soon_list.append(tg.soonify(Path.resolve)(p, strict=True))
        inputs = tuple(sv.value for sv in soon_list)
        await main(Arguments(filter=entry.filter, inputs=inputs))

    parser.set_defaults(invoke=invoke)
    return parser


if __name__ == "__main__":
    basicConfig(level=INFO)
    entry = parser().parse_args(argv[1:])
    # `asyncer.runnify` converts an async function into a normal callable
    # that runs the coroutine to completion on the current thread.  It uses
    # AnyIO under the hood just like ``run`` would, but it integrates better
    # with sync code and gives nicer typing support for editors.
    run_sync = runnify(entry.invoke)
    run_sync(entry)
