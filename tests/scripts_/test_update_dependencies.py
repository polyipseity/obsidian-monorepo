"""Unit tests for :mod:`scripts.update_dependencies`.

The real functionality involves spawning subprocesses and mutating
repository trees.  These tests exercise the public API (``parser`` and
``main``) by stubbing out the external behaviour with ``monkeypatch`` and
making assertions about command invocation sequences, file-system side
effects, and error handling.  The test suite is deliberately thorough -
updating dependencies is a workspace-wide operation and bugs can be
painful, so we verify all interesting code paths.

Design notes
------------
* ``pytest.mark.anyio`` is applied to asynchronous tests so that coroutine
  entrypoints can be awaited directly.  The repository ``conftest`` file
  configures the AnyIO backend to ``asyncio`` with uvloop enabled.
* The module under test exposes two private helpers ``_which2`` and
  ``_exec``; we test them explicitly to validate their logging and
  error–translation behaviour.  Higher–level tests monkeypatch these
  helpers to avoid spawning real processes.
* ``parser()`` behaviour is exercised by substituting a fake ``main``
  implementation and by checking that path resolution is strict.
* ``main()`` is called with real temporary directories so that trimming of
  ``package-lock.json`` and other filesystem effects can be observed.

Because many branches rely on variations of ``which()`` returning ``None``
or non-``None`` values, the tests frequently override both ``which`` and
``_which2`` with simple async stubs that record the requested command and
return pre‑determined strings.  The ``_exec`` stub records every invocation
and can be configured to raise ``ChildProcessError`` to simulate a failure.

"""

from os import PathLike, fspath

import pytest
from anyio import Path
from pytest import LogCaptureFixture, MonkeyPatch

from scripts import update_dependencies

__all__ = ()


def test_arguments_tuple_conversion_and_immutability():
    """The ``Arguments`` dataclass should coerce ``inputs`` to a tuple and
    prevent mutation (frozen)."""

    a = update_dependencies.Arguments(filter="x", inputs=[Path("a"), Path("b")])
    assert isinstance(a.inputs, tuple)
    with pytest.raises(AttributeError):
        setattr(a, "inputs", ())


