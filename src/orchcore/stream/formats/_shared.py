"""Shared coercion helpers and the wire-validation error channel (WP-25).

Format modules validate each JSON object into a private Pydantic envelope
model and read typed attributes from it; these helpers perform the lenient
scalar coercions the wire formats need (agent CLIs are not strict about
field types). They are pure functions with no side effects.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ValidationError

from orchcore.stream.events import StreamEvent  # noqa: TC001 — Protocol signature

if TYPE_CHECKING:
    from collections.abc import Mapping


class _WireValidationError(Exception):
    """A syntactically valid JSON object failed a format's wire schema.

    Raised by format modules on envelope ``ValidationError`` so that
    ``StreamParser.parse_line`` can count the failure into
    ``wire_validation_error_count`` instead of letting it vanish silently.
    Kept separate from ``json_parse_error_count``, which retains its
    JSON-syntax-only meaning (surfaced on ``AgentResult`` by WP-18).
    """


class FormatParser(Protocol):
    """A stateful per-format parser turning wire objects into StreamEvents."""

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one decoded JSON object into zero or more events."""
        ...


def validate_or_none[ModelT: BaseModel](model_cls: type[ModelT], value: object) -> ModelT | None:
    """Validate ``value`` into ``model_cls``; ``None`` when it does not fit.

    Mirrors the pre-WP-25 ``isinstance(value, dict)`` guards: a nested object
    of the wrong shape degrades gracefully instead of failing the envelope.
    """
    try:
        return model_cls.model_validate(value)
    except ValidationError:
        return None


def str_or_none(value: object) -> str | None:
    """Coerce to a non-empty string, or None."""
    if value is None:
        return None
    s = str(value)
    return s if s else None


def first_str(*values: object) -> str | None:
    """Return the first value that coerces to a non-empty string."""
    for value in values:
        if (coerced := str_or_none(value)) is not None:
            return coerced
    return None


def int_or_none(value: object) -> int | None:
    """Coerce to int via str (accepts numeric strings), or None."""
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def to_decimal(value: object) -> Decimal | None:
    """Coerce to Decimal via str, or None when not a valid decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def dict_or_none(value: object) -> dict[str, object] | None:
    """Return the value when it is a mapping usable as tool input, else None."""
    return value if isinstance(value, dict) else None


def error_text_or_none(value: object) -> str | None:
    """Extract a human-readable error string from a str/mapping/other value."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return first_str(
            value.get("message"),
            value.get("text"),
            value.get("detail"),
            value.get("error"),
            value.get("code"),
            value.get("status"),
        ) or str(value)
    return str_or_none(value)


def token_usage_or_none(value: object) -> dict[str, int] | None:
    """Build a token-usage mapping from a wire ``usage`` object, or None."""
    if not isinstance(value, dict):
        return None
    return {k: int(v) for k, v in value.items() if isinstance(v, (int, float))} or None


def extract_tool_detail(
    tool_name: str | None, tool_input: Mapping[str, object] | None
) -> str | None:
    """Derive a short human-readable detail string for a tool invocation.

    Tool inputs are open-world mappings owned by the agent CLIs; this is the
    one place that intentionally navigates them.
    """
    if not tool_name or not tool_input:
        return None
    name_lower = tool_name.lower()
    if name_lower == "read":
        return str(tool_input.get("file_path") or tool_input.get("filename") or "")
    if name_lower in ("write", "edit"):
        return str(tool_input.get("file_path") or tool_input.get("filename") or "")
    if name_lower == "glob":
        return str(tool_input.get("pattern", ""))
    if name_lower == "grep":
        pattern = str(tool_input.get("pattern", ""))
        return pattern[:50]
    if name_lower == "bash":
        cmd = str(tool_input.get("command", ""))
        return f"$ {cmd}"[:80]
    if name_lower == "agent":
        desc = str(tool_input.get("description", ""))
        return f'Agent "{desc}"'
    if "web_search" in name_lower or "deep_search" in name_lower:
        query = str(tool_input.get("query", ""))
        return f'Web "{query}"'[:50]
    if name_lower.startswith("mcp__"):
        return f"MCP {tool_name}"
    if name_lower == "ls":
        return str(tool_input.get("path", ""))
    return None
