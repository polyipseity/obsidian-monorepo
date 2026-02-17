"""Command-line helper to update dependencies across repositories.

This module provides an asynchronous CLI that runs `npm-check-updates` (`ncu`),
performs `npm`/`pnpm` dedupe operations, normalises `package-lock.json`, and
creates signed git commits/tags for each repository supplied on the command
line.

Usage: run the included `parser()`/`invoke` entry point from a script
invocation; see `main()` for the high-level workflow.
"""

from argparse import ONE_OR_MORE, ArgumentParser, Namespace
from asyncio import BoundedSemaphore, create_subprocess_exec, create_task, gather, run
from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial, wraps
from logging import INFO, basicConfig, error, info
from os import cpu_count
from sys import argv, exit
from typing import Any, final

from aioshutil import which
from anyio import Path

__all__ = ("Arguments", "parser", "main")

_GIT_FILES = "package-lock.json", "package.json", "pnpm-lock.yaml"
_GIT_MESSAGE = "Update dependencies"
_GIT_TAG = "rolling"
_SUBPROCESS_SEMAPHORE = BoundedSemaphore(cpu_count() or 4)


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

    Parameters
    ----------
    cmd:
        Command name to locate on PATH.

    Returns
    -------
    str | Path
        Absolute path to the executable as returned by `which()`.

    Raises
    ------
    FileNotFoundError
        If the `cmd` is not found on PATH.
    """
    ret = await which(cmd)
    if ret is None:
        raise FileNotFoundError(cmd)
    return ret


@wraps(create_subprocess_exec)
async def _exec(*args: Any, **kwargs: Any):
    """Run a subprocess with bounded concurrency, log output, and raise on error.

    The helper acquires a module-level semaphore to limit the number of
    concurrently executing child processes. `stdout`/`stderr` are captured,
    decoded, and emitted to the logger; a non-zero exit code raises
    `ChildProcessError` (return code, stderr).

    Parameters
    ----------
    *args, **kwargs:
        Forwarded to `asyncio.create_subprocess_exec()` (command and its
        arguments; `cwd` and other kwargs are accepted).

    Raises
    ------
    ChildProcessError
        If the subprocess exits with a non-zero return code. The exception
        contains the return code and the captured stderr text.
    """
    async with _SUBPROCESS_SEMAPHORE:
        proc = await create_subprocess_exec(
            *args,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
            **kwargs,
        )
        stdout, stderr = await proc.communicate()
    stdout, stderr = (
        stdout.decode(errors="ignore").strip(),
        stderr.decode(errors="ignore").strip(),
    )
    if stdout:
        info(stdout)
    if stderr:
        error(stderr)
    if proc.returncode:
        raise ChildProcessError(proc.returncode, stderr)


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
    git, ncu, npm, pnpm = await gather(
        _which2("git"), which("ncu"), _which2("npm"), _which2("pnpm")
    )
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
        await gather(
            _exec(npm, "dedupe", "--package-lock-only", cwd=path),
            _exec(pnpm, "dedupe", cwd=path),
        )
        async with await (path / "package-lock.json").open(
            "r+t", encoding="UTF-8", errors="strict", newline=None
        ) as packageLock:
            read = await packageLock.read()
            seek = create_task(packageLock.seek(0))
            try:
                if (text := read.strip()) != read:
                    await seek
                    await packageLock.write(text)
                    await packageLock.truncate()
            finally:
                seek.cancel()
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

    errors = tuple(
        err
        for err in await gather(*map(exec, args.inputs), return_exceptions=True)
        if err
    )
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
        await main(
            Arguments(
                filter=entry.filter,
                inputs=await gather(
                    *map(partial(Path.resolve, strict=True), entry.inputs)
                ),
            )
        )

    parser.set_defaults(invoke=invoke)
    return parser


if __name__ == "__main__":
    basicConfig(level=INFO)
    entry = parser().parse_args(argv[1:])
    run(entry.invoke(entry))
