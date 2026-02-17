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
    filter: str | None
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
    git, ncu, npm, pnpm = await gather(
        _which2("git"), which("ncu"), _which2("npm"), _which2("pnpm")
    )
    if ncu is None:
        await _exec(npm, "install", "--global", "npm-check-updates")
        ncu = await _which2("ncu")

    async def exec(path: Path):
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
