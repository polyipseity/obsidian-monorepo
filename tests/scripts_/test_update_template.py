"""Tests for :mod:`scripts.update_template`.

These exercises focus on the decision logic in ``main()``, the two helper
functions ``_exec`` and ``_which2`` and the argument parser.  Both workflows
("continue" and "update") are covered, along with error conditions such as
an unknown action or an underlying subprocess failure.

The tests mirror the style used for ``update_dependencies`` and emphasise
clarity over cleverness: each assertion is tied directly to the behaviour
being validated, and test helpers are small, in-lined coroutines that
simulate external effects.
"""

from argparse import ArgumentError
from os import PathLike, fspath
from typing import Literal, cast

import pytest
from anyio import Path
from pytest import LogCaptureFixture, MonkeyPatch

from scripts import update_template

"""Public API of this test module (empty)."""
__all__ = ()


def test_arguments_tuple_conversion_and_immutability():
    """Behaviour of the ``Arguments`` dataclass mirrors the one in
    ``update_dependencies``: inputs become a tuple and the object is frozen.
    """

    a = update_template.Arguments(action="continue", inputs=[Path("a")])
    assert isinstance(a.inputs, tuple)
    with pytest.raises(AttributeError):
        setattr(a, "inputs", ())


class _DummyCP:
    """Minimal stub for a ``CompletedProcess`` returned by ``_exec``."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        """Construct a dummy completed process object for use by stubs."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.anyio
