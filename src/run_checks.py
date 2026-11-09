"""
Verification of an automated run, so that nothing goes wrong silently.

Every nightly job (Forecast, Suivi de production) reads sources, computes, then writes a Google Sheet.
Three things can go wrong without any error message:

1. a source answers, but with nothing or with too little (an expired right, a renamed field,
   a filter that suddenly matches nothing);
2. the computation loses or doubles something (a row assigned to no project, a total that no longer
   equals the sum of its lines);
3. the job does not run at all (cron stopped, server down).

``RunChecks`` covers the first two. Each check is either blocking (the sheet is not written: it keeps
yesterday's version, and an email says why) or a warning (the sheet is written, the line goes to the
Contrôles tab). The third is covered by the date of the last successful run, written in the sheet and
checked by a watcher outside the job.

Example: the Suivi read 13 512 purchases last night and 2 104 tonight. ``source("purchases", rows)``
blocks the run: "purchases: 2 104 rows, 84 % fewer than the last good run (13 512)".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

MAX_DROP = 0.2  # a source may shrink by 20 % at most from one good run to the next


@dataclass
class Finding:
    blocking: bool
    check: str
    message: str


@dataclass
class RunChecks:
    job: str
    state_dir: Path
    counts: Dict[str, int] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)

    @property
    def state_path(self) -> Path:
        return self.state_dir / f"{self.job}.json"

    def last_good(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ checks
    def block(self, check: str, message: str) -> None:
        self.findings.append(Finding(True, check, message))

    def warn(self, check: str, message: str) -> None:
        self.findings.append(Finding(False, check, message))

    def source(self, key: str, rows: Sequence, min_rows: int = 1, max_drop: float = MAX_DROP) -> None:
        """A source must bring at least ``min_rows`` rows and not shrink much since the last good run."""
        n = len(rows)
        self.counts[key] = n
        if n < min_rows:
            self.block("source", f"{key} : {n} lignes lues, au moins {min_rows} attendues")
            return
        before = self.last_good().get("counts", {}).get(key)
        if before and n < before * (1 - max_drop):
            self.block("source", f"{key} : {n} lignes lues, {1 - n / before:.0%} de moins que la dernière bonne "
                                 f"mise à jour ({before})")

    def equal(self, check: str, label: str, a: float, b: float, tol: float = 1.0, blocking: bool = True) -> None:
        """Two amounts that must agree, for example a total and the sum of its lines."""
        if abs(a - b) > tol:
            (self.block if blocking else self.warn)(check, f"{label} : {a:,.2f} contre {b:,.2f} (écart {a - b:,.2f})")

    # ------------------------------------------------------------------ outcome
    @property
    def ok(self) -> bool:
        return not any(f.blocking for f in self.findings)

    def blocking(self) -> List[Finding]:
        return [f for f in self.findings if f.blocking]

    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if not f.blocking]

    def save_good_run(self, when: Optional[datetime] = None) -> None:
        """Called after the sheet is written: these counts become the reference for the next run."""
        if not self.ok:
            raise RuntimeError("a run with blocking findings is not a good run")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"at": (when or datetime.now()).isoformat(timespec="seconds"),
                                               "counts": self.counts}, indent=1))

    def report(self) -> str:
        lines = [f"{self.job} : " + ("OK" if self.ok else "BLOQUÉ"),
                 "Lu : " + ", ".join(f"{k} {v}" for k, v in self.counts.items())]
        lines += [("BLOQUANT " if f.blocking else "à voir ") + f"[{f.check}] {f.message}" for f in self.findings]
        return "\n".join(lines)
