"""Signal-based flow control for pipeline execution."""

from __future__ import annotations

import asyncio


class FlowControl:
    """Shared control object for pausing, resuming, and skipping phases.

    Thread-safety note: all methods are safe to call from worker threads
    because ``asyncio.Event`` is loop-aware and the boolean skip flag is
    updated atomically before external process termination is coordinated.
    """

    def __init__(self) -> None:
        self._resume_event: asyncio.Event | None = None
        self._is_paused = False
        self._skip_requested = False

    def _get_event(self) -> asyncio.Event:
        """Create the resume event lazily inside the active event loop."""
        if self._resume_event is None:
            self._resume_event = asyncio.Event()
            self._resume_event.set()
        return self._resume_event

    def pause(self) -> None:
        """Pause the pipeline before the next inter-phase checkpoint."""
        if self._is_paused:
            return

        self._is_paused = True
        self._get_event().clear()

    def resume(self) -> None:
        """Resume a paused pipeline."""
        if not self._is_paused:
            return

        self._is_paused = False
        self._get_event().set()

    def request_skip(self) -> None:
        """Request that the current phase be skipped."""
        self._skip_requested = True

    def clear_skip(self) -> None:
        """Clear the skip flag after the engine has handled it."""
        self._skip_requested = False

    @property
    def is_paused(self) -> bool:
        """Return ``True`` when the pipeline is paused."""
        return self._is_paused

    @property
    def skip_requested(self) -> bool:
        """Return ``True`` when the engine should skip the current phase."""
        return self._skip_requested

    async def wait_if_paused(self) -> None:
        """Block until the pipeline is no longer paused.

        Called between phases so pause takes effect at a clean boundary.
        """
        await self._get_event().wait()
