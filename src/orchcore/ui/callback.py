"""UICallback protocol -- decouples engine from presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from orchcore.pipeline.phase import Phase, PhaseResult, PipelineResult
    from orchcore.stream.events import (
        AgentResult,
        StreamEvent,
    )


@runtime_checkable
class UICallback(Protocol):
    """Contract between the orchestration engine and the UI/presentation layer.

    Consuming projects implement this protocol for their specific UI
    (Rich CLI, Textual TUI, headless JSONL, etc.). orchcore never imports
    any display framework -- all presentation is mediated through this protocol.
    """

    def on_pipeline_start(self, phases: list[Phase]) -> None: ...

    def on_pipeline_complete(self, result: PipelineResult) -> None: ...

    def on_phase_start(self, phase: Phase) -> None: ...

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None: ...

    def on_phase_skip(self, phase: Phase, reason: str) -> None: ...

    def on_agent_start(self, agent_name: str, phase: str) -> None: ...

    def on_agent_event(self, event: StreamEvent) -> None: ...

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None: ...

    def on_agent_error(self, agent_name: str, error: str) -> None: ...

    def on_stall_detected(self, agent_name: str, duration: float) -> None: ...

    def on_rate_limit(self, agent_name: str, message: str) -> None: ...

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None: ...

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None: ...

    def on_git_recovery(self, action: str, detail: str) -> None: ...

    def on_shutdown(self, reason: str) -> None: ...


class NullCallback:
    """No-op implementation of UICallback. All methods do nothing."""

    def on_pipeline_start(self, phases: list[Phase]) -> None:
        pass

    def on_pipeline_complete(self, result: PipelineResult) -> None:
        pass

    def on_phase_start(self, phase: Phase) -> None:
        pass

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None:
        pass

    def on_phase_skip(self, phase: Phase, reason: str) -> None:
        pass

    def on_agent_start(self, agent_name: str, phase: str) -> None:
        pass

    def on_agent_event(self, event: StreamEvent) -> None:
        pass

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None:
        pass

    def on_agent_error(self, agent_name: str, error: str) -> None:
        pass

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        pass

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        pass

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        pass

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        pass

    def on_git_recovery(self, action: str, detail: str) -> None:
        pass

    def on_shutdown(self, reason: str) -> None:
        pass


class LoggingCallback(NullCallback):
    """UICallback implementation that logs events via Python logging."""

    def __init__(self) -> None:
        import logging

        self._logger = logging.getLogger("orchcore.ui")

    def on_pipeline_start(self, phases: list[Phase]) -> None:
        self._logger.info("Pipeline starting with %d phases", len(phases))

    def on_pipeline_complete(self, result: PipelineResult) -> None:
        self._logger.info("Pipeline complete: success=%s", result.success)

    def on_phase_start(self, phase: Phase) -> None:
        self._logger.info("Phase '%s' starting", phase.name)

    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None:
        self._logger.info("Phase '%s' ended: %s", phase.name, result.status)

    def on_phase_skip(self, phase: Phase, reason: str) -> None:
        self._logger.info("Phase '%s' skipped: %s", phase.name, reason)

    def on_agent_start(self, agent_name: str, phase: str) -> None:
        self._logger.info("Agent '%s' starting in phase '%s'", agent_name, phase)

    def on_agent_event(self, event: StreamEvent) -> None:
        self._logger.debug("Agent event: %s", event.event_type)

    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None:
        self._logger.info("Agent '%s' complete: exit_code=%d", agent_name, result.exit_code)

    def on_agent_error(self, agent_name: str, error: str) -> None:
        self._logger.error("Agent '%s' error: %s", agent_name, error)

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        self._logger.warning("Agent '%s' stalled for %.1f seconds", agent_name, duration)

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        self._logger.warning("Agent '%s' rate limited: %s", agent_name, message)

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        self._logger.warning(
            "Agent '%s' waiting %.1f seconds after rate limit",
            agent_name,
            wait_seconds,
        )

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        self._logger.warning(
            "Retrying agent '%s' (attempt %d/%d)",
            agent_name,
            attempt,
            max_attempts,
        )

    def on_git_recovery(self, action: str, detail: str) -> None:
        self._logger.info("Git recovery '%s': %s", action, detail)

    def on_shutdown(self, reason: str) -> None:
        self._logger.info("Shutdown: %s", reason)


__all__ = ["LoggingCallback", "NullCallback", "UICallback"]
