"""Utilities to merge or continue template updates across repositories.

This script allows applying the upstream plugin template changes to a set of
local repositories: either perform a merge from the template branch (`update`),
or finish a previously staged template update by committing/tagging (`continue`).

See `parser()` and `main()` for invocation details.
"""

import subprocess
from argparse import ONE_OR_MORE, ArgumentParser, Namespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from logging import INFO, basicConfig, error, info
from os import cpu_count
from sys import argv, exit
from typing import Any, Literal, final

from aioshutil import which
from anyio import CapacityLimiter, Path, run_process
from asyncer import SoonValue, create_task_group, runnify

__all__ = ("Arguments", "parser", "main")

_ACTION_TYPES = Literal["continue", "update"]
_ACTIONS: tuple[_ACTION_TYPES, ...] = "continue", "update"
_BRANCH = "forks/polyipseity"
_REMOTE_URL = "https://github.com/polyipseity/obsidian-plugin-template.git"
_GIT_MESSAGE = "chore(template): merge updates from template"
_GIT_TAG = "rolling"
# limit concurrent subprocesses
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
    """Immutable container for the `update_template` script arguments.

    Attributes
    ----------
    action:
        One of the literal action strings: ``"continue"`` or ``"update"``.
        Determines which workflow is executed for each repository.
    inputs:
        Sequence of repository `Path` objects to operate on; converted to an
        immutable `tuple` in `__post_init__`.
    """

    action: _ACTION_TYPES
    inputs: Sequence[Path]

    def __post_init__(self):
        """Convert `inputs` to a `tuple` to preserve immutability.

        The dataclass is frozen, so `object.__setattr__` is used to replace the
        attribute with a `tuple` for stable hashing and to avoid accidental
        mutation after construction.
        """
        object.__setattr__(self, "inputs", tuple(self.inputs))


@wraps(which)
async def _which2(cmd: str):
    """Async wrapper for `aioshutil.which()` that raises when the command is missing.

    Parameters
    ----------
    cmd:
        Command name to locate on PATH.

    Returns
    -------
    str | Path
        Absolute path to the located executable.

    Raises
    ------
    FileNotFoundError
        If the command cannot be found on PATH.
    """
    ret = await which(cmd)
    if ret is None:
        raise FileNotFoundError(cmd)
    return ret


async def _exec(*args: Any, **kwargs: Any):
    """Run a subprocess with concurrency limiting and surface any errors.

    ``anyio.run_process`` is a native async API that returns a
    ``subprocess.CompletedProcess`` instance.  We simply call it while
    holding the semaphore to avoid spawning too many concurrent worker tasks.
    """
    async with _SUBPROCESS_SEMAPHORE:
        # `run_process` expects a single sequence argument for the command
        # rather than variadic args like subprocess.run.
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
    # return the underlying CompletedProcess so callers (and tests) can
    # inspect stdout/returncode if desired.
    return cp


async def main(args: Arguments):
    """Apply the selected `action` across the provided repository paths.

    Behaviour depends on `args.action`:
    - ``"continue"``: create a signed commit (no-edit) and refresh the rolling tag.
    - ``"update"``: fetch the configured remote template branch, merge it into
      the current branch, and refresh the rolling tag.

    For each repository the appropriate nested coroutine (`continue_` or
    `update`) is executed concurrently using asyncer.soonify. Any failures are
    captured and re-raised as a `BaseExceptionGroup`.

    Parameters
    ----------
    args:
        `Arguments` instance containing the `action` and `inputs` to process.

    Raises
    ------
    BaseExceptionGroup
        If one or more repository operations failed.
    """
    git = await _which2("git")

    async def continue_(path: Path):
        """Commit current changes (no-edit) and update the rolling tag for `path`.

        This coroutine performs a signed commit with the configured message and
        then force-updates a signed `rolling` tag in the repository.
        """
        await _exec(
            git,
            "commit",
            "--gpg-sign",
            "--message",
            _GIT_MESSAGE,
            "--no-edit",
            cwd=path,
        )
        await _exec(
            git, "tag", "--force", "--message", _GIT_TAG, "--sign", _GIT_TAG, cwd=path
        )

    async def update(path: Path):
        """Fetch the upstream template branch and merge it into `path`.

        The coroutine fetches `_REMOTE_URL`:`_BRANCH` into `FETCH_HEAD`, merges
        `FETCH_HEAD` with a signed merge commit using the configured message,
        then updates the `rolling` tag.
        """
        await _exec(git, "fetch", _REMOTE_URL, _BRANCH, cwd=path)
        await _exec(
            git,
            "merge",
            "--gpg-sign",
            "--message",
            _GIT_MESSAGE,
            "FETCH_HEAD",
            cwd=path,
        )
        await _exec(
            git, "tag", "--force", "--message", _GIT_TAG, "--sign", _GIT_TAG, cwd=path
        )

    if args.action == "continue":
        action = continue_
    elif args.action == "update":
        action = update
    else:
        raise TypeError(args.action)

    # execute all repository operations concurrently using soonify
    soon_list: list[SoonValue[Any]] = []
    async with create_task_group() as tg:
        for path in args.inputs:
            soon_list.append(tg.soonify(action)(path))
    errors = tuple(sv.value for sv in soon_list if isinstance(sv.value, BaseException))
    if errors:
        raise BaseExceptionGroup("", errors)

    exit(0)


def parser(parent: Callable[..., ArgumentParser] | None = None):
    """Create and return an `ArgumentParser` for the update_template tool.

    The parser defines the `action` and `inputs` CLI arguments and sets an
    async `invoke` function as the parser default so callers may `await
    entry.invoke(entry)` after parsing.

    Parameters
    ----------
    parent:
        Optional parent parser class or factory to use instead of
        `argparse.ArgumentParser`.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser with an `invoke` coroutine set as the default handler.
    """
    prog = argv[0]

    parser = (ArgumentParser if parent is None else parent)(
        prog=prog,
        description="update template",
        add_help=True,
        allow_abbrev=False,
        exit_on_error=False,
    )
    parser.add_argument(
        "action",
        action="store",
        choices=_ACTIONS,
        help="action",
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

        Resolves `inputs` to absolute `Path` objects (strict existence check)
        and constructs an `Arguments` instance that is forwarded to `main()`.
        """
        # resolve input paths concurrently using soonify
        soon_paths: list[SoonValue[Path]] = []
        async with create_task_group() as tg:
            for p in entry.inputs:
                soon_paths.append(tg.soonify(Path.resolve)(p, strict=True))
        inputs = tuple(sv.value for sv in soon_paths)
        await main(Arguments(action=entry.action, inputs=inputs))

    parser.set_defaults(invoke=invoke)
    return parser


def __main__() -> None:
    """Configure logging, parse CLI arguments, and dispatch to the async `invoke` wrapper."""

    basicConfig(level=INFO)
    entry = parser().parse_args(argv[1:])
    # Use asyncer.runnify to call the async invoke wrapper from synchronous
    # startup code; this avoids the explicit `anyio.run` call and provides
    # better editor type hints.
    runnify(entry.invoke, backend_options={"use_uvloop": True})(entry)


if __name__ == "__main__":
    __main__()
