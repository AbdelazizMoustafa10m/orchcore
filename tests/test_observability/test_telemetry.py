from __future__ import annotations

import importlib
import types
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from orchcore.observability.telemetry import OrchcoreTelemetry
from orchcore.stream.events import ToolExecution

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager


class _FakeSpan:
    def __init__(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class _BrokenSpan(_FakeSpan):
    def set_attribute(self, key: str, value: object) -> None:
        del key, value
        raise RuntimeError("cannot set attributes")


class _FakeTraceModule(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("opentelemetry.trace")
        self.current_span: _FakeSpan | None = None
        self.provider: _FakeTracerProvider | None = None
        self.tracer = _FakeTracer(self)

    def set_tracer_provider(self, provider: _FakeTracerProvider) -> None:
        self.provider = provider

    def get_tracer(self, name: str) -> _FakeTracer:
        self.tracer.name = name
        return self.tracer

    def get_current_span(self) -> _FakeSpan | None:
        return self.current_span


class _FakeTracer:
    def __init__(self, trace_module: _FakeTraceModule) -> None:
        self._trace_module = trace_module
        self.current_spans: list[_FakeSpan] = []
        self.name = ""

    def start_as_current_span(
        self,
        name: str,
        attributes: dict[str, object] | None = None,
    ) -> object:
        span = _FakeSpan(name, attributes)
        self.current_spans.append(span)
        trace_module = self._trace_module

        class _ContextManager:
            def __enter__(self_nonlocal) -> _FakeSpan:
                self_nonlocal._previous = trace_module.current_span
                trace_module.current_span = span
                return span

            def __exit__(self_nonlocal, exc_type, exc, tb) -> bool:
                del exc_type, exc, tb
                trace_module.current_span = self_nonlocal._previous
                return False

        return _ContextManager()


class _FakeResource:
    @classmethod
    def create(cls, attributes: dict[str, object]) -> dict[str, object]:
        return attributes


class _FakeTracerProvider:
    def __init__(self, resource: dict[str, object]) -> None:
        self.resource = resource
        self.processors: list[_FakeBatchSpanProcessor] = []

    def add_span_processor(self, processor: _FakeBatchSpanProcessor) -> None:
        self.processors.append(processor)


class _FakeBatchSpanProcessor:
    def __init__(self, exporter: _FakeOTLPSpanExporter) -> None:
        self.exporter = exporter


class _FakeOTLPSpanExporter:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint


def _install_fake_opentelemetry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_exporter: bool,
) -> _FakeTraceModule:
    trace_module = _FakeTraceModule()
    exporter_module = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    exporter_module.OTLPSpanExporter = _FakeOTLPSpanExporter
    export_module = types.ModuleType("opentelemetry.sdk.trace.export")
    export_module.BatchSpanProcessor = _FakeBatchSpanProcessor
    resource_module = types.ModuleType("opentelemetry.sdk.resources")
    resource_module.Resource = _FakeResource
    sdk_trace_module = types.ModuleType("opentelemetry.sdk.trace")
    sdk_trace_module.TracerProvider = _FakeTracerProvider

    modules: dict[str, types.ModuleType] = {
        "opentelemetry.trace": trace_module,
        "opentelemetry.sdk.resources": resource_module,
        "opentelemetry.sdk.trace": sdk_trace_module,
        "opentelemetry.sdk.trace.export": export_module,
    }
    if with_exporter:
        modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"] = exporter_module

    def fake_import(module_name: str) -> types.ModuleType:
        if module_name in modules:
            return modules[module_name]
        raise ImportError(f"missing fake module: {module_name}")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    return trace_module


def _build_tool_execution() -> ToolExecution:
    return ToolExecution(
        tool_id="tool-9",
        name="Read",
        friendly_name="Read file",
        detail="README.md",
        started_at=datetime.now(),
    )


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
    assert not telemetry._enabled
    assert telemetry._trace_api is None
    assert telemetry._tracer is None
    assert import_calls == []


@pytest.mark.parametrize(
    "call_span",
    [
        pytest.param(
            lambda t: t.pipeline_span("delivery", "task-123"),
            id="pipeline_span",
        ),
        pytest.param(
            lambda t: t.phase_span("implementation", agent="worker-g"),
            id="phase_span",
        ),
        pytest.param(
            lambda t: t.agent_span("implementation", "worker-g"),
            id="agent_span",
        ),
        pytest.param(
            lambda t: t.tool_span("worker-g", _build_tool_execution()),
            id="tool_span",
        ),
    ],
)
def test_span_yields_none_when_telemetry_is_disabled(
    call_span: Callable[[OrchcoreTelemetry], AbstractContextManager[object]],
) -> None:
    # Arrange
    telemetry = OrchcoreTelemetry(enabled=False)

    # Act / Assert
    with call_span(telemetry) as span:
        assert span is None


def test_record_cost_does_not_raise_when_telemetry_is_disabled() -> None:
    # Arrange
    telemetry = OrchcoreTelemetry(enabled=False)

    # Act / Assert
    telemetry.record_cost(agent="worker-g", cost_usd=Decimal("1.25"))


def test_telemetry_builds_spans_when_fake_otel_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    trace_module = _install_fake_opentelemetry(monkeypatch, with_exporter=True)
    telemetry = OrchcoreTelemetry(
        enabled=True,
        service_name="orchcore-tests",
        otlp_endpoint="http://collector:4317",
    )
    tool = _build_tool_execution()

    # Act
    with telemetry.phase_span("implementation", agent="worker-g") as phase_span:
        telemetry.record_cost("worker-g", Decimal("2.50"))

    with telemetry.tool_span("worker-g", tool) as tool_span:
        pass

    # Assert
    assert telemetry._enabled
    assert trace_module.provider is not None
    assert trace_module.provider.resource == {"service.name": "orchcore-tests"}
    assert len(trace_module.provider.processors) == 1
    assert phase_span is not None
    assert phase_span.attributes == {
        "orchcore.phase": "implementation",
        "orchcore.agent": "worker-g",
        "orchcore.cost.worker-g": 2.5,
        "orchcore.cost.total": 2.5,
    }
    assert tool_span is not None
    assert tool_span.attributes == {
        "orchcore.agent": "worker-g",
        "orchcore.tool.name": "Read",
        "orchcore.tool.detail": "README.md",
        "orchcore.tool.id": "tool-9",
    }


def test_telemetry_warns_when_exporter_module_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    _install_fake_opentelemetry(monkeypatch, with_exporter=False)

    # Act
    with caplog.at_level("WARNING"):
        telemetry = OrchcoreTelemetry(
            enabled=True,
            otlp_endpoint="http://collector:4317",
        )

    # Assert
    assert telemetry._enabled
    assert "Telemetry will run without exporting spans" in caplog.text


def test_record_cost_swallows_span_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    trace_module = _install_fake_opentelemetry(monkeypatch, with_exporter=True)
    telemetry = OrchcoreTelemetry(enabled=True)
    trace_module.current_span = _BrokenSpan("broken")

    # Act
    with caplog.at_level("DEBUG"):
        telemetry.record_cost("worker-g", Decimal("1.00"))

    # Assert
    assert "Failed to record telemetry cost for agent worker-g" in caplog.text
