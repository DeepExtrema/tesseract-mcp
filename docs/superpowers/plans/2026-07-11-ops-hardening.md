# M0 Ops Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live tesseract-mcp deployment actually work: fix the search_brain worker-thread import stall, run and schedule the Librarian against the live vault, build the live graph, and clean up stale branches.

**Architecture:** One code change (eager embedder warm-up in `server.main()` before the event loop starts), one new diagnostic script (stdio probe), one doc rule, then a sequence of consent-gated operational steps against the live vault at `C:\Vaults\Tesseract`. Spec: `docs/superpowers/specs/2026-07-11-ops-hardening-design.md`.

**Tech Stack:** Python 3.14 (repo venv at `.venv`), pytest, mcp SDK (FastMCP), Windows Task Scheduler (`schtasks`), git.

## Global Constraints

- Working dir: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`; branch `codex/architecture-roadmap`.
- Run tests with the repo-pinned venv python: `.venv\Scripts\python -m pytest tests/test_server.py -v` (pytest config maps `src/` via `pythonpath`).
- The live server binary is `.venv\Scripts\tesseract-mcp.exe` (editable install — source changes are live, but **running processes hold old code**; restarting them is Task 4).
- **Never lazy-import C-extension chains (numpy/torch/sentence_transformers) inside MCP tool bodies** — root cause of the audit bug. Eager-import at startup in the main thread.
- Steps marked **STOP (consent)** require Taimoor's explicit go in chat before executing. Do not proceed past them autonomously.
- Live vault: `C:\Vaults\Tesseract`. Nothing in this plan writes to the vault except the Librarian sweep (Task 5, consent-gated) and normal `log_session` calls.

---

### Task 1: Eager embedder warm-up in server startup

**Files:**
- Modify: `src/tesseract_mcp/server.py` (the `_get_embedder` area, ~line 49, and `main()`, ~line 344)
- Test: `tests/test_server.py` (append two tests)

**Interfaces:**
- Produces: `server._warm_start() -> None` — constructs the module-global embedder eagerly. `server.main()` calls `_warm_start()` before `mcp.run()`. Task 2's probe and Task 4's restart rely on this behavior existing.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_server.py`:

```python
def test_warm_start_constructs_embedder(monkeypatch):
    class FakeEmbedder:
        pass

    monkeypatch.setattr(server, "SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr(server, "_embedder", None)
    server._warm_start()
    assert isinstance(server._embedder, FakeEmbedder)


def test_main_warm_starts_before_run(monkeypatch):
    order = []
    monkeypatch.setattr(server, "_warm_start", lambda: order.append("warm"))
    monkeypatch.setattr(server.mcp, "run", lambda: order.append("run"))
    server.main()
    assert order == ["warm", "run"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::test_warm_start_constructs_embedder tests/test_server.py::test_main_warm_starts_before_run -v`
Expected: both FAIL — `AttributeError: <module 'tesseract_mcp.server'> has no attribute '_warm_start'`.

- [ ] **Step 3: Implement** — in `src/tesseract_mcp/server.py`, directly below `_get_embedder()`:

```python
def _warm_start() -> None:
    """Load the embedding stack in the main thread before the event loop.

    On Python 3.14 + Windows, the first import of numpy/torch inside a
    FastMCP tool worker thread stalls until the next stdin message arrives
    (2026-07-11 audit). Constructing the embedder here forces those imports
    and the model load onto the main thread at startup instead.
    """
    _get_embedder()
```

and change `main()`:

```python
def main() -> None:
    _warm_start()
    mcp.run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: all tests in the file PASS (the two new ones included; no existing test regresses — `main()` is not invoked by other tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/server.py tests/test_server.py
git commit -m "fix(server): eager embedder warm-up before event loop starts

Worker-thread C-extension imports stall under the MCP stdio server on
Py3.14/Windows until the next stdin message arrives (2026-07-11 audit).
Load the embedding stack in the main thread at startup instead."
```

---

### Task 2: stdio probe script (the instrument that caught the bug)

**Files:**
- Create: `scripts/probe_server.py`

**Interfaces:**
- Consumes: the built server exe `.venv\Scripts\tesseract-mcp.exe` with Task 1's fix (editable install — no reinstall needed).
- Produces: a manual/CI verification command. Exit 0 = server answers a single `search_brain` with no wake-up message; exit 1 = regression.

