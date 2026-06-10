"""Property-based tests for WP-20 topological phase ordering."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from orchcore.pipeline.phase import Phase
from orchcore.pipeline.pipeline import _topological_phases


def _build_phase(name: str, depends_on: list[str]) -> Phase:
    return Phase(name=name, agents=[f"{name}-agent"], depends_on=depends_on)


@st.composite
def _random_dag_phases(draw: st.DrawFn) -> list[Phase]:
    """A random DAG declared in a random (possibly ill-sorted) order.

    Edges only point from higher to lower creation index, which guarantees
    acyclicity; the random permutation then scrambles declaration order so
    the sorter has real work to do.
    """
    size = draw(st.integers(min_value=1, max_value=8))
    names = [f"phase-{index}" for index in range(size)]
    dependencies: dict[str, list[str]] = {}
    for index, name in enumerate(names):
        candidates = names[:index]
        chosen = draw(
            st.lists(st.sampled_from(candidates), unique=True, max_size=3)
            if candidates
            else st.just([])
        )
        dependencies[name] = chosen
    declaration_order = draw(st.permutations(names))
    return [_build_phase(name, dependencies[name]) for name in declaration_order]


def _reference_order(phases: list[Phase]) -> list[str]:
    """Independent greedy reference: repeatedly emit the earliest-declared
    phase whose dependencies have all been emitted."""
    emitted: list[str] = []
    remaining = list(phases)
    while remaining:
        for index, phase in enumerate(remaining):
            if all(dependency in emitted for dependency in phase.depends_on):
                emitted.append(phase.name)
                del remaining[index]
                break
        else:  # pragma: no cover - unreachable for a valid DAG
            raise AssertionError("cycle in generated DAG")
    return emitted


@given(phases=_random_dag_phases())
def test_topological_phases_is_valid_and_declaration_stable(phases: list[Phase]) -> None:
    ordered = _topological_phases(phases)

    # Same phases, no duplicates or drops.
    assert sorted(phase.name for phase in ordered) == sorted(phase.name for phase in phases)

    # Valid topological order: every dependency precedes its dependent.
    position = {phase.name: index for index, phase in enumerate(ordered)}
    for phase in phases:
        for dependency in phase.depends_on:
            assert position[dependency] < position[phase.name]

    # Deterministic declaration stability: at every step the earliest-declared
    # runnable phase runs first (matches the greedy reference).
    assert [phase.name for phase in ordered] == _reference_order(phases)


@given(phases=_random_dag_phases())
def test_topological_phases_preserves_already_valid_declaration_order(
    phases: list[Phase],
) -> None:
    """A declaration order that is already topologically valid is unchanged."""
    valid_order = _topological_phases(phases)

    assert _topological_phases(valid_order) == valid_order
