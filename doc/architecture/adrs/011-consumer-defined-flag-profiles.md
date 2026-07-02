---
id: ADR-011
title: Consumer-defined flag profiles replace the AgentMode enum
status: ACCEPTED
date: 2026-07-02
decision_makers:
  - Abdelaziz Abdelrasol
consulted: []
informed: []
confidence: HIGH
tags:
  - registry
  - configuration
  - layering
  - api-design
  - breaking-change
related_decisions:
  - ADR-001
  - ADR-007
  - ADR-009
supersedes: []
superseded_by: []
---

# ADR-011: Consumer-defined flag profiles replace the AgentMode enum

## Status

ACCEPTED — shipped in orchcore 2.0.0. Amends ADR-007 and ADR-009.

## Context and Problem Statement

orchcore 1.x shipped a closed `AgentMode` enum (`PLAN`, `FIX`, `AUDIT`, `REVIEW`)
in `orchcore.registry.agent`. `AgentConfig.flags` was keyed by it, TOML
`[agents.<name>.flags]` keys were validated against it (any other key was a
hard load error), and `run_pipeline`/`AgentRunner.run` silently defaulted to
`AgentMode.PLAN` when no mode was passed.

The enum's only runtime semantics was a dictionary lookup: select a named
bundle of CLI flags (`agent.flags.get(mode, ())`), applied only when no
`ToolSet` resolved for the invocation (ADR-009). orchcore implements **no
behavior** per mode value.

The vocabulary itself leaked in from the source systems during extraction
(ADR-001). Planora's original enum had two members (`PLAN`, `FIX`); orchcore
grew it to four to absorb Finvault's audit and Raven's review workflows. That
growth pattern is the defining symptom of an open, consumer-owned vocabulary
trapped in a closed infrastructure type: the next consumer with a `research`
or `draft` phase must either edit orchcore or fail at TOML load time.

This contradicts orchcore's own stated principles:

- "Built-in agents are NOT hardcoded — consuming projects register their own
  agents" (`AgentRegistry` docstring); "No hardcoded agent policy in core"
  (ADR-007). Plan/fix/audit/review *is* workflow policy.
- ADR-009 asserted "agent modes are an intrinsic property of the agent."
  This premise was wrong: what is intrinsic to a CLI is which flags *exist*
  (`--think`, `--sandbox`). *When* to use them — "this phase is planning
  work" — is workflow policy owned by the consumer. **This ADR corrects that
  premise.**

A secondary incoherence: because `ToolSet` *replaced* mode flags instead of
composing with them, a phase that set `tools` silently dropped behavioral
flags like `--think` — access control and behavior selection were entangled
in one either/or mechanism.

### Design rule

The codebase already demonstrates the correct criterion in both directions:

- `StreamFormat` (claude/codex/…) is rightly a closed enum — orchcore ships
  a parser implementation per value.
- `ToolSet.permission` values are rightly infra vocabulary — the runner
  implements a per-CLI translation for each value.

**A closed enum is justified only when the library implements distinct
behavior per value; when values merely select user-supplied data, the keys
must be user-defined.** `AgentMode` failed this test.

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Layering: workflow vocabulary belongs to the domain layer | Critical | orchcore is infrastructure; consumers (Planora, Articles, Finvault, Raven) define what kinds of work exist |
| Extensibility without core edits | Critical | New consumer vocabulary (`research`, `draft`, `art`) must not require an orchcore release; Python enums cannot be extended by consumers |
| No silent domain defaults | High | Infrastructure defaulting to `PLAN` embeds a domain assumption and hides misconfiguration |
| Keep the useful mechanism | High | A per-agent translation table from a role name to that CLI's dialect is genuinely valuable — only the closed key set was wrong |
| Data-format stability | High | Existing `agents.toml` files must keep parsing (keys were already plain TOML strings) |
| Behavioral flags must compose with ToolSet | Medium | `--think` should not vanish because a phase declares tool access |
| Typo visibility | Medium | Dropping enum validation must not turn misspelled names into silent no-ops |

