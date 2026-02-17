from argparse import (
    ONE_OR_MORE as _ONE_OR_MORE,
)
from argparse import (
    ArgumentParser as _ArgParser,
)
from argparse import (
    Namespace as _NS,
)
from asyncio import (
    BoundedSemaphore as _BSemp,
)
from asyncio import (
    create_subprocess_exec as _new_sproc,
)
from asyncio import (
    create_task,
)
from asyncio import (
    gather as _gather,
)
from asyncio import (
    run as _run,
)
from asyncio.subprocess import DEVNULL as _DEVNULL
from asyncio.subprocess import PIPE as _PIPE
from collections.abc import Callable as _Call
from collections.abc import Sequence as _Seq
from dataclasses import dataclass as _dc
from functools import partial as _partial
from functools import wraps as _wraps
from logging import (
    INFO as _INFO,
)
from logging import (
    basicConfig as _basicConfig,
)
from logging import (
    error as _err,
)
from logging import (
    info as _info,
)
from os import cpu_count as _cpu_c
from sys import argv as _argv
from sys import exit as _exit
from typing import Any as _Any
from typing import final as _fin

from aioshutil import which as _which
from anyio import Path as _Path

_GIT_FILES = "package-lock.json", "package.json", "pnpm-lock.yaml"
_GIT_MESSAGE = "Update dependencies"
_GIT_TAG = "rolling"
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
        for err in await _gather(*map(exec, args.inputs), return_exceptions=True)
        if err
    )
    if errors:
        raise BaseExceptionGroup("", errors)

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
        help="sequence of repository(s)",
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