async def test_which2_and_exec_behaviour(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    """Verify that the helpers behave correctly in both success and failure cases."""

    async def yes(cmd: str) -> str | None:  # pragma: no cover
        """Stub returning a fake path for any command success case."""
        return f"/foo/{cmd}"

    async def no(cmd: str) -> None:  # pragma: no cover
        """Stub simulating absence of a command by returning None."""
        return None

    monkeypatch.setattr(update_template, "which", yes)
    assert await update_template._which2("git") == "/foo/git"

    monkeypatch.setattr(update_template, "which", no)
    with pytest.raises(FileNotFoundError):
        await update_template._which2("git")

    async def rp_ok(*args: object, **kwargs: object):  # pragma: no cover
        """Fake run_process that returns a successful completed process."""

        class CP:
            """Dummy CP indicating success."""

            returncode = 0
            stdout = b"stdout"
            stderr = b"stderr"

        return CP()

    async def rp_fail(*args: object, **kwargs: object):  # pragma: no cover
        """Fake run_process that returns a failing completed process."""

        class CP:
            """Dummy CP indicating failure."""

            returncode = 7
            stdout = b""
            stderr = b"err"

        return CP()

    monkeypatch.setattr(update_template, "run_process", rp_ok)
    caplog.set_level("INFO")
    cp = await update_template._exec("echo")
    assert cp.returncode == 0
    assert "stdout" in caplog.text
    assert "stderr" in caplog.text

    monkeypatch.setattr(update_template, "run_process", rp_fail)
    with pytest.raises(ChildProcessError):
        await update_template._exec("fail")


@pytest.mark.anyio
async def test_parser_and_path_resolution(
    monkeypatch: MonkeyPatch, tmp_path: PathLike[str]
) -> None:
    """``parser()`` should resolve provided inputs and dispatch gracefully."""

    called: dict[str, update_template.Arguments] = {}

    async def fake_main(args: update_template.Arguments):
        """Capture the arguments passed from the parser for assertion."""
        called["args"] = args

    monkeypatch.setattr(update_template, "main", fake_main)

    repo = Path(tmp_path) / "repo"
    await repo.mkdir()
    parser = update_template.parser()
    ns = parser.parse_args(["continue", fspath(repo)])
    await ns.invoke(ns)

    assert called["args"].action == "continue"
    assert called["args"].inputs == (await repo.resolve(),)

    # parser should enforce the ``choices`` on the first argument.  our
    # helper uses ``exit_on_error=False`` so ``parse_args`` raises an
    # ``argparse.ArgumentError`` instead of exiting.

    with pytest.raises(ArgumentError):
        parser.parse_args(["bogus", fspath(repo)])

    # invalid path should raise; the behaviour mirrors the dependency
    # tool and wraps the error in an ExceptionGroup.
    ns2 = parser.parse_args(["update", fspath(repo / "x")])
    with pytest.raises(BaseExceptionGroup) as exc:
        await ns2.invoke(ns2)
    assert any(isinstance(e, FileNotFoundError) for e in exc.value.exceptions)


@pytest.mark.anyio
async def test_main_continue_and_update(
    monkeypatch: MonkeyPatch, tmp_path: PathLike[str]
) -> None:
    """The two action types drive different sets of git commands."""

    repo = Path(tmp_path) / "foo"
    await repo.mkdir()

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_exec(*args: object, **kwargs: object):
        """Stub for _exec used during continue/update workflow tests; records calls."""
        calls.append((args, kwargs))
        return _DummyCP()

    monkeypatch.setattr(update_template, "_exec", fake_exec)

    async def _git(cmd: str) -> str:
        """Stub returning a fixed git path used in workflow assertions."""
        return "/git"

    monkeypatch.setattr(update_template, "_which2", _git)

    # "continue" should only commit/tag; main() exits with code 0
    with pytest.raises(SystemExit) as se1:
        await update_template.main(
            update_template.Arguments(action="continue", inputs=[repo])
        )
    assert se1.value.code == 0
    assert any("commit" in c[0] for c in calls)
    assert any("tag" in c[0] for c in calls)
    calls.clear()

    # "update" should fetch, merge, then tag (also exits)
    with pytest.raises(SystemExit) as se2:
        await update_template.main(
            update_template.Arguments(action="update", inputs=[repo])
        )
    assert se2.value.code == 0
    assert any("fetch" in c[0] for c in calls)
    assert any("merge" in c[0] for c in calls)
    assert any("tag" in c[0] for c in calls)


@pytest.mark.anyio
async def test_main_invalid_action_raises(
    monkeypatch: MonkeyPatch, tmp_path: PathLike[str]
) -> None:
    """Supplying an unknown action string results in ``TypeError``.

    The error is raised *before* any subprocess invocation, so we use a
    stubbed ``_exec`` to ensure we would have detected a call if the logic
    were incorrect.
    """

    repo = Path(tmp_path) / "bar"
    await repo.mkdir()

    def _exec(*args: object, **kwargs: object):
        """Synchronous stub for _exec that returns a dummy process object."""
        return _DummyCP()

    monkeypatch.setattr(update_template, "_exec", _exec)

    async def _git(cmd: str) -> str:
        """Stub returning a constant git path for use in invalid-action tests."""
        return "/git"

    monkeypatch.setattr(update_template, "_which2", _git)

    with pytest.raises(TypeError):
        await update_template.main(
            update_template.Arguments(
                action=cast(Literal["update"], "bogus"), inputs=[repo]
            )
        )


@pytest.mark.anyio
async def test_main_error_grouping(
    monkeypatch: MonkeyPatch, tmp_path: PathLike[str]
) -> None:
    """Failures in multiple repositories are aggregated into a
    ``BaseExceptionGroup``.
    """

    repo1 = Path(tmp_path) / "a"
    repo2 = Path(tmp_path) / "b"
    await repo1.mkdir()
    await repo2.mkdir()

    async def broken_exec(*args: object, **kwargs: object):
        """Stub for _exec that fails when run in the first repo and succeeds otherwise."""
        if kwargs.get("cwd") == repo1:
            raise ChildProcessError(2, "nope")
        return _DummyCP()

    monkeypatch.setattr(update_template, "_exec", broken_exec)

    async def _git(cmd: str) -> str:
        """Stub returning a constant git path used in the error-grouping scenario."""
        return "/git"

    monkeypatch.setattr(update_template, "_which2", _git)

    with pytest.raises(BaseExceptionGroup) as excinfo:
        await update_template.main(
            update_template.Arguments(action="continue", inputs=[repo1, repo2])
        )
    assert any(isinstance(e, ChildProcessError) for e in excinfo.value.exceptions)
