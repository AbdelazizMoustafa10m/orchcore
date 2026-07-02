"""Async subprocess runner for agent CLIs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import warnings
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003 — used at runtime in path operations
from typing import IO, TYPE_CHECKING, Any, Literal

from orchcore.recovery.rate_limit import RateLimitDetector, ResetTimeParser
from orchcore.registry.agent import (
    CODEX_PERMISSION_VALUES,
    DEFAULT_TOOLSET_MAX_TURNS,
    DEFAULT_TOOLSET_PERMISSION,
    AgentConfig,
    OutputExtraction,
    ToolSet,
    is_valid_flag_profile_name,
)
from orchcore.registry.versioning import (
    VERSION_OUTPUT_RE,
    CompatibilityStatus,
    evaluate_compatibility,
)
from orchcore.stream.events import (
    AgentErrorCategory,
    AgentResult,
    AgentState,
    StreamEvent,
    StreamEventType,
    StreamFormat,
)
from orchcore.stream.filter import StreamFilter
from orchcore.stream.monitor import AgentMonitor
from orchcore.stream.parser import StreamParser
from orchcore.stream.stall import StallDetector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from orchcore.stream.events import AgentMonitorSnapshot

logger: logging.Logger = logging.getLogger(__name__)

type RequiredFlagCheck = tuple[str, tuple[str, ...]]

_REQUIRED_STREAM_FLAG_CHECKS: dict[StreamFormat, tuple[RequiredFlagCheck, ...]] = {
    StreamFormat.CODEX: (("--json", ("--json",)),),
    StreamFormat.OPENCODE: (("--format json", ("--format", "json")),),
}

# Flags the ToolSet owns per stream format: everything the ToolSet translation
# can emit (including CLI aliases), plus known permission/approval-bypass
# flags in the same domain. When a ToolSet is in effect it is the single
# authority for tool access, permissions, and stream-output flags, so profile
# flags in this domain are dropped (with a warning). Appending the translation
# last is NOT enough: clap-based CLIs such as Codex hard-fail on a duplicated
# singleton flag (e.g. two ``-s`` values), and a bypass flag like ``--yolo``
# cannot be neutralized by later flags at all.
#
# The mapping value is the flag's value arity: ``"none"`` (boolean flag),
# ``"one"`` (consumes one following value token), or ``"greedy"`` (variadic —
# consumes following tokens until the next ``-``-prefixed token, e.g. Claude's
# space-separated ``--allowedTools Read Edit``).
type _FlagArity = Literal["none", "one", "greedy"]

_TOOLSET_MANAGED_FLAGS: dict[StreamFormat, dict[str, _FlagArity]] = {
    StreamFormat.CLAUDE: {
        "--allowedTools": "greedy",
        "--allowed-tools": "greedy",
        "--disallowedTools": "greedy",
        "--disallowed-tools": "greedy",
        "--tools": "greedy",
        "--max-turns": "one",
        "--verbose": "none",
        "--output-format": "one",
        "--include-partial-messages": "none",
        "--permission-mode": "one",
        "--dangerously-skip-permissions": "none",
    },
    StreamFormat.CODEX: {
        "-s": "one",
        "--sandbox": "one",
        "--json": "none",
        "-a": "one",
        "--ask-for-approval": "one",
        "--full-auto": "none",
        "--dangerously-bypass-approvals-and-sandbox": "none",
    },
    StreamFormat.GEMINI: {
        "--yolo": "none",
        "--approval-mode": "one",
    },
    StreamFormat.COPILOT: {
        "--allow-tool": "one",
        "--deny-tool": "one",
        "--allow-all-tools": "none",
    },
    StreamFormat.OPENCODE: {
        "--format": "one",
    },
}

_DEFAULT_EXCLUDED_ENV_PATTERNS: tuple[str, ...] = (
    r"ANTHROPIC_.*",
    r"OPENAI_.*",
    r"GEMINI_.*",
    r"GOOGLE_.*",
    r"COPILOT_.*",
    r"GH_.*",
    r"GITHUB_.*",
    r"OTEL_.*",
    r"AWS_.*",
    r"CLAUDE_.*",
    r"CODEX_.*",
    r"OPENCODE_.*",
    r"AZURE_.*",
    r"HTTP_PROXY",
    r"HTTPS_PROXY",
    r"ALL_PROXY",
)

_CLEAN_KEEP_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        # Windows essentials: without these, many child EXEs fail to start.
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
    }
)

_EXCLUDED_ENV_RE = re.compile("|".join(_DEFAULT_EXCLUDED_ENV_PATTERNS), re.IGNORECASE)

# Advisory CLI version detection (WP-26): one exec per binary path per process.
_VERSION_CACHE: dict[str, str | None] = {}
_VERSION_CHECK_TIMEOUT = 10.0

# Bounded in-memory stream collection (WP-30): a collector keeps chunks in
# memory up to this many characters, then spills them to an unnamed temp file.
_SPILL_THRESHOLD_CHARS = 8 * 1024 * 1024
# Non-blank lines retained in memory for error derivation (_non_blank_tail
# reports at most 10).
_TAIL_KEEP_LINES = 10


class _LineBuffer:
    """Collects stream chunks with bounded peak memory (WP-30).

    Below the spill threshold behavior is identical to a plain list join;
    past it, buffered chunks move to an unnamed temp file and later appends
    stream straight to it. A short non-blank tail always stays in memory for
    error derivation. ``getvalue()`` concatenates at write-out time (callers
    run it off the event loop); ``close()`` releases the spill file.
    """

    def __init__(self, spill_threshold: int | None = None) -> None:
        self._threshold = _SPILL_THRESHOLD_CHARS if spill_threshold is None else spill_threshold
        self._chunks: list[str] = []
        self._size = 0
        self._spill: IO[str] | None = None
        self._tail: deque[str] = deque(maxlen=_TAIL_KEEP_LINES)

    def append(self, chunk: str) -> None:
        if chunk.strip():
            self._tail.append(chunk)
        self._size += len(chunk)
        if self._spill is not None:
            self._spill.write(chunk)
            return
        self._chunks.append(chunk)
        if self._size > self._threshold:
            # Per-chunk writes after rollover are small and OS-buffered, the
            # same trade-off _tee_to_file documents for its line writes. The
            # unnamed temp file is reclaimed by the OS even on hard crashes.
            spill = tempfile.TemporaryFile(  # noqa: SIM115 - lifetime spans appends; close()d in run()'s finally
                mode="w+", encoding="utf-8", errors="replace"
            )
            spill.writelines(self._chunks)
            self._chunks.clear()
            self._spill = spill

    @property
    def spilled(self) -> bool:
        return self._spill is not None

    @property
    def buffered_chunk_count(self) -> int:
        """In-memory chunk count (instrumentation for the bounding tests)."""
        return len(self._chunks)

    def tail_lines(self) -> list[str]:
        """Most recent non-blank chunks, enough for _non_blank_tail."""
        return list(self._tail)

    def getvalue(self) -> str:
        if self._spill is None:
            return "".join(self._chunks)
        self._spill.flush()
        self._spill.seek(0)
        return self._spill.read()

    def close(self) -> None:
        if self._spill is not None:
            self._spill.close()
            self._spill = None
        self._chunks.clear()


async def _read_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded lines from an asyncio StreamReader."""
    while True:
        line_bytes = await stream.readline()
        if not line_bytes:
            break
        yield line_bytes.decode("utf-8", errors="replace")