## Considered Options

### Option 1: Consumer-defined string profile names (CHOSEN)

**Overview:** Delete `AgentMode`. `AgentConfig.flags` becomes
`dict[str, tuple[str, ...]]` — "flag profiles" — with names validated only
against a safety pattern (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, so a name cannot be
mistaken for a flag). Selection is a plain string: `Phase.flag_profile`
(per-phase) overriding a pipeline-wide default (`run_pipeline(flag_profile=)`).
`None` means *no profile flags* — there is no implicit default. Selecting a
profile an agent does not define logs a warning and applies no flags. Profile
flags compose additively with the ToolSet translation (see below).

Consumers that want compile-time safety define their own `StrEnum` in their
own package; `StrEnum` members are `str`, so they pass through the API
unchanged with full type checking on the consumer's side.

**Pros:**
- Vocabulary moves to the layer that owns it; core never changes for a new workflow role
- TOML data format is 100% unchanged — only the Python-side key validation loosens
- Per-phase selection is strictly more expressive than 1.x's pipeline-global mode, and consistent with ADR-009's phase-level philosophy
- Removes the silent `PLAN` default — explicit beats implicit
- Prior art: Cargo profiles, tox environments, Docker Compose profiles, npm scripts — named bundles with user-defined names over an infra lookup mechanism

**Cons:**
- Loses enum exhaustiveness/typo checking in core — mitigated by the name pattern validation, the unknown-profile warning, and consumer-side enums
- Breaking API change (1.0.0 → 2.0.0)

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | The change is a type widening plus parameter renames; runtime semantics is one dict lookup |
| Adoption | Low | No known consumers import orchcore yet; semver major protects unknown users |
| Data migration | None | Existing TOML files parse identically |

---

### Option 2: Keep the closed enum and grow it on demand (status quo)

**Overview:** Add `RESEARCH`, `DRAFT`, … members as consumers need them.

**Why not chosen:** Every new consumer vocabulary requires an orchcore
release; unrelated consumers see each other's vocabulary in the core type;
the enum's growth from 2 to 4 members during extraction already demonstrated
the failure mode. Python enums cannot be extended from outside the defining
module, so there is no incremental fix.

---

### Option 3: Remove `AgentConfig.flags` entirely; phases carry raw argv

**Overview:** Delete the named-bundle mechanism too; each `Phase` lists extra
argv per agent name.

**Why not chosen:** This discards the genuinely useful part — a registry-level
translation table from one role name to each CLI's dialect (`plan` →
`--think` for Claude, `--reasoning high` for Codex). Pipelines would duplicate
CLI details per phase and per project, and the TOML data format would break.
The mechanism was right; only the closed key set was wrong.

---

### Option 4: Runtime-extensible mode registration in core

**Overview:** Keep a mode concept but let consumers register additional mode
names with orchcore at startup (a registry of vocabularies).

**Why not chosen:** Adds mutable global state and ordering concerns
(registration must precede TOML load) to reach the same endpoint as plain
strings, with none of the enum's static guarantees. Complexity without
payoff.

## Decision

**We have decided to delete the `AgentMode` enum and model named flag bundles
as consumer-defined "flag profiles": `AgentConfig.flags: dict[str, tuple[str,
...]]` with pattern-validated names, selected per phase via
`Phase.flag_profile` with a pipeline-level fallback via
`run_pipeline(flag_profile=...)`, no implicit default, a logged warning when a
selected profile is absent from an agent, and additive composition with the
ToolSet translation.**

### Composition with ToolSet (amends ADR-009)

ADR-009 made the ToolSet translation *replace* mode flags (mode flags were the
backward-compatibility fallback for tool restriction). With profiles holding
behavioral flags, replacement is wrong: a phase that declares tool access
would silently lose `--think`. In 2.0.0:

