from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import pytest

from orchcore.signals.handler import SignalManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType


class FakeLoop:
    def __init__(self) -> None:
        self.added: list[
            tuple[signal.Signals, Callable[..., object], tuple[signal.Signals, ...]]
        ] = []
        self.removed: list[signal.Signals] = []

    def add_signal_handler(
        self,
        sig: signal.Signals,
        callback: Callable[..., object],
        *args: signal.Signals,
    ) -> None:
        self.added.append((sig, callback, args))

    def remove_signal_handler(self, sig: signal.Signals) -> bool:
        self.removed.append(sig)
        return True


def test_shutdown_requested_is_initially_false() -> None:
    manager = SignalManager()

    assert manager.shutdown_requested is False


def test_check_shutdown_does_not_raise_when_not_requested() -> None:
    manager = SignalManager()

    manager.check_shutdown()


@pytest.mark.asyncio
async def test_signal_manager_supports_async_context_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_loop = FakeLoop()
    original_handlers: dict[
        signal.Signals,
        int | Callable[[int, FrameType | None], object] | None,
    ] = {
        signal.SIGINT: signal.default_int_handler,
        signal.SIGTERM: signal.SIG_DFL,
    }
    restored_handlers: list[
        tuple[signal.Signals, int | Callable[[int, FrameType | None], object] | None]
    ] = []

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(signal, "getsignal", original_handlers.__getitem__)
    monkeypatch.setattr(
        signal,
        "signal",
        lambda sig, handler: restored_handlers.append((sig, handler)),
    )

    manager = SignalManager()

    async with manager as active_manager:
        assert active_manager is manager
        assert active_manager.shutdown_requested is False
        active_manager.check_shutdown()
        assert [entry[0] for entry in fake_loop.added] == [signal.SIGINT, signal.SIGTERM]
        assert [entry[2] for entry in fake_loop.added] == [
            (signal.SIGINT,),
            (signal.SIGTERM,),
        ]

    assert fake_loop.removed == [signal.SIGINT, signal.SIGTERM]
    assert restored_handlers == [
        (signal.SIGINT, signal.default_int_handler),
        (signal.SIGTERM, signal.SIG_DFL),
    ]
