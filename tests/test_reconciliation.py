"""Tests for the Envoyé/Signé reconciliation guard."""

import pandas as pd
import pytest

from src.processing.reconciliation import reconcile, reconcile_view, DEFAULT_MIN_AMOUNT_EUR


class FakeSheets:
    """Minimal GoogleSheetsClient stand-in: {view_type: {sheet_name: DataFrame}}."""

    def __init__(self, sheets):
        self._sheets = sheets

    def list_worksheets(self, view_type=None, year=None):
        return list(self._sheets.get(view_type, {}).keys())

    def read_worksheet(self, name, view_type=None, year=None):
        return self._sheets.get(view_type, {}).get(name, pd.DataFrame())


def _devis(id, amount, statut_clean, date, **extra):
    row = {
        "id": id,
        "amount": amount,
        "statut_clean": statut_clean,
        "date": pd.Timestamp(date),
        "title": extra.get("title", f"devis {id}"),
        "assigned_to": extra.get("assigned_to", "clemence"),
        "cf_typologie_de_devis": extra.get("typologie", "Travaux DV"),
        "statut": extra.get("statut", statut_clean),
        "date_effective_won": pd.Timestamp(extra["won"]) if extra.get("won") else pd.NaT,
    }
    return row


def _sheet_df(ids_amounts):
    return pd.DataFrame([{"id": i, "amount": a} for i, a in ids_amounts])


def test_missing_pending_devis_is_flagged():
    # Two pending devis dated 2026; only one made it into the sheets.
    df = pd.DataFrame([
        _devis("100", 450000, "envoyée(s) attente réponse", "2026-02-28"),
        _devis("200", 60000, "brief", "2026-06-30"),
    ])
    sheets = FakeSheets({"envoye": {"Envoyé Fevrier 2026": _sheet_df([("100", 450000)])}})
    rc = reconcile_view(df, sheets, "envoye", 2026)
    assert rc.furious_count == 2
    assert rc.sheet_count == 1
    assert [m["id"] for m in rc.missing] == ["200"]
    assert rc.significant_missing(DEFAULT_MIN_AMOUNT_EUR)[0]["amount"] == 60000


def test_created_at_does_not_save_a_missing_devis():
    # Regression for the real bug: a devis DATED 2026 belongs in the 2026 truth even
    # though (historically) it was filed by created_at elsewhere. If it's absent from
    # the sheets, it must be flagged regardless of any created_at value.
    df = pd.DataFrame([
        {**_devis("EY", 450000, "envoyée(s) attente réponse", "2026-02-28"),
         "created_at": pd.Timestamp("2024-12-10")},
    ])
    sheets = FakeSheets({"envoye": {"Envoyé Janvier 2026": _sheet_df([])}})
    rc = reconcile_view(df, sheets, "envoye", 2026)
    assert [m["id"] for m in rc.missing] == ["EY"]


def test_no_drift_when_everything_present():
    df = pd.DataFrame([
        _devis("1", 10000, "brief", "2026-01-10"),
        _devis("2", 20000, "en cours", "2026-03-10"),
    ])
    sheets = FakeSheets({
        "envoye": {"Envoyé 2026": _sheet_df([("1", 10000), ("2", 20000)])},
        "signe": {},
    })
    report = reconcile(df, sheets, 2026)
    assert report["alert"] is False
    assert report["views"]["envoye"]["missing_count"] == 0


def test_manual_rows_excluded_from_both_sides():
    df = pd.DataFrame([_devis("MAN-abc", 99999, "brief", "2026-05-01")])
    sheets = FakeSheets({"envoye": {"Envoyé Mai 2026": _sheet_df([("MAN-abc", 99999)])}})
    rc = reconcile_view(df, sheets, "envoye", 2026)
    assert rc.furious_count == 0 and rc.sheet_count == 0
    assert rc.missing == [] and rc.extra == []


def test_small_amount_missing_is_not_significant():
    df = pd.DataFrame([_devis("z", 1, "en cours", "2026-04-01")])
    sheets = FakeSheets({"envoye": {"Envoyé Avril 2026": _sheet_df([])}})
    report = reconcile(df, sheets, 2026, min_amount=500)
    assert report["views"]["envoye"]["missing_count"] == 1
    assert report["alert"] is False  # below the €500 floor


def test_signe_coverage_missing_won_devis():
    # A won devis signed in 2026 absent from the Signé sheets is flagged.
    df = pd.DataFrame([
        {**_devis("w1", 80000, "gagnés en cours", "2025-12-20"), "date_effective_won": pd.Timestamp("2026-01-15")},
    ])
    sheets = FakeSheets({"signe": {"Signé Janvier 2026": _sheet_df([])}})
    rc = reconcile_view(df, sheets, "signe", 2026)
    assert [m["id"] for m in rc.missing] == ["w1"]


def test_small_churn_does_not_alert():
    # 2 missing significant devis (~28k€) — below both the count (5) and gross (100k)
    # thresholds → reported but NOT an alert (normal daily CRM churn).
    df = pd.DataFrame([
        _devis("a", 22700, "envoyée(s) attente réponse", "2026-05-01"),
        _devis("b", 5387, "envoyée(s) attente réponse", "2026-05-01"),
    ])
    sheets = FakeSheets({"envoye": {"Envoyé Mai 2026": _sheet_df([])}})
    report = reconcile(df, sheets, 2026)
    assert report["views"]["envoye"]["missing_significant_count"] == 2
    assert report["alert"] is False


def test_systemic_gap_alerts_on_count():
    # 6 missing significant devis → over the count threshold → alert.
    df = pd.DataFrame([
        _devis(str(i), 5000, "brief", "2026-03-01") for i in range(6)
    ])
    sheets = FakeSheets({"envoye": {"Envoyé Mars 2026": _sheet_df([])}})
    report = reconcile(df, sheets, 2026)
    assert report["alert"] is True


def test_systemic_gap_alerts_on_gross():
    # 1 missing devis but 450k€ → over the gross threshold → alert.
    df = pd.DataFrame([_devis("big", 450000, "envoyée(s) attente réponse", "2026-02-28")])
    sheets = FakeSheets({"envoye": {"Envoyé Fevrier 2026": _sheet_df([])}})
    report = reconcile(df, sheets, 2026)
    assert report["alert"] is True


def test_signe_extra_is_informational_not_alert():
    # Cumulative Signé sheets keep earlier rows; an extra won devis already in the
    # sheet but not in this year's truth must not raise an alert.
    df = pd.DataFrame([_devis("won2026", 50000, "gagnés et finis", "2026-02-01", won="2026-02-01")])
    sheets = FakeSheets({"signe": {"Signé Fevrier 2026": _sheet_df([("won2026", 50000), ("old", 12345)])}})
    report = reconcile(df, sheets, 2026)
    assert report["views"]["signe"]["extra_count"] == 1
    assert report["alert"] is False
