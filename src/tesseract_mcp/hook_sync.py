"""Merge the discipline-hook entries into the Claude Code settings JSON.

Additive by default, mirroring skill_sync's philosophy: an existing hook
entry belonging to us is NEVER overwritten if it has drifted from the
expected command — that's manual-fix territory, reported by --check.
Unrelated hooks (any other event, or any other entry within our events)
are always preserved untouched. --check reports without writing (exit 1
when anything is pending, for use as a drift probe).

Config placement: hooks live in the user-scope
~/.claude/settings.json (override via CLAUDE_SETTINGS_PATH, used by tests
and anyone syncing a non-default profile) — see
docs/superpowers/specs/2026-07-11-discipline-hooks-design.md, "Config
placement".
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "scripts" / "hooks"

# Claude Code hook event -> the script we install for it.
HOOK_SCRIPTS = {
    "SessionStart": "session-start.py",
    "Stop": "stop-nudge.py",
}


def default_settings_path() -> Path:
    override = os.environ.get("CLAUDE_SETTINGS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "settings.json"


def venv_python(repo_root: Path = REPO_ROOT) -> Path:
    """The repo's own virtualenv interpreter (Windows layout)."""
    return repo_root / ".venv" / "Scripts" / "python.exe"


def expected_command(event: str, repo_root: Path = REPO_ROOT) -> str:
    """The absolute command string this installer writes for `event`."""
    python = venv_python(repo_root)
    script = repo_root / "scripts" / "hooks" / HOOK_SCRIPTS[event]
    return f'"{python}" "{script}"'


def _read_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: settings.json must be a JSON object")
    return data


def _find_ours(entries: list, marker: str) -> dict | None:
    """The array entry (if any) whose command mentions our script filename."""
    for entry in entries:
        for h in entry.get("hooks", []):
            if marker in h.get("command", ""):
                return entry
    return None


def classify(settings: dict, repo_root: Path = REPO_ROOT) -> dict:
    """Classify each of our hook events as missing / present / drifted."""
    hooks = settings.get("hooks", {}) or {}
    result: dict = {"missing": [], "present": [], "drift": []}
    for event, script_name in HOOK_SCRIPTS.items():
        entries = hooks.get(event, []) or []
        expected = expected_command(event, repo_root)
        ours = _find_ours(entries, script_name)
        if ours is None:
            result["missing"].append(event)
            continue
        actual = (ours.get("hooks") or [{}])[0].get("command", "")
        if actual == expected:
            result["present"].append(event)
        else:
            result["drift"].append(event)
    return result


def sync(settings_path: Path, repo_root: Path = REPO_ROOT, check: bool = False) -> dict:
    """Additively merge missing hook entries. --check writes nothing."""
    settings = _read_settings(settings_path)
    result = classify(settings, repo_root)
    if check or not result["missing"]:
        return result

    hooks = settings.setdefault("hooks", {})
    for event in result["missing"]:
        entry = {"hooks": [{"type": "command",
                            "command": expected_command(event, repo_root)}]}
        hooks.setdefault(event, []).append(entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install session-start/stop-nudge hooks into Claude Code's "
                    "settings.json (additive)."
    )
    parser.add_argument("--check", action="store_true",
                        help="report only; write nothing (exit 1 if pending)")
    parser.add_argument("--install", action="store_true",
                        help="merge missing hook entries")
    args = parser.parse_args()
    if not args.check and not args.install:
        parser.error("specify --check or --install")

    settings_path = default_settings_path()
    result = sync(settings_path, check=args.check)
    print(json.dumps(result, indent=2))
    if args.check and (result["missing"] or result["drift"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