- Profile flags are appended first, the ToolSet translation last.
- Ordering alone is **not** sufficient for safety: clap-based CLIs (Codex)
  hard-fail on duplicated singleton flags (verified: `codex exec -s
  read-only -s workspace-write` and `--json --json` are argument errors,
  not last-wins), and bypass flags such as `--yolo` or
  `--dangerously-skip-permissions` cannot be neutralized by later flags.
  Therefore, when a ToolSet is in effect, profile flags in the
  ToolSet-managed domain (per-format `_TOOLSET_MANAGED_FLAGS`: everything
  the translation can emit plus known permission/approval-bypass flags) are
  **dropped with a warning**. Without a ToolSet, profile flags pass through
  verbatim — full parity with the 1.x mode-flags fallback.
- Ownership guidance: profiles hold *behavioral* flags (thinking, verbosity,
  effort); tool access, permissions, and turn limits belong in ToolSets.
- ToolSet resolution itself is unchanged:
  `Phase.agent_tools[agent] > explicit toolset > Phase.tools > none`.

Malformed profile *selections* (empty or flag-like names) are rejected at
every API boundary — `Phase.flag_profile` (pydantic pattern),
`run_pipeline` (`PipelineValidationError`), `run_phase`/`run_parallel` and
`AgentRunner.run` (`ValueError`) — while selecting a *well-formed but
undefined* profile stays a per-agent warning, because sparse registries
(a profile defined for some agents only) are legitimate, matching 1.x's
`flags.get(mode, ())` semantics but visibly.

### Implementation Details

- `orchcore/registry/agent.py`: `AgentMode` deleted; `flags` re-typed with a
  `field_validator` enforcing the name pattern; field now defaults to `{}`.
- `orchcore/registry/registry.py`: TOML flags keys pass through as strings;
  `AgentConfig` validation reports invalid names per entry (atomic load
  semantics unchanged).
- `orchcore/runner/subprocess.py`: `AgentRunner.run(flag_profile: str | None
  = None)`; `_build_command` appends `_resolve_profile_flags(agent, profile)`
  then the ToolSet translation. Unknown profile → `WARNING` log naming the
  agent, the requested profile, and the available names.
- `orchcore/pipeline/phase.py`: `Phase.flag_profile: str | None = None`.
- `orchcore/pipeline/engine.py`: `run_phase`/`run_parallel` take
  `flag_profile: str | None = None` as the fallback; `Phase.flag_profile`
  wins when set.
- `orchcore/pipeline/pipeline.py`: `run_pipeline(flag_profile: str | None =
  None)`; the former `mode`/silent-`PLAN` behavior is removed.

### Migration (1.x → 2.0.0)

| 1.x | 2.0.0 |
|---|---|
| `from orchcore.registry import AgentMode` | delete; define vocabulary in your project (plain strings or your own `StrEnum`) |
| `flags={AgentMode.PLAN: [...]}` | `flags={"plan": [...]}` |
| `run_pipeline(..., mode=AgentMode.FIX)` | `run_pipeline(..., flag_profile="fix")` or per-phase `Phase(flag_profile="fix")` |
| `run_pipeline(...)` (no mode → silent PLAN) | `run_pipeline(...)` selects **no** profile; pass `flag_profile="plan"` to keep 1.x behavior |
| `AgentRunner.run(..., mode=...)` | `AgentRunner.run(..., flag_profile=...)` |
| Registry TOML `[agents.X.flags]` | unchanged |
| Profiles containing tool-restriction flags as ToolSet fallback | move tool access into `ToolSet`; keep only behavioral flags in profiles (profiles now also apply *alongside* ToolSets) |

### When to Revisit This Decision

- If orchcore ever implements real per-role behavior (not data lookup), that
  specific mechanism may justify a closed enum — following the design rule
  above, as `StreamFormat` and `ToolSet.permission` already do.
- If several consumers converge on a shared vocabulary, publish it as
  documentation (conventional names) or a separate tiny conventions package —
  not as a core type.
