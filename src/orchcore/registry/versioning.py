"""Agent CLI version-compatibility engine (WP-26, analysis/06 plan).

A dependency-free, declarative version-specifier framework in the style of
VAgentTest's backend compatibility data: version ranges live in the agent
registry (TOML), are checked once per binary at runtime with calibrated
logging, and never fail a run — the check is advisory.

Specifier grammar: comma-separated AND of parts, each ``<op><version>`` with
``op`` one of ``==  !=  >=  <=  >  <``. ``==``/``!=`` accept a trailing
``.*`` wildcard for prefix matching (e.g. ``"==2.1.*"``). Lists of specifier
strings combine as OR.

Version strings compare numerically per dot-separated component; a component
contributes its leading integer and non-numeric suffixes are ignored
(``"2.1.112-beta"`` → ``(2, 1, 112)``), so pre-release tags from agent CLIs
do not break ordering. Missing components compare as zero (``"2.1" ==
"2.1.0"``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Sequence

logger: logging.Logger = logging.getLogger(__name__)

_PART_RE = re.compile(r"^(==|!=|>=|<=|>|<)\s*([0-9A-Za-z][0-9A-Za-z.\-+]*?)(\.\*)?$")

# First dotted-number group in a CLI's version output ("claude 2.1.112 (stable)").
VERSION_OUTPUT_RE = re.compile(r"\d+(?:\.\d+)+")


class VersionSpecifierError(ValueError):
    """A version specifier string does not conform to the grammar."""


class VersionCondition(StrEnum):
    """Comparison operator of a single specifier part."""

    EQ = "=="
    NE = "!="
    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"


def version_key(version: str) -> tuple[int, ...]:
    """Best-effort numeric sort key for a CLI version string."""
    components: list[int] = []
    for component in version.strip().split("."):
        match = re.match(r"\d+", component)
        components.append(int(match.group()) if match else 0)
    return tuple(components)


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


@dataclass(frozen=True)
class VersionSpecifierPart:
    """One ``<op><version>`` clause of a specifier."""

    condition: VersionCondition
    version: str
    wildcard: bool = False

    @classmethod
    def parse(cls, text: str) -> VersionSpecifierPart:
        """Parse one clause; raises :class:`VersionSpecifierError` on bad input."""
        match = _PART_RE.match(text.strip())
        if match is None:
            raise VersionSpecifierError(
                f"Invalid version specifier part {text!r}; expected e.g. '>=2.1.112' or '==2.1.*'"
            )
        condition = VersionCondition(match.group(1))
        wildcard = match.group(3) is not None
        if wildcard and condition not in (VersionCondition.EQ, VersionCondition.NE):
            raise VersionSpecifierError(
                f"Wildcard versions are only valid with == or != (got {text!r})"
            )
        return cls(condition=condition, version=match.group(2), wildcard=wildcard)

    def matches(self, version: str) -> bool:
        """Return True when ``version`` satisfies this clause."""
        candidate = version_key(version)
        reference = version_key(self.version)
        if self.wildcard:
            prefix_equal = candidate[: len(reference)] == reference
            return prefix_equal if self.condition is VersionCondition.EQ else not prefix_equal
        ordering = _compare(candidate, reference)
        match self.condition:
            case VersionCondition.EQ:
                return ordering == 0
            case VersionCondition.NE:
                return ordering != 0
            case VersionCondition.GE:
                return ordering >= 0
            case VersionCondition.LE:
                return ordering <= 0
            case VersionCondition.GT:
                return ordering > 0
            case VersionCondition.LT:
                return ordering < 0


@dataclass(frozen=True)
class VersionSpecifier:
    """Comma-separated AND of specifier parts (``">=2.1.112,<3"``)."""

    parts: tuple[VersionSpecifierPart, ...]

    @classmethod
    def parse(cls, spec: str) -> VersionSpecifier:
        """Parse a full specifier; raises :class:`VersionSpecifierError` on bad input."""
        parts = tuple(
            VersionSpecifierPart.parse(clause) for clause in spec.split(",") if clause.strip()
        )
        if not parts:
            raise VersionSpecifierError(f"Empty version specifier {spec!r}")
        return cls(parts=parts)

    def matches(self, version: str) -> bool:
        """Return True when ``version`` satisfies every clause."""
        return all(part.matches(version) for part in self.parts)


class IncompatibleVersionSpec(BaseModel):
    """A known-incompatible version range with its documented reason.

    The reason is maintained data, ideally a link to the upstream issue or
    changelog entry that motivated the exclusion.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    spec: str
    reason: str = ""


class CompatibilityStatus(StrEnum):
    """Outcome of evaluating a detected version against declared ranges."""

    COMPATIBLE = "compatible"  # matches a declared compatible range -> DEBUG
    INCOMPATIBLE = "incompatible"  # matches a known-incompatible range -> WARNING
    UNKNOWN = "unknown"  # ranges declared, version outside them -> INFO
    UNDECLARED = "undeclared"  # no ranges declared at all -> DEBUG


@dataclass(frozen=True)
class CompatibilityVerdict:
    """Status plus the recorded reason for known-incompatible versions."""

    status: CompatibilityStatus
    reason: str = ""


def _spec_matches(spec: str, version: str) -> bool:
    """Advisory matching: malformed registry data warns instead of failing a run."""
    try:
        return VersionSpecifier.parse(spec).matches(version)
    except VersionSpecifierError as exc:
        logger.warning("Ignoring malformed version specifier %r: %s", spec, exc)
        return False


def evaluate_compatibility(
    version: str,
    compatible_versions: Sequence[str],
    incompatible_versions: Sequence[IncompatibleVersionSpec],
) -> CompatibilityVerdict:
    """Evaluate a detected CLI version against the declared ranges.

    Known-incompatible ranges take precedence over compatible ones so a
    targeted exclusion inside a broad compatible range wins.
    """
    for incompatible in incompatible_versions:
        if _spec_matches(incompatible.spec, version):
            return CompatibilityVerdict(
                status=CompatibilityStatus.INCOMPATIBLE,
                reason=incompatible.reason,
            )
    if any(_spec_matches(spec, version) for spec in compatible_versions):
        return CompatibilityVerdict(status=CompatibilityStatus.COMPATIBLE)
    if compatible_versions:
        return CompatibilityVerdict(status=CompatibilityStatus.UNKNOWN)
    return CompatibilityVerdict(status=CompatibilityStatus.UNDECLARED)


__all__ = [
    "VERSION_OUTPUT_RE",
    "CompatibilityStatus",
    "CompatibilityVerdict",
    "IncompatibleVersionSpec",
    "VersionCondition",
    "VersionSpecifier",
    "VersionSpecifierError",
    "VersionSpecifierPart",
    "evaluate_compatibility",
    "version_key",
]
