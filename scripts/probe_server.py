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

DEFAULT_EXE = os.path.join(".venv", "Scripts", "tesseract-mcp.exe")
DEFAULT_VAULT = r"C:\Vaults\Tesseract"


def read_line(stream, timeout: float) -> bytes | None:
    result: list[bytes | None] = [None]

    def target() -> None:
        result[0] = stream.readline()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
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
        stderr=subprocess.DEVNULL, env=env,
    )

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj).encode() + b"\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "probe", "version": "0"}}})
        if read_line(proc.stdout, 60) is None:
            print("FAIL: no initialize response within 60s")
            return 1
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        start = time.time()
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "search_brain",
                         "arguments": {"query": "probe", "limit": 1}}})
        line = read_line(proc.stdout, args.timeout)
        elapsed = round(time.time() - start, 1)
        if line is None:
            print(f"FAIL: search_brain gave no response in {args.timeout}s "
                  "(worker-thread import stall regression?)")
            return 1
        resp = json.loads(line)
        if resp.get("id") != 2 or "result" not in resp:
            print(f"FAIL: unexpected response after {elapsed}s: {line[:200]!r}")
            return 1
        print(f"PASS: search_brain answered in {elapsed}s")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
