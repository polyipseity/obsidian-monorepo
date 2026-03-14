"""Command-line helper to update dependencies across repositories.

This module provides an asynchronous CLI that runs `npm-check-updates` via
`bun x -- npm-check-updates`, updates the lockfile for the repository's
package manager (`bun.lock`), and creates signed git commits/tags for each
repository supplied on the command line.

Usage: run the included `parser()`/`invoke` entry point from a script
invocation; see `main()` for the high-level workflow.
"""

import subprocess
from argparse import ONE_OR_MORE, ArgumentParser, Namespace
from asyncio import CancelledError
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from logging import INFO, basicConfig, error, info
from os import cpu_count
from subprocess import CompletedProcess
from sys import argv, exit
from typing import Any, final

from aioshutil import which
from anyio import CapacityLimiter, Path, run_process
from asyncer import SoonValue, create_task_group, runnify

"""Public API of this script."""
__all__ = ("Arguments", "parser", "main")

"""Names of git-tracked manifest/lock files updated by this tool."""
_GIT_FILES = "bun.lock", "package.json"
"""Commit message used when recording dependency updates."""
_GIT_MESSAGE = "Update dependencies"
"""Rolling tag name pointing at the latest dependency update commit."""
_GIT_TAG = "rolling"
# Bound the number of concurrently running subprocesses.
"""Capacity limiter used to bound concurrent subprocess executions."""
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
        Optional string passed to `npm-check-updates` (`--filter`). When
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
async def _exec(*args: Any, **kwargs: Any) -> CompletedProcess[bytes]:
    """Run a subprocess with bounded concurrency, log output, and raise on error.

    ``anyio.run_process`` provides a native async implementation and returns a
    ``CompletedProcess`` similar to the old blocking API.  We hold
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

    The function ensures required tooling is present (`git` and `bun`). For
    each supplied repository path it runs the
    following sequence:

    - `bun x -- npm-check-updates --upgrade` (optionally with `--filter`)
    - `bun install` to update the lockfile
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
    async with create_task_group() as tg:
        soon_git = tg.soonify(_which2)("git")
    # the values are available once the task group exits
    assert soon_git is not None
    git = soon_git.value

    async def exec(path: Path):
        """Perform the dependency update workflow for a single `path`.

        The coroutine runs `bun x -- npm-check-updates --upgrade` (respecting `args.filter`), runs
        `bun install` to update `bun.lock`, and then stages/commits/tags
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
        try:
            # Run npm-check-updates via bun; this avoids needing an installed ncu binary.
            await _exec(
                "bun",
                "x",
                "--",
                "npm-check-updates",
                *() if args.filter is None else ("--filter", args.filter),
                "--upgrade",
                cwd=path,
            )

            # Update the bun lockfile.
            await _exec("bun", "install", cwd=path)

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
                git,
                "tag",
                "--force",
                "--message",
                _GIT_TAG,
                "--sign",
                _GIT_TAG,
                cwd=path,
            )
        except BaseException as exc:
            if isinstance(exc, CancelledError):
                # propagate cancellations immediately to avoid unnecessary work.
                raise
            return exc

    # launch all repository operations concurrently and collect any errors
    soon_list: list[SoonValue[None | BaseException]] = []
    async with create_task_group() as tg:
        for path in args.inputs:
            soon_list.append(tg.soonify(exec)(path))

    errors = tuple(sv.value for sv in soon_list if isinstance(sv.value, BaseException))
    if errors:
        raise BaseExceptionGroup("", errors)

    exit(0)


def parser(parent: Callable[..., ArgumentParser] | None = None):
    """Create and return an `ArgumentParser` for the script.

    The returned parser defines the CLI for `update_dependencies.py` and sets a
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


def __main__() -> None:
    """Configure logging, parse CLI arguments, and dispatch to the async `invoke` wrapper."""
    basicConfig(level=INFO)
    entry = parser().parse_args(argv[1:])
    # `asyncer.runnify` converts an async function into a normal callable
    # that runs the coroutine to completion on the current thread.  It uses
    # AnyIO under the hood just like ``run`` would, but it integrates better
    # with sync code and gives nicer typing support for editors.
    runnify(entry.invoke, backend_options={"use_uvloop": True})(entry)


if __name__ == "__main__":
    __main__()
