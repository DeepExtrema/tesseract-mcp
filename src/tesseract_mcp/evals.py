"""Golden-query evaluation harness for hybrid search.

Scores the production retrieval path (hybrid.hybrid_search with real
embeddings) against golden query sets: a synthetic fixture corpus
committed under evals/, and optionally a private set stored in the live
vault at Claude/Evals.md. Metrics are rank-based (success@k, recall@k,
MRR) with threshold floors asserted in tests -- never exact-order
assertions, which punish semantic recall improvements.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import hybrid, indexer
from .vault import Vault, VaultError

# Source-checkout layout (src/tesseract_mcp/evals.py -> repo root). The
# fixture paths only make sense in a checkout, not an installed wheel;
# this harness is a repo-local dev tool.
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_VAULT = REPO_ROOT / "evals" / "vault"
FIXTURE_GOLDEN = REPO_ROOT / "evals" / "golden.yaml"
LIVE_GOLDEN_REL = "Claude/Evals.md"
HISTORY_FILE = "eval_history.jsonl"
RETRIEVE_LIMIT = 20
KS = (5, 10)


class EvalConfigError(Exception):
    """Bad golden set, bad paths, or bad invocation -- exit code 2."""


@dataclass
class GoldenQuery:
    id: str
    query: str
    expect: list[str]
    accept: list[str] = field(default_factory=list)
    tags: list[str] | None = None
    folder: str | None = None
    note: str = ""


@dataclass
class QueryResult:
    id: str
    hits: list[str]
    first_rank: int | None
    recall_at: dict[int, float]
    success_at: dict[int, bool]
    missing: list[str]
    skipped: bool = False


@dataclass
class Scorecard:
    results: list[QueryResult]
    success_at: dict[int, float]
    recall_at: dict[int, float]
    mrr: float
    skipped: int


def first_relevant_rank(hits: list[str], relevant: set[str]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if h in relevant:
            return i
    return None


def recall_at_k(hits: list[str], expect: set[str], k: int) -> float:
    if not expect:
        return 0.0
    return len(set(hits[:k]) & expect) / len(expect)


def success_at_k(hits: list[str], relevant: set[str], k: int) -> bool:
    return any(h in relevant for h in hits[:k])


_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


def load_golden(path: str | Path) -> list[GoldenQuery]:
    p = Path(path)
    if not p.is_file():
        raise EvalConfigError(f"golden file not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".md":
        m = _YAML_FENCE_RE.search(text)
        if not m:
            raise EvalConfigError(f"no ```yaml block found in {p}")
        text = m.group(1)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise EvalConfigError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, list):
        raise EvalConfigError(f"golden set must be a YAML list: {p}")
    queries: list[GoldenQuery] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "id" not in item or "query" not in item:
            raise EvalConfigError(f"entry {i} in {p} needs 'id' and 'query'")
        queries.append(
            GoldenQuery(
                id=str(item["id"]),
                query=str(item["query"]),
                expect=[str(x) for x in item.get("expect") or []],
                accept=[str(x) for x in item.get("accept") or []],
                tags=[str(t) for t in item["tags"]] if item.get("tags") else None,
                folder=str(item["folder"]) if item.get("folder") else None,
                note=str(item.get("note") or ""),
            )
        )
    seen: set[str] = set()
    for q in queries:
        if q.id in seen:
            raise EvalConfigError(f"duplicate golden id: {q.id}")
        seen.add(q.id)
        if not q.expect:
            raise EvalConfigError(f"golden {q.id}: 'expect' must be non-empty")
    return queries


def validate_paths(
    queries: list[GoldenQuery], vault_root: str | Path, strict: bool
) -> dict[str, list[str]]:
    """Map of query id -> expect/accept paths missing from the vault."""
    root = Path(vault_root)
    missing: dict[str, list[str]] = {}
    for q in queries:
        gone = [p for p in q.expect + q.accept if not (root / p).is_file()]
        if gone:
            missing[q.id] = gone
    if strict and missing:
        detail = "; ".join(f"{qid}: {', '.join(ps)}" for qid, ps in missing.items())
        raise EvalConfigError(f"golden paths missing from vault: {detail}")
    return missing


def run_evals(
    vault: Vault,
    state_root: str | Path,
    embedder,
    queries: list[GoldenQuery],
    ks: tuple[int, ...] = KS,
    limit: int = RETRIEVE_LIMIT,
    lenient: bool = False,
) -> Scorecard:
    # A fresh state dir must not crash the first fallback-cache save
    # (embeddings._save_fallback_cache writes without mkdir).
    Path(state_root).mkdir(parents=True, exist_ok=True)
    missing = validate_paths(queries, vault.root, strict=not lenient)
    results: list[QueryResult] = []
    for q in queries:
        gone = missing.get(q.id, [])
        if lenient and set(gone) >= set(q.expect):
            results.append(
                QueryResult(q.id, [], None, {k: 0.0 for k in ks},
                            {k: False for k in ks}, gone, skipped=True)
            )
            continue
        hits = hybrid.hybrid_search(
            vault, state_root, embedder, q.query,
            tags=q.tags, folder=q.folder, limit=limit,
        )
        paths = [h.path for h in hits]
        expect = set(q.expect) - set(gone)
        relevant = (set(q.expect) | set(q.accept)) - set(gone)
        results.append(
            QueryResult(
                q.id, paths,
                first_relevant_rank(paths, relevant),
                {k: recall_at_k(paths, expect, k) for k in ks},
                {k: success_at_k(paths, relevant, k) for k in ks},
                gone,
            )
        )
    scored = [r for r in results if not r.skipped]
    n = len(scored) or 1
    return Scorecard(
        results=results,
        success_at={k: sum(r.success_at[k] for r in scored) / n for k in ks},
        recall_at={k: sum(r.recall_at[k] for r in scored) / n for k in ks},
        mrr=sum(1.0 / r.first_rank for r in scored if r.first_rank) / n,
        skipped=len(results) - len(scored),
    )


def format_table(sc: Scorecard) -> str:
    # Derive k values from the scorecard so custom ks never KeyError.
    kmax = max(sc.recall_at)
    lines = [f"{'id':<28} {'rank':>4} {'r@' + str(kmax):>5}  miss"]
    for r in sc.results:
        if r.skipped:
            lines.append(f"{r.id:<28} {'skip':>4} {'-':>5}  {', '.join(r.missing)}")
            continue
        rank = str(r.first_rank) if r.first_rank else "-"
        lines.append(
            f"{r.id:<28} {rank:>4} {r.recall_at[kmax]:>5.2f}  {', '.join(r.missing)}"
        )
    scored = len(sc.results) - sc.skipped
    agg = "  ".join(
        f"success@{k} {sc.success_at[k]:.2f}" for k in sorted(sc.success_at)
    )
    agg += "  " + "  ".join(
        f"recall@{k} {sc.recall_at[k]:.2f}" for k in sorted(sc.recall_at)
    )
    lines.append("-" * 60)
    lines.append(
        f"queries {scored}  skipped {sc.skipped}  {agg}  MRR {sc.mrr:.2f}"
    )
    return "\n".join(lines)


def to_json(sc: Scorecard) -> dict:
    kmax = max(sc.recall_at)
    d: dict = {f"success_at_{k}": v for k, v in sorted(sc.success_at.items())}
    d.update({f"recall_at_{k}": v for k, v in sorted(sc.recall_at.items())})
    d["mrr"] = sc.mrr
    d["skipped"] = sc.skipped
    d["n_queries"] = len(sc.results)
    d["queries"] = [
        {
            "id": r.id,
            "first_rank": r.first_rank,
            "skipped": r.skipped,
            "recall": r.recall_at.get(kmax),
            "missing": r.missing,
        }
        for r in sc.results
    ]
    return d


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def append_history(
    state_root: str | Path, sc: Scorecard, vault_path, golden_path
) -> Path:
    state = Path(state_root)
    state.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "git": _git_sha(),
        "vault": str(vault_path),
        "golden": str(golden_path),
        **to_json(sc),
    }
    p = state / HISTORY_FILE
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return p


def _make_embedder():
    from .embeddings import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()


def init_live(vault: Vault) -> tuple[Path, bool]:
    raise EvalConfigError("--init-live not implemented yet")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tesseract_mcp.evals",
        description="Golden-query evaluation for hybrid search.",
    )
    p.add_argument("--vault", help="vault root (default: fixture corpus)")
    p.add_argument("--golden", help="golden file (default: evals/golden.yaml)")
    p.add_argument("--live", action="store_true",
                   help="use TESSERACT_VAULT_PATH and Claude/Evals.md "
                        "(takes precedence over --vault/--golden)")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--init-live", action="store_true",
                   help="create Claude/Evals.md template if absent, then exit")
    args = p.parse_args(argv)
    try:
        if args.live or args.init_live:
            root = os.environ.get("TESSERACT_VAULT_PATH")
            if not root:
                raise EvalConfigError(
                    "--live/--init-live require TESSERACT_VAULT_PATH"
                )
            vault_path = Path(root)
            golden_path = vault_path / LIVE_GOLDEN_REL
            lenient = True
        else:
            vault_path = Path(args.vault) if args.vault else FIXTURE_VAULT
            golden_path = Path(args.golden) if args.golden else FIXTURE_GOLDEN
            lenient = False
        vault = Vault(vault_path)
        if args.init_live:
            target, created = init_live(vault)
            print(f"{'created' if created else 'already exists'}: {target}")
            return 0
        queries = load_golden(golden_path)
        sc = run_evals(
            vault, indexer.state_dir(vault.root), _make_embedder(),
            queries, lenient=lenient,
        )
    except (EvalConfigError, VaultError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(to_json(sc), indent=2) if args.as_json else format_table(sc))
    if not args.no_history:
        append_history(indexer.state_dir(vault.root), sc, vault_path, golden_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
