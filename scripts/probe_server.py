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
