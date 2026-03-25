"""Async subprocess runner for agent CLIs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path  # noqa: TC003 — used at runtime in path operations
from typing import TYPE_CHECKING

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, ToolSet
from orchcore.stream.events import AgentResult, StreamEvent, StreamEventType, StreamFormat
from orchcore.stream.filter import StreamFilter
from orchcore.stream.monitor import AgentMonitor
from orchcore.stream.parser import StreamParser
from orchcore.stream.stall import StallDetector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from orchcore.stream.events import AgentMonitorSnapshot

logger = logging.getLogger(__name__)


async def _read_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded lines from an asyncio StreamReader."""
    while True:
        line_bytes = await stream.readline()
        if not line_bytes:
            break
        yield line_bytes.decode("utf-8", errors="replace")


class AgentRunner:
    """Executes an agent as an async subprocess and streams output through the pipeline."""

    async def run(
        self,
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        mode: AgentMode = AgentMode.PLAN,
        dry_run: bool = False,
        on_event: Callable[[StreamEvent], None] | None = None,
        on_snapshot: Callable[[AgentMonitorSnapshot], None] | None = None,
        snapshot_interval: float | None = None,
        stall_check_interval: float = 5.0,
        on_process_start: Callable[[asyncio.subprocess.Process], None] | None = None,
        on_process_end: Callable[[asyncio.subprocess.Process], None] | None = None,
        toolset: ToolSet | None = None,
    ) -> AgentResult:
        """Run the agent subprocess and return a fully-populated AgentResult."""
        cmd = self._build_command(agent, prompt, output_path, mode, toolset)

        env = {**os.environ, **agent.env_vars}

        if dry_run:
            logger.info("dry_run=True, skipping subprocess. Command: %s", cmd)
            return AgentResult(
                agent_name=agent.name,
                output_path=output_path,
                stream_path=output_path.with_suffix(".stream"),
                log_path=output_path.with_suffix(".log"),
                exit_code=0,
                duration=timedelta(0),
                output_empty=True,
            )

        started_at = datetime.now()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        if on_process_start is not None:
            on_process_start(proc)

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

        text_chunks: list[str] = []

        def _collecting_on_event(event: StreamEvent) -> None:
            if event.event_type == StreamEventType.TEXT and event.text_preview is not None:
                text_chunks.append(event.text_preview)
            if on_event is not None:
                on_event(event)

        assert proc.stdout is not None  # guaranteed by PIPE flag
        assert proc.stderr is not None  # guaranteed by PIPE flag

        primary_is_stderr = agent.output_extraction.stderr_as_stream
        if primary_is_stderr:
            primary_stream = proc.stderr
            secondary_stream = proc.stdout
        else:
            primary_stream = proc.stdout
            secondary_stream = proc.stderr

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def _read_secondary() -> None:
            async for line in _read_lines(secondary_stream):
                if primary_is_stderr:
                    stdout_chunks.append(line)
                else:
                    stderr_chunks.append(line)

        secondary_task = asyncio.create_task(_read_secondary())
        snapshot_task = _start_snapshot_task(
            monitor=monitor,
            on_snapshot=on_snapshot,
            snapshot_interval=snapshot_interval,
        )

        try:
            raw_lines = _read_lines(primary_stream)
            filtered = stream_filter.filter_stream(
                _tee_to_file(
                    raw_lines,
                    stream_path,
                    collector=stderr_chunks if primary_is_stderr else None,
                )
            )
            parsed = stream_parser.parse_stream(filtered)
            watched = stall_detector.watch(parsed)

            await monitor.consume(watched, on_event=_collecting_on_event)

            await secondary_task

            await asyncio.to_thread(log_path.write_text, "".join(stderr_chunks), encoding="utf-8")

            exit_code = await proc.wait()

            duration = datetime.now() - started_at

            await asyncio.to_thread(
                _write_output,
                agent=agent,
                output_path=output_path,
                text_chunks=text_chunks,
                stdout_chunks=stdout_chunks,
            )

            snap = monitor.snapshot()
            if on_snapshot is not None:
                on_snapshot(snap)

            output_empty = await asyncio.to_thread(_check_output_empty, output_path)

            return AgentResult(
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
                error=_derive_error(exit_code, stderr_chunks) if exit_code != 0 else None,
            )
        except BaseException:
            await _shutdown_process(proc)
            raise
        finally:
            await _cancel_task(snapshot_task)
            await _cancel_task(secondary_task)
            if on_process_end is not None:
                on_process_end(proc)

    @staticmethod
    def _build_command(
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> list[str]:
        """Build the subprocess command list."""
        cmd = [agent.binary, agent.subcommand, prompt]
        cmd.extend(["--model", agent.model])

        if toolset is not None:
            # ToolSet overrides mode-based flags
            cmd.extend(_translate_toolset(agent, toolset))
        else:
            # Fall back to mode-based flags
            cmd.extend(agent.flags.get(mode, []))

        # Codex uses -o flag for direct file output
        if agent.output_extraction.strategy == OutputExtraction.Strategy.DIRECT_FILE:
            cmd.extend(["-o", str(output_path)])
        return cmd


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
        if all_tools:
            flags.extend(["--allowedTools", ",".join(all_tools)])
        if toolset.max_turns > 0:
            flags.extend(["--max-turns", str(toolset.max_turns)])
        # Claude always needs streaming flags
        flags.extend(["--verbose", "--output-format", "stream-json", "--include-partial-messages"])

    elif format_ == StreamFormat.CODEX:
        # Codex uses --sandbox for permission level
        if toolset.permission:
            flags.extend(["-s", toolset.permission])
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

    elif format_ == StreamFormat.OPENCODE:
        # OpenCode uses agent-specific flags
        flags.extend(["--format", "json"])

    return flags


async def _tee_to_file(
    raw: AsyncIterator[str],
    path: Path,
    collector: list[str] | None = None,
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


def _write_output(
    agent: AgentConfig,
    output_path: Path,
    text_chunks: list[str],
    stdout_chunks: list[str],
) -> None:
    """Write extracted text to output_path based on the agent's OutputExtraction strategy."""
    extraction = agent.output_extraction

    match extraction.strategy:
        case OutputExtraction.Strategy.DIRECT_FILE:
            # Agent wrote directly to output_path — nothing to do.
            return

        case OutputExtraction.Strategy.JQ_FILTER:
            # text_preview fields are already the extracted text fragments.
            text = "".join(text_chunks)
            if extraction.strip_preamble:
                text = _strip_preamble_text(text)
            output_path.write_text(text, encoding="utf-8")

        case OutputExtraction.Strategy.STDOUT_CAPTURE:
            text = "".join(stdout_chunks) if extraction.stderr_as_stream else "".join(text_chunks)
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


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    """Cancel and await a background task, ignoring normal cancellation noise."""
    if task is None or task.done():
        return

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _shutdown_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate a live subprocess and wait for it to exit."""
    if proc.returncode is not None:
        return

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def _check_output_empty(path: Path) -> bool:
    """Return True when path is absent or has zero bytes (blocking; run in thread pool)."""
    return not path.exists() or path.stat().st_size == 0


def _derive_error(exit_code: int, stderr_chunks: list[str]) -> str:
    """Build a diagnostic error message from exit code and available stderr content.

    Returns the last few lines of stderr when available, otherwise a generic
    message quoting the exit code so callers always get actionable context.
    """
    if stderr_chunks:
        # Take up to the last 10 non-blank lines from stderr for conciseness.
        non_blank = [line.rstrip() for line in stderr_chunks if line.strip()]
        tail = non_blank[-10:] if len(non_blank) > 10 else non_blank
        return "\n".join(tail) if tail else f"Process exited with code {exit_code}"
    return f"Process exited with code {exit_code}"