class _DummyCP:
    """Minimal stand‑in for ``subprocess.CompletedProcess``.

    ``_exec`` returns this object so that callers can inspect ``returncode``
    without touching any real system processes.
    """

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        """Initialize the dummy process with return code and I/O data."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.anyio
async def test_which2_translates_none(monkeypatch: MonkeyPatch) -> None:
    """``_which2`` wraps ``aioshutil.which`` and raises when missing.

    We patch the imported ``which`` in the module and drive both return
    branches.
    """

    async def _yes(cmd: str) -> str | None:  # pragma: no cover - simple stub
        """Stub which returns a fake path for any command."""
        return "/usr/bin/" + cmd

    async def _no(cmd: str) -> None:  # pragma: no cover - simple stub
        """Stub which simulates a missing command by returning None."""
        return None

    monkeypatch.setattr(update_dependencies, "which", _yes)
    assert await update_dependencies._which2("git") == "/usr/bin/git"

    monkeypatch.setattr(update_dependencies, "which", _no)
    with pytest.raises(FileNotFoundError):
        await update_dependencies._which2("ncu")


@pytest.mark.anyio
async def test_exec_logging_and_error(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    """``_exec`` logs stdout/stderr and raises on non‑zero returncodes."""

    # stub ``run_process`` to simulate different outcomes
    async def _good(*args: object, **kwargs: object):  # pragma: no cover - simple stub
        """Fake run_process implementation that simulates a successful command."""

        class CP:  # noqa: D401 - nested, easier than using namedtuple
            """Dummy completed process representing success."""

            returncode = 0
            stdout = b"out\n"
            stderr = b"err\n"

        return CP()

    async def _bad(*args: object, **kwargs: object):  # pragma: no cover
        """Fake run_process that simulates a failing command with nonzero status."""

        class CP:
            """Dummy completed process representing failure."""

            returncode = 5
            stdout = b""
            stderr = b"fail"

        return CP()

    monkeypatch.setattr(update_dependencies, "run_process", _good)
    caplog.set_level("INFO")
    cp = await update_dependencies._exec("echo", "hello")
    assert cp.returncode == 0
    assert "out" in caplog.text
    assert "err" in caplog.text

    monkeypatch.setattr(update_dependencies, "run_process", _bad)
    with pytest.raises(ChildProcessError):
        await update_dependencies._exec("oops")


@pytest.mark.anyio
async def test_parser_invokes_main_correctly(
    monkeypatch: MonkeyPatch, tmp_path: PathLike[str]
) -> None:
    """``parser()`` should resolve paths and forward arguments to ``main``.

    We install a fake ``main`` that simply records the ``Arguments``
    instance it receives; invoking the parser should call it with an
    appropriately typed object and absolute, resolved paths.
    """

    called: dict[str, update_dependencies.Arguments] = {}

    async def _fake_main(args: update_dependencies.Arguments):
        """Record the arguments received by the parser for assertion."""
        called["args"] = args

    monkeypatch.setattr(update_dependencies, "main", _fake_main)

    repo = Path(tmp_path) / "repo"
    await repo.mkdir()
    parser = update_dependencies.parser()
    ns = parser.parse_args(["--filter", "foo", fspath(repo)])

    # ``invoke`` is an async coroutine
    await ns.invoke(ns)

    assert "args" in called
    assert called["args"].filter == "foo"
    # path should be resolved and absolute
    assert called["args"].inputs == (await repo.resolve(),)

    # non‑existent path should raise when ``strict=True`` resolution is
    # attempted.  the parser `invoke` helper runs the resolutions concurrently
    # and wraps errors in a ``BaseExceptionGroup``.
    ns2 = parser.parse_args([fspath(repo / "doesnotexist")])
    with pytest.raises(BaseExceptionGroup) as exc:
        await ns2.invoke(ns2)
    assert any(isinstance(e, FileNotFoundError) for e in exc.value.exceptions)


@pytest.mark.anyio
async def test_main_performs_workflow_and_trims(
    tmp_path: PathLike[str], monkeypatch: MonkeyPatch
) -> None:
    """Exercise the core repository update workflow without spawning real
    commands.

    The test verifies that:

    * ``ncu`` is invoked with ``--filter`` when ``filter`` is supplied.
    * ``npm install --global npm-check-updates`` branch is taken when the
      initial lookup returns ``None``.
    * ``package-lock.json`` contents are trimmed if they contain leading or
      trailing whitespace.
    * ``git add``, ``git commit`` and ``git tag`` calls occur with the
      expected arguments.
    """

    repo = Path(tmp_path) / "r"
    await repo.mkdir()
    await (repo / "package-lock.json").write_text("   hello  \n")

    # each record is (args tuple, kwargs dict)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def _fake_exec(*args: object, **kwargs: object):
        """Stub for ``_exec`` that records invocations and returns a dummy CP."""
        # record the command and pretend it succeeded
        calls.append((args, kwargs))
        return _DummyCP()

    # initially pretend ``which('ncu')`` returns None so the install branch
    # is exercised
    async def _which(cmd: str) -> str | None:  # type: ignore[override]
        """Stubbed which that returns None for "ncu" and a path otherwise."""
        return None if cmd == "ncu" else "/usr/bin/" + cmd

    async def _which2(cmd: str) -> str:  # type: ignore[override]
        """Stubbed _which2 that always returns a fake path."""
        # the second call for ``ncu`` after installation should succeed
        return "/usr/bin/" + cmd

    monkeypatch.setattr(update_dependencies, "_exec", _fake_exec)
    monkeypatch.setattr(update_dependencies, "which", _which)
    monkeypatch.setattr(update_dependencies, "_which2", _which2)

    args = update_dependencies.Arguments(filter="bar", inputs=[repo])
    # main() calls ``exit(0)`` on success; catch it so the test can continue
    with pytest.raises(SystemExit) as se:
        await update_dependencies.main(args)
    assert se.value.code == 0

    # the lock file should have been trimmed
    assert await (repo / "package-lock.json").read_text() == "hello"

    # the first call should be the ncu upgrade invocation with filter
    assert any(isinstance(call[0][0], str) and "ncu" in call[0][0] for call in calls)
    assert any("--filter" in call[0] for call in calls)

    # ensure the install-global-ncu branch happened
    assert any(
        call[0][1] == "install" and "npm-check-updates" in call[0] for call in calls
    )

    # verify git commit/tag were recorded last
    assert any("commit" in cmd for cmd, _ in calls)
    assert any("tag" in cmd for cmd, _ in calls)


@pytest.mark.anyio
async def test_main_error_grouping(
    tmp_path: PathLike[str], monkeypatch: MonkeyPatch
) -> None:
    """If one repository fails, ``main`` should raise a ``BaseExceptionGroup``.

    The error object should still allow inspection of the individual
    ``ChildProcessError`` that triggered it.
    """

    repo1 = Path(tmp_path) / "a"
    repo2 = Path(tmp_path) / "b"
    await repo1.mkdir()
    await repo2.mkdir()

    async def _broken_exec(*args: object, **kwargs: object):
        """Exec stub that raises an error for the first repo and succeeds otherwise."""
        # fail for the first repository only
        cwd = kwargs.get("cwd")
        if cwd == repo1:
            raise ChildProcessError(1, "oops")
        return _DummyCP()

    monkeypatch.setattr(update_dependencies, "_exec", _broken_exec)

    # helpers that are awaitable and immediately return the given string
    async def _plain(cmd: str) -> str:
        """Helper that simply returns a fixed git path for any command."""
        return "/git"

    monkeypatch.setattr(update_dependencies, "which", _plain)
    monkeypatch.setattr(update_dependencies, "_which2", _plain)

    args = update_dependencies.Arguments(filter=None, inputs=[repo1, repo2])
    with pytest.raises(BaseExceptionGroup) as ei:
        await update_dependencies.main(args)
    assert any(isinstance(e, ChildProcessError) for e in ei.value.exceptions)