- [ ] **Step 1: Write the script** — create `scripts/probe_server.py`:

```python
"""Probe the tesseract-mcp server over stdio with a single tool call.

Regression guard for the 2026-07-11 worker-thread import stall: a healthy
server answers one search_brain request without needing a second message
to wake it. Usage:

    python scripts/probe_server.py [--exe PATH] [--vault PATH] [--timeout SEC]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque

DEFAULT_EXE = os.path.join(".venv", "Scripts", "tesseract-mcp.exe")
DEFAULT_VAULT = r"C:\Vaults\Tesseract"

# Sentinel distinguishing "stream hit EOF" (child closed stdout, almost
# certainly because it exited) from "still waiting" and from real data.
_EOF = object()


class ProbeFailure(Exception):
    """Raised for any FAIL condition; carries the message to print."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def read_line(stream, timeout: float):
    """Read one line from *stream* with a timeout.

    Returns the raw line (bytes) on success, ``None`` on timeout (no data
    arrived in time), or the ``_EOF`` sentinel if the stream hit EOF —
    which for a subprocess pipe means the child closed it, almost always
    because the process exited.
    """
    result: list = [None]

    def target() -> None:
        line = stream.readline()
        result[0] = line if line else _EOF

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None  # thread is still blocked in readline() -> timeout
    return result[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=DEFAULT_EXE)
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds to wait for the search response")
    args = parser.parse_args()

    env = dict(os.environ, TESSERACT_VAULT_PATH=args.vault)
    proc = subprocess.Popen(
        [args.exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env,
    )

    # Capture stderr into a bounded background buffer instead of DEVNULL,
    # so a FAIL can show what the server printed on its way out.
    stderr_tail: "deque[bytes]" = deque(maxlen=10)

    def drain_stderr() -> None:
        try:
            for raw_line in iter(proc.stderr.readline, b""):
                stderr_tail.append(raw_line)
        except (OSError, ValueError):
            pass

    threading.Thread(target=drain_stderr, daemon=True).start()

    def exit_code() -> int | None:
        # EOF on stdout means the pipe closed, but the OS may not have
        # finished tearing down the process yet -- wait briefly so we
        # report the real exit code instead of a racy None from poll().
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return proc.poll()

    def send(obj: dict) -> None:
        try:
            proc.stdin.write(json.dumps(obj).encode() + b"\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ProbeFailure("FAIL: server closed stdin pipe")

    def print_stderr_tail() -> None:
        if not stderr_tail:
            return
        print("--- server stderr (last lines) ---", file=sys.stderr)
        for raw_line in stderr_tail:
            print(raw_line.decode(errors="replace").rstrip("\n"), file=sys.stderr)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "probe", "version": "0"}}})
        line = read_line(proc.stdout, 60)
        if line is None:
            raise ProbeFailure("FAIL: no initialize response within 60s")
        if line is _EOF:
            raise ProbeFailure(
                f"FAIL: server exited before responding (exit code {exit_code()})")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        start = time.time()
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "search_brain",
                         "arguments": {"query": "probe", "limit": 1}}})
        line = read_line(proc.stdout, args.timeout)
        elapsed = round(time.time() - start, 1)
        if line is None:
            raise ProbeFailure(
                f"FAIL: search_brain gave no response in {args.timeout}s "
                "(worker-thread import stall regression?)")
        if line is _EOF:
            raise ProbeFailure(
                f"FAIL: server exited before responding (exit code {exit_code()})")

        try:
            resp = json.loads(line)
            if not isinstance(resp, dict):
                raise ValueError("response is not a JSON object")
        except (json.JSONDecodeError, ValueError):
            raise ProbeFailure(
                f"FAIL: unexpected response after {elapsed}s: {line[:200]!r}")
        if resp.get("id") != 2 or "result" not in resp:
            raise ProbeFailure(
                f"FAIL: unexpected response after {elapsed}s: {line[:200]!r}")

        print(f"PASS: search_brain answered in {elapsed}s")
        return 0
    except ProbeFailure as exc:
        print(exc.message)
        print_stderr_tail()
        return 1
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it to verify the fix end-to-end** (this is the test cycle for Tasks 1+2 together)

Run: `python scripts/probe_server.py --timeout 30`
Expected: `PASS: search_brain answered in <N>s` where N ≤ ~15 (cold model load included), exit code 0. If it prints FAIL, Task 1 is not actually fixing the stall — stop and debug before continuing.

- [ ] **Step 3: Commit**

```bash
git add scripts/probe_server.py
git commit -m "test(scripts): stdio probe guarding the worker-thread stall fix"
```

---

### Task 3: Document the no-lazy-heavy-imports rule

**Files:**
- Modify: `AGENTS.md` (repo root — append to the end)
- Modify: `docs/ARCHITECTURE.md` (append to the end)

**Interfaces:**
- Consumes: nothing. Produces: the standing rule future contributors and agents read.

- [ ] **Step 1: Append to `AGENTS.md`** (exact text, at end of file):

```markdown

