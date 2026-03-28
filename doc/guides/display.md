# Display Utilities

orchcore's `display` module provides colored ANSI logging and formatting helpers for building terminal UIs. It uses only ANSI escape codes — no Rich or other dependencies.

## Overview

The display module is designed for consuming projects that want simple colored terminal output without pulling in a full TUI framework. It writes to stderr so it doesn't interfere with stdout-based output pipelines.

## Logging Functions

All log functions write to stderr with a UTC timestamp prefix:

```python
from orchcore.display import log_info, log_success, log_warn, log_error, log_dim

log_info("Starting pipeline with 3 phases")     # cyan  ">" icon
log_success("Phase planning completed")          # green "+" icon
log_warn("Agent stalled for 300 seconds")        # yellow "!" icon
log_error("Agent claude failed with exit code 1") # red   "x" icon
log_dim("Skipping optional cleanup step")         # dim, no icon
```

Output looks like:

```
14:30:00 > Starting pipeline with 3 phases
14:30:45 + Phase planning completed
14:35:00 ! Agent stalled for 300 seconds
14:36:00 x Agent claude failed with exit code 1
```

## Status Line

`status_line()` writes an overwriting status line (using `\r`) for real-time progress display:

```python
from orchcore.display import status_line, clear_status_line

# Updates in place on a single line
status_line(elapsed=125.0, tool_count=12, cost=0.42, state="WRITING")
# Output: # 2m05s | 12 tools | $0.42 | WRITING

# Clear when done
clear_status_line()
```

## Phase Header

`phase_header()` renders a section divider for phase transitions:

```python
from orchcore.display import phase_header

phase_header("execution", index=1, total=3)
```

Output:

```
============================================================
  Phase 2/3: execution
============================================================
```

## Summary Box

`summary_box()` renders a bordered key-value summary:

```python
from orchcore.display import summary_box

summary_box("Pipeline Complete", {
    "Status": "Success",
    "Duration": "4m 32s",
    "Total Cost": "$1.23",
    "Phases": "3/3 passed",
})
```

## Formatting Helpers

The `formatting` submodule provides value formatters:

```python
from orchcore.display import format_cost, format_duration, format_tokens

format_cost(1.2345)          # "$1.23"
format_duration(272.5)       # "4m 32s"
format_tokens(15234)         # "15.2k"
```

## ANSI Color Constants

Available for custom formatting in consuming projects:

| Constant | Color |
|----------|-------|
| `RED` | Red text |
| `GREEN` | Green text |
| `YELLOW` | Yellow text |
| `CYAN` | Cyan text |
| `MAGENTA` | Magenta text |
| `DIM` | Dimmed/gray text |
| `BOLD` | Bold text |
| `NC` | Reset (no color) |

```python
from orchcore.display import CYAN, GREEN, NC

print(f"{CYAN}Phase:{NC} planning | {GREEN}Status:{NC} complete")
```

## Design Notes

- All output goes to **stderr** — stdout remains clean for agent output piping
- Timestamps are **UTC** — consistent across environments
- Uses `\r` for status line overwrites — works in all standard terminals
- No Rich, colorama, or other dependencies — pure ANSI escape codes

## Related

- [Writing a UICallback](writing-a-uicallback.md) — building custom display layers
- [Architecture Overview](../architecture/overview.md) — how display fits into the broader system
