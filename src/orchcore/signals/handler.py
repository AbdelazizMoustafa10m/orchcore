"""Signal handling for graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Callable
from types import FrameType, TracebackType

logger = logging.getLogger(__name__)

type SignalHandler = int | Callable[[int, FrameType | None], object] | None


class SignalManager:
    """Context manager for SIGINT/SIGTERM handling with graceful shutdown."""

    def __init__(self) -> None:
        self._shutdown_requested = False
        self._signal_count = 0
        self._original_handlers: dict[signal.Signals, SignalHandler] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def shutdown_requested(self) -> bool:
        """True if a shutdown signal has been received."""
        return self._shutdown_requested

    async def __aenter__(self) -> SignalManager:
        """Install signal handlers on the running event loop."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            return self

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._original_handlers[sig] = signal.getsignal(sig)
            self._loop.add_signal_handler(sig, self._handle_signal, sig)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Restore original signal handlers."""
        del exc_type, exc, traceback

        if self._loop is None:
            return

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                self._loop.remove_signal_handler(sig)

            original_handler = self._original_handlers.get(sig)
            if original_handler is None:
                continue
            signal.signal(sig, original_handler)

        self._original_handlers.clear()
        self._loop = None

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle a received signal."""
        self._signal_count += 1
        sig_name = sig.name

        if self._signal_count == 1:
            logger.info("Received %s, initiating graceful shutdown...", sig_name)
            self._shutdown_requested = True
            return

        logger.warning("Received second %s, forcing exit.", sig_name)
        sys.exit(130)

    def check_shutdown(self) -> None:
        """Raise CancelledError if shutdown has been requested."""
        if self._shutdown_requested:
            raise asyncio.CancelledError("Shutdown requested via signal")