- If duplicate-flag conflicts between profiles and ToolSet translations occur
  in practice, consider structured deduplication in `_build_command`.

## Consequences

### Positive

- The infrastructure/domain boundary matches ADR-001's goal: consuming
  projects own all workflow vocabulary; orchcore owns mechanism only
- New consumer vocabularies require zero orchcore changes
- Per-phase profile selection closes a 1.x expressiveness gap (mode was
  pipeline-global while ADR-009 established phase-level variation)
- Behavioral flags survive alongside ToolSets instead of being silently
  dropped
- Misconfiguration is visible: no silent `PLAN` default; unknown profiles warn

### Negative

- Breaking change for any unknown 1.x users (mitigated by semver major and
  the migration table)
- Core no longer statically rejects misspelled profile names at TOML load;
  the check moves to runtime (warning) and to consumer-side enums
- The ToolSet-managed flag list is maintained per stream format and can lag
  new CLI flags; an unlisted bypass flag in a profile would pass through
  (mitigated by ownership guidance and the warning on every drop)

### Neutral

- `AgentMode` was not re-exported at the package root, so the break surface
  is `orchcore.registry` / `orchcore.registry.agent` imports and keyword
  names
- The unknown-profile warning fires per invocation; noisy only under
  persistent misconfiguration

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Consumer-defined names load | Arbitrary valid names accepted from TOML | `test_load_from_toml_accepts_consumer_defined_profile_names` |
| Flag-like names rejected | Names failing the pattern fail the load atomically | `test_load_from_toml_rejects_profile_names_that_look_like_flags` |
| No implicit default | `flag_profile=None` adds no profile flags | `test_build_command_no_profile_selects_no_profile_flags` |
| Unknown profile is visible | WARNING log naming agent, profile, available names | `test_build_command_warns_on_unknown_flag_profile` |
| Additive composition | Behavioral profile flags precede ToolSet translation | `test_build_command_composes_profile_flags_before_toolset_translation` |
| Managed-flag stripping | ToolSet-domain flags dropped from profiles under a ToolSet (incl. bypasses) | `test_build_command_strips_toolset_managed_flags_from_profile`, `test_build_command_strips_bypass_flags_under_toolset` |
| 1.x fallback parity | Without a ToolSet, profile flags pass through verbatim | `test_build_command_profile_flags_untouched_without_toolset` |
| Malformed selection fails fast | Empty/flag-like names rejected at Phase/run_pipeline/run_phase/run boundaries | `test_phase_rejects_malformed_flag_profile_names`, `test_run_pipeline_rejects_malformed_flag_profile`, `test_run_phase_rejects_malformed_flag_profile_fallback`, `test_run_rejects_malformed_flag_profile` |
| Per-phase override | `Phase.flag_profile` beats the pipeline fallback | `test_phase_flag_profile_overrides_fallback_profile` |

**Review Schedule:**
- On first consumer migration (Planora): confirm the consumer-side `StrEnum`
  pattern reads well and the migration table is complete
- Quarterly: check whether duplicate-flag conflicts warrant deduplication

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — this ADR finishes the extraction: the modes were domain residue
- **ADR-007:** [Registry pattern for agent management](./007-registry-pattern-for-agent-management.md) — amended: registry data remains "what the agent supports"; profile *names* are consumer vocabulary
- **ADR-009:** [Tool assignment as phase-level concern](./009-tool-assignment-as-phase-level-concern.md) — amended: corrects the "modes are intrinsic" premise; ToolSet no longer replaces profile flags but composes after them

## References

- [Cargo custom profiles](https://doc.rust-lang.org/cargo/reference/profiles.html#custom-profiles) — user-named config bundles over an infra mechanism
- [tox environments](https://tox.wiki/) / [Docker Compose profiles](https://docs.docker.com/compose/profiles/) — same pattern
- Python `enum` documentation — enums cannot be extended outside the defining module

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-02 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