def build_agent_env(agent: AgentConfig) -> dict[str, str]:
    """Build the exact environment used for an agent subprocess."""
    source = dict(os.environ)
    if agent.env_policy == "inherit":
        base = source
    elif agent.env_policy == "clean":
        if os.name == "nt":
            # Windows environment names are case-insensitive; preserve mixed-case essentials.
            base = {key: value for key, value in source.items() if key.upper() in _CLEAN_KEEP_KEYS}
        else:
            # POSIX environment names are case-sensitive; lowercase path/home must not leak.
            base = {key: value for key, value in source.items() if key in _CLEAN_KEEP_KEYS}
    else:
        allowed = (
            re.compile("|".join(agent.env_passlist), re.IGNORECASE) if agent.env_passlist else None
        )
        base = {
            key: value
            for key, value in source.items()
            if not _EXCLUDED_ENV_RE.fullmatch(key)
            or (allowed is not None and allowed.fullmatch(key) is not None)
        }
        removed_names = sorted(set(source) - set(base))
        if removed_names:
            logger.debug(
                "Filtered %d environment variable(s) for agent %s: %s%s",
                len(removed_names),
                agent.name,
                ", ".join(removed_names[:10]),
                "..." if len(removed_names) > 10 else "",
            )

    return {**base, **agent.env_vars}


async def _detect_agent_version(agent: AgentConfig, cwd: Path | None) -> str | None:
    """Detect the agent CLI version once per binary path; advisory only (WP-26).

    The result (including failure, cached as None) is memoized per resolved
    binary path so a process never runs more than one version exec per CLI.
    """
    key = shutil.which(agent.binary) or agent.binary
    if key in _VERSION_CACHE:
        cached_version = _VERSION_CACHE[key]
        if cached_version is not None:
            _log_version_compatibility(agent, cached_version)
        return cached_version
    version = await _run_version_command(agent, cwd)
    _VERSION_CACHE[key] = version
    if version is None:
        logger.info(
            "Agent %s: no version detected from `%s %s` (advisory check only)",
            agent.name,
            agent.binary,
            " ".join(agent.version_command),
        )
        return None
    _log_version_compatibility(agent, version)
    return version


async def _run_version_command(agent: AgentConfig, cwd: Path | None) -> str | None:
    """Run the version command across the same explicit boundary as agent runs.

    Filtered env per the agent's env_policy, the run's explicit cwd, no stdin,
    and a hard timeout — the check must not reintroduce the ambient-env or
    ambient-cwd holes WP-10/WP-17 closed. Every failure mode returns None.
    """
    proc: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(_VERSION_CHECK_TIMEOUT):
            proc = await asyncio.create_subprocess_exec(
                agent.binary,
                *agent.version_command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=build_agent_env(agent),
                cwd=cwd,
            )
            stdout, stderr = await proc.communicate()
    except (TimeoutError, OSError) as exc:
        logger.debug("Agent %s: version command failed: %s", agent.name, exc)
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        return None
    # CLIs print versions to stdout or stderr; read both.
    output = (
        stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")
    )
    match = VERSION_OUTPUT_RE.search(output)
    return match.group() if match else None


