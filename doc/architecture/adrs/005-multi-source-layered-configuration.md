---
id: ADR-005
title: Use multi-source layered configuration with TOML
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [configuration, toml, pydantic-settings, profiles, extensibility]
related_decisions: [ADR-001, ADR-006, ADR-007]
supersedes: []
superseded_by: []
---

# ADR-005: Use multi-source layered configuration with TOML

## Status

ACCEPTED

## Context and Problem Statement

orchcore needs a configuration system that satisfies multiple competing requirements. It must provide sensible zero-config defaults so that consuming projects work out of the box. It must support multiple configuration sources (TOML files, environment variables, CLI flags, .env files) with a clear priority chain. It must support named profiles (e.g., "fast" vs. "deep") that adjust multiple settings simultaneously. It must support per-agent overrides (e.g., a longer timeout for Claude than Codex). And it must be extensible — consuming projects need to add domain-specific fields without forking the configuration system.

In the four source systems:
- **Planora** (Python): Uses pydantic-settings with env vars and a custom TOML loader. Domain fields (churn_threshold, min_reviewers) are mixed with infrastructure fields (concurrency, timeout).
- **Articles** (Bash): Uses env vars with defaults (`CONCURRENCY=${CONCURRENCY:-4}`). No file-based config.
- **Finvault** (Bash): Same env var approach, with a sourced config file for overrides.
- **Raven** (Bash): Env vars plus a config file parsed with `grep` and `cut`. Has a primitive profile system (fast vs. thorough modes via a flag).

The common pattern is clear: base defaults + env var overrides + optional file config + CLI flags. But each system implements it ad hoc. orchcore needs a principled, type-safe, extensible configuration system.

### Business Context

- Zero-config must work: `pip install orchcore` followed by immediate use with all defaults
- Enterprise users may need org-wide configuration (`~/.config/orchcore/config.toml`)
- CI/CD environments need env var configuration (no interactive file editing)
- Consuming projects have domain-specific settings that should coexist with orchcore's base settings
- TOML is the Python ecosystem standard for project configuration (pyproject.toml)

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Zero-config defaults | Critical | orchcore must work immediately after installation, no setup required |
| Multi-source priority chain | Critical | Different environments (dev, CI, prod) need different config mechanisms |
| Extensibility for consuming projects | Critical | Planora, Articles, etc. each have domain-specific fields |
| Named profiles | High | "fast" vs. "deep" modes adjust 5+ settings simultaneously |
| Type-safe validation | High | Invalid config values (negative timeout, unknown agent) should fail fast with clear errors |
| Per-agent overrides | High | Different agents need different timeouts, models, and env vars |
| TOML as primary file format | Medium | Aligns with Python ecosystem conventions (pyproject.toml) |

## Considered Options

### Option 1: pydantic-settings with native TOML source and subclass extensibility (CHOSEN)

**Overview:** Use pydantic-settings >= 2.7 as the configuration framework. Define `BaseSettings` with orchcore's common fields and TOML as the primary file source. Consuming projects extend by subclassing. Named profiles are TOML sections merged on top of base config.

**Priority chain (highest to lowest):**
1. CLI flags (passed as constructor kwargs or `_cli_settings_source`)
2. Environment variables (`ORCHCORE_` prefix)
3. `.env` files (dotenv source)
4. Project TOML (`orchcore.toml`)
5. User TOML (`~/.config/orchcore/config.toml`)
6. `pyproject.toml` `[tool.orchcore]` section
7. Built-in field defaults

**Pros:**
- pydantic-settings provides multi-source resolution with configurable priority out of the box
- Native TOML support (since pydantic-settings 2.7) — no custom loader needed
- Type validation via Pydantic: invalid values produce clear error messages
- Subclass extensibility: `class PlanoraSettings(orchcore.BaseSettings)` inherits all base fields and adds domain fields
- Named profiles via TOML sections: `[profiles.fast]` merged programmatically
- Per-agent overrides via nested dicts: `agents: dict[str, dict[str, Any]]`
- Environment variable support with configurable prefix (`ORCHCORE_`)
- `.env` file support for local development
- `pyproject.toml` integration aligns with Python packaging conventions

