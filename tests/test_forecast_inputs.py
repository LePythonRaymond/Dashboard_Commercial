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


def test_contract_references_follow_the_number_in_the_title():
    from src.forecast.build import site_refs
    from src.forecast.maintenance import Site

    sites = [
        Site("p1-000001", "Cogéva - 1116 (EXT)", 1, furious_id="1119", int_ext="EXT"),  # wrong ID in Notion
        Site("p2-000002", "Green Corner - 1119 (EXT)", 1, furious_id="1119", int_ext="EXT"),
        Site("p3-000003", "INSEE - 1044 (EXT)", 1, int_ext="EXT"),  # ID furious empty
        Site("p4-000004", "Capital 8 - 721 (EXT)", 1, furious_id="721", int_ext="EXT"),
        Site("p5-000005", "Capital 8 - 721 (Maraîchage)", 1, furious_id="721", int_ext="EXT"),
        Site("p6-000006", "BOETIE WONDERLAND - 209 (EXT)", 1, furious_id="209", int_ext="EXT"),
        Site("p7-000007", "ROYALE WONDERLAND - 209 (EXT)", 1, furious_id="209", int_ext="EXT"),
    ]
    assert site_refs(sites) == {
        "p1-000001": "S1116", "p2-000002": "S1119", "p3-000003": "S1044", "p4-000004": "S721-EXT",
        "p5-000005": "S721-Maraichage", "p6-000006": "S209-EXT-BOETIE", "p7-000007": "S209-EXT-ROYALE",
    }


def test_summary_headers_notes_and_one_chart_per_pole():
    from datetime import datetime

    from src.forecast import months as M
    from src.forecast.layout import col_letter
    from src.forecast.sheet import CHART_BLOCK, CHART_COL, CHART_ROW, YEARS_FIRST, render_summary

    params = Params("2026-08", 2, None, {}, {("Travaux", 2026): (3500000.0, 1.47, {})}, {("Travaux", 2026): 23})
    pl = {"Travaux": {"report": 1859642.0}, "Conception": {"report": 423060.0}, "Maintenance": {"report": 941984.0}}
    columns = {"report": "B", **{m: col_letter(2 + i) for i, m in enumerate(M.span("2026-01", "2027-12"))}}
    state = SheetState(params, [], pl, {}, {}, {}, {}, columns)
    out = assemble(state, pd.DataFrame(), pd.DataFrame(), [], date(2026, 10, 26))
    values, formulas, styled = render_summary(out, state, datetime(2026, 10, 26, 8, 0))
    f = dict(formulas)
    assert values[3][1] == "Réalisé compta (janv. à août 2026)" and values[3][4] == "Total prévu fin 2026 (atterrissage)"
    assert values[YEARS_FIRST - 1][1] == "Cumul janv. à août"  # was "Report"
    assert styled["notes"] == []  # no hover notes anywhere (asked on 2026-10-26)
    assert values[1] == [""]  # no explanatory sentence under the title (asked on 2026-10-27)
    assert values[3][8] == "Manque pour l'objectif (sans compter le pipe)"  # column I, was "(hors pipe)"
    from src.forecast.sheet import STATUS_COL
    assert values[3][STATUS_COL:STATUS_COL + 2] == ["Contrats d'entretien", "Nombre"]
    assert [values[4 + i][STATUS_COL] for i in range(4)] == ["Safe", "À risque", "Mort", "Total"]
    assert f[f"{col_letter(STATUS_COL + 1)}5"].startswith("=COUNTIF('Maintenance contrats'!")
    for b, name in enumerate(["Total", "Travaux", "Conception", "Maintenance"]):
        c0 = CHART_COL + CHART_BLOCK * b
        assert values[CHART_ROW - 1][c0:c0 + 5] == ["Mois", "Réalisé", "Signé", "Pipe pondéré", "Objectif"], name
        assert values[CHART_ROW][c0] == "Moy. janv. à août", name  # the months known only as a total, as one average bar
        assert f[f"{col_letter(c0 + 1)}{CHART_ROW + 1}"].endswith("/8"), name
        assert values[CHART_ROW + 1][c0] == "sept. 2026", name



