from __future__ import annotations

import asyncio

import pytest

from orchcore.pipeline.control import FlowControl


def test_flow_control_initial_state() -> None:
    # Arrange
    control = FlowControl()

    # Assert
    assert not control.is_paused
    assert not control.skip_requested


def test_pause_and_resume_toggle_paused_state() -> None:
    # Arrange
    control = FlowControl()

    # Act
    control.pause()

    # Assert
    assert control.is_paused

    # Act
    control.resume()

    # Assert
    assert not control.is_paused


def test_request_skip_and_clear_skip_toggle_skip_flag() -> None:
    # Arrange
    control = FlowControl()

    # Act
    control.request_skip()

    # Assert
    assert control.skip_requested

    # Act
    control.clear_skip()

    # Assert
    assert not control.skip_requested


@pytest.mark.asyncio
async def test_wait_if_paused_does_not_block_when_not_paused() -> None:
    # Arrange
    control = FlowControl()

    # Act / Assert
    await asyncio.wait_for(control.wait_if_paused(), timeout=0.1)


@pytest.mark.asyncio
async def test_wait_if_paused_blocks_until_resume() -> None:
    # Arrange
    control = FlowControl()
    control.pause()
    wait_task = asyncio.create_task(control.wait_if_paused())

    # Act / Assert
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.05)

    assert not wait_task.done()

    # Act
    control.resume()

    # Assert
    await asyncio.wait_for(wait_task, timeout=0.1)
    assert wait_task.done()
