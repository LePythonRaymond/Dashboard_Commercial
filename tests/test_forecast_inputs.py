"""Typed values found in the sheet (build.detect_typed) and the start-up from the legacy workbook."""

from datetime import date

import pandas as pd

from src.forecast.build import FinanceProject, Params, SheetState, assemble, detect_typed, seed_from_legacy, window
from src.forecast.sheet import manual_fields, normalize_typed
from src.forecast.sources import Legacy, LegacyRow


def _state(visible, rendered, inputs=None):
    params = Params("2026-09", 2, None, {}, {})
    return SheetState(params, [], {}, {}, inputs or {}, rendered, {"Travaux": visible})


FIELDS = {"Travaux": manual_fields("Travaux", ["2026-10", "2026-11"])}


def test_a_changed_cell_becomes_a_typed_value_and_an_emptied_one_goes_back_to_automatic():
    rendered = {"P1": {"label": "Chantier A", "2026-10": 1000.0, "2026-11": 2000.0, "kv": 1.4}}
    visible = [{"ref": "P1", "label": "Chantier A", "2026-10": 5000, "2026-11": 2000.0, "kv": ""}]
    inputs, notes = detect_typed(_state(visible, rendered, {"P1": {"kv": 1.6}}), FIELDS, normalize_typed)
    assert inputs == {"P1": {"2026-10": 5000.0}}  # October typed, KV cleared
    assert notes == []


def test_unchanged_cells_keep_the_typed_values_already_recorded():
    rendered = {"P1": {"label": "A", "2026-10": 5000.0}}
    visible = [{"ref": "P1", "label": "A", "2026-10": 5000.0}]
    inputs, _ = detect_typed(_state(visible, rendered, {"P1": {"2026-10": 5000.0}}), FIELDS, normalize_typed)
    assert inputs == {"P1": {"2026-10": 5000.0}}


def test_a_row_whose_label_does_not_match_its_ref_is_ignored():
    rendered = {"P1": {"label": "Chantier A", "2026-10": 1000.0}}
    visible = [{"ref": "P1", "label": "Chantier B", "2026-10": 9999}]  # partial sort moved the cells
    inputs, notes = detect_typed(_state(visible, rendered), FIELDS, normalize_typed)
    assert inputs == {}
    assert "tri partiel" in notes[0]


def test_a_line_typed_under_the_table_becomes_a_manual_line():
    # 46 402 = 15/01/2027 as a date serial; "30" typed in a percent cell means 30 %
    visible = [{"ref": "", "label": "Opportunité Z", "cat": "Pipe", "proba": 30, "2026-11": 12000, "stop": 46402}]
    inputs, notes = detect_typed(_state(visible, {}), FIELDS, normalize_typed)
    assert inputs == {"M1": {"label": "Opportunité Z", "cat": "Pipe", "proba": 0.3, "2026-11": 12000.0,
                             "stop": "2027-01", "_pole": "Travaux"}}
    assert "M1" in notes[0]


def test_a_deleted_manual_line_is_forgotten():
    rendered = {"M2": {"label": "Ajustement"}}
    inputs, notes = detect_typed(_state([], rendered, {"M2": {"_pole": "Travaux", "label": "Ajustement"}}), FIELDS, normalize_typed)
    assert inputs == {}
    assert "M2" in notes[0]


def test_window_is_18_months_from_the_current_month():
    grid, open_from = window("2026-09", date(2026, 10, 26))
    assert grid[0] == "2026-10" and grid[-1] == "2028-03" and len(grid) == 18 and open_from == "2026-10"
    grid, open_from = window("2026-08", date(2026, 10, 26))
    assert grid[0] == "2026-09" and open_from == "2026-10"  # September not closed yet: shown, not refilled


