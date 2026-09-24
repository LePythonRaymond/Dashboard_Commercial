"""
Tests for the Notion vs Furious check of "Devis à suivre" and "Devis gagnés".
"""

from datetime import datetime

import pandas as pd

from src.integrations.notion_sync_check import (
    FOLLOWUP_TABLE,
    LOST_TABLE,
    WON_TABLE,
    TableCheck,
    build_checks_html,
    check_followup_table,
    check_lost_table,
    check_notion_tables,
    check_won_table,
    recently_modified_ids,
)


def _text(content):
    return {"type": "text", "text": {"content": content, "link": None}, "plain_text": content}


def _followup_page(devis_id, status, amount=1000.0, date="2026-09-01", title=None):
    return {"id": f"fu-{devis_id}", "properties": {
        "Name": {"type": "title", "title": [_text(title or f"Devis {devis_id}")]},
        "ID Devis": {"type": "rich_text", "rich_text": [_text(devis_id)]},
        "Statut": {"type": "status", "status": {"name": status}},
        "Montant": {"type": "number", "number": amount},
        "Date": {"type": "date", "date": {"start": date}},
    }}


def _won_page(devis_id, status="Gagnés en cours", amount=1000.0, date="2026-09-01", parent_page=None):
    page = {"id": f"won-{devis_id}", "properties": {
        "Nom": {"type": "title", "title": [_text(f"Devis {devis_id}")]},
        "ID Devis": {"type": "rich_text", "rich_text": [_text(devis_id)]},
        "Statut Furious": {"type": "select", "select": {"name": status}},
        "Montant HT": {"type": "number", "number": amount},
        "Date gagné": {"type": "date", "date": {"start": date}},
    }}
    if parent_page is not None:
        page["properties"]["Devis parent"] = {"type": "relation", "relation": [{"id": parent_page}] if parent_page else [],
                                              "has_more": False}
    return page


def _lost_page(devis_id, reasons=(), amount=1000.0, date="2026-09-01", status="Perdu"):
    return {"id": f"lost-{devis_id}", "properties": {
        "Nom": {"type": "title", "title": [_text(f"Devis {devis_id}")]},
        "ID Devis": {"type": "rich_text", "rich_text": [_text(devis_id)]},
        "Statut Furious": {"type": "select", "select": {"name": status}},
        "Montant HT": {"type": "number", "number": amount},
        "Date perdu": {"type": "date", "date": {"start": date}},
        "Motif de perte": {"type": "multi_select", "multi_select": [{"name": r} for r in reasons]},
    }}


def _devis(devis_id, statut, amount=1000.0, date="2026-09-01", updated="2026-09-20"):
    return {"id": devis_id, "title": f"Devis {devis_id}", "statut": statut, "statut_clean": statut.lower(),
            "amount": amount, "date": pd.Timestamp(date), "last_updated_at": pd.Timestamp(updated)}


def test_followup_table_in_sync_is_ok():
    df = pd.DataFrame([_devis("1", "Brief"), _devis("2", "Envoyée(s) attente réponse"), _devis("3", "Perdu")])
    pages = [_followup_page("1", "brief"), _followup_page("2", "envoyée(s) attente réponse"),
             _followup_page("3", "Perdu")]  # lost devis, relabelled: hidden by the views, fine

    check = check_followup_table(df, pages)

    assert check.ok and (check.expected, check.shown) == (2, 2)


def test_followup_table_reports_missing_extra_mismatch_and_duplicates():
    df = pd.DataFrame([
        _devis("1", "Brief"),                        # no page at all -> missing
        _devis("2", "En cours"),                     # page still shows "Perdu" -> missing (hidden)
        _devis("3", "Brief", amount=2500.0),         # amount changed -> mismatch
        _devis("4", "Gagnés en cours"),              # page still waiting -> extra
        _devis("6", "Brief"),                        # duplicated page
    ])
    pages = [_followup_page("2", "Perdu"), _followup_page("3", "brief", amount=1000.0),
             _followup_page("4", "brief"), _followup_page("5", "en cours"),   # 5: gone from Furious -> extra
             _followup_page("6", "brief"), _followup_page("6", "brief")]

    check = check_followup_table(df, pages)

    assert [m["id"] for m in check.missing] == ["1", "2"]
    assert check.missing[1]["notion"] == "Perdu"
    assert [(m["id"], m["field"], m["notion"], m["furious"]) for m in check.mismatches] == [("3", "Montant", 1000.0, 2500.0)]
    assert {e["id"]: e["furious"] for e in check.extra} == {"4": "Gagnés en cours", "5": "absent de Furious"}
    assert check.duplicates == ["6"] and check.drift and not check.ok


def test_devis_modified_today_are_skipped():
    df = pd.DataFrame([_devis("1", "Brief", updated="2026-09-24"), _devis("2", "Brief")])
    skip = recently_modified_ids(df, today=datetime(2026, 9, 24, 7, 30))

    check = check_followup_table(df, [_followup_page("2", "brief")], skip_ids=skip)

    assert skip == {"1"} and check.ok and check.skipped_recent == 1


