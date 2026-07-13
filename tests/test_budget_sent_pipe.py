"""Tests for the budget sent-pipe hygiene: dedup, carryover freshness, store."""

from datetime import date, datetime, timedelta

import pandas as pd

from src.integrations.budget_export import (
    dedupe_sent_pipe,
    drop_stale_sent_carryover,
    filter_carryover_by_pending,
)
from src.integrations.pending_ids_store import read_pending_ids, write_pending_ids


def _df(rows):
    return pd.DataFrame(rows)


def test_dedupe_keeps_live_year_copy():
    df = _df([
        {"id": "100", "signed_year": 2025, "Montant Total 2026": 100_000},
        {"id": "100", "signed_year": 2026, "Montant Total 2026": 116_000},
        {"id": "200", "signed_year": 2025, "Montant Total 2026": 50_000},
    ])
    out = dedupe_sent_pipe(df)
    assert len(out) == 2
    kept = out[out["id"] == "100"].iloc[0]
    assert kept["signed_year"] == 2026
    assert kept["Montant Total 2026"] == 116_000


def test_dedupe_without_signed_year_keeps_one():
    df = _df([{"id": "1", "x": "a"}, {"id": "1", "x": "b"}])
    assert len(dedupe_sent_pipe(df)) == 1


def test_carryover_dropped_when_no_longer_pending():
    df = _df([
        {"id": "old-lost", "signed_year": 2025, "amount": 10_000},
        {"id": "old-pending", "signed_year": 2025, "amount": 20_000},
        {"id": "current", "signed_year": 2026, "amount": 30_000},
    ])
    out = filter_carryover_by_pending(df, 2026, pending_ids={"old-pending"})
    ids = set(out["id"])
    # lost 2025 carryover dropped; still-pending carryover kept; current-year rows
    # untouched even though "current" is not in the pending set (live sheets are
    # already fresh — not this filter's job).
    assert ids == {"old-pending", "current"}


def test_carryover_kept_when_store_unavailable():
    df = _df([{"id": "old", "signed_year": 2025, "amount": 1}])
    out = filter_carryover_by_pending(df, 2026, pending_ids=None)
    assert len(out) == 1


def test_pending_ids_store_roundtrip(tmp_path):
    p = tmp_path / "pending_ids.json"
    write_pending_ids(p, {"123", "456"})
    assert read_pending_ids(p) == {"123", "456"}


def test_pending_ids_store_stale_returns_none(tmp_path):
    p = tmp_path / "pending_ids.json"
    write_pending_ids(p, {"1"})
    # Rewrite fetched_at to 3 days ago
    import json
    payload = json.loads(p.read_text())
    payload["fetched_at"] = (datetime.now() - timedelta(days=3)).isoformat()
    p.write_text(json.dumps(payload))
    assert read_pending_ids(p) is None


def test_full_sent_pipe_hygiene_chain():
    """dedup → pending filter → stale pruning, matching the app.py wiring."""
    today = date(2026, 7, 11)
    df = _df([
        # duplicated devis: frozen 2025 copy + live 2026 copy → keep 2026
        {"id": "dup", "signed_year": 2025, "projet_start": "2026-10-01", "Montant Total 2026": 90_000},
        {"id": "dup", "signed_year": 2026, "projet_start": "2026-10-01", "Montant Total 2026": 95_000},
        # 2025 carryover, lost since (not in pending set) → dropped by filter
        {"id": "lost25", "signed_year": 2025, "projet_start": "2026-11-01", "Montant Total 2026": 40_000},
        # 2025 carryover, still pending, future start → kept
        {"id": "live25", "signed_year": 2025, "projet_start": "2026-12-01", "Montant Total 2026": 30_000},
        # 2025 carryover, still pending but overdue start → dropped by pruning
        {"id": "late25", "signed_year": 2025, "projet_start": "2026-01-01", "Montant Total 2026": 20_000},
    ])
    out = dedupe_sent_pipe(df)
    out = filter_carryover_by_pending(out, 2026, pending_ids={"dup", "live25", "late25"})
    out = drop_stale_sent_carryover(out, 2026, today)
    assert set(out["id"]) == {"dup", "live25"}
    assert out.loc[out["id"] == "dup", "Montant Total 2026"].iloc[0] == 95_000
