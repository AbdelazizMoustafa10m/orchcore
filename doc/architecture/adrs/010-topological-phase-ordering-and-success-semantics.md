---
id: ADR-010
title: Topological phase ordering and explicit success semantics
status: ACCEPTED
date: 2026-06-10
decision_makers:
  - Abdelaziz Abdelrasol
consulted: []
informed: []
confidence: HIGH
tags:
  - pipeline
  - ordering
  - dependencies
  - success-semantics
related_decisions:
  - ADR-008
supersedes: []
superseded_by: []
---

# ADR-010: Topological phase ordering and explicit success semantics

## Status

ACCEPTED

## Context and Problem Statement

The documentation has always promised that `PipelineRunner` executes phases in
"dependency (DAG) order". The implementation, however, iterated the given list
order and silently *skipped* any phase whose `depends_on` had not completed
yet: `[b(depends_on=a), a]` skipped `b`, ran `a`, and — because the success
predicate counted `SKIPPED` as success — reported `success=True` for required
work that never ran (Codex finding F10).

There was also an asymmetry: an *optional* phase that failed flipped
`success` to `False`, while a *required* phase that was dependency-skipped did
not.

## Decision

1. **Topological execution order.** `run_pipeline` orders phases with Kahn's
   algorithm before execution (`_topological_phases`). The ready queue is a
   min-heap keyed on declaration index.

2. **Stability guarantee.** At every step, the earliest-*declared* runnable
   phase runs first. Two corollaries consumers can rely on:
   - a declaration order that is already topologically valid is preserved
     verbatim;
   - the output is deterministic — the lexicographically smallest valid order
     with respect to declaration indices.

   (A small Kahn implementation was chosen over `graphlib.TopologicalSorter`
   because `static_order()` does not guarantee declaration stability;
   deterministic output matters for resume state and UX. Cycle detection
   stays in the pre-existing `_find_dependency_cycle`, which produces
   friendlier cycle paths.)

3. **Explicit success semantics.** With ordering guaranteed, "dependencies
   not met" can only mean the dependency itself failed or was skipped. A
   **required** phase with unmet dependencies is recorded as `SKIPPED` with
   the existing reason, **fails the pipeline** (`success=False`), and stops
   further execution — matching the required-phase failure rule. Optional
   phases keep skipping freely.

   | Phase kind | Outcome | Pipeline effect |
   |---|---|---|
   | any | `DONE` / `PARTIAL` | success preserved |
   | any | `FAILED` | `success=False`; stops when required |
   | any | user skip (`skip_phases`, resume, FlowControl) | success preserved |
   | required | dependency-skip | `success=False`, execution stops |
   | optional | dependency-skip | success preserved, execution continues |

   `PipelineResult.success` is therefore: no `FAILED` phase **and** no
   dependency-skipped required phase.

   A new `PhaseStatus.BLOCKED` value was considered and rejected to avoid
   widening the status enum consumers already switch on; the reason string
   (`"Dependencies not met: ..."`) already distinguishes the case.

## Consequences

### Positive

- The documented "dependency order" claim is now true.
- `success=True` means every required phase actually ran (or was explicitly
  skipped by the user).
- Ill-ordered phase lists work instead of silently dropping phases.

### Negative

- **Behavior change** twice over (pre-1.0 window): execution order changes
  for ill-ordered lists, and `success` flips to `False` for pipelines that
  previously "succeeded" with dependency-skipped required phases.
- `only_phase` targeting a phase with dependencies now fails the pipeline
  unless those dependencies are satisfied from resume state — previously it
  reported success without running anything.

### Neutral

- Resume state files are unaffected: they store completed phase *names* only,
  and resume interacts with the *ordered* sequence the runner executes.

## Validation

- `tests/test_pipeline/test_pipeline.py` — reordering, F10 regression
  (`test_required_phase_dep_skip_fails_pipeline`), optional dep-skip,
  resume-satisfies-dependencies.
- `tests/test_pipeline/test_pipeline_ordering_hypothesis.py` — property
  tests: random DAGs produce a valid, declaration-stable order.

## Related Decisions

- ADR-008 — failure modes and retry semantics at the phase level.

## Document History

| Date | Change |
|------|--------|
| 2026-06-10 | Initial decision (WP-20). |