## MCP server rule: no lazy heavy imports in tool bodies

Never import C-extension chains (numpy, torch, sentence_transformers)
inside an MCP tool body or anything a tool calls lazily. On Python 3.14 +
Windows, the first such import inside the FastMCP worker thread stalls
until the next stdin message arrives — the server appears to hang forever
on single requests (root-caused 2026-07-11; see the audit session log in
the vault). Eager-import at server startup in the main thread instead:
`server._warm_start()` exists for exactly this. Verify with
`python scripts/probe_server.py`.
```

- [ ] **Step 2: Append to `docs/ARCHITECTURE.md`** (exact text, at end of file):

```markdown

## Appendix: the worker-thread import stall (2026-07-11)

`server.main()` calls `_warm_start()` before `mcp.run()` to construct the
embedder — forcing sentence_transformers/torch/numpy imports and the model
load onto the main thread. Without it, the first heavy C-extension import
inside a FastMCP tool worker thread (Python 3.14, Windows, mcp 1.28.x)
does not complete until the *next* client message arrives on stdin, so
single tool calls time out indefinitely. `scripts/probe_server.py` is the
regression guard: one search_brain request over stdio, no wake-up message.
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md docs/ARCHITECTURE.md
git commit -m "docs: no lazy heavy imports in MCP tool bodies (audit rule)"
```

---

### Task 4: Restart live servers and verify from a real session

**Files:** none (operational).

**Interfaces:**
- Consumes: Tasks 1–2 shipped. Produces: a live, working server for Tasks 5–6.

- [ ] **Step 1: Kill running server processes** (they hold pre-fix code; sessions respawn them on next connect — 2026-07-08 lesson)

Run (PowerShell): `Get-Process tesseract-mcp -ErrorAction SilentlyContinue | Stop-Process -Force; Get-Process tesseract-mcp -ErrorAction SilentlyContinue`
Expected: second command prints nothing (no survivors).

- [ ] **Step 2: Verify via live MCP tools** (from the current Claude session, which reconnects automatically): call `search_brain` with query "recall harness", then `context_bundle` with query "librarian".
Expected: both return ranked results in seconds; no -32001 timeout. `graph_stats` is *expected* to still error ("Graph cache not built yet") until Task 5.

- [ ] **Step 3: Record** — no commit; note the verification result in the Task 5 session log.

---

### Task 5: First Librarian sweep against the live vault (consent-gated)

**Files:** none (operational; writes happen in the vault + state dir, not the repo).

**Interfaces:**
- Consumes: working server (Task 4). Produces: populated `librarian_state.json` + `graph.db` for state dir `8175395c1bbf`, `Claude/Librarian.md` report — Task 6 schedules what this proves.

- [ ] **Step 1: Confirm the extractor backend is available**

Run (PowerShell): `$env:TESSERACT_EXTRACTOR; where.exe codex claude 2>$null`
Expected: at least one CLI resolves. If `TESSERACT_EXTRACTOR` is empty, default is `codex`. If neither CLI resolves, **STOP (consent)**: ask Taimoor which backend to configure before sweeping — the graph phase needs it.

- [ ] **Step 2: Dry-run**

Run: `.venv\Scripts\python.exe -m tesseract_mcp.librarian C:\Vaults\Tesseract --dry-run`
Expected: exit 0 and a printed report: pending index count (~359 first time), organizer move candidates with scores, consolidation proposals, health items. Nothing is written.

- [ ] **Step 3: STOP (consent)** — show Taimoor the dry-run report verbatim, flag every organizer move it proposes (moves are journaled + `undo_move`-able, but review first). Proceed only on explicit yes. If specific moves look wrong, note them — the sweep can run and bad moves get undone after, or the sweep waits.

- [ ] **Step 4: Real sweep** (long: first-time graph extraction over ~359 notes via the LLM CLI — minutes to hours; run in a terminal, never inside an MCP call)

Run: `.venv\Scripts\python.exe -m tesseract_mcp.librarian C:\Vaults\Tesseract`
Expected: exit 0, JSON result printed with `"errors": []`.

- [ ] **Step 5: Verify all caretaker surfaces**
  - MCP `librarian_status` → shows the sweep timestamp (no more "no sweep yet").
  - `C:\Vaults\Tesseract\Claude\Librarian.md` → contains the sweep report.
  - MCP `graph_stats` → returns entity/note counts (Fix 3 of the spec lands here).
  - MCP `related_notes` on `Claude/Sessions/2026-07-11 Full-system audit - search_brain root cause, cold caretakers, branch verdicts.md` → returns connected notes.
  - Dir `~/.tesseract-mcp/8175395c1bbf/` → now contains `graph.db`, `librarian_state.json`, `manifest.json`.

- [ ] **Step 6: Log** — `log_session` to the vault: sweep ran, moves applied/undone, graph counts.

---

### Task 6: Schedule the Librarian

**Files:**
- Create: `scripts/librarian-task.cmd`

**Interfaces:**
- Consumes: a proven sweep (Task 5). Produces: unattended daily caretaking.

- [ ] **Step 1: Write the wrapper** — create `scripts/librarian-task.cmd` (Task Scheduler needs env vars + logging; a wrapper beats a long `/tr` string):

```bat
@echo off
rem Daily tesseract Librarian sweep (created 2026-07-11, M0 ops hardening).
rem Remove with: schtasks /delete /tn tesseract-librarian /f
set TESSERACT_EXTRACTOR=codex
"C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\python.exe" -m tesseract_mcp.librarian "C:\Vaults\Tesseract" >> "%USERPROFILE%\.tesseract-mcp\librarian-task.log" 2>&1
```

(If Task 5 Step 1 settled on the `claude` backend, set that here instead.)

- [ ] **Step 2: Register the scheduled task**

Run (PowerShell): `schtasks /create /tn tesseract-librarian /tr "C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\scripts\librarian-task.cmd" /sc daily /st 07:00 /f`
Expected: `SUCCESS: The scheduled task "tesseract-librarian" has successfully been created.`

- [ ] **Step 3: Verify by forced run**

Run: `schtasks /run /tn tesseract-librarian`, wait ~2 minutes (incremental sweep is small), then check: `Get-Content "$env:USERPROFILE\.tesseract-mcp\librarian-task.log" -Tail 20` shows a fresh JSON result with `"errors": []`, and `librarian_status` shows the new timestamp.
Expected: both true. If the task ran but the log is empty, check `schtasks /query /tn tesseract-librarian /v /fo list` for the last result code.

- [ ] **Step 4: Commit**

```bash
git add scripts/librarian-task.cmd
git commit -m "chore(ops): scheduled Librarian sweep wrapper (tesseract-librarian task)"
```

---

### Task 7: Branch cleanup

**Files:** none (git administration).

**Interfaces:** consumes nothing; produces a repo where no branch is silently stale.

- [ ] **Step 1: Delete the superseded local eval branch** (verified duplicate: roadmap has its work plus fixes d2ef270/79ffdcb)

Run: `git branch -D feat/search-eval-harness`
Expected: `Deleted branch feat/search-eval-harness ...`.

- [ ] **Step 2: Fast-forward master to the roadmap tip**

Run: `git fetch origin && git push origin codex/architecture-roadmap:master && git branch -f master origin/master`
Expected: push reports `master` updated to the roadmap head (fast-forward — master has zero unique commits, verified in the audit; if the push is rejected as non-fast-forward, STOP and investigate, do not force).

- [ ] **Step 3: STOP (consent)** — ask Taimoor once: delete `origin/feat/recall-harness` (merged via PR #4)? On yes: `git push origin --delete feat/recall-harness`. On no: leave it, done.

- [ ] **Step 4: Verify**

Run: `git branch -a -v`
Expected: local `codex/architecture-roadmap` + `master` (same tip), remotes matching; no `feat/search-eval-harness`.

- [ ] **Step 5: Log** — `log_session`: M0 complete, acceptance list from the spec checked off.