def test_won_table_checks_the_window_only():
    start = pd.Timestamp("2025-09-24")
    items = [_devis("10", "Gagnés en cours", amount=5000.0, date="2025-10-01"),
             _devis("11", "Gagnés et finis", date="2026-03-01"),
             _devis("12_AV3", "Gagnés en cours", amount=300.0, date="2026-04-01")]
    status_by_id = {"10": "Gagnés en cours", "11": "Gagnés et finis", "12_AV3": "Gagnés en cours",
                    "13": "Perdu", "9": "Gagnés et finis"}
    pages = [
        _won_page("10", amount=4000.0, date="2025-10-01"),   # amount differs -> mismatch
        _won_page("11", status="Gagnés et finis", date="2026-03-01"),
        _won_page("13", date="2026-05-01"),                  # lost in Furious, still "won" here -> extra
        _won_page("9", status="Gagnés et finis", date="2025-01-15"),  # before the window: history, ignored
        _won_page("14", status="Perdu", date="2026-05-01"),  # relabelled page: not shown as won, ignored
    ]

    check = check_won_table(items, status_by_id, pages, start)

    assert (check.expected, check.shown) == (3, 3)
    assert [m["id"] for m in check.missing] == ["12_AV3"]
    assert [(m["id"], m["field"]) for m in check.mismatches] == [("10", "Montant HT")]
    assert [(e["id"], e["furious"]) for e in check.extra] == [("13", "Perdu")]


def test_won_check_verifies_the_devis_each_avenant_sits_under():
    start = pd.Timestamp("2025-09-24")
    items = [
        _devis("20", "Gagnés en cours", amount=5000.0, date="2025-11-10"),
        dict(_devis("7", "Gagnés en cours", amount=9000.0, date="2024-03-01"), context=True),  # old parent
        dict(_devis("20_AV1", "Gagnés en cours", amount=300.0, date="2025-12-01"), parent_id="20"),
        dict(_devis("7_AV2", "Gagnés en cours", amount=400.0, date="2026-01-05"), parent_id="7"),
    ]
    pages = [
        _won_page("20", amount=5000.0, date="2025-11-10"),
        _won_page("7", amount=9000.0, date="2024-03-01"),
        _won_page("20_AV1", amount=300.0, date="2025-12-01", parent_page="won-20"),
        _won_page("7_AV2", amount=400.0, date="2026-01-05", parent_page=""),   # not under its devis yet
    ]

    check = check_won_table(items, {}, pages, start)

    assert (check.expected, check.shown) == (3, 3)   # the old parent exists but is not in the window
    assert [(m["id"], m["field"], m["notion"], m["furious"]) for m in check.mismatches] == [
        ("7_AV2", "Devis parent", None, "won7")]
    assert not check.missing and not check.extra


def test_lost_table_check_compares_reasons():
    start = pd.Timestamp("2025-09-24")
    items = [dict(_devis("30", "Perdu", date="2026-06-01"), lost_reasons=["Budget trop élevé", "Autre"]),
             dict(_devis("31", "Perdu", date="2026-06-02"), lost_reasons=[])]
    pages = [_lost_page("30", reasons=["Autre"], date="2026-06-01"),
             _lost_page("32", reasons=[], date="2026-06-03")]            # reopened in Furious

    check = check_lost_table(items, {"32": "Brief"}, pages, start)

    assert [m["id"] for m in check.missing] == ["31"]
    assert [(m["field"], m["notion"], m["furious"]) for m in check.mismatches] == [
        ("Motif de perte", "Autre", "Autre, Budget trop élevé")]
    assert [(e["id"], e["furious"]) for e in check.extra] == [("32", "Brief")]


def test_check_notion_tables_never_raises():
    df = pd.DataFrame([_devis("1", "Brief"), _devis("2", "Gagnés en cours", date="2026-02-01"),
                       _devis("3", "Perdu", date="2026-03-01")])
    df["title"] = ["Devis 1", "Devis 2", "Devis 3"]

    def broken_loader():
        raise RuntimeError("Notion is down")

    checks = check_notion_tables(df, pd.DataFrame(), pd.Timestamp("2025-09-24"), lost_tags={"3": "Autre"},
                                 followup_loader=broken_loader,
                                 won_loader=lambda: [_won_page("2", date="2026-02-01", amount=1000.0)],
                                 lost_loader=lambda: [_lost_page("3", reasons=["Autre"], date="2026-03-01")])

    by_table = {c.table: c for c in checks}
    assert by_table[FOLLOWUP_TABLE].error.startswith("RuntimeError: Notion is down")
    assert by_table[WON_TABLE].ok and by_table[LOST_TABLE].ok

    unchecked = check_notion_tables(df, None, pd.Timestamp("2025-09-24"), lost_tags=None,
                                    followup_loader=lambda: [], won_loader=lambda: [], lost_loader=lambda: [],
                                    tables=(WON_TABLE, LOST_TABLE))
    assert [c.table for c in unchecked] == [WON_TABLE, LOST_TABLE]
    assert "avenants" in unchecked[0].error and "loss reasons" in unchecked[1].error


def test_email_section_lists_problems_in_french():
    check = TableCheck(FOLLOWUP_TABLE, expected=2, shown=1,
                       missing=[{"id": "1", "title": "Devis <1>", "furious": "Brief", "notion": "absent"}])
    html = build_checks_html([check, TableCheck(WON_TABLE, error="Notion is down")])

    assert "⚠️ écart" in html and "Absents de Notion" in html and "Devis &lt;1&gt;" in html
    assert "contrôle impossible" in html
    assert build_checks_html([]) == ""
