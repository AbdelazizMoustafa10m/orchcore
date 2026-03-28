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

The `formatting` submodule provides value formatters. These accept typed values, not raw primitives:

```python
from datetime import timedelta
from decimal import Decimal
from orchcore.display import format_cost, format_duration, format_tokens, format_file_size

format_duration(timedelta(minutes=4, seconds=32))   # "4m 32s"
format_duration(timedelta(seconds=8))                # "8s"
format_duration(None)                                # "—"

format_cost(Decimal("1.2345"))                       # "$1.2345"
format_cost(None)                                    # "N/A"

format_tokens({"input_tokens": 15000, "output_tokens": 3200})  # "15,000/3,200"
format_tokens(None)                                             # "—"

format_file_size(2_500_000)                          # "2.4 MB"
format_file_size(512)                                # "512 bytes"
```

**Note:** The ANSI color constants (`RED`, `CYAN`, `BOLD`, etc.) are defined in `orchcore.display.logging` but are not part of the public API exported by `orchcore.display`. If you need them, import from the submodule directly: `from orchcore.display.logging import CYAN, NC`.

## Design Notes

- All output goes to **stderr** — stdout remains clean for agent output piping
- Timestamps are **UTC** — consistent across environments
- Uses `\r` for status line overwrites — works in all standard terminals
- No Rich, colorama, or other dependencies — pure ANSI escape codes

## Related

- [Writing a UICallback](writing-a-uicallback.md) — building custom display layers
- [Architecture Overview](../architecture/overview.md) — how display fits into the broader system
