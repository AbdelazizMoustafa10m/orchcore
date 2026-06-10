from __future__ import annotations

import asyncio
import importlib
import types
from contextvars import ContextVar
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

import pytest

from orchcore.observability.telemetry import OrchcoreTelemetry

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
        self._current_span: ContextVar[_FakeSpan | None] = ContextVar(
            "fake_current_span",
            default=None,
        )
        self.provider: _FakeTracerProvider | None = None
        self.tracer = _FakeTracer(self)

    @property
    def current_span(self) -> _FakeSpan | None:
        return self._current_span.get()

    @current_span.setter
    def current_span(self, span: _FakeSpan | None) -> None:
        self._current_span.set(span)

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
    ) -> AbstractContextManager[_FakeSpan]:
        span = _FakeSpan(name, attributes)
        self.current_spans.append(span)
        trace_module = self._trace_module

        class _ContextManager:
            def __enter__(self_nonlocal) -> _FakeSpan:
                self_nonlocal._token = trace_module._current_span.set(span)
                return span

            def __exit__(
                self_nonlocal,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: object,
            ) -> Literal[False]:
                del exc_type, exc, tb
                trace_module._current_span.reset(self_nonlocal._token)
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
    exporter_module.OTLPSpanExporter = _FakeOTLPSpanExporter  # type: ignore[attr-defined]  # fake module API for importlib test.
    export_module = types.ModuleType("opentelemetry.sdk.trace.export")
    export_module.BatchSpanProcessor = _FakeBatchSpanProcessor  # type: ignore[attr-defined]  # fake module API for importlib test.
    resource_module = types.ModuleType("opentelemetry.sdk.resources")
    resource_module.Resource = _FakeResource  # type: ignore[attr-defined]  # fake module API for importlib test.
    sdk_trace_module = types.ModuleType("opentelemetry.sdk.trace")
    sdk_trace_module.TracerProvider = _FakeTracerProvider  # type: ignore[attr-defined]  # fake module API for importlib test.

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

    # Act
    with telemetry.phase_span("implementation", agent="worker-g") as phase_span:
        telemetry.record_cost("worker-g", Decimal("2.50"))

    # Assert
    assert telemetry._enabled
    assert trace_module.provider is not None
    assert trace_module.provider.resource == {"service.name": "orchcore-tests"}
    assert len(trace_module.provider.processors) == 1
    assert phase_span is not None
    phase_span = cast("_FakeSpan", phase_span)
    assert phase_span.attributes == {
        "orchcore.phase": "implementation",
        "orchcore.agent": "worker-g",
        "orchcore.cost.worker-g": 2.5,
    }


def test_record_cost_accumulates_within_pipeline_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_opentelemetry(monkeypatch, with_exporter=True)
    telemetry = OrchcoreTelemetry(enabled=True)

    with telemetry.pipeline_span("delivery", "task-123") as span:
        telemetry.record_cost("alpha", Decimal("1.25"))
        assert span is not None
        span = cast("_FakeSpan", span)
        assert span.attributes["orchcore.cost.total"] == 1.25

        telemetry.record_cost("beta", Decimal("2.75"))
        assert span.attributes["orchcore.cost.total"] == 4.0


def test_record_cost_resets_between_pipeline_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_opentelemetry(monkeypatch, with_exporter=True)
    telemetry = OrchcoreTelemetry(enabled=True)

    with telemetry.pipeline_span("first", "task-1") as first_span:
        telemetry.record_cost("alpha", Decimal("2.00"))

    with telemetry.pipeline_span("second", "task-2") as second_span:
        telemetry.record_cost("alpha", Decimal("3.00"))

    assert first_span is not None
    assert second_span is not None
    first_span = cast("_FakeSpan", first_span)
    second_span = cast("_FakeSpan", second_span)
    assert first_span.attributes["orchcore.cost.total"] == 2.0
    assert second_span.attributes["orchcore.cost.total"] == 3.0


@pytest.mark.asyncio
async def test_record_cost_isolates_concurrent_pipeline_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_opentelemetry(monkeypatch, with_exporter=True)
    telemetry = OrchcoreTelemetry(enabled=True)
    first_ready = asyncio.Event()
    second_ready = asyncio.Event()
    release = asyncio.Event()

    async def record_pipeline(
        name: str,
        first_cost: Decimal,
        second_cost: Decimal,
        ready: asyncio.Event,
    ) -> float:
        with telemetry.pipeline_span(name, f"{name}-task") as span:
            assert span is not None
            span = cast("_FakeSpan", span)
            telemetry.record_cost(f"{name}-first", first_cost)
            ready.set()
            await release.wait()
            telemetry.record_cost(f"{name}-second", second_cost)
            total = span.attributes["orchcore.cost.total"]
            assert isinstance(total, float)
            return total

    first_task = asyncio.create_task(
        record_pipeline("first", Decimal("1.00"), Decimal("2.00"), first_ready)
    )
    second_task = asyncio.create_task(
        record_pipeline("second", Decimal("10.00"), Decimal("20.00"), second_ready)
    )
    await first_ready.wait()
    await second_ready.wait()
    release.set()

    assert await first_task == 3.0
    assert await second_task == 30.0


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
