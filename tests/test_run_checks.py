from datetime import datetime

import pytest

from src.run_checks import RunChecks


def test_empty_or_shrunk_source_blocks_the_run(tmp_path):
    first = RunChecks("suivi", tmp_path)
    first.source("purchases", list(range(13512)))
    assert first.ok
    first.save_good_run(datetime(2026, 10, 28, 6, 30))

    tonight = RunChecks("suivi", tmp_path)
    tonight.source("purchases", list(range(2104)))  # 84 % fewer than the last good run
    tonight.source("invoices", [])  # nothing at all
    assert not tonight.ok
    assert [f.message for f in tonight.blocking()] == [
        "purchases : 2104 lignes lues, 84% de moins que la dernière bonne mise à jour (13512)",
        "invoices : 0 lignes lues, au moins 1 attendues",
    ]
    with pytest.raises(RuntimeError):
        tonight.save_good_run()  # a blocked run never becomes the reference


def test_small_drop_and_equal_amounts_pass_and_warnings_do_not_block(tmp_path):
    run = RunChecks("forecast", tmp_path)
    run.counts["sites"] = 169
    run.save_good_run()
    again = RunChecks("forecast", tmp_path)
    again.source("sites", list(range(160)))  # 5 % fewer: normal life (a site archived)
    again.equal("totaux", "Total Travaux = somme des projets", 2078128.89, 2078128.40)
    again.equal("totaux", "Factures du mois = Furious", 412000.0, 411000.0, blocking=False)
    assert again.ok and len(again.warnings()) == 1
    assert "BLOQUÉ" not in again.report() and "à voir [totaux]" in again.report()
