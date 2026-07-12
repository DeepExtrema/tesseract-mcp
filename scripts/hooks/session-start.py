"""Claude Code SessionStart hook: recall-at-start.

Reads the hook JSON payload from stdin (Claude Code provides `cwd` among
other fields), infers the project from the workspace folder name, and execs
D1's recall CLI (`python -m tesseract_mcp.recall --context`) to print a
compact context block to stdout. Claude Code injects that stdout as
`additionalContext` for the new session.

Contract (spec: docs/superpowers/specs/2026-07-11-discipline-hooks-design.md,
component 2): ANY exception -> print nothing, exit 0. A broken hook must
never block a session. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

DEFAULT_VAULT = r"C:\Vaults\Tesseract"


def infer_project(cwd: str | None) -> str:
    """Best-effort project name: the workspace folder's basename.

    Accepts either Windows (`\\`) or POSIX (`/`) separators regardless of
    the host platform, since the hook JSON's `cwd` reflects Claude Code's
    OS, not necessarily this interpreter's.
    """
    cwd = (cwd or "").strip()
    if not cwd:
        return ""
    normalized = cwd.replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def build_command(project: str, vault: str, python: str | None = None) -> list[str]:
    """The D1 CLI invocation for this project/vault."""
    python = python or sys.executable
    return [
        python, "-m", "tesseract_mcp.recall",
        "--vault", vault,
        "--context", "--project", project,
    ]


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        project = infer_project(payload.get("cwd", ""))
        if not project:
            return
        vault = os.environ.get("TESSERACT_VAULT_PATH", DEFAULT_VAULT)
        result = subprocess.run(
            build_command(project, vault),
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            sys.stdout.write(result.stdout)
    except Exception:
        # A broken hook must never block a session.
        return


if __name__ == "__main__":
    main()
