"""Claude Code Stop hook: log-at-end nudge.

Reads the hook JSON payload from stdin (Claude Code provides
`transcript_path`, a JSONL transcript of the session). Counts assistant
`tool_use` entries and checks whether a `log_session` tool call appears
anywhere in the transcript. If the session looks significant (tool uses
>= NUDGE_THRESHOLD) and no `log_session` was seen, prints a one-line
reminder to stdout.

Contract (spec: docs/superpowers/specs/2026-07-11-discipline-hooks-design.md,
component 3): nudge, not enforcement. ANY doubt or error -> exit 0 silently.
Never exit nonzero (zero false blocks). Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NUDGE_THRESHOLD = 10
NUDGE_MESSAGE = (
    "Significant session with no log_session — consider filing a session "
    "log before finishing."
)


def count_tool_uses(lines: list[str]) -> tuple[int, bool]:
    """Return (tool_use_count, saw_log_session) from transcript JSONL lines.

    Tolerant of malformed/partial lines and of transcript entry shapes that
    don't match expectations — anything unrecognized is just skipped, never
    raised, since a parsing hiccup here must not turn into a false block.
    """
    count = 0
    saw_log_session = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message", entry)
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            count += 1
            name = str(block.get("name", ""))
            if "log_session" in name:
                saw_log_session = True
    return count, saw_log_session


def evaluate(tool_use_count: int, saw_log_session: bool) -> str | None:
    """The nudge message, or None if the session doesn't warrant one."""
    if tool_use_count >= NUDGE_THRESHOLD and not saw_log_session:
        return NUDGE_MESSAGE
    return None


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        transcript_path = payload.get("transcript_path", "")
        if not transcript_path:
            return
        path = Path(transcript_path)
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        count, saw_log_session = count_tool_uses(lines)
        message = evaluate(count, saw_log_session)
        if message:
            print(message)
    except Exception:
        # Zero false blocks: any doubt or error -> silent, exit 0.
        return


if __name__ == "__main__":
    main()
