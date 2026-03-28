"""Rate-limit detection, reset time parsing, and backoff strategy."""

import datetime
import logging
import random
import re
from datetime import timezone
from typing import ClassVar

logger: logging.Logger = logging.getLogger(__name__)


class RateLimitDetector:
    PATTERNS: ClassVar[dict[str, list[re.Pattern[str]]]] = {
        "claude": [
            re.compile(r"hit your usage limit", re.IGNORECASE),
            re.compile(r"rate[_\s]?limit", re.IGNORECASE),
            re.compile(r"too many requests", re.IGNORECASE),
            re.compile(r"usage limit reached", re.IGNORECASE),
            re.compile(r"resets\s+\d", re.IGNORECASE),
        ],
        "codex": [
            re.compile(r"try again in\s+", re.IGNORECASE),
            re.compile(r"rate[_\s]?limit", re.IGNORECASE),
            re.compile(r"429", re.IGNORECASE),
        ],
        "gemini": [
            re.compile(r"RESOURCE_EXHAUSTED", re.IGNORECASE),
            re.compile(r"quota exceeded", re.IGNORECASE),
            re.compile(r"rate[_\s]?limit", re.IGNORECASE),
        ],
        "generic": [
            re.compile(r"rate limit exceeded", re.IGNORECASE),
            re.compile(r"too many requests", re.IGNORECASE),
            re.compile(r"throttl", re.IGNORECASE),
            re.compile(r"429", re.IGNORECASE),
        ],
    }

    def is_rate_limited(self, output: str) -> bool:
        if not output:
            return False

        return any(
            pattern.search(output) is not None
            for patterns in self.PATTERNS.values()
            for pattern in patterns
        )

    def extract_message(self, output: str) -> str | None:
        if not output:
            return None

        for line in output.splitlines():
            stripped_line = line.strip()
            if not stripped_line:
                continue

            if self.is_rate_limited(stripped_line):
                return stripped_line

        stripped_output = output.strip()
        if not stripped_output or not self.is_rate_limited(stripped_output):
            return None

        return stripped_output


class ResetTimeParser:
    ABSOLUTE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"resets?\s+(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
        r"(?P<meridiem>am|pm)?(?:\s+(?P<tz>[A-Za-z][A-Za-z0-9_+/\-]*))?",
        re.IGNORECASE,
    )
    RELATIVE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?P<duration>(?:\d+\s*"
        r"(?:days?|hours?|hrs?|hr|minutes?|mins?|min|seconds?|secs?|sec)"
        r"\b[\s,]*)+)",
        re.IGNORECASE,
    )
    RELATIVE_PART_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?P<value>\d+)\s*"
        r"(?P<unit>days?|hours?|hrs?|hr|minutes?|mins?|min|seconds?|secs?|sec)\b",
        re.IGNORECASE,
    )
    SECONDS_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?P<seconds>\d+)\s*seconds?\b",
        re.IGNORECASE,
    )

    def parse(self, output: str) -> int | None:
        relative_seconds = self._parse_relative(output)
        if relative_seconds is not None:
            return relative_seconds

        seconds_match = self.SECONDS_PATTERN.search(output)
        if seconds_match is not None:
            return int(seconds_match.group("seconds"))

        return self._parse_absolute(output)

    def _parse_relative(self, output: str) -> int | None:
        match = self.RELATIVE_PATTERN.search(output)
        if match is None:
            return None

        unit_seconds = {
            "day": 86400,
            "days": 86400,
            "hour": 3600,
            "hours": 3600,
            "hr": 3600,
            "hrs": 3600,
            "minute": 60,
            "minutes": 60,
            "min": 60,
            "mins": 60,
            "second": 1,
            "seconds": 1,
            "sec": 1,
            "secs": 1,
        }

        total_seconds = 0
        for part_match in self.RELATIVE_PART_PATTERN.finditer(match.group("duration")):
            value = int(part_match.group("value"))
            unit = part_match.group("unit").lower()
            total_seconds += value * unit_seconds[unit]

        if total_seconds == 0:
            return None

        return total_seconds

    def _parse_absolute(self, output: str) -> int | None:
        match = self.ABSOLUTE_PATTERN.search(output)
        if match is None:
            return None

        hour = int(match.group("hour"))
        minute = int(match.group("minute") or "0")
        meridiem = match.group("meridiem")

        if minute > 59:
            return None

        if meridiem is not None:
            if hour < 1 or hour > 12:
                return None

            if meridiem.lower() == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        elif hour > 23:
            return None

        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        target_timezone: timezone | ZoneInfo = datetime.UTC
        timezone_name = match.group("tz")
        if timezone_name:
            try:
                target_timezone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                logger.warning(
                    "Invalid reset timezone %r; falling back to UTC.",
                    timezone_name,
                )

        now = datetime.datetime.now(target_timezone)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = datetime.datetime.fromordinal(now.toordinal() + 1).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=target_timezone,
            )

        return max(int((target - now).total_seconds()), 0)


class BackoffStrategy:
    DEFAULT_SCHEDULE: ClassVar[list[int]] = [120, 300, 900, 1800]
    DEFAULT_JITTER_RANGE: ClassVar[tuple[int, int]] = (0, 30)

    def __init__(
        self,
        schedule: list[int] | None = None,
        jitter_range: tuple[int, int] | None = None,
        max_wait: int = 21600,
    ) -> None:
        resolved_schedule = list(schedule) if schedule is not None else list(self.DEFAULT_SCHEDULE)
        if not resolved_schedule:
            raise ValueError("BackoffStrategy schedule must not be empty")
        self._schedule = resolved_schedule
        self._jitter_range = jitter_range if jitter_range is not None else self.DEFAULT_JITTER_RANGE
        self._max_wait = max_wait

    def compute_wait(
        self,
        attempt: int,
        reset_seconds: int | None = None,
    ) -> float:
        if reset_seconds is not None and reset_seconds > 0:
            base_wait = reset_seconds
        else:
            schedule_index = min(attempt - 1, len(self._schedule) - 1)
            base_wait = self._schedule[schedule_index]

        jitter = random.randint(*self._jitter_range)  # noqa: S311
        return float(min(base_wait + jitter, self._max_wait))
