# Software Design Document

**Project:** orchcore | **Author:** Abdelaziz Abdelrasol | **Date:** 2026-03-25 | **Status:** DRAFT

---

## TL;DR

orchcore extracts the common orchestration infrastructure from four production AI agent orchestration systems (Planora, Articles, Finvault, Raven/Ralph) into a standalone, reusable Python package. It provides 12 components — agent registry, subprocess runner, stream processing pipeline, phase engine, rate-limit recovery, workspace management, configuration, prompt templating, logging, UI protocol, signal handling, and observability — unified by a `UICallback` protocol that decouples the engine from presentation. The extraction eliminates 60-70% code duplication across the four systems.

## Problem Statement

### Current Situation

- Four production orchestration systems exist — Planora (Python), Articles (Bash), Finvault (Bash), and Raven/Ralph (Go) — each independently implementing the same infrastructure: subprocess launching, JSONL stream parsing, rate-limit detection, workspace management, and configuration handling.
- An estimated 60-70% of the orchestration code across these four systems is functionally identical infrastructure.
- Bug fixes in one system must be manually ported to the other three.
- Each new agent CLI (Gemini, Copilot) requires N implementations across N systems.

### Opportunity

- A shared package eliminates duplication — improvements benefit all consumers immediately.
- Dramatically lowers the barrier to building new AI agent orchestration systems.
- The Python ecosystem lacks a purpose-built library for orchestrating CLI-based AI coding agents (existing tools like LangChain operate at the API level, not the subprocess level).

## Goals

- Provide a pip-installable Python package with 12 documented, tested components
- Zero code changes to add new agent CLI support — TOML configuration only
- Sequential and parallel phase execution with configurable partial failure semantics
- Process JSONL from 5 agent CLI formats into a unified `StreamEvent` model
- Automatic rate-limit recovery with timezone-aware reset parsing and exponential backoff
- mypy strict mode with zero errors
- Each component usable independently (no all-or-nothing dependency)

## Non-Goals

- orchcore is **not** an AI agent — it does not make API calls or generate code
- orchcore does **not** include a TUI framework — it provides hooks that a TUI implements
- orchcore does **not** handle API keys — agent CLIs manage their own credentials
- Windows is **not** a first-class platform in v1.0

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Code duplication across systems | ~60-70% | < 5% |
| Time to add new agent | 2-4 hours × 4 systems | < 30 minutes (one TOML entry) |
| Test coverage | Varies (20-80%) | > 90% |
| mypy strict compliance | Partial | 100% (zero errors) |

## Requirements

### Functional Requirements

| Component | Key Requirements |
|-----------|-----------------|
| **Agent Registry** | Built-in configs for 5 agents; custom agents via TOML; mode-specific flags (PLAN, FIX, AUDIT, REVIEW) |
| **Subprocess Runner** | Async launch with stream capture; concurrency via Semaphore; structured `AgentResult` |
| **Stream Processing** | Pre-parse filtering (~95% noise reduction); 5-format parsing; 9-state machine; stall detection |
| **Tool Assignment** | Per-phase `ToolSet`; per-agent overrides; layered resolution order; permission levels |
| **Pipeline Engine** | Sequential/parallel phases; dependency ordering; resume, skip, only-phase options |
| **Recovery** | Regex rate-limit detection; timezone-aware reset parsing; exponential backoff; git dirty-tree recovery |
| **Workspace** | Active directories; timestamped archives; gzip compression; "latest" symlink |
| **Configuration** | 7-level priority chain; named profiles; per-agent overrides; extensible via subclassing |
| **Prompt Templating** | Jinja2 rendering; frontmatter stripping; configurable template directories |
| **Signal Handling** | SIGINT/SIGTERM trap; cooperative `shutdown_requested` flag; PhaseRunner owns subprocess cleanup and 30s grace period |
| **UI Protocol** | 15 callback methods; NullCallback and LoggingCallback built-in |

### Non-Functional Requirements

| Category | Target |
|----------|--------|
| Performance | < 5ms per event, < 100ms subprocess launch |
| Memory | < 50MB per concurrent agent (line-by-line streaming) |
| Extensibility | New agent via TOML only |
| Composability | Each component usable standalone |
| Type Safety | mypy strict, zero errors |
| Testing | > 90% line coverage |

## Key Interfaces

### UICallback Protocol

```python
class UICallback(Protocol):
    def on_pipeline_start(self, phases: Sequence[Phase]) -> None: ...
    def on_pipeline_complete(self, result: PipelineResult) -> None: ...
    def on_phase_start(self, phase: Phase) -> None: ...
    def on_phase_end(self, phase: Phase, result: PhaseResult) -> None: ...
    def on_phase_skip(self, phase: Phase, reason: str) -> None: ...
    def on_agent_start(self, agent_name: str, phase: str) -> None: ...
    def on_agent_event(self, event: StreamEvent) -> None: ...
    def on_agent_complete(self, agent_name: str, result: AgentResult) -> None: ...
    def on_agent_error(self, agent_name: str, error: str) -> None: ...
    def on_stall_detected(self, agent_name: str, duration: float) -> None: ...
    def on_rate_limit(self, agent_name: str, message: str) -> None: ...
    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None: ...
    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None: ...
    def on_git_recovery(self, action: str, detail: str) -> None: ...
    def on_shutdown(self, reason: str) -> None: ...
```

### AgentRunner

```python
class AgentRunner:
    async def run(
        self,
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        mode: AgentMode = AgentMode.PLAN,
        dry_run: bool = False,
        on_event: Callable[[StreamEvent], None] | None = None,
        on_snapshot: Callable[[AgentMonitorSnapshot], None] | None = None,
        snapshot_interval: float | None = None,
        stall_check_interval: float = 5.0,
        on_process_start: Callable[[asyncio.subprocess.Process], None] | None = None,
        on_process_end: Callable[[asyncio.subprocess.Process], None] | None = None,
        toolset: ToolSet | None = None,
        on_stall: Callable[[str, float], None] | None = None,
    ) -> AgentResult: ...
```

### PipelineRunner

```python
class PipelineRunner:
    async def run_pipeline(
        self,
        phases: list[Phase],
        prompts: dict[str, str],
        ui_callback: UICallback,
        mode: AgentMode | None = None,
        resume_from: str | None = None,
    ) -> PipelineResult: ...
```

## Alternatives Considered

| Alternative | Why Not |
|-------------|---------|
| LangChain / CrewAI | Operate at the API level, not CLI subprocess level |
| Shared Bash library | Not feasible for complex async orchestration |
| Monorepo with shared code | Tight coupling, hard to version independently |
| Per-system copy-paste | Current state — 60-70% duplication, maintenance burden |

## Related

- [Architecture Overview](overview.md) — package layout and component diagrams
- [Architecture Decision Records](adrs/) — all 9 ADRs
- [Configuration Reference](../reference/configuration.md)
- [Stream Events Reference](../reference/stream-events.md)