def test_contract_status_from_notion_etat_contrat():
    from src.forecast.maintenance import Site, contract_checks, contract_health

    today = date(2026, 10, 28)
    # The status is the account manager's "État contrat" in Notion (decided on 2026-10-28); dates only add signals.
    safe = Site("p1", "Capital 8 - 721 (EXT)", 38800, start=date(2024, 7, 1), end=date(2050, 12, 31), etat="Safe")
    assert contract_health(safe, today) == ("Safe", "") and contract_checks(safe, "Safe", today) == []
    safe_passed = Site("p2", "Adyen - 179 (INT)", 4420, end=date(2024, 12, 6), etat="Safe", dates=["2026-10-30", "2026-11-27"])
    assert contract_health(safe_passed, today) == (
        "Safe", "à vérifier : fin dépassée le 06/12/2024, encore 2 passages au planning après")
    assert contract_checks(safe_passed, "Safe", today) == [
        "Safe mais fin dépassée le 06/12/2024 : mettre à jour la date de fin dans Notion"]
    # the Notion comment comes first, on one line
    risky = Site("p3", "AXEO - 1255 - (INT)", 1656, end=date(2026, 12, 31), etat="À risque",
                 follow_up="renégocier la prolongation\npour 2027\n")
    assert contract_health(risky, today) == (
        "À risque", "renégocier la prolongation / pour 2027 · fin le 31/12/2026, dans moins de 3 mois")
    # Mort: the months stop at the end date (lost is set by the Notion reader)
    dead = Site("p4", "INSEE - 1044 (EXT)", 16043, end=date(2026, 12, 30), lost=True, etat="Mort",
                follow_up="Ne se renouvelle pas")
    assert contract_health(dead, today) == ("Mort", "Ne se renouvelle pas · plus rien compté après le 30/12/2026")
    assert contract_checks(dead, "Mort", today) == []
    dead_open = Site("p5", "x", 1000, lost=True, etat="Mort")
    assert contract_health(dead_open, today) == ("Mort", "pas de date de fin dans Notion, donc encore compté")
    assert contract_checks(dead_open, "Mort", today)[0].startswith("Mort mais pas de date de fin")
    # Safe in État contrat but Perdu in Reconduction: shown Safe, counted as lost, listed in Contrôles
    clash = Site("p6", "MGK - 848 (INT", 3100, end=date(2026, 10, 4), lost=True, reconduction="Perdu", etat="Safe")
    health, why = contract_health(clash, today)
    assert health == "Safe" and why.endswith("à vérifier : Reconduction « Perdu » : plus rien compté après le 04/10/2026")
    assert contract_checks(clash, health, today)[0].startswith("Safe mais Reconduction « Perdu » dans Notion")
    # empty or unknown: À risque until someone chooses
    assert contract_health(Site("p7", "x", 1000, end=date(2027, 6, 1)), today) == (
        "À risque", "État contrat vide (à remplir dans Notion)")
    assert contract_health(Site("p8", "x", 1000, end=date(2027, 6, 1), etat="Bof"), today)[1].startswith(
        "État contrat « Bof » inconnu")
    assert contract_checks(Site("p9", "x", 1000), "À risque", today) == [
        "État contrat à choisir dans Notion : Safe, À risque ou Mort"]
    assert contract_health(Site("p10", "x", 1000, archived=True, etat="Safe"), today) == ("Mort", "Site archivé dans Notion")
    assert contract_health(Site("p11", "x", 0, etat="Safe"), today) == ("", "Pas de montant annuel dans Notion")


