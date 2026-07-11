"""Sync the repo's skills/ into the personal Claude Code skills directory.

Additive by default, mirroring mcp_sync's philosophy: an existing skill is
NEVER modified unless --force. --check reports without writing (exit 1 when
anything is pending, for use as a drift probe).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO_SKILLS = Path(__file__).resolve().parent.parent.parent / "skills"


def default_dest() -> Path:
    return Path.home() / ".claude" / "skills"


def _same(a: Path, b: Path) -> bool:
    a_files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    if a_files != b_files:
        return False
    return all((a / f).read_bytes() == (b / f).read_bytes() for f in a_files)


def sync(
    src: Path = REPO_SKILLS,
    dest: Path | None = None,
    force: bool = False,
    check: bool = False,
) -> dict:
    src = Path(src)
    dest = Path(dest) if dest else default_dest()
    result: dict = {"installed": [], "updated": [], "up_to_date": [], "drift": []}
    if not src.is_dir():
        return result
    for skill_dir in sorted(p for p in src.iterdir() if (p / "SKILL.md").is_file()):
        name = skill_dir.name
        target = dest / name
        if not target.exists():
            if not check:
                shutil.copytree(skill_dir, target)
            result["installed"].append(name)
        elif _same(skill_dir, target):
            result["up_to_date"].append(name)
        elif force and not check:
            shutil.rmtree(target)
            shutil.copytree(skill_dir, target)
            result["updated"].append(name)
        else:
            result["drift"].append(name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install repo skills/ into ~/.claude/skills (additive)."
    )
    parser.add_argument("--check", action="store_true",
                        help="report only; write nothing (exit 1 if pending)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite skills that drifted from the repo")
    parser.add_argument("--dest", default=None,
                        help="target skills dir (default ~/.claude/skills)")
    args = parser.parse_args()
    if not REPO_SKILLS.is_dir():
        # installed wheels don't package skills/ — fail fast instead of
        # printing an empty success (mcp_sync does the same for its manifest)
        parser.error(
            f"skills directory not found: {REPO_SKILLS} (run from a source checkout)"
        )
    result = sync(src=REPO_SKILLS, dest=args.dest, force=args.force, check=args.check)
    print(json.dumps(result, indent=2))
    if args.check and (result["installed"] or result["drift"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
