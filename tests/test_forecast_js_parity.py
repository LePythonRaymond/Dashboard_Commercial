"""The Apps Script (src/forecast/apps_script/Prevision.gs) must compute exactly what the server computes.

Runs the script's pure functions under Node (skipped when Node is not installed) on:
- the shared distribute() cases of tests/forecast_vectors.json;
- a few hundred random rows through computeRow() versus model.compute_row().
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from src.forecast import months as M
from src.forecast.engine import myrium_allocation
from src.forecast.model import Row, compute_row

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "forecast" / "apps_script" / "Prevision.gs"
VECTORS = json.loads((Path(__file__).parent / "forecast_vectors.json").read_text())["cases"]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(calls):
    program = (
        "const fs=require('fs');const vm=require('vm');const ctx={module:{exports:{}},console};vm.createContext(ctx);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(SCRIPT))},'utf8'),ctx);const lib=ctx.module.exports;"
        "const calls=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(calls.map(c=>lib[c.fn](...c.args))));"
    )
    out = subprocess.run([NODE, "-e", program], input=json.dumps(calls), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_distribute_vectors_in_javascript():
    calls = [{"fn": "distribute", "args": [c["remaining"], c["weights"], c["pins"], c["grid"], c["open_from"], c["fallback_months"]]}
             for c in VECTORS]
    for case, got in zip(VECTORS, run_node(calls)):
        exp = case["expect"]
        for m, v in exp["values"].items():
            assert got["values"][m] == pytest.approx(v, abs=1e-3), (case["name"], m)
        assert got["beyond"] == pytest.approx(exp["beyond"], abs=1e-3), case["name"]
        assert got["unplaced"] == pytest.approx(exp["unplaced"], abs=1e-3), case["name"]
        assert got["fallback"] is exp["fallback"], case["name"]


def _random_case(rnd: random.Random, first: str = "2026-10"):
    grid = M.span(first, "2028-03")
    kind = rnd.choice(["Projet", "Devis", "Manuel", "Contrat", "TS"])
    rule = rnd.choice(["TRAVAUX", "CONCEPTION", "MAINTENANCE"])
    ca = rnd.choice([0, 4000, 18000, 52000, 410000])
    start = rnd.choice([None, "2025-11-15", "2026-06-01", "2026-12-01", "2027-04-10"])
    stop = rnd.choice([None, "2026-08-31", "2027-02-28", "2027-09-30"])
    fixed = None
    if kind in ("Contrat", "TS"):
        fixed = {m: round(rnd.uniform(0, 3000), 2) for m in rnd.sample(grid, 8)}
    row = Row(ref="X1", pole="Travaux", category="Pipe" if kind == "Devis" else rnd.choice(["Signé", "Pipe"]) if kind == "Manuel" else "Signé",
              kind=kind, label="x", ca=ca, proba=rnd.choice([None, 0.0, 0.1, 0.5]), mai=rnd.choice([0.0, 1500.0]),
              produced=rnd.choice([0.0, 12000.0, 500000.0]), start=M.of(start), stop=M.of(stop), proposal_date="2026-03",
              rule=rule, kv_default=rnd.choice([1.4, 2.2]), fixed=fixed, closed=rnd.random() < 0.1)
    typed = {}
    for m in rnd.sample(grid, rnd.randint(0, 4)):
        typed[m] = rnd.choice([0, 1000, 25000, 400000])
    if rnd.random() < 0.3:
        typed["stop"] = rnd.choice(["2027-01", "2027-06", "2026-11"])
    if rnd.random() < 0.2:
        typed["start"] = rnd.choice(["2026-12", "2027-03"])
    if rnd.random() < 0.3:
        typed["proba"] = rnd.choice([0.2, 0.9])
    if rnd.random() < 0.3:
        typed["kv"] = 1.8
    if rnd.random() < 0.2:
        typed["cost"] = 9000
    if kind == "Manuel" and rnd.random() < 0.5:
        typed["ca"] = 30000
    calc = {"kind": row.kind, "cat": row.category, "rule": row.rule, "produced": row.produced, "ca": row.ca, "mai": row.mai,
            "proba": row.proba, "kv_default": row.kv_default, "start": row.start, "stop": row.stop, "closed": row.closed,
            "fixed": row.fixed, "alloc": None if row.fixed is not None else myrium_allocation(
                row.rule, row.ca if row.ca > 0 else 1.0, row.start, row.stop, row.proposal_date)}
    return row, typed, calc, grid


def test_compute_row_matches_python_on_random_rows():
    rnd = random.Random(20261026)
    cases = [_random_case(rnd) for _ in range(300)]
    meta = {"grid": cases[0][3], "open_from": "2026-10", "fallback": 2, "pending_from": "2026-11"}
    got = run_node([{"fn": "computeRow", "args": [calc, typed, meta]} for _, typed, calc, _ in cases])
    for (row, typed, calc, grid), js in zip(cases, got):
        py = compute_row(row, typed, grid, "2026-10", 2, "2026-11")
        ctx = (row.kind, row.rule, row.ca, typed)
        assert js["remaining"] == pytest.approx(py.remaining, abs=1e-6), ctx
        for m in grid:
            assert js["months"][m] == pytest.approx(py.months[m], abs=1e-6), (ctx, m)
        assert js["beyond"] == pytest.approx(py.beyond, abs=1e-6), ctx
        assert js["gap"] == pytest.approx(py.gap, abs=1e-6), ctx
        assert js["kv"] == pytest.approx(py.kv, abs=1e-9), ctx
        assert js["cost"] == pytest.approx(py.cost, abs=1e-6), ctx
        assert js["proba"] == pytest.approx(py.proba), ctx


def test_compute_row_matches_python_with_a_past_month_not_closed_yet():
    # August closed, today in October: September is in the grid but no longer open.
    rnd = random.Random(20261027)
    cases = [_random_case(rnd, "2026-09") for _ in range(300)]
    meta = {"grid": cases[0][3], "open_from": "2026-10", "fallback": 2, "pending_from": "2026-11"}
    got = run_node([{"fn": "computeRow", "args": [calc, typed, meta]} for _, typed, calc, _ in cases])
    for (row, typed, calc, grid), js in zip(cases, got):
        py = compute_row(row, typed, grid, "2026-10", 2, "2026-11")
        ctx = (row.kind, row.rule, row.ca, typed)
        assert js["remaining"] == pytest.approx(py.remaining, abs=1e-6), ctx
        for m in grid:
            assert js["months"][m] == pytest.approx(py.months[m], abs=1e-6), (ctx, m)
        assert js["beyond"] == pytest.approx(py.beyond, abs=1e-6), ctx
        assert js["gap"] == pytest.approx(py.gap, abs=1e-6), ctx


def test_recalculating_a_row_records_what_the_edit_handler_missed():
    fields = ["ref", "label", "kv", "start", "2026-10", "2026-11", "comment"]
    before = {"label": "Farman", "kv": 1.4, "start": "2026-02", "2026-10": 56268.0, "2026-11": 56268.0, "comment": ""}
    # 2026-10 typed (missed by onEdit), 2026-11 emptied, start unchanged (shown as a date serial), kv unchanged
    line = ["P1081", "Farman", 1.4, 46054, 40000, "", ""]
    typed = {"2026-11": 56268.0, "kv": 1.4}
    got = run_node([{"fn": "recordRow_", "args": [fields, line, "P1081", typed, before]}])[0]
    assert got == {"2026-10": 40000, "kv": 1.4}