**Cons:**
- pydantic-settings is an additional dependency (beyond pydantic itself)
- Profile merging requires custom logic on top of pydantic-settings (not built-in)
- Per-agent overrides as `dict[str, dict[str, Any]]` lose some type safety inside the nested dict
- Multiple TOML file sources (project, user, pyproject.toml) could cause confusion about which file is active

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | pydantic-settings is mature and well-documented |
| Schedule | Low | Configuration system is straightforward to implement |
| Ecosystem | Low | pydantic-settings is the standard companion to pydantic; actively maintained |

**Trade-offs:**
- We gain type-safe, multi-source configuration with subclass extensibility and TOML support, accepting pydantic-settings as a dependency and the need for custom profile merging logic

---

### Option 2: Custom configuration loader (no framework)

**Overview:** Build a custom configuration system from scratch using `tomllib` (stdlib) for TOML parsing, `os.environ` for env vars, and manual priority chain resolution.

**Pros:**
- No additional dependencies beyond stdlib
- Full control over priority chain, merging, and validation logic
- Simpler mental model — no framework abstractions to learn

**Cons:**
- Significant implementation effort for multi-source resolution, type coercion, and validation
- No automatic env var binding (must manually map `ORCHCORE_X` to settings fields)
- No `.env` file support without additional code
- No automatic type validation — must implement manually or use raw pydantic
- Subclass extensibility requires custom metaclass or registry pattern
- Bug-prone: custom config systems are a common source of subtle bugs

**Why not chosen:**
- Reimplementing what pydantic-settings provides out of the box is wasted effort. The multi-source resolution, type coercion, env var binding, and validation logic in pydantic-settings is well-tested and covers orchcore's requirements completely. Building this from scratch would take 2-3 weeks and produce an inferior result.

---

### Option 3: dynaconf

**Overview:** Use the dynaconf library for layered configuration with environment-aware settings, TOML/YAML/JSON support, and `.env` file loading.

**Pros:**
- Mature, feature-rich configuration library
- Built-in support for environments (development, production, testing)
- Redis/vault backends for distributed config
- Merging logic built-in

