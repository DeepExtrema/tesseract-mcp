"""LLM entity extraction via pluggable CLI backends (codex / claude)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

ENTITY_TYPES = {"person", "organization", "domain", "topic", "project", "source"}
RELATIONS = {"mentions", "works_at", "part_of", "operates_in", "about", "related_to"}

PROMPT_TEMPLATE = """You are an entity-extraction engine for a personal knowledge vault.
Read the note below and extract entities and relationships.

Entity types (use EXACTLY one of): person, organization, domain, topic, project, source.
Relation types (use EXACTLY one of): mentions, works_at, part_of, operates_in, about, related_to.

Reply with ONLY a JSON object, no prose, matching:
{{"entities": [{{"name": str, "type": str, "aliases": [str], "summary": str}}],
  "relations": [{{"from": str, "from_type": str, "rel": str, "to": str, "to_type": str, "evidence": str}}]}}

Rules:
- Extract only significant real-world entities (skip generic words).
- NEVER extract file paths, file names, note titles, or URLs as entities
  (e.g. "README.md", "Claude/Index.md", "DEPLOY.md" are NOT entities).
- Use the most canonical name for each entity and fold variants into aliases
  rather than creating separate entities (e.g. "Oracle VM" and "Oracle VM
  deploy" are ONE entity; put the variant in aliases).
- Choose the type carefully: organization (companies, services, tools like
  CouchDB/Caddy), person (named people), domain (fields/industries), project
  (named initiatives), topic (concepts/themes), source (a cited document,
  paper, interview, or dataset — NOT a filename).
- Summaries are one sentence; evidence is a short quote or paraphrase; every
  relation must connect two entities you extracted.

Note path: {path}
Note content:
---
{content}
---"""


class ExtractorError(Exception):
    """Raised when extraction fails after retry."""


@dataclass
class Extraction:
    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)


def _coerce(raw: dict) -> Extraction:
    """Fold arbitrary extractor output into the fixed vocabularies."""
    entities = []
    for e in raw.get("entities") or []:
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        etype = str(e.get("type") or "").strip().lower()
        if etype not in ENTITY_TYPES:
            etype = "topic"
        aliases = [str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()]
        entities.append(
            {"name": name, "type": etype, "aliases": aliases,
             "summary": str(e.get("summary") or "").strip()}
        )
    relations = []
    for r in raw.get("relations") or []:
        src = str(r.get("from") or "").strip()
        dst = str(r.get("to") or "").strip()
        if not src or not dst:
            continue
        rel = str(r.get("rel") or "").strip().lower()
        if rel not in RELATIONS:
            rel = "related_to"
        from_type = str(r.get("from_type") or "").strip().lower()
        to_type = str(r.get("to_type") or "").strip().lower()
        relations.append(
            {"from": src,
             "from_type": from_type if from_type in ENTITY_TYPES else "topic",
             "rel": rel,
             "to": dst,
             "to_type": to_type if to_type in ENTITY_TYPES else "topic",
             "evidence": str(r.get("evidence") or "").strip()}
        )
    return Extraction(entities, relations)


_ERROR_LINE = re.compile(r"\b(ERROR|FATAL)\b", re.IGNORECASE)


def _stderr_summary(stderr: str | None, cap: int = 300) -> str:
    """The most useful `cap` chars of a failed CLI's stderr.

    CLIs like codex print cosmetic warnings first and the fatal error last,
    so head-truncation blames the wrong cause: prefer the last ERROR/FATAL
    line, fall back to the tail.
    """
    text = (stderr or "").strip()
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        if _ERROR_LINE.search(line):
            return line.strip()[:cap]
    return text[-cap:]


class CliExtractor:
    COMMANDS = {"codex": ["codex", "exec"], "claude": ["claude", "-p"]}

    def __init__(
        self,
        backend: str | None = None,
        timeout: int = 120,
        runner=subprocess.run,
        which=shutil.which,
        model: str | None = None,
    ):
        self.backend = backend or os.environ.get("TESSERACT_EXTRACTOR", "codex")
        if self.backend not in self.COMMANDS:
            raise ExtractorError(f"Unknown backend: {self.backend}")
        self.timeout = timeout
        self._run = runner
        self._which = which
        self.model = model

    def _resolve_cmd(self) -> list[str]:
        exe, *args = self.COMMANDS[self.backend]
        resolved = self._which(exe)
        if not resolved:
            raise ExtractorError(f"{self.backend} CLI not found on PATH")
        low = resolved.lower()
        if low.endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c", resolved, *args]
        elif low.endswith(".ps1"):
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", resolved, *args]
        else:
            cmd = [resolved, *args]
        if self.model and self.backend == "claude":
            cmd += ["--model", self.model]
        return cmd

    def _invoke(self, prompt: str) -> str:
        cmd = self._resolve_cmd()
        try:
            proc = self._run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired as e:
            raise ExtractorError(f"{self.backend} timed out after {self.timeout}s") from e
        except OSError as e:
            raise ExtractorError(f"failed to run {self.backend}: {e}") from e
        if proc.returncode != 0:
            raise ExtractorError(
                f"{self.backend} exited {proc.returncode}: {_stderr_summary(proc.stderr)}"
            )
        return proc.stdout or ""

    @staticmethod
    def _parse(output: str) -> dict:
        import re

        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
        if fence:
            return json.loads(fence.group(1))

        decoder = json.JSONDecoder()
        for i, ch in enumerate(output):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(output[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj

        raise ExtractorError("no JSON object in extractor output")

    def complete_json(self, prompt: str) -> dict:
        """Send any prompt via the CLI backend; return its JSON object reply
        (one repair retry, then ExtractorError)."""
        out = self._invoke(prompt)
        try:
            return self._parse(out)
        except (ExtractorError, json.JSONDecodeError):
            repair = prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            out = self._invoke(repair)
            try:
                return self._parse(out)
            except json.JSONDecodeError as e:
                raise ExtractorError(f"invalid JSON after retry: {e}") from e

    def extract(self, path: str, content: str) -> Extraction:
        prompt = PROMPT_TEMPLATE.format(path=path, content=content)
        return _coerce(self.complete_json(prompt))


def extraction_extractor(backend: str | None = None) -> CliExtractor:
    """Backend for per-note entity extraction (high-volume → cheap model)."""
    return CliExtractor(
        backend=backend,
        model=os.environ.get("TESSERACT_EXTRACT_MODEL", "haiku"),
    )


def consolidation_extractor(backend: str | None = None) -> CliExtractor:
    """Backend for entity dedupe (judgment-heavy, rare → mid-tier model)."""
    return CliExtractor(
        backend=backend,
        model=os.environ.get("TESSERACT_CONSOLIDATE_MODEL", "sonnet"),
    )
