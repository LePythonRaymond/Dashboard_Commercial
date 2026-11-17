#!/usr/bin/env python3
"""
Serve the dashboard's "Actualiser les données" requests. Run by cron every minute:

    * * * * * cd /root/Customs/Dashboard_Commercial && venv/bin/python3 scripts/refresh_watch.py >> logs/refresh_watch_$(date +\\%Y\\%m).log 2>&1

Each call reads the "Actualisation" tab once (src/integrations/refresh_request.py).
When a request is newer than the last start, it runs the sheets part of the daily
pipeline, the same command as a manual rerun:

    scripts/run_pipeline.py --skip-emails --skip-notion --live-snapshot

and writes its progress back in the tab. Prints nothing when there is nothing to do.
The pipeline's own lock (logs/.pipeline.lock) keeps it from running alongside the
06:00 cron; a request made during that run starts right after it.
"""

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations import refresh_request as rr
from src.run_lock import FileLock, is_locked, pipeline_lock_path

WATCH_LOCK_PATH = PROJECT_ROOT / "logs" / ".refresh_watch.lock"
PIPELINE_LOCK_PATH = pipeline_lock_path(PROJECT_ROOT)
PIPELINE_TIMEOUT_SECONDS = 30 * 60
PIPELINE_ARGS = ["--skip-emails", "--skip-notion", "--live-snapshot"]


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def run_pipeline(log_path: Path) -> Tuple[int, str]:
    """Run the pipeline into log_path; (exit code, its output)."""
    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_pipeline.py")] + PIPELINE_ARGS
    with open(log_path, "w") as out:
        try:
            code = subprocess.run(
                command, cwd=PROJECT_ROOT, stdout=out, stderr=subprocess.STDOUT,
                timeout=PIPELINE_TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            code = -1
    return code, log_path.read_text(errors="replace")


def outcome(code: int, output: str, seconds: float) -> Tuple[str, str]:
    """(status, message) to write back, from the pipeline's exit code and log."""
    duration = f"{int(seconds // 60)} min {int(seconds % 60):02d} s"
    if code == -1:
        return rr.STATUS_FAILED, f"arrêtée après {PIPELINE_TIMEOUT_SECONDS // 60} min"
    if code != 0:
        errors: List[str] = re.findall(r" - ERROR - (.+)", output)
        return rr.STATUS_FAILED, (errors[-1] if errors else f"code de sortie {code}")[:300]
    if "PIPELINE COMPLETED WITH WARNINGS" in output:
        return rr.STATUS_DONE, f"terminée en {duration}, avec des avertissements (voir l'e-mail d'alerte)"
    return rr.STATUS_DONE, f"terminée en {duration}"


def serve_once(
    client,
    pipeline: Callable[[Path], Tuple[int, str]] = run_pipeline,
    pipeline_busy: Callable[[], bool] = lambda: is_locked(PIPELINE_LOCK_PATH),
    log_dir: Path = PROJECT_ROOT / "logs",
    now: Callable[[], datetime] = rr.now_utc,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[str]:
    """
    Serve at most one request; returns what was done (None when there was nothing to do).

    The caller holds the watcher lock, so no other watcher is running: a status left
    at "en cours" with the pipeline lock free is a run that died.
    """
    state = rr.read_state(client)

    if not state.pending:
        if state.status == rr.STATUS_RUNNING and not pipeline_busy():
            rr.write_status(client, rr.STATUS_FAILED, state.started_at, now(), "interrompue (processus arrêté)")
            return "interrupted run marked as failed"
        return None

    if pipeline_busy():
        if state.status != rr.STATUS_WAITING:
            rr.write_status(client, rr.STATUS_WAITING, state.started_at, state.finished_at, "")
        return "waiting for the run in progress"

    started_at = now()
    rr.write_status(client, rr.STATUS_RUNNING, started_at, None, "")
    log(f"request of {rr.to_iso(state.requested_at)}: pipeline started")
    log_path = log_dir / f"pipeline_manual_{started_at.astimezone(rr.PARIS):%Y%m%d_%H%M%S}.log"
    t0 = clock()
    code, output = pipeline(log_path)
    status, message = outcome(code, output, clock() - t0)
    rr.write_status(client, status, started_at, now(), message)
    log(f"pipeline {status}: {message} ({log_path.name})")
    return f"pipeline {status}"


def main() -> int:
    watch_lock = FileLock(WATCH_LOCK_PATH)
    if not watch_lock.try_acquire():
        return 0  # the previous minute's watcher is still serving a request
    try:
        from src.integrations.google_sheets import GoogleSheetsClient

        serve_once(GoogleSheetsClient())
    except Exception as e:
        log(f"error: {type(e).__name__}: {e}")
        return 1
    finally:
        watch_lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
