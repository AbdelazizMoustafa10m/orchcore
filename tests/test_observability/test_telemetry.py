from __future__ import annotations

import importlib
from decimal import Decimal
from typing import TYPE_CHECKING

from orchcore.observability.telemetry import OrchcoreTelemetry

if TYPE_CHECKING:
    import pytest


def test_telemetry_disabled_constructor_is_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    import_calls: list[str] = []

    def fail_if_called(module_name: str) -> object:
        import_calls.append(module_name)
        raise AssertionError("disabled telemetry should not import OpenTelemetry")

    monkeypatch.setattr(importlib, "import_module", fail_if_called)

    # Act
    telemetry = OrchcoreTelemetry(enabled=False)

    # Assert
    assert telemetry._enabled is False
    assert telemetry._trace_api is None
    assert telemetry._tracer is None
    assert import_calls == []


def test_pipeline_span_yields_none_when_telemetry_is_disabled() -> None:
    # Arrange
    telemetry = OrchcoreTelemetry(enabled=False)

    # Act
    with telemetry.pipeline_span("delivery", "task-123") as span:
        # Assert
        assert span is None


def test_phase_span_yields_none_when_telemetry_is_disabled() -> None:
    # Arrange
    telemetry = OrchcoreTelemetry(enabled=False)

    # Act
    with telemetry.phase_span("implementation", agent="worker-g") as span:
        # Assert
        assert span is None


def test_record_cost_does_not_raise_when_telemetry_is_disabled() -> None:
    # Arrange
    telemetry = OrchcoreTelemetry(enabled=False)

    # Act / Assert
    telemetry.record_cost(agent="worker-g", cost_usd=Decimal("1.25"))
