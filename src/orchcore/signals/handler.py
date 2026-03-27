"""Signal handling for graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from types import FrameType, TracebackType

logger = logging.getLogger(__name__)

type SignalHandler = int | Callable[[int, FrameType | None], object] | None


class SignalManager:
    """Context manager for SIGINT/SIGTERM handling with graceful shutdown.

    The first shutdown signal requests graceful cancellation. Only repeated
    ``SIGINT`` escalates to a forced exit; ``SIGTERM`` participates in graceful
    shutdown but never counts toward the forced-exit threshold.
    """

    def __init__(self) -> None:
        self._shutdown_requested = False
        self._sigint_count = 0
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
        sig_name = sig.name

        if sig is signal.SIGINT:
            self._sigint_count += 1
            if self._sigint_count >= 2:
                logger.warning("Received second SIGINT, forcing shutdown.")
                # Raise KeyboardInterrupt instead of sys.exit so embedding
                # applications retain control over the process exit path.
                raise KeyboardInterrupt

        if not self._shutdown_requested:
            logger.info("Received %s, initiating graceful shutdown...", sig_name)
            self._shutdown_requested = True

    def check_shutdown(self) -> None:
        """Raise CancelledError if shutdown has been requested."""
        if self._shutdown_requested:
            raise asyncio.CancelledError("Shutdown requested via signal")
