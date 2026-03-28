"""Git workspace recovery helpers for retry flows."""

from __future__ import annotations

import asyncio
import logging
import re

logger: logging.Logger = logging.getLogger(__name__)


class GitRecovery:
    """Manage git workspace state during retries."""

    def __init__(self, working_dir: str | None = None) -> None:
        self._cwd = working_dir

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """Run a git command and return ``(exit_code, stdout, stderr)``."""
        cmd = ["git", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        assert proc.returncode is not None
        return proc.returncode, stdout, stderr

    async def is_tree_dirty(self) -> bool:
        """Check if the git working tree has uncommitted changes."""
        exit_code, stdout, _ = await self._run_git("status", "--porcelain")
        return exit_code == 0 and len(stdout) > 0

    async def auto_commit(self, message: str | None = None) -> bool:
        """Stage all changes and commit with the given message."""
        commit_msg = message or "orchcore: auto-commit before retry"

        exit_code, _, stderr = await self._run_git("add", "-A")
        if exit_code != 0:
            logger.warning("git add failed: %s", stderr)
            return False

        exit_code, _, stderr = await self._run_git(
            "commit",
            "-m",
            commit_msg,
            "--no-verify",
        )
        if exit_code != 0:
            logger.warning("git commit failed: %s", stderr)
            return False

        logger.info("Auto-committed changes: %s", commit_msg)
        return True

    async def stash_dirty_tree(self) -> bool:
        """Stash uncommitted changes in the working tree."""
        exit_code, stdout, stderr = await self._run_git(
            "stash",
            "push",
            "-m",
            "orchcore: stash before retry",
        )
        if exit_code != 0:
            logger.warning("git stash failed: %s", stderr)
            return False

        if "No local changes" in stdout:
            return False

        logger.info("Stashed dirty tree changes")
        return True

    async def restore_stash(self) -> bool:
        """Restore the most recent stash entry."""
        exit_code, _, stderr = await self._run_git("stash", "pop")
        if exit_code != 0:
            logger.warning("git stash pop failed: %s", stderr)
            return False

        logger.info("Restored stashed changes")
        return True

    @staticmethod
    def extract_commit_message(agent_output: str) -> str:
        """Extract a generic commit message from agent output."""
        match = re.search(
            r"commit\s+message\s*:\s*(.+)",
            agent_output,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()[:200]

        match = re.search(
            r"```(?:[a-zA-Z0-9_+-]+\n)?(.*?)```",
            agent_output,
            re.DOTALL,
        )
        if match:
            fence_content = match.group(1).strip()
            if fence_content:
                return fence_content.splitlines()[0].strip()[:200]

        match = re.search(r"^#\s+(.+)$", agent_output, re.MULTILINE)
        if match:
            return match.group(1).strip()[:200]

        return "orchcore: auto-commit of agent changes"