def test_notion_site_reader_takes_etat_contrat(monkeypatch):
    from src.forecast import sources

    def page(pid, etat, reconduction=(), comment=""):
        return {"id": pid, "properties": {
            "Nom": {"title": [{"plain_text": f"Site {pid}"}]}, "Total HT Annuel": {"number": 1200},
            "Date fin Contrat": {"date": {"start": "2027-01-02"}},
            "Reconduction": {"multi_select": [{"name": r} for r in reconduction]},
            "État contrat": {"select": {"name": etat} if etat else None},
            "Commentaire suivi contrat": {"rich_text": [{"plain_text": comment}] if comment else []},
        }}

    pages = [page("a", "Safe"), page("b", "a risque"), page("c", "Mort", comment="Fin de contrat 2026"),
             page("d", "Safe", reconduction=["Perdu"]), page("e", "")]
    monkeypatch.setattr(sources, "_notion_query_all", lambda ds, key: pages if ds == sources.SITES_DATA_SOURCE else [])
    got = {s.page_id: s for s in sources.fetch_sites("key")}
    assert [got[k].etat for k in "abcde"] == ["Safe", "À risque", "Mort", "Safe", ""]  # case and accents ignored
    assert [got[k].lost for k in "abcde"] == [False, False, True, True, False]  # Mort or Perdu: stops at the end date
    assert got["c"].follow_up == "Fin de contrat 2026"


def test_maintenance_rows_split_between_contracts_and_ts_tabs():
    from src.forecast.layout import TAB_CONTRACTS, TAB_TS
    from src.forecast.maintenance import Site

    projects = pd.DataFrame([
        {"id": "1270", "title": "(E) - TS - Curage", "total_amount": 3762.0, "start_date": "2026-11-01", "end_date": "2026-11-30",
         "created_at": "2026-10-01", "project_manager": "luana", "cf_bu": "MAINTENANCE", "typologies": ["Maintenance TS"]},
    ])
    sites = [Site("aaaa-1", "Capital 8 - 721 (EXT)", 12000, start=date(2024, 7, 1), end=date(2050, 12, 31), furious_id="721",
                  passages={"2026-11": 1, "2026-12": 1}),
             Site("bbbb-2", "Neuf - 1350 (INT)", 4576, start=date(2026, 11, 2), end=date(2027, 11, 2), furious_id="1350",
                  passages={"2026-11": 1}, etat="")]
    params = Params("2026-09", 2, None, {}, {})
    sites[0].etat = "Safe"
    out = assemble(SheetState(params, [], {}, {}, {}, {}, {}), projects, pd.DataFrame(), sites, date(2026, 10, 27))
    contracts = {r.ref: r for r, _ in out.rows[TAB_CONTRACTS]}
    assert list(contracts) == ["S721", "S1350"] and [r.ref for r, _ in out.rows[TAB_TS]] == ["T1270"]  # Safe first
    assert contracts["S721"].extra["health"] == "Safe" and contracts["S1350"].extra["health"] == "À risque"
    # a contract without État contrat is listed in Contrôles
    assert [c[1] for c in out.checks if c[3].startswith("État contrat à choisir")] == ["S1350"]
    # Début and Fin of a contract are its Notion dates, even when it renews (asked on 2026-10-27: Fin was empty)
    assert contracts["S721"].start == "2024-07" and contracts["S721"].stop == "2050-12"
    assert "contract_end" not in contracts["S721"].extra


def test_pole_tab_without_legend_and_home_page_without_preamble():
    from datetime import datetime

    from src.forecast.layout import ROW_LEGEND, TAB_CONTRACTS
    from src.forecast.sheet import HOME_UPDATED, render_home, render_pole

    rend = render_pole(TAB_CONTRACTS, [], ["2026-10", "2026-11"], "2026-10", datetime(2026, 10, 27, 7, 30), 2)
    assert all(v == "" for v in rend.values[ROW_LEGEND - 1])  # the colour legend lives in the Accueil only
    values, styles = render_home(datetime(2026, 10, 27, 7, 30))
    assert values[0] == [HOME_UPDATED, "27/10/2026 à 07:30, automatique chaque nuit"]
    texts = " ".join(str(c) for line in values for c in line)
    assert "À quoi sert ce fichier" not in texts and "Prévision de production · Merci Raymond" not in texts
    sections = [label for _, kind, label in styles if kind == "section"]
    assert sections == ["Les onglets", "Les couleurs", "État d'un contrat", "Les références (colonne Réf.)",
                        "Comment les chiffres sont calculés"]
    assert all(values[r0][0] == label for r0, kind, label in styles if kind in ("section", "line"))
