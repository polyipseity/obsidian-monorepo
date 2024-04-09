# -*- coding: UTF-8 -*-
from aioshutil import which as _which
from anyio import Path as _Path
from argparse import (
    ArgumentParser as _ArgParser,
    Namespace as _NS,
    ONE_OR_MORE as _ONE_OR_MORE,
)
from asyncio import (
    BoundedSemaphore as _BSemp,
    create_subprocess_exec as _new_sproc,
    gather as _gather,
    run as _run,
)
from asyncio.subprocess import DEVNULL as _DEVNULL, PIPE as _PIPE
from dataclasses import dataclass as _dc
from functools import partial as _partial, wraps as _wraps
from logging import (
    INFO as _INFO,
    basicConfig as _basicConfig,
    error as _err,
    info as _info,
)
from os import cpu_count as _cpu_c
from sys import argv as _argv, exit as _exit
from typing import (
    Any as _Any,
    Callable as _Call,
    Literal as _Lit,
    Sequence as _Seq,
    final as _fin,
)

_ACTION_TYPES = _Lit["continue", "update"]
_ACTIONS: tuple[_ACTION_TYPES, ...] = "continue", "update"
_BRANCH = "forks/polyipseity"
_REMOTE = "template"
_GIT_TAG = "latest"
_SUBPROCESS_SEMAPHORE = _BSemp(_cpu_c() or 4)


@_fin
@_dc(
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
    inputs: _Seq[_Path]

    def __post_init__(self):
        object.__setattr__(self, "inputs", tuple(self.inputs))


@_wraps(_which)
async def _which2(cmd: str):
    ret = await _which(cmd)
    if ret is None:
        raise FileNotFoundError(cmd)
    return ret


@_wraps(_new_sproc)
async def _exec(*args: _Any, **kwargs: _Any):
    async with _SUBPROCESS_SEMAPHORE:
        proc = await _new_sproc(
            *args,
            stdin=_DEVNULL,
            stdout=_PIPE,
            stderr=_PIPE,
            **kwargs,
        )
        stdout, stderr = await proc.communicate()
    stdout, stderr = (
        stdout.decode(errors="ignore").strip(),
        stderr.decode(errors="ignore").strip(),
    )
    if stdout:
        _info(stdout)
    if stderr:
        _err(stderr)
    if proc.returncode:
        raise ChildProcessError(proc.returncode, stderr)


async def main(args: Arguments):
    git = await _which2("git")

    async def continue_(path: _Path):
        await _exec(git, "commit", "--gpg-sign", "--no-edit", "--signoff", cwd=path)
        await _exec(
            git, "tag", "--force", "--message", _GIT_TAG, "--sign", _GIT_TAG, cwd=path
        )

    async def update(path: _Path):
        await _exec(git, "fetch", _REMOTE, _BRANCH, cwd=path)
        await _exec(
            git,
            "merge",
            "--gpg-sign",
            "--signoff",
            f"refs/remotes/{_REMOTE}/{_BRANCH}",
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
        for err in await _gather(*map(action, args.inputs), return_exceptions=True)
        if err
    )
    if errors:
        raise BaseExceptionGroup("", errors)

    _exit(0)


def parser(parent: _Call[..., _ArgParser] | None = None):
    prog = _argv[0]

    parser = (_ArgParser if parent is None else parent)(
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
        help="sequence of reposoitory(s)",
        nargs=_ONE_OR_MORE,
        type=_Path,
    )

    @_wraps(main)
    async def invoke(entry: _NS):
        await main(
            Arguments(
                action=entry.action,
                inputs=await _gather(
                    *map(_partial(_Path.resolve, strict=True), entry.inputs)
                ),
            )
        )

    parser.set_defaults(invoke=invoke)
    return parser


if __name__ == "__main__":
    _basicConfig(level=_INFO)
    entry = parser().parse_args(_argv[1:])
    _run(entry.invoke(entry))
