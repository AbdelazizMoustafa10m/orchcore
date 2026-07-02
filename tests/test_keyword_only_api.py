"""WP-19 canaries: public signatures are keyword-only after their core args."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchcore.observability.telemetry import OrchcoreTelemetry
from orchcore.pipeline.engine import PhaseRunner
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner
from orchcore.workspace.manager import WorkspaceManager

if TYPE_CHECKING:
    from pathlib import Path

    from orchcore.registry.agent import AgentConfig


@pytest.mark.asyncio
async def test_agent_runner_run_rejects_positional_flag_profile(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="positional"):
        await AgentRunner().run(  # type: ignore[misc]
            sample_agent_config,
            "prompt",
            tmp_path / "output.md",
            "plan",
        )


def test_phase_runner_rejects_positional_workspace(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path)
    with pytest.raises(TypeError, match="positional"):
        PhaseRunner(AgentRunner(), AgentRegistry(), workspace)  # type: ignore[misc]


def test_workspace_manager_rejects_positional_workspace_name(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="positional"):
        WorkspaceManager(tmp_path, "custom-workspace")  # type: ignore[misc]


def test_telemetry_rejects_positional_enabled() -> None:
    with pytest.raises(TypeError, match="positional"):
        OrchcoreTelemetry(True)  # type: ignore[misc]
