"""
scripts/archive_old_pages.py: a table that fails (e.g. no longer shared with the
integration, met on 2026-09-29) must not stop the monthly clean-up of the others.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import archive_old_pages as job  # noqa: E402


def test_a_table_that_fails_does_not_stop_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(job, "SCOPED_TABLES", (("A", "a_db", ()), ("B", "b_db", ()), ("C", "c_db", ())))
    monkeypatch.setattr(job, "settings", SimpleNamespace(a_db="aaa", b_db="", c_db="ccc"))
    monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive_old_pages.py", "--apply"])
    done = []

    def archive_table(label, database_id, cutoff, args):
        if label == "A":
            raise RuntimeError("GET data_sources -> 404: object_not_found")
        done.append((label, database_id, args.apply))
        return {"total_pages": 10, "due": 0, "moved": 0, "pages": []}

    monkeypatch.setattr(job, "archive_table", archive_table)

    assert job.main() == 1                   # the cron log shows the failure
    assert done == [("C", "ccc", True)]      # B is not configured, C still runs
    report = json.loads(next((tmp_path / "logs").glob("archive_old_pages_*.json")).read_text())
    assert report["tables"]["A"] == {"error": "RuntimeError: GET data_sources -> 404: object_not_found"}
    assert report["tables"]["C"]["total_pages"] == 10 and "B" not in report["tables"]
