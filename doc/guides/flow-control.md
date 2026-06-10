# Flow Control

`FlowControl` lets an embedding application (for example a TUI) pause, resume,
and skip phases of a running pipeline without cancelling it.

## Usage

Construct a `FlowControl` and hand it to the `PipelineRunner`:

```python
from orchcore.pipeline import FlowControl, PipelineRunner

flow_control = FlowControl()
pipeline_runner = PipelineRunner(
    phase_runner=phase_runner,
    flow_control=flow_control,
)
```

From a UI callback or another task on the same event loop:

```python
flow_control.pause()         # pipeline stalls at the next phase boundary
flow_control.resume()        # released; the next phase starts
flow_control.request_skip()  # the next phase that would run is skipped instead
```

## Semantics

- **Phase-boundary checkpoints.** Pause and skip take effect *between*
  phases, never mid-phase: a phase that has started always runs to
  completion. The checkpoint sits after the `only_phase`/resume/`skip_phases`
  filters, so a pending skip applies to the next phase that would actually
  execute.
- **Skip is one-shot.** `request_skip()` skips exactly one phase and the flag
  clears automatically; the result records the phase as `SKIPPED` with reason
  `"Skipped via FlowControl"`.
- **Success semantics.** A FlowControl skip counts as a *user-requested* skip
  ([ADR-010](../architecture/adrs/010-topological-phase-ordering-and-success-semantics.md)):
  it does not fail the pipeline by itself. Skipping a required phase,
  however, leaves its dependents dependency-blocked — and a dependency-blocked
  *required* phase fails the pipeline and stops execution.
- **Async-only safety.** `pause()`/`resume()` mutate an `asyncio.Event` and
  must be called from within the running event loop (from a coroutine or a
  callback scheduled on the loop). From worker threads, hand off with
  `loop.call_soon_threadsafe`.

## Typed agent overrides

Related runtime-tweaking surface: `AgentRegistry.with_overrides` accepts the
typed `AgentOverrideConfig` (from `orchcore.config`) as well as plain dicts:

```python
from orchcore.config import AgentOverrideConfig

patched = registry.with_overrides(
    {"claude": AgentOverrideConfig(model="claude-opus-4-8", env={"X": "1"})}
)
```

The schema field `env` maps to `AgentConfig.env_vars` and merges with the
agent's existing `env_vars` (override wins per key).

## Related

- [ADR-010 — Topological ordering & success semantics](../architecture/adrs/010-topological-phase-ordering-and-success-semantics.md)
- [Writing a UICallback](writing-a-uicallback.md)