def test_start_up_reproduces_the_workbook_months_and_keeps_new_projects():
    legacy = Legacy(
        rows=[LegacyRow("Travaux", "1081", "Farman", "ouvert", "Mathieu", 410000, 34886, 262578, 1.4,
                        {"2026-10": 56268, "2026-11": 56268, "2026-12": 0.0})],
        pl_report={}, pl_september={}, objectives={}, kv={"Travaux Direct": 1.4},
    )
    finance, inputs, _ = seed_from_legacy(legacy, pd.DataFrame(), "2026-09")
    assert finance[0].produced == 262578 and inputs["P1081"]["mai"] == 34886
    projects = pd.DataFrame([
        {"id": "1081", "title": "(T) Farman", "total_amount": 410000.0, "start_date": "2026-02-26", "end_date": "2026-10-30",
         "created_at": "2026-02-26", "project_manager": "mathieu", "cf_bu": "TRAVAUX", "typologies": ["Travaux Direct"]},
        {"id": "1400", "title": "(DV) Nouveau", "total_amount": 9000.0, "start_date": "2026-11-01", "end_date": "2026-12-31",
         "created_at": "2026-10-20", "project_manager": "alix", "cf_bu": "TRAVAUX", "typologies": ["Travaux DV"]},
    ])
    params = Params("2026-09", 2, date(2026, 10, 1), {"Travaux Direct": 1.4, "Travaux DV": 1.7}, {})
    state = SheetState(params, finance, {}, {}, inputs, {}, {})
    out = assemble(state, projects, pd.DataFrame(), [], date(2026, 10, 26))
    rows = {r.ref: s for r, s in out.rows["Travaux"]}
    farman = rows["P1081"]
    assert farman.months["2026-10"] == 56268 and farman.months["2026-12"] == 0.0
    # 410 000 - 34 886 - 262 578 = 112 536 = 2 x 56 268: nothing left for 2027
    assert sum(v for m, v in farman.months.items() if m.startswith("2027")) == 0
    new = rows["P1400"]
    assert new.months["2026-11"] == 4500 and new.months["2026-12"] == 4500
    assert any(f.number == "1400" for f in out.finance)


def test_a_won_devis_hands_its_typed_months_to_its_project():
    projects = pd.DataFrame([
        {"id": "1500", "title": "(DV) Gagné", "total_amount": 20000.0, "start_date": "2026-11-01", "end_date": "2027-01-31",
         "created_at": "2026-10-25", "project_manager": "alix", "cf_bu": "TRAVAUX", "typologies": ["Travaux DV"],
         "proposal_id": "263999"},
    ])
    params = Params("2026-09", 2, date(2026, 10, 1), {"Travaux DV": 1.7}, {})
    inputs = {"D263999": {"2026-12": 15000.0, "proba": 0.6, "comment": "livraison en décembre"}}
    state = SheetState(params, [], {}, {}, inputs, {}, {})
    out = assemble(state, projects, pd.DataFrame(), [], date(2026, 10, 26))
    row = {r.ref: s for r, s in out.rows["Travaux"]}["P1500"]
    assert row.months["2026-12"] == 15000 and row.comment == "livraison en décembre"
    assert "D263999" not in out.inputs and "proba" not in out.inputs["P1500"]
    assert any("263999" in n for n in out.notes)


def test_contract_references_are_readable_and_unique():
    from src.forecast.build import site_refs
    from src.forecast.maintenance import Site

    sites = [
        Site("aaaa-1111", "Monolithe (EXT)", 1, furious_id="409", int_ext="EXT"),
        Site("bbbb-2222", "Monolithe (INT)", 1, furious_id="409", int_ext="INT"),
        Site("cccc-3333", "Clever", 1, furious_id="1089 + 1090 + 1091"),
        Site("dddd-4444ab12cd", "Sans numéro", 1),
    ]
    refs = site_refs(sites)
    assert refs == {"aaaa-1111": "S409-EXT", "bbbb-2222": "S409-INT", "cccc-3333": "S1089-1090-1091",
                    "dddd-4444ab12cd": "Sab12cd"}


def test_quarters_and_type_labels():
    from src.forecast.layout import quarters
    from src.forecast.model import Row
    from src.forecast.sheet import type_label

    assert quarters(["2026-11", "2026-12", "2027-01"]) == [("q:2026-4", "T4 2026", ["2026-11", "2026-12"]),
                                                          ("q:2027-1", "T1 2027", ["2027-01"])]
    ts = Row(ref="T1", pole="Maintenance", category="Signé", kind="TS", label="x")
    devis_ts = Row(ref="D1", pole="Maintenance", category="Pipe", kind="Devis", label="x", typologie="Maintenance TS (+DT)")
    assert type_label(ts, "Signé") == "TS signé" and type_label(devis_ts, "Pipe") == "Devis TS en attente"


def test_a_closed_project_shows_only_its_typed_months():
    # Closed by the finance, with a last amount typed in September: only that amount counts.
    finance = [FinanceProject("Conception", "276", "Smartseille 2", 12324.64, "2026-08", "fermé", 60225.0),
               FinanceProject("Conception", "277", "Fini sans montant", 5000.0, "2026-08", "fermé", 5000.0)]
    params = Params("2026-08", 2, None, {}, {})
    state = SheetState(params, finance, {}, {}, {"P276": {"2026-09": 4675.0}}, {}, {})
    out = assemble(state, pd.DataFrame(), pd.DataFrame(), [], date(2026, 10, 26))
    rows = {r.ref: s for r, s in out.rows["Conception"]}
    assert "P277" not in rows  # closed and nothing typed: not listed
    smart = rows["P276"]
    assert smart.months["2026-09"] == 4675 and smart.remaining == 4675
    assert sum(v for m, v in smart.months.items() if m != "2026-09") == 0 and smart.gap == 0
