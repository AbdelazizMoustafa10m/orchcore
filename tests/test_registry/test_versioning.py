"""Tests for the agent CLI version-compatibility engine (WP-26)."""

from __future__ import annotations

import pytest

from orchcore.registry.versioning import (
    VERSION_OUTPUT_RE,
    CompatibilityStatus,
    IncompatibleVersionSpec,
    VersionCondition,
    VersionSpecifier,
    VersionSpecifierError,
    VersionSpecifierPart,
    evaluate_compatibility,
    version_key,
)

# ---- version_key ----


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.1.112", (2, 1, 112)),
        ("0.2", (0, 2)),
        ("2.1.112-beta.1", (2, 1, 112, 1)),
        ("1.0.0rc1", (1, 0, 0)),
        ("3", (3,)),
        ("weird", (0,)),
    ],
)
def test_version_key(version: str, expected: tuple[int, ...]) -> None:
    assert version_key(version) == expected


# ---- VersionSpecifierPart.parse ----


@pytest.mark.parametrize(
    ("text", "condition", "version", "wildcard"),
    [
        (">=2.1.112", VersionCondition.GE, "2.1.112", False),
        ("<= 3.0", VersionCondition.LE, "3.0", False),
        ("==2.1.*", VersionCondition.EQ, "2.1", True),
        ("!=2.0.*", VersionCondition.NE, "2.0", True),
        (">1", VersionCondition.GT, "1", False),
        ("<4.0.0", VersionCondition.LT, "4.0.0", False),
        ("==1.0.0-beta", VersionCondition.EQ, "1.0.0-beta", False),
    ],
)
def test_specifier_part_parse(
    text: str,
    condition: VersionCondition,
    version: str,
    wildcard: bool,
) -> None:
    part = VersionSpecifierPart.parse(text)

    assert part.condition is condition
    assert part.version == version
    assert part.wildcard is wildcard


@pytest.mark.parametrize(
    "text",
    ["2.0", "~=2.0", "", ">=", ">=2.1.*", "<3.*", "== =2"],
)
def test_specifier_part_parse_rejects_invalid(text: str) -> None:
    with pytest.raises(VersionSpecifierError):
        VersionSpecifierPart.parse(text)


# ---- VersionSpecifierPart.matches ----


@pytest.mark.parametrize(
    ("spec", "version", "expected"),
    [
        (">=2.1.112", "2.1.112", True),
        (">=2.1.112", "2.1.111", False),
        (">=2.1.112", "2.2", True),
        ("<=2.0.0", "2.0.0", True),
        ("<=2.0.0", "2.0.1", False),
        (">2.0", "2.0.1", True),
        (">2.0", "2.0", False),
        ("<3", "2.99.99", True),
        ("<3", "3.0.0", False),
        ("==2.1", "2.1.0", True),  # missing components compare as zero
        ("==2.1", "2.1.1", False),
        ("!=2.1", "2.1.0", False),
        ("!=2.1", "2.2", True),
        ("==2.1.112", "2.1.112-beta", True),  # suffixes ignored
        ("==2.1.*", "2.1.999", True),
        ("==2.1.*", "2.2.0", False),
        ("!=2.1.*", "2.1.999", False),
        ("!=2.1.*", "2.2.0", True),
    ],
)
def test_specifier_part_matches(spec: str, version: str, expected: bool) -> None:
    assert VersionSpecifierPart.parse(spec).matches(version) is expected


# ---- VersionSpecifier (AND composition) ----


@pytest.mark.parametrize(
    ("spec", "version", "expected"),
    [
        (">=2.1.112,<3", "2.1.112", True),
        (">=2.1.112,<3", "2.99", True),
        (">=2.1.112,<3", "3.0", False),
        (">=2.1.112,<3", "2.1.111", False),
        (">=2, !=2.5.*, <3", "2.4.9", True),
        (">=2, !=2.5.*, <3", "2.5.1", False),
    ],
)
def test_specifier_matches_all_parts(spec: str, version: str, expected: bool) -> None:
    assert VersionSpecifier.parse(spec).matches(version) is expected


def test_specifier_parse_rejects_empty() -> None:
    with pytest.raises(VersionSpecifierError):
        VersionSpecifier.parse("  ,  ")


# ---- evaluate_compatibility ----


def test_evaluate_incompatible_takes_precedence() -> None:
    verdict = evaluate_compatibility(
        "2.0.0",
        compatible_versions=[">=1,<3"],
        incompatible_versions=[
            IncompatibleVersionSpec(spec="<=2.0.0", reason="stream-json v1 format")
        ],
    )

    assert verdict.status is CompatibilityStatus.INCOMPATIBLE
    assert verdict.reason == "stream-json v1 format"


def test_evaluate_compatible_any_spec_matches() -> None:
    verdict = evaluate_compatibility(
        "4.2.0",
        compatible_versions=[">=2.1,<3", ">=4"],
        incompatible_versions=[],
    )

    assert verdict.status is CompatibilityStatus.COMPATIBLE


def test_evaluate_unknown_outside_declared_ranges() -> None:
    verdict = evaluate_compatibility(
        "3.5",
        compatible_versions=[">=2.1,<3"],
        incompatible_versions=[],
    )

    assert verdict.status is CompatibilityStatus.UNKNOWN


def test_evaluate_undeclared_when_no_ranges() -> None:
    verdict = evaluate_compatibility("1.0", compatible_versions=[], incompatible_versions=[])

    assert verdict.status is CompatibilityStatus.UNDECLARED


def test_evaluate_malformed_spec_warns_and_never_matches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        verdict = evaluate_compatibility(
            "2.0",
            compatible_versions=["~=banana"],
            incompatible_versions=[],
        )

    # Malformed registry data degrades to "unknown", never crashes a run.
    assert verdict.status is CompatibilityStatus.UNKNOWN
    assert "malformed version specifier" in caplog.text.lower()


# ---- VERSION_OUTPUT_RE ----


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("claude 2.1.112 (stable)", "2.1.112"),
        ("v0.45.1\n", "0.45.1"),
        ("Python 3.12.4", "3.12.4"),
        ("no version here", None),
        ("singleton 7 only", None),
    ],
)
def test_version_output_regex(output: str, expected: str | None) -> None:
    match = VERSION_OUTPUT_RE.search(output)

    assert (match.group() if match else None) == expected
