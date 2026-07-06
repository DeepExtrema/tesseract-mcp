# Routing Rules + Retention Policy — Design Spec

**Date:** 2026-07-06
**Status:** Approved by Taimoor ("hit it")
**Source:** Gap analysis against "Every Level of a Claude Second Brain Explained"
(youtu.be/DTCyvo6cC54) — adopt Level-1 routing, retention philosophy, decision
log. Explicitly NOT adopted: vector search, always-on ingestion, grill-me skill.

## 1. Vault-root agent guides (routing layer)

New template `vault/root-guide.md` in the repo, installed to the vault root as
BOTH `CLAUDE.md` and `AGENTS.md` (identical content — Claude Code reads the
former automatically when the vault is the working directory, e.g. inside the
Claudian Obsidian plugin; Codex reads the latter). Content: what the vault is,
routing rules (constitution first, Claude/ subtree map, prefer tesseract MCP
tools, outside-Claude/ is read-only-unless-asked), plus an HTML comment showing
how to add a routing line per new top-level content folder.

## 2. Retention rule (constitution)

New `## Retention` section in `vault/constitution.md`: distinguish evergreen
context (Sessions, Concepts, Decisions — keep; ask "will this matter in a
year?") from transient connections (Inbox — prunable anytime; graduate items
worth keeping into Concepts/Tasks/Decisions).

## 3. Decision log

`Claude/Decisions.md`, append-only, one line per decision:
`- YYYY-MM-DD — <decision> ([[session note]])`. Seeded by the installer;
listed in the constitution's Structure section. Agents append when a session
makes a real decision (also still narrated in the session note).

## 4. Installer + tests

`scripts/install_conventions.py` additionally installs (only if missing,
idempotent): vault-root `CLAUDE.md`, vault-root `AGENTS.md` (both copied from
`vault/root-guide.md`), and `Claude/Decisions.md` seed. `install()` return
list grows accordingly; tests updated (structure count 5 → 8).

## Out of scope

Embeddings/vector DB, scheduled always-on ingestion (CLI exists, stays
manual), interview/grill-me skills, any new MCP tools.
