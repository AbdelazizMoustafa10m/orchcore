"""Optional OpenTelemetry integration with graceful degradation."""

from __future__ import annotations

import importlib
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from decimal import Decimal

    from orchcore.stream.events import ToolExecution

logger: logging.Logger = logging.getLogger(__name__)


class OrchcoreTelemetry:
    """Optional OpenTelemetry integration with graceful degradation.

    If the opentelemetry packages are not installed, all methods
    silently become no-ops.
    """

    def __init__(
        self,
        enabled: bool = False,
        service_name: str = "orchcore",
        otlp_endpoint: str | None = None,
        otlp_protocol: str = "grpc",
    ) -> None:
        self._enabled = enabled
        self._trace_api: types.ModuleType | None = None
        self._tracer: Any | None = None

        if not self._enabled:
            return

        try:
            self._trace_api = importlib.import_module("opentelemetry.trace")
            resource_module = importlib.import_module("opentelemetry.sdk.resources")
            sdk_trace_module = importlib.import_module("opentelemetry.sdk.trace")

            resource = resource_module.Resource.create({"service.name": service_name})
            provider = sdk_trace_module.TracerProvider(resource=resource)

            if otlp_endpoint:
                try:
                    exporter_path = (
                        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
                        if otlp_protocol == "grpc"
                        else "opentelemetry.exporter.otlp.proto.http.trace_exporter"
                    )
                    exporter_module = importlib.import_module(exporter_path)
                    export_module = importlib.import_module("opentelemetry.sdk.trace.export")
                    exporter = exporter_module.OTLPSpanExporter(endpoint=otlp_endpoint)
                    processor = export_module.BatchSpanProcessor(exporter)
                    provider.add_span_processor(processor)
                except ImportError:
                    logger.warning(
                        "OTLP exporter packages not installed. "
                        "Telemetry will run without exporting spans."
                    )

            self._trace_api.set_tracer_provider(provider)
            self._tracer = self._trace_api.get_tracer("orchcore")
        except ImportError:
            logger.debug("OpenTelemetry packages not installed. Telemetry disabled.")
            self._enabled = False
            self._trace_api = None
            self._tracer = None

    @contextmanager
    def _span(
        self,
        span_name: str,
        attributes: dict[str, str],
    ) -> Iterator[object | None]:
        """Start a span when telemetry is active, or yield None when disabled.

        Centralises the disabled-check/start-span scaffolding shared by
        pipeline_span, phase_span, agent_span, and tool_span.
        """
        if not self._enabled or self._tracer is None:
            yield None
            return

        with self._tracer.start_as_current_span(span_name, attributes=attributes) as span:
            yield span

    @contextmanager
    def pipeline_span(
        self,
        pipeline_name: str,
        task_slug: str,
    ) -> Iterator[object | None]:
        """Create a root span for the entire orchestration pipeline."""
        with self._span(
            "orchcore.pipeline",
            {
                "orchcore.pipeline": pipeline_name,
                "orchcore.task_slug": task_slug,
            },
        ) as span:
            yield span

    @contextmanager
    def phase_span(
        self,
        phase: str,
        agent: str | None = None,
    ) -> Iterator[object | None]:
        """Create a workflow phase span or yield `None` when telemetry is disabled."""
        with self._span(
            f"orchcore.phase.{phase}",
            {
                "orchcore.phase": phase,
                "orchcore.agent": agent or "",
            },
        ) as span:
            yield span

    @contextmanager
    def agent_span(self, phase: str, agent: str) -> Iterator[object | None]:
        """Create an agent-level span within a phase."""
        with self._span(
            f"orchcore.agent.{agent}",
            {
                "orchcore.phase": phase,
                "orchcore.agent": agent,
            },
        ) as span:
            yield span

    @contextmanager
    def tool_span(self, agent: str, tool: ToolExecution) -> Iterator[object | None]:
        """Create a child span for a tool invocation."""
        with self._span(
            f"orchcore.tool.{tool.name}",
            {
                "orchcore.agent": agent,
                "orchcore.tool.name": tool.name,
                "orchcore.tool.detail": tool.detail or "",
                "orchcore.tool.id": tool.tool_id,
            },
        ) as span:
            yield span

    def record_cost(self, agent: str, cost_usd: Decimal) -> None:
        """Record cost information on the current span if telemetry is active."""
        if not self._enabled or self._trace_api is None:
            return

        try:
            span = self._trace_api.get_current_span()
            if span is None or not getattr(span, "is_recording", lambda: True)():
                return
            span.set_attribute("orchcore.agent", agent)
            span.set_attribute(f"orchcore.cost.{agent}", float(cost_usd))
            span.set_attribute("orchcore.cost.total", float(cost_usd))
        except (AttributeError, RuntimeError, ValueError) as exc:
            # AttributeError: span type doesn't expose set_attribute (non-recording span).
            # RuntimeError: span implementation rejects the mutation (e.g. already ended).
            # ValueError: OTel rejects an unsupported attribute value type.
            logger.debug(
                "Failed to record telemetry cost for agent %s: %s",
                agent,
                exc,
                exc_info=True,
            )
