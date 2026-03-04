"""Tests ensuring scripts under top-level directories are executable.

The helpers here verify that files in configured script folders have the
executable bit set on non-Windows platforms and that git's index marks them
appropriately.  Additional utilities support querying git and determining the
candidate directories.  Added tests also exercise these helpers directly so
that their behaviour is covered and type checked.
"""

import os
import stat
import subprocess
from collections.abc import Sequence

import pytest
from anyio import IncompleteRead, Path, run_process

__all__ = ()


def _get_candidate_dirs() -> Sequence[Path]:
    """Return top-level folders whose immediate files should be executable.

    The list is intentionally easy to extend; add more directories when
    the repository grows additional script folders at the root level.
    """
    root = Path(__file__).parent.parent
    return (root / "scripts",)


async def git_mode(path: Path) -> str | None:
    """Query git for the index mode of a file.

    The return value will look like ``"100644"`` or ``"100755"``; if the
    file is not tracked at all the function returns ``None``.  We use
    ``anyio.run_process`` so that the helper is fully async-friendly.
    """
    root = Path(__file__).parent.parent
    # git understands forward slashes even on Windows; convert to posix
    rel = path.relative_to(root).as_posix()
    try:
        proc = await run_process(
            ["git", "ls-files", "--stage", "--", rel],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return None
    out = proc.stdout.decode().strip()  # type: ignore[attr-defined]
    if not out:
        return None
    return out.split()[0]


@pytest.mark.anyio
async def test_top_level_scripts_executable() -> None:
    """Ensure every file directly under the configured directories has an
    executable bit set (on platforms where that makes sense).
    """

    for scripts_dir in _get_candidate_dirs():
        assert await scripts_dir.is_dir(), f"expected {scripts_dir} to exist"
        async for entry in scripts_dir.iterdir():
            if not await entry.is_file():
                # ignore subdirectories; only check files directly under
                continue

            # permissions check
            try:
                st = await entry.stat()
                is_exec = bool(
                    st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                )
            except (OSError, IncompleteRead):
                is_exec = False

            git_mode_str: str | None = await git_mode(entry)
            if git_mode_str is not None:
                # sanity check: git index mode should be one of the known values.
                # `100644`/`100755` are regular files; `120000` is a symlink.
                assert git_mode_str in (
                    "100644",
                    "100755",
                    "120000",
                ), f"unexpected mode {git_mode_str} for {entry}"

            # Windows doesn't honor the executable bit; just skip without
            # emitting warnings. The earlier implementation warned, but the
            # user requested silence on Windows.
            if os.name == "nt":
                continue

            # on non-Windows platforms we insist on the bit being present and
            # additionally that git considers the file executable.  The git
            # check is a best-effort attempt because the file might not have
            # been added yet (newly created in a test branch), so we only assert
            # when ``git_mode_str`` is available.  Symlinks (mode 120000) are
            # allowed by virtue of the above check; they do not have a separate
            # executable bit of their own.
            assert is_exec, f"{entry} is not marked executable"
            if git_mode_str is not None and not git_mode_str.startswith("12"):
                assert git_mode_str.startswith("1007"), (
                    f"{entry} is tracked but the git index does not mark it as executable"
                )


@pytest.mark.anyio
async def test_get_candidate_dirs_basic() -> None:
    """Verify that the helper returns a non-empty tuple of Path objects."""
    dirs = _get_candidate_dirs()
    assert isinstance(dirs, tuple)
    assert dirs, "should return at least one directory"
    assert all(isinstance(p, Path) for p in dirs)


@pytest.mark.anyio
async def test_git_mode_tracked() -> None:
    """Verify that the helper returns a valid mode string for a tracked file."""
    path = Path(__file__)
    mode = await git_mode(path)
    assert mode is not None


@pytest.mark.anyio
async def test_git_mode_untracked(tmp_path: Path) -> None:
    """Verify that the helper returns None for an untracked file."""
    root = Path(__file__).parent.parent
    # create a file inside the repository but do not add it to git.  to avoid
    # collisions with any real file we generate a unique temporary subdirectory
    # using ``tmp_path.name`` which is guaranteed not to exist already.  the
    # helper only works on files beneath the repo root so we create the
    # directory here rather than relying on ``tmp_path`` directly.
    unique_dir = root / tmp_path.name
    await unique_dir.mkdir()
    new_file = unique_dir / "tmp_untracked.txt"
    await new_file.write_text("x")
    try:
        mode = await git_mode(new_file)
        assert mode is None
    finally:
        # clean up both the file and the directory; ignore errors since the
        # filesystem may already have removed them.
        try:
            await new_file.unlink()
        except Exception:
            pass
        try:
            await unique_dir.rmdir()
        except Exception:
            pass