**Cons:**
- Does not integrate with Pydantic's type system — configuration values are dynamically typed
- Large dependency surface (supports many backends we don't need)
- No subclass extensibility pattern — consuming projects would need a different extension mechanism
- Learning curve for dynaconf-specific patterns (settings objects, environments, loaders)
- Redundant: pydantic-settings already covers orchcore's requirements with better type integration

**Why not chosen:**
- orchcore already depends on pydantic for data models. pydantic-settings provides native integration (same type system, same validation, subclass extensibility), making dynaconf redundant. dynaconf's dynamic typing contradicts orchcore's commitment to mypy strict mode.

---

### Option 4: YAML instead of TOML

**Overview:** Use YAML as the primary configuration file format instead of TOML.

**Pros:**
- More expressive (anchors, references, multi-line strings)
- Widely used in DevOps tooling

**Cons:**
- Requires PyYAML dependency (TOML has `tomllib` in stdlib since Python 3.11)
- YAML's implicit type coercion causes bugs: `NO` becomes `False`, `3.10` becomes `3.1`
- TOML is the Python ecosystem standard (pyproject.toml, Ruff, Black, etc.)
- pydantic-settings has native TOML source; YAML requires custom implementation

**Why not chosen:**
- TOML is in stdlib, is the Python convention, has native pydantic-settings support, and avoids YAML's implicit type coercion. Every Python developer already encounters TOML via pyproject.toml.

## Decision

**We have decided to use pydantic-settings >= 2.7 with native TOML source as the configuration framework, with a seven-level priority chain, named profile support via TOML sections, and subclass extensibility for consuming projects.**

### Implementation Details

- `orchcore.config.BaseSettings` extends `pydantic_settings.BaseSettings` with fields for all common orchestration settings (concurrency, timeouts, workspace_dir, reports_dir, max_retries, max_wait, log_level)
- TOML sources: `orchcore.toml` (project-level), `~/.config/orchcore/config.toml` (user-level), `pyproject.toml [tool.orchcore]` (project metadata)
- Environment variables prefixed with `ORCHCORE_`: e.g., `ORCHCORE_CONCURRENCY=8`
- Named profiles implemented as a post-init step: if `profile` is set, read `[profiles.{name}]` from TOML and merge values on top of resolved settings
- Per-agent overrides in TOML:
  ```toml
  [agents.claude]
  model = "claude-sonnet-4-20250514"
  stall_timeout = 400

  [agents.codex]
  model = "o3"
  stall_timeout = 200
  ```
- Consuming projects extend:
  ```python
  class PlanoraSettings(orchcore.config.BaseSettings):
      churn_threshold: float = 0.7
      min_reviewers: int = 2
      model_config = SettingsConfigDict(env_prefix="PLANORA_")
  ```

### When to Revisit This Decision

- If pydantic-settings drops TOML support or makes breaking changes
- If consuming projects need dynamic configuration reloading (current design is load-once)
- If a distributed configuration backend (Consul, etcd) is needed for multi-machine orchestration
- If the number of configuration fields exceeds 50 (consider splitting into sub-settings classes)

## Consequences

### Positive

- Zero-config defaults work immediately — no configuration file required
- Type-safe validation catches errors at startup with clear messages
- Seven-level priority chain covers all deployment scenarios (local dev, CI, enterprise)
- Subclass extensibility lets consuming projects add domain fields without forking
- TOML format aligns with Python ecosystem conventions
- Named profiles simplify switching between "fast" and "deep" modes
- Per-agent overrides enable fine-grained agent configuration without code changes

### Negative

- pydantic-settings is an additional dependency (beyond pydantic core)
- Profile merging is custom logic on top of pydantic-settings (not a built-in feature)
- Multiple TOML file sources could confuse users about config precedence (mitigated by clear documentation and a `--show-config` debug flag)
- Per-agent overrides use `dict[str, dict[str, Any]]` which loses type safety inside the nested dict

### Neutral

- Configuration lives in standard locations (`orchcore.toml`, `~/.config/orchcore/`, `pyproject.toml`) that Python developers expect
- Environment variables with `ORCHCORE_` prefix follow established conventions (e.g., `DJANGO_`, `FLASK_`)

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Zero-config startup | orchcore works with no config file present | Integration test with no config files |
| Priority chain correctness | CLI flags override env vars override TOML override defaults | Unit test with all 7 sources providing conflicting values |
| Profile merging | `--profile fast` correctly overrides base settings | Unit test with profile TOML and assertions on merged values |
| Extension works | PlanoraSettings subclass inherits base fields and adds domain fields | Unit test creating and validating a subclass |
| Invalid config rejected | Negative timeout, unknown profile produce clear Pydantic errors | Unit test with invalid inputs |

**Review Schedule:**
- Quarterly: Review configuration-related issues and confusion
- Annually: Reassess pydantic-settings vs. alternatives

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — configuration is a core component
- **ADR-006:** [Use Pydantic for all data models](./006-pydantic-for-all-data-models.md) — settings use Pydantic validation
- **ADR-007:** [Use registry pattern for agents](./007-registry-pattern-for-agent-management.md) — per-agent overrides come from config

## References

- [pydantic-settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [pydantic-settings TOML source](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#toml)
- [TOML specification](https://toml.io/)
- [tomllib (Python stdlib)](https://docs.python.org/3/library/tomllib.html)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
