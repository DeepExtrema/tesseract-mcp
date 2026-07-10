"""Tests for the Librarian caretaker loop."""

from datetime import datetime, timedelta

import pytest

from tesseract_mcp import librarian
from tesseract_mcp.vault import Vault

NOW = datetime(2026, 7, 9, 12, 0, 0)


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def embed_batch(self, texts):
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture(autouse=True)
def _no_model_downloads(monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)


def _throttle_state(baseline: int, last_pass: datetime) -> dict:
    return {"consolidation": {"entities_at_last_pass": baseline,
                              "last_pass": last_pass.strftime(librarian.TS_FMT),
                              "pending_proposals": []}}


def test_constants_match_spec():
    assert librarian.CONSOLIDATE_MIN_NEW_ENTITIES == 15
    assert librarian.CONSOLIDATE_MAX_AGE_DAYS == 14


def test_load_state_default_when_missing(vault):
    state = librarian.load_state(vault)
    assert state["last_sweep"] is None
    assert state["consolidation"] == {}


def test_state_roundtrip(vault):
    state = librarian.load_state(vault)
    state["last_sweep"] = "2026-07-09 12:00:00"
    librarian.save_state(vault, state)
    assert librarian.load_state(vault)["last_sweep"] == "2026-07-09 12:00:00"


def test_first_pass_runs_when_entities_exist():
    due, reason = librarian.should_consolidate({"consolidation": {}}, 3, NOW)
    assert due
    assert reason == "first pass"


def test_no_entities_never_runs():
    due, _ = librarian.should_consolidate({"consolidation": {}}, 0, NOW)
    assert not due


def test_14_new_entities_skips():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 24, NOW + timedelta(days=1))
    assert not due


def test_15_new_entities_runs():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 25, NOW + timedelta(days=1))
    assert due


def test_age_trigger_requires_a_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 10, NOW + timedelta(days=20))
    assert not due


def test_age_trigger_fires_at_14_days_with_one_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 11, NOW + timedelta(days=14))
    assert due
