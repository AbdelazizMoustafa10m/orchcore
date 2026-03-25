from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from orchcore.recovery.git_recovery import GitRecovery

if TYPE_CHECKING:
    from pathlib import Path


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.mark.parametrize(
    ("agent_output", "expected"),
    [
        pytest.param(
            "Summary\ncommit message: orchcore: checkpoint before retry\nMore details",
            "orchcore: checkpoint before retry",
            id="commit-message-label",
        ),
        pytest.param(
            "# orchcore: heading commit message\nBody text",
            "orchcore: heading commit message",
            id="heading",
        ),
        pytest.param(
            "No commit metadata was produced by the agent.",
            "orchcore: auto-commit of agent changes",
            id="fallback",
        ),
    ],
)
def test_extract_commit_message_cases(agent_output: str, expected: str) -> None:
    result = GitRecovery.extract_commit_message(agent_output)

    assert result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        pytest.param(0, b" M src/orchcore/recovery/retry.py\n", True, id="dirty"),
        pytest.param(0, b"", False, id="clean"),
    ],
)
async def test_is_tree_dirty_uses_mocked_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stdout: bytes,
    expected: bool,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    working_dir = str(tmp_path / "repo")

    async def fake_create_subprocess_exec(
        *args: str,
        **kwargs: object,
    ) -> _FakeProcess:
        calls.append((args, kwargs))
        return _FakeProcess(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(
        "orchcore.recovery.git_recovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    recovery = GitRecovery(working_dir=working_dir)

    result = await recovery.is_tree_dirty()

    assert result is expected
    assert len(calls) == 1
    assert calls[0][0] == ("git", "status", "--porcelain")
    assert calls[0][1]["cwd"] == working_dir
    assert calls[0][1]["stdout"] is asyncio.subprocess.PIPE
    assert calls[0][1]["stderr"] is asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_auto_commit_runs_git_add_then_commit_with_mocked_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    working_dir = str(tmp_path / "repo")
    processes = iter(
        [
            _FakeProcess(returncode=0),
            _FakeProcess(returncode=0),
        ]
    )

    async def fake_create_subprocess_exec(
        *args: str,
        **kwargs: object,
    ) -> _FakeProcess:
        calls.append((args, kwargs))
        return next(processes)

    monkeypatch.setattr(
        "orchcore.recovery.git_recovery.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    recovery = GitRecovery(working_dir=working_dir)

    result = await recovery.auto_commit("orchcore: worker-f checkpoint")

    assert result is True
    assert [call[0] for call in calls] == [
        ("git", "add", "-A"),
        (
            "git",
            "commit",
            "-m",
            "orchcore: worker-f checkpoint",
            "--no-verify",
        ),
    ]
    assert all(call[1]["cwd"] == working_dir for call in calls)
