from __future__ import annotations

import asyncio
import signal
import sys
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
    # Arrange
    manager = SignalManager()

    # Act / Assert
    assert manager.shutdown_requested is False


def test_check_shutdown_does_not_raise_when_not_requested() -> None:
    # Arrange
    manager = SignalManager()

    # Act
    manager.check_shutdown()


@pytest.mark.asyncio
async def test_signal_manager_supports_async_context_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
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

    # Act
    async with manager as active_manager:
        # Assert
        assert active_manager is manager
        assert active_manager.shutdown_requested is False
        active_manager.check_shutdown()
        assert [entry[0] for entry in fake_loop.added] == [signal.SIGINT, signal.SIGTERM]
        assert [entry[2] for entry in fake_loop.added] == [
            (signal.SIGINT,),
            (signal.SIGTERM,),
        ]

    # Assert
    assert fake_loop.removed == [signal.SIGINT, signal.SIGTERM]
    assert restored_handlers == [
        (signal.SIGINT, signal.default_int_handler),
        (signal.SIGTERM, signal.SIG_DFL),
    ]


def test_sigterm_then_sigint_does_not_force_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    manager = SignalManager()
    exit_calls: list[int] = []

    monkeypatch.setattr(sys, "exit", lambda code: exit_calls.append(code))

    # Act
    manager._handle_signal(signal.SIGTERM)
    manager._handle_signal(signal.SIGINT)

    # Assert
    assert manager.shutdown_requested is True
    assert exit_calls == []
    with pytest.raises(asyncio.CancelledError, match="Shutdown requested via signal"):
        manager.check_shutdown()


def test_second_sigint_forces_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    manager = SignalManager()

    def raise_system_exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", raise_system_exit)

    # Act
    manager._handle_signal(signal.SIGINT)

    # Assert
    assert manager.shutdown_requested is True
    with pytest.raises(SystemExit, match="130"):
        manager._handle_signal(signal.SIGINT)