def _log_version_compatibility(agent: AgentConfig, version: str) -> None:
    """Calibrated logging: WARNING known-bad, DEBUG known-good, INFO unknown."""
    verdict = evaluate_compatibility(
        version, agent.compatible_versions, agent.incompatible_versions
    )
    if verdict.status is CompatibilityStatus.INCOMPATIBLE:
        logger.warning(
            "Agent %s CLI version %s is known-incompatible: %s",
            agent.name,
            version,
            verdict.reason or "no reason recorded",
        )
    elif verdict.status is CompatibilityStatus.COMPATIBLE:
        logger.debug(
            "Agent %s CLI version %s is within the declared compatible ranges",
            agent.name,
            version,
        )
    elif verdict.status is CompatibilityStatus.UNKNOWN:
        logger.info(
            "Agent %s CLI version %s is outside the declared compatible ranges %s; "
            "proceeding (advisory check only)",
            agent.name,
            version,
            list(agent.compatible_versions),
        )
    else:
        logger.debug(
            "Agent %s CLI version %s detected (no version expectations declared)",
            agent.name,
            version,
        )


class AgentRunner:
    """Executes an agent as an async subprocess and streams output through the pipeline.

    Memory behavior (WP-30): stream content is collected through spill
    buffers — in memory up to ``_SPILL_THRESHOLD_CHARS`` (8 MiB) per
    collector, then spilled to unnamed temp files — so chatty agents cannot
    grow the process heap without bound. Behavior below the threshold is
    byte-identical to plain in-memory collection.
    """

    async def run(
        self,
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        *,
        flag_profile: str | None = None,
        dry_run: bool = False,
        on_event: Callable[[StreamEvent], None] | None = None,
        on_snapshot: Callable[[AgentMonitorSnapshot], None] | None = None,
        snapshot_interval: float | None = None,
        stall_check_interval: float = 5.0,
        on_process_start: Callable[[asyncio.subprocess.Process], None] | None = None,
        on_process_end: Callable[[asyncio.subprocess.Process], None] | None = None,
        toolset: ToolSet | None = None,
        on_stall: Callable[[str, float], None] | None = None,
        cwd: Path | None = None,
    ) -> AgentResult:
        """Run the agent subprocess and return a fully-populated AgentResult."""
        if flag_profile is not None and not is_valid_flag_profile_name(flag_profile):
            msg = f"Invalid flag profile name {flag_profile!r}"
            raise ValueError(msg)
        cmd = self._build_command(agent, prompt, output_path, flag_profile, toolset)
        _warn_if_missing_required_stream_flags(agent, cmd)

        env = build_agent_env(agent)

        if dry_run:
            logger.info("dry_run=True, skipping subprocess. Command: %s (cwd=%s)", cmd, cwd)
            return AgentResult(
                agent_name=agent.name,
                output_path=output_path,
                stream_path=output_path.with_suffix(".stream"),
                log_path=output_path.with_suffix(".log"),
                exit_code=0,
                duration=timedelta(0),
                output_empty=True,
            )

        agent_version: str | None = None
        if agent.version_command:
            agent_version = await _detect_agent_version(agent, cwd)

        started_at = datetime.now(UTC)

        use_stdin = agent.prompt_via == "stdin"
        stdin_pipe = asyncio.subprocess.PIPE if use_stdin else None
        logger.debug("Launching agent command: %s (cwd=%s)", cmd, cwd)
        if os.name != "nt":
            # POSIX agents run in a new session so shutdown can signal the whole tree.
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin_pipe,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                start_new_session=True,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin_pipe,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
        if on_process_start is not None:
            on_process_start(proc)

        async def _feed_stdin() -> None:
            # Runs as a background task: a large prompt can exceed the OS pipe
            # buffer, and the child may block writing stdout before reading
            # all of stdin — feeding while we consume avoids the classic
            # pipe deadlock.
            stdin = proc.stdin
            if stdin is None:
                raise RuntimeError("stdin transport requested but no stdin pipe attached")
            try:
                stdin.write(prompt.encode("utf-8"))
                await stdin.drain()
            except OSError as exc:
                # BrokenPipeError/ConnectionResetError: the child exited (or
                # closed stdin) before reading the full prompt. Its own exit
                # state decides the run's outcome; the pipe error is expected.
                logger.debug("agent closed stdin before reading the full prompt: %s", exc)
            finally:
                with contextlib.suppress(OSError):
                    stdin.close()
                    await stdin.wait_closed()

        stdin_task = asyncio.create_task(_feed_stdin()) if use_stdin else None

        stream_path = output_path.with_suffix(".stream")
        log_path = output_path.with_suffix(".log")

        stream_filter = StreamFilter(agent.stream_format)
        stream_parser = StreamParser(agent.stream_format)
        stall_detector = StallDetector(
            normal_timeout=agent.stall_timeout,
            deep_timeout=agent.deep_tool_timeout,
            check_interval=stall_check_interval,
        )
        monitor = AgentMonitor(agent.name)

        text_buffer = _LineBuffer()
        stream_error: str | None = None
        stream_error_category: AgentErrorCategory | None = None
        stream_rate_limit_retry_delay_ms: int | None = None
        stall_idle_seconds: float | None = None
        stall_abort = asyncio.Event()
        # Prefer the explicit on_stall parameter; fall back to introspecting
        # the on_event callback for a bound on_stall_detected method to
        # maintain backward compatibility with existing consumers.
        stall_callback = on_stall if on_stall is not None else _resolve_stall_callback(on_event)

        def _collecting_on_event(event: StreamEvent) -> None:
            nonlocal stall_idle_seconds, stream_error, stream_error_category
            nonlocal stream_rate_limit_retry_delay_ms
            if event.event_type == StreamEventType.TEXT:
                full = event.text_full or event.text_preview
                if full is not None:
                    text_buffer.append(full)
            if on_event is not None:
                on_event(event)
            if event.event_type == StreamEventType.RESULT:
                if event.error:
                    stream_error = event.error
                    stream_error_category = AgentErrorCategory.STREAM_ERROR
                elif event.exit_code is not None and event.exit_code != 0:
                    stream_error = f"stream reported exit code {event.exit_code}"
                    stream_error_category = AgentErrorCategory.STREAM_ERROR
                else:
                    stream_error = None
                    stream_error_category = None
            elif event.event_type == StreamEventType.ERROR:
                stream_error = event.error or event.text_preview or "agent reported an error event"
                stream_error_category = AgentErrorCategory.STREAM_ERROR
            elif event.event_type == StreamEventType.RATE_LIMIT:
                stream_error = (
                    event.error
                    or event.text_preview
                    or (
                        f"agent reported a rate limit ({event.error_category})"
                        if event.error_category
                        else None
                    )
                    or "agent reported a rate limit"
                )
                stream_error_category = AgentErrorCategory.RATE_LIMIT
                if event.retry_delay_ms is not None:
                    stream_rate_limit_retry_delay_ms = event.retry_delay_ms
            if (
                event.event_type == StreamEventType.STALL
                and event.idle_seconds is not None
                and stall_callback is not None
            ):
                stall_callback(agent.name, event.idle_seconds)
            if event.event_type == StreamEventType.STALL and agent.kill_on_stall:
                stall_idle_seconds = event.idle_seconds
                stall_abort.set()

        if proc.stdout is None or proc.stderr is None:  # pragma: no cover - PIPE guarantees both.
            raise RuntimeError("subprocess pipes not connected despite PIPE flags")

        primary_is_stderr = agent.output_extraction.stderr_as_stream
        if primary_is_stderr:
            primary_stream = proc.stderr
            secondary_stream = proc.stdout
        else:
            primary_stream = proc.stdout
            secondary_stream = proc.stderr

        stdout_buffer = _LineBuffer()
        stderr_buffer = _LineBuffer()

        async def _read_secondary() -> None:
            async for line in _read_lines(secondary_stream):
                if primary_is_stderr:
                    stdout_buffer.append(line)
                else:
                    stderr_buffer.append(line)

        secondary_task = asyncio.create_task(_read_secondary())
        snapshot_task = _start_snapshot_task(
            monitor=monitor,
            on_snapshot=on_snapshot,
            snapshot_interval=snapshot_interval,
        )
        run_completed = False

        try:
            raw_lines = _read_lines(primary_stream)
            filtered = stream_filter.filter_stream(
                _tee_to_file(
                    raw_lines,
                    stream_path,
                    collector=stderr_buffer if primary_is_stderr else stdout_buffer,
                )
            )
            parsed = stream_parser.parse_stream(filtered)
            watched = stall_detector.watch(parsed)

            timed_out = False
            stalled_out = False

            async def _consume_events() -> None:
                await monitor.consume(watched, on_event=_collecting_on_event)

            consume_task = asyncio.create_task(_consume_events())
            abort_task = asyncio.create_task(stall_abort.wait()) if agent.kill_on_stall else None
            timeout_cm = (
                asyncio.timeout(agent.max_runtime)
                if agent.max_runtime is not None
                else contextlib.nullcontext()
            )
            try:
                async with timeout_cm:
                    if abort_task is None:
                        await consume_task
                    else:
                        done, _pending = await asyncio.wait(
                            {consume_task, abort_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if abort_task in done and proc.returncode is None:
                            stalled_out = True
                        else:
                            await consume_task
            except TimeoutError:
                timed_out = True
            finally:
                if timed_out or stalled_out:
                    await _shutdown_process(proc)
                    await _cancel_task(consume_task)
                if abort_task is not None:
                    await _cancel_task(abort_task)

            await secondary_task

            await asyncio.to_thread(_write_log, log_path, stderr_buffer)

            exit_code = await proc.wait()

            # Sync monitor state with process exit when stream didn't emit a terminal event.
            if exit_code != 0:
                current_snap = monitor.snapshot()
                if current_snap.state not in {
                    AgentState.COMPLETED,
                    AgentState.FAILED,
                    AgentState.CANCELLED,
                }:
                    monitor.update(
                        StreamEvent(
                            event_type=StreamEventType.RESULT,
                            exit_code=exit_code,
                            error=_derive_error(
                                exit_code, stderr_buffer.tail_lines(), stdout_buffer.tail_lines()
                            ),
                        )
                    )

            duration = datetime.now(UTC) - started_at

            await asyncio.to_thread(
                _write_output,
                agent=agent,
                output_path=output_path,
                text_buffer=text_buffer,
                stdout_buffer=stdout_buffer,
            )

            snap = monitor.snapshot()
            result_error, error_category, reset_seconds = _resolve_result_state(
                exit_code=exit_code,
                stderr_chunks=stderr_buffer.tail_lines(),
                stdout_chunks=stdout_buffer.tail_lines(),
                stream_error=stream_error,
                stream_error_category=stream_error_category,
                snap_state=snap.state,
                timed_out=timed_out,
                max_runtime=agent.max_runtime,
                stalled_out=stalled_out,
                stall_idle_seconds=stall_idle_seconds,
                stream_rate_limit_retry_delay_ms=stream_rate_limit_retry_delay_ms,
            )
            if on_snapshot is not None:
                on_snapshot(snap)

            output_empty = await asyncio.to_thread(_check_output_empty, output_path)
            if result_error is None and error_category is None and output_empty:
                result_error = f"Agent {agent.name!r} completed without producing output"
                error_category = AgentErrorCategory.EMPTY_OUTPUT

            result = AgentResult(
                agent_name=agent.name,
                output_path=output_path,
                stream_path=stream_path,
                log_path=log_path,
                exit_code=exit_code,
                duration=duration,
                cost_usd=snap.cost_usd,
                token_usage=snap.token_usage,
                num_turns=snap.num_turns,
                session_id=snap.session_id,
                output_empty=output_empty,
                error=result_error,
                error_category=error_category,
                rate_limit_reset_seconds=reset_seconds,
                json_parse_error_count=stream_parser.json_parse_error_count,
                wire_validation_error_count=stream_parser.wire_validation_error_count,
                agent_version=agent_version,
            )
            run_completed = True
            return result
        finally:
            if not run_completed:
                await _shutdown_process(proc)
            # Not _cancel_task: that returns early for already-finished tasks
            # without retrieving their exception, which would surface as
            # "Task exception was never retrieved" from the feeder.
            await _settle_stdin_task(stdin_task)
            await _cancel_task(snapshot_task)
            await _cancel_task(secondary_task)
            for buffer in (text_buffer, stdout_buffer, stderr_buffer):
                buffer.close()
            if on_process_end is not None:
                on_process_end(proc)

    @staticmethod
    def _build_command(
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        flag_profile: str | None,
        toolset: ToolSet | None = None,
    ) -> list[str]:
        """Build the subprocess command list.

        An empty ``subcommand`` is omitted entirely (it must never become a
        literal ``''`` argv element). Under ``prompt_via="stdin"`` the prompt
        is omitted from argv; ``stdin_sentinel`` (e.g. ``"-"``) takes its
        place when the CLI requires a placeholder argument.

        Flag profiles and ToolSets compose additively: profile flags come
        first, the ToolSet translation last. When a ToolSet is in effect,
        profile flags in the ToolSet-managed domain (tool access, permissions,
        stream-output format) are dropped with a warning — see
        ``_strip_toolset_managed_flags``.
        """
        cmd = [agent.binary]
        if agent.subcommand:
            cmd.append(agent.subcommand)
        if agent.prompt_via == "argv":
            cmd.append(prompt)
        elif agent.stdin_sentinel is not None:
            cmd.append(agent.stdin_sentinel)
        cmd.extend(["--model", agent.model])

        profile_flags = _resolve_profile_flags(agent, flag_profile)
        if toolset is not None:
            profile_flags = _strip_toolset_managed_flags(agent, profile_flags)
        cmd.extend(profile_flags)
        if toolset is not None:
            cmd.extend(_translate_toolset(agent, toolset))

        # Codex uses -o flag for direct file output
        if agent.output_extraction.strategy == OutputExtraction.Strategy.DIRECT_FILE:
            cmd.extend(["-o", str(output_path)])
        return cmd


def _resolve_profile_flags(agent: AgentConfig, flag_profile: str | None) -> tuple[str, ...]:
    """Return the argv contribution of the selected flag profile.

    ``None`` selects no profile and contributes nothing. A selected profile
    missing from ``agent.flags`` logs a warning and contributes nothing —
    the run proceeds so a partially-migrated registry degrades visibly
    rather than fatally.
    """
    if flag_profile is None:
        return ()
    profile_flags = agent.flags.get(flag_profile)
    if profile_flags is None:
        available = ", ".join(sorted(agent.flags)) or "none defined"
        logger.warning(
            "Agent %r has no flags for profile %r (available: %s); applying no profile flags",
            agent.name,
            flag_profile,
            available,
        )
        return ()
    return profile_flags


def _strip_toolset_managed_flags(
    agent: AgentConfig, profile_flags: tuple[str, ...]
) -> tuple[str, ...]:
    """Drop profile flags that belong to the ToolSet-managed domain.

    Called only when a ToolSet is in effect. Without this, registry data
    that keeps tool/permission flags in profiles (the 1.x fallback style)
    would emit duplicate or conflicting argv next to the ToolSet translation
    — clap-based CLIs reject duplicated singleton flags outright — or smuggle
    in bypass flags (``--yolo``) that the translation cannot neutralize.
    Value-taking flags consume their following value token as well.
    """
    managed = _TOOLSET_MANAGED_FLAGS.get(agent.stream_format)
    if not managed or not profile_flags:
        return profile_flags

    kept: list[str] = []
    dropped: list[str] = []
    index = 0
    while index < len(profile_flags):
        token = profile_flags[index]
        arity = managed.get(token.split("=", 1)[0])
        if arity is None:
            kept.append(token)
            index += 1
            continue
        end = index + 1
        if "=" not in token:
            if arity == "one" and end < len(profile_flags):
                end += 1
            elif arity == "greedy":
                # Variadic flags (e.g. --allowedTools Read Edit) own every
                # following token up to the next flag-like token.
                while end < len(profile_flags) and not profile_flags[end].startswith("-"):
                    end += 1
        dropped.extend(profile_flags[index:end])
        index = end

    if dropped:
        logger.warning(
            "Agent %r: dropping ToolSet-managed flag(s) %s from the selected flag "
            "profile; tool access, permissions, and stream-output flags are owned "
            "by the ToolSet when one is in effect. Keep only behavioral flags in "
            "profiles.",
            agent.name,
            dropped,
        )
    return tuple(kept)


def _translate_toolset(agent: AgentConfig, toolset: ToolSet) -> list[str]:
    """Translate a ToolSet into agent-specific CLI flags.

    Each agent CLI has different mechanisms for tool restriction.
    This function handles the translation per agent format.
    """
    flags: list[str] = []
    format_ = agent.stream_format

    if format_ == StreamFormat.CLAUDE:
        # Claude uses --allowedTools with comma-separated tool names
        all_tools = toolset.internal + toolset.mcp
        flags.extend(["--allowedTools", ",".join(all_tools)])
        if toolset.max_turns > 0:
            flags.extend(["--max-turns", str(toolset.max_turns)])
        # Claude always needs streaming flags
        flags.extend(["--verbose", "--output-format", "stream-json", "--include-partial-messages"])
        return flags

    _log_ignored_non_claude_toolset_fields(format_, toolset)

    if format_ == StreamFormat.CODEX:
        # Codex uses --sandbox for permission level
        _append_codex_permission_flag(flags, toolset.permission)
        flags.append("--json")
    elif format_ == StreamFormat.GEMINI:
        # Gemini uses --yolo for full access, default is sandboxed
        if toolset.permission == "full-access":
            flags.append("--yolo")
    elif format_ == StreamFormat.COPILOT:
        # Copilot uses --allow-tool and --deny-tool
        if toolset.internal:
            for tool in toolset.internal:
                flags.extend(["--allow-tool", tool.lower()])
        _log_ignored_permission_for_partial_support(format_, toolset.permission)
    elif format_ == StreamFormat.OPENCODE:
        # OpenCode uses agent-specific flags
        _log_ignored_permission_for_partial_support(format_, toolset.permission)
        flags.extend(["--format", "json"])

    return flags


def _append_codex_permission_flag(flags: list[str], permission: str) -> None:
    """Append the validated Codex sandbox flag when a permission is configured."""
    if not permission:
        return

    if permission not in CODEX_PERMISSION_VALUES:
        logger.warning(
            "Codex received unknown ToolSet.permission=%r; expected one of %s. "
            "Skipping sandbox flag.",
            permission,
            sorted(CODEX_PERMISSION_VALUES),
        )
        return

    flags.extend(["-s", permission])


def _log_ignored_non_claude_toolset_fields(format_: StreamFormat, toolset: ToolSet) -> None:
    """Log ToolSet fields that non-Claude formats do not translate into CLI flags."""
    if toolset.mcp:
        logger.debug(
            "%s ignores ToolSet.mcp on the command line; configure MCP tools on the agent CLI: %s",
            format_.value,
            toolset.mcp,
        )

    if toolset.max_turns != DEFAULT_TOOLSET_MAX_TURNS:
        logger.debug(
            "%s ignores ToolSet.max_turns=%s; only Claude translates max_turns to CLI flags.",
            format_.value,
            toolset.max_turns,
        )


def _log_ignored_permission_for_partial_support(format_: StreamFormat, permission: str) -> None:
    """Log non-default permissions for CLIs whose permission mapping is not implemented."""
    if permission in ("", DEFAULT_TOOLSET_PERMISSION):
        return

    logger.debug(
        "%s ignores ToolSet.permission=%r; permission flag mapping is not implemented.",
        format_.value,
        permission,
    )


def _warn_if_missing_required_stream_flags(agent: AgentConfig, cmd: list[str]) -> None:
    """Warn when the final command is missing required parsing flags for the stream format."""
    missing_flags = _find_missing_required_stream_flags(cmd, agent.stream_format)
    if not missing_flags:
        return

    logger.warning(
        "Agent %s (%s) command is missing required stream/JSON flags: %s. Stream parsing may fail.",
        agent.name,
        agent.stream_format.value,
        ", ".join(missing_flags),
    )


def _find_missing_required_stream_flags(
    cmd: list[str],
    stream_format: StreamFormat,
) -> list[str]:
    """Return required stream/JSON flags that are absent from the final command."""
    checks = _REQUIRED_STREAM_FLAG_CHECKS.get(stream_format, ())
    return [
        display_flag
        for display_flag, expected_sequence in checks
        if not _command_contains_flag_sequence(cmd, expected_sequence)
    ]


def _command_contains_flag_sequence(cmd: list[str], expected_sequence: tuple[str, ...]) -> bool:
    """Return True when the command contains the expected flag sequence."""
    expected_length = len(expected_sequence)
    if expected_length == 0 or expected_length > len(cmd):
        return False

    return any(
        cmd[start_index : start_index + expected_length] == list(expected_sequence)
        for start_index in range(len(cmd) - expected_length + 1)
    )


async def _tee_to_file(
    raw: AsyncIterator[str],
    path: Path,
    collector: _LineBuffer | None = None,
) -> AsyncIterator[str]:
    """Yield each line from raw while also writing it to path (tee for archival).

    The file is opened via asyncio.to_thread to avoid blocking the event loop
    on the file-system open() call. Individual line writes are OS-buffered and
    small enough that they do not cause measurable event-loop stalls.
    """
    fh = await asyncio.to_thread(path.open, "w", encoding="utf-8")
    try:
        async for line in raw:
            if collector is not None:
                collector.append(line)
            fh.write(line)
            yield line
    finally:
        await asyncio.to_thread(fh.close)


def _write_log(log_path: Path, stderr_buffer: _LineBuffer) -> None:
    """Write captured stderr to the log file (blocking; run in thread pool)."""
    log_path.write_text(stderr_buffer.getvalue(), encoding="utf-8")


def _write_output(
    agent: AgentConfig,
    output_path: Path,
    text_buffer: _LineBuffer,
    stdout_buffer: _LineBuffer,
) -> None:
    """Write extracted text to output_path based on the agent's OutputExtraction strategy.

    Blocking; run in thread pool. Concatenation happens here, at write-out
    time, so chatty streams stay spilled to disk until the single final read.
    """
    extraction = agent.output_extraction

    match extraction.strategy:
        case OutputExtraction.Strategy.DIRECT_FILE:
            # Agent wrote directly to output_path — nothing to do.
            return

        case OutputExtraction.Strategy.JQ_FILTER:
            # text_preview fields are already the extracted text fragments.
            text = text_buffer.getvalue()
            if extraction.strip_preamble:
                text = _strip_preamble_text(text)
            output_path.write_text(text, encoding="utf-8")

        case OutputExtraction.Strategy.STDOUT_CAPTURE:
            text = (
                stdout_buffer.getvalue() if extraction.stderr_as_stream else text_buffer.getvalue()
            )
            output_path.write_text(text, encoding="utf-8")


def _strip_preamble_text(text: str) -> str:
    """Remove text before the first markdown heading (^# )."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^#\s", line):
            return "\n".join(lines[i:])
    return text


def _start_snapshot_task(
    *,
    monitor: AgentMonitor,
    on_snapshot: Callable[[AgentMonitorSnapshot], None] | None,
    snapshot_interval: float | None,
) -> asyncio.Task[None] | None:
    """Start the periodic snapshot emitter when snapshot delivery is enabled."""
    if on_snapshot is None or snapshot_interval is None or snapshot_interval <= 0:
        return None

    async def _emit_snapshots() -> None:
        while True:
            await asyncio.sleep(snapshot_interval)
            on_snapshot(monitor.snapshot())

    return asyncio.create_task(_emit_snapshots())


def _resolve_stall_callback(
    on_event: Callable[[StreamEvent], None] | None,
) -> Callable[[str, float], None] | None:
    """Extract a stall callback from a bound UI event handler when available."""
    if on_event is None:
        return None

    callback_owner = getattr(on_event, "__self__", None)
    if callback_owner is None:
        return None

    stall_callback = getattr(callback_owner, "on_stall_detected", None)
    if not callable(stall_callback):
        return None

    warnings.warn(
        "Implicit stall-callback discovery via on_event.__self__ is deprecated; "
        "pass on_stall= explicitly. This shim is removed in 1.0.",
        DeprecationWarning,
        stacklevel=3,
    )

    def _invoke_stall(agent_name: str, duration: float) -> None:
        stall_callback(agent_name, duration)

    return _invoke_stall


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel and await a background task, ignoring normal cancellation noise."""
    if task is None:
        return

    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _settle_stdin_task(task: asyncio.Task[None] | None) -> None:
    """Cancel the stdin feeder if still running, then always await it so any
    exception is retrieved (awaiting a finished task re-raises it)."""
    if task is None:
        return

    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError, OSError):
        await task


def _kill_tree_windows(pid: int) -> subprocess.CompletedProcess[bytes]:
    """Force-kill a Windows process tree with taskkill."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell interpolation.
        ["taskkill.exe", "/F", "/T", "/PID", str(pid)],  # noqa: S607
        check=False,
        capture_output=True,
    )


def _signal_posix_process_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    """Signal the POSIX process group created for an agent subprocess."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), sig)


def terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Send the graceful shutdown signal for an agent process tree."""
    if proc.returncode is not None:
        return
    if os.name == "nt":
        # Windows has no POSIX-style process groups for asyncio children; taskkill
        # with /T is the reliable tree operation available on stock systems.
        outcome = _kill_tree_windows(proc.pid)
        if outcome.returncode == 0:
            logger.debug("taskkill tree-kill ok for pid %d", proc.pid)
        else:
            logger.warning(
                "taskkill failed for pid %d (rc=%d): %s",
                proc.pid,
                outcome.returncode,
                outcome.stderr.decode(errors="replace").strip(),
            )
        return

    _signal_posix_process_group(proc, signal.SIGTERM)


def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Force-kill an agent process tree."""
    if proc.returncode is not None:
        return
    if os.name == "nt":
        outcome = _kill_tree_windows(proc.pid)
        if outcome.returncode != 0:
            logger.warning(
                "taskkill force kill failed for pid %d (rc=%d): %s",
                proc.pid,
                outcome.returncode,
                outcome.stderr.decode(errors="replace").strip(),
            )
        return

    _signal_posix_process_group(proc, signal.SIGKILL)


async def _shutdown_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate a live subprocess tree and wait for it to exit."""
    if proc.returncode is not None:
        return

    terminate_process_tree(proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        kill_process_tree(proc)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def _check_output_empty(path: Path) -> bool:
    """Return True when path is absent or has zero bytes (blocking; run in thread pool)."""
    return not path.exists() or path.stat().st_size == 0


def _resolve_result_state(
    *,
    exit_code: int,
    stderr_chunks: list[str],
    stdout_chunks: list[str],
    stream_error: str | None,
    stream_error_category: AgentErrorCategory | None,
    snap_state: AgentState,
    timed_out: bool,
    max_runtime: float | None,
    stalled_out: bool,
    stall_idle_seconds: float | None,
    stream_rate_limit_retry_delay_ms: int | None = None,
) -> tuple[str | None, AgentErrorCategory | None, int | None]:
    """Resolve AgentResult error, category, and rate-limit reset from run state.

    Population map (mirrors the WP-18 table in doc/reference/stream-events.md):
    max_runtime timeout -> TIMEOUT; kill_on_stall -> STALL_TIMEOUT; typed
    RATE_LIMIT stream event -> RATE_LIMIT; ERROR / RESULT(error=...) with exit
    0 -> STREAM_ERROR; nonzero exit without a stream category -> NONZERO_EXIT,
    upgraded to RATE_LIMIT when the fallback detector matches the stderr tail
    (CLIs that never emit typed events). The reset time is parsed here, once,
    so the engine only consumes ``rate_limit_reset_seconds``.
    """
    error: str | None
    category: AgentErrorCategory | None
    if timed_out:
        runtime = "unknown" if max_runtime is None else f"{max_runtime:g}s"
        return f"max_runtime exceeded after {runtime}", AgentErrorCategory.TIMEOUT, None
    if stalled_out:
        idle = "unknown" if stall_idle_seconds is None else f"{stall_idle_seconds:g}s"
        return f"stalled for {idle} (kill_on_stall)", AgentErrorCategory.STALL_TIMEOUT, None
    if exit_code != 0:
        error = _derive_error(exit_code, stderr_chunks, stdout_chunks)
        if stream_error_category is AgentErrorCategory.RATE_LIMIT:
            category = AgentErrorCategory.RATE_LIMIT
        elif RateLimitDetector().is_rate_limited(error):
            # Fallback classifier for CLIs that never emit typed events.
            category = AgentErrorCategory.RATE_LIMIT
        else:
            category = AgentErrorCategory.NONZERO_EXIT
    elif stream_error is not None:
        error = stream_error
        category = stream_error_category or AgentErrorCategory.STREAM_ERROR
    elif snap_state is AgentState.RATE_LIMITED:
        error = f"stream reported terminal state {snap_state.value} without a message"
        category = AgentErrorCategory.RATE_LIMIT
    elif snap_state is AgentState.FAILED:
        error = f"stream reported terminal state {snap_state.value} without a message"
        category = AgentErrorCategory.STREAM_ERROR
    else:
        return None, None, None

    reset_seconds: int | None = None
    if category is AgentErrorCategory.RATE_LIMIT:
        reset_seconds = _retry_delay_ms_to_seconds(stream_rate_limit_retry_delay_ms)
        if reset_seconds is None:
            parser = ResetTimeParser()
            reset_seconds = parser.parse(error)
            if reset_seconds is None and stream_error is not None and stream_error != error:
                reset_seconds = parser.parse(stream_error)
    return error, category, reset_seconds


def _retry_delay_ms_to_seconds(retry_delay_ms: int | None) -> int | None:
    """Convert typed retry-delay milliseconds into whole seconds for AgentResult."""
    if retry_delay_ms is None:
        return None
    if retry_delay_ms <= 0:
        return 0
    return (retry_delay_ms + 999) // 1000


def _derive_error(
    exit_code: int,
    stderr_chunks: list[str],
    stdout_chunks: list[str],
) -> str:
    """Build a diagnostic error message from exit code and available stream content.

    Returns the last few non-blank lines of stderr when available. When stderr
    is empty, falls back to stdout before emitting the generic exit-code
    message so callers still get actionable context.
    """
    if error_text := _non_blank_tail(stderr_chunks):
        return error_text
    if error_text := _non_blank_tail(stdout_chunks):
        return error_text
    return f"Process exited with code {exit_code}"


def _non_blank_tail(chunks: list[str]) -> str | None:
    """Return the last few non-blank lines from a stream, if any."""
    non_blank = [line.rstrip() for line in chunks if line.strip()]
    if not non_blank:
        return None

    tail = non_blank[-10:] if len(non_blank) > 10 else non_blank
    return "\n".join(tail)
