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
    TaskGroup as _TskGrp,
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
from typing import Any as _Any, Callable as _Call, Sequence as _Seq, final as _fin

_GIT_FILES = "package-lock.json", "package.json", "pnpm-lock.yaml"
_GIT_MESSAGE = "Update dependencies"
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
    filter: str | None
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
    git, ncu, npm, pnpm = await _gather(
        _which2("git"), _which("ncu"), _which2("npm"), _which2("pnpm")
    )
    if ncu is None:
        await _exec(npm, "install", "--global", "npm-check-updates")
        ncu = await _which2("ncu")

    async def exec(path: _Path):
        await _exec(
            ncu,
            *() if args.filter is None else ("--filter", args.filter),
            "--upgrade",
            cwd=path,
        )
        await _gather(
            _exec(npm, "dedupe", "--package-lock-only", cwd=path),
            _exec(pnpm, "dedupe", cwd=path),
        )
        async with await (path / "package-lock.json").open(
            "r+t", encoding="UTF-8", errors="strict", newline=None
        ) as packageLock:
            read = await packageLock.read()
            async with _TskGrp() as grp:
                grp.create_task(packageLock.seek(0))
                read = read.strip()
            await packageLock.write(read)
            await packageLock.truncate()
        await _exec(git, "add", *_GIT_FILES, cwd=path)
        await _exec(
            git,
            "commit",
            "--gpg-sign",
            "--message",
            _GIT_MESSAGE,
            "--signoff",
            cwd=path,
        )
        await _exec(
            git, "tag", "--force", "--message", _GIT_TAG, "--sign", _GIT_TAG, cwd=path
        )

    errors = tuple(
        err
        for err in await _gather(*map(exec, args.inputs), return_exceptions=True)
        if err
    )
    if errors:
        raise ExceptionGroup("", errors)

    _exit(0)


def parser(parent: _Call[..., _ArgParser] | None = None):
    prog = _argv[0]

    parser = (_ArgParser if parent is None else parent)(
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
        help="sequence of reposoitory(s)",
        nargs=_ONE_OR_MORE,
        type=_Path,
    )

    @_wraps(main)
    async def invoke(entry: _NS):
        await main(
            Arguments(
                filter=entry.filter,
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
