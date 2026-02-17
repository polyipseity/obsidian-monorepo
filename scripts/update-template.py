from argparse import ONE_OR_MORE, ArgumentParser, Namespace
from asyncio import BoundedSemaphore, create_subprocess_exec, gather, run
from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial, wraps
from logging import INFO, basicConfig, error, info
from os import cpu_count
from sys import argv, exit
from typing import Any, Literal, final

from aioshutil import which
from anyio import Path

__all__ = ("Arguments", "parser", "main")

_ACTION_TYPES = Literal["continue", "update"]
_ACTIONS: tuple[_ACTION_TYPES, ...] = "continue", "update"
_BRANCH = "forks/polyipseity"
_REMOTE_URL = "https://github.com/polyipseity/obsidian-plugin-template.git"
_GIT_MESSAGE = "chore(template): merge updates from template"
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
    action: _ACTION_TYPES
    inputs: Sequence[Path]

    def __post_init__(self):
        object.__setattr__(self, "inputs", tuple(self.inputs))


@wraps(which)
async def _which2(cmd: str):
    ret = await which(cmd)
    if ret is None:
        raise FileNotFoundError(cmd)
    return ret


@wraps(create_subprocess_exec)
async def _exec(*args: Any, **kwargs: Any):
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
    git = await _which2("git")

    async def continue_(path: Path):
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

    errors = tuple(
        err
        for err in await gather(*map(action, args.inputs), return_exceptions=True)
        if err
    )
    if errors:
        raise BaseExceptionGroup("", errors)

    exit(0)


def parser(parent: Callable[..., ArgumentParser] | None = None):
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
        await main(
            Arguments(
                action=entry.action,
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
