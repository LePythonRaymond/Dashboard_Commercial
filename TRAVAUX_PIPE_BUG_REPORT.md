# TRAVAUX "Pipe travaux" — Missing Proposals: Investigation Report

**Date:** 2026-06-08 · **Author:** Claude (investigation) · **Status:** Diagnosis only — *no code changed, nothing deployed*
**Trigger:** Team reported that recent proposals (created by Clémence) never appeared in the Notion "Pipe travaux".

---

## 1. Executive summary

The Notion sync is **not broken**. Proposals are being **filtered out upstream**, before they ever reach Notion, by the TRAVAUX-projection criteria.

**Primary root cause:** the projection requires **`probability ≥ 25%`**, but Furious "Brief"-stage devis default to **10%**. Six of the seven reported proposals are at 10% and are silently dropped for this reason alone.

**Secondary cause:** the date filter is **forward-only** (`projet_start` must be between today and +365 days). The 7th proposal (Ocellis) clears the probability gate but is dropped because its project start date is already in the past.

**Scale:** of **41** Brief-stage TRAVAUX proposals currently in Furious, only **20** sit at probability ≥ 25%. So roughly **21** are invisible to the pipe for this one reason (more once you count the other "waiting" statuses).

---

## 2. What the "pipe" actually is

| Thing | Value |
|---|---|
| Notion DB | **"⚽ Pipe travaux"** — `2d5d927802d78002b8cbcee60cc75c29` (= `.env` `NOTION_TRAVAUX_PROJECTION_DATABASE_ID`) |
| The id you gave | `2d5d9278-02d7-8060-b210-000b5edcbdcd` = the **data source** "🛰️ Previsions Travaux" *inside* that DB |
| Relationship | Same pipe — one is the `database_id`, the other the `data_source_id` (Notion's 2025-09-03 API model) |
| Fed by | **`scripts/run_travaux_pipeline.py`** — runs **weekly, Sundays 22:00** |
| **Not** fed by | The daily 06:00 `run_pipeline.py` (the cron you pasted). It explicitly skips TRAVAUX projection. |

> ⚠️ **Cadence note:** because only the weekly job feeds this pipe, a proposal created during the week will not appear until the following Sunday — even with everything else correct. The "daily pipeline" you mentioned does not write here.

---

## 3. The entry filter (where proposals are lost)

`src/processing/travaux_projection.py` → `_matches_criteria()`. A proposal enters the pipe **only if all four hold**:

1. `final_bu == "TRAVAUX"`
2. `statut_clean` ∈ `STATUS_WAITING` (includes `brief`, `en cours`, `envoyée(s)…`) — `config/settings.py:170`
3. **`probability ≥ 25`** — `TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD`, `config/settings.py:238`
4. `date` **OR** `projet_start` within **`[today, today + 365 days]`** (forward only) — `config/settings.py:239`

**Subtlety on rule 4:** the `date` half is effectively dead. A devis `date` is virtually always in the *past*, but the check requires `date ≥ today`. So in practice entry hinges entirely on **`projet_start` being a future date within 12 months** — which salespeople often leave blank or set in the past.

---

## 4. Evidence — the 7 reported proposals (live Furious values)

Today = **2026-06-08**, so the date window is **2026-06-08 → 2027-06-08**. All 7 are `cf_bu = TRAVAUX`, `statut = Brief` (a waiting status ✓), assigned to `clemence` ✓.

| ID | Client | € | prob | ≥25%? | projet_start | in window? | **Verdict** |
|---|---|---|---|---|---|---|---|
| D263235 | KARDHAM (ESSEC) | 15k | 10 | ❌ | 2026-10-13 | ✓ | **blocked: prob 10 < 25** |
| D263254 | Quadrilatère (DOMUS VI) | 15k | 10 | ❌ | 2026-09-25 | ✓ | **blocked: prob 10 < 25** |
| D263253 | LA SIERRA PROD (Le Ciney) | 5k | 10 | ❌ | 2026-08-27 | ✓ | **blocked: prob 10 < 25** |
| D263226 | eres expertise (atrium) | 25k | 10 | ❌ | 2026-10-15 | ✓ | **blocked: prob 10 < 25** |
| D263234 | GA SMART BUILDING (Quai Valmy) | 150k | 10 | ❌ | 2027-01-12 | ✓ | **blocked: prob 10 < 25** |
| D263231 | NEXITY (Schneider) | 30k | 10 | ❌ | 2026-10-01 | ✓ | **blocked: prob 10 < 25** |
| D263230 | OCELLIS (Particulier 16e) | 2k | 25 | ✓ | 2026-06-03 | ❌ *(5 days past)* | **blocked: project start already passed** |

→ **6 of 7 fail only on probability.** The 7th fails only on the forward-only date window.

**Scale check (Furious aggregates, 2026-06-08):**
- Brief-stage TRAVAUX proposals total: **41**
- …of which probability ≥ 25%: **20**
- → **~21 Brief TRAVAUX proposals are excluded purely by the 25% gate.**

---

## 5. What was ruled out

- **Notion sync failing** — No. Last run (Sun 2026-06-07 22:00): 2089 fetched → 1654 cleaned → **59 matched → 3 created, 56 updated, 0 errors**. All HTTP 200.
- **Wrong Notion data source** — No. The DB has two data sources; the sync correctly resolved "Previsions Travaux" (`…8060`) — verified in the run log (schema + `GET /data_sources/2d5d9278-02d7-8060…`).
- **Code drift between local and VPS** — No. `travaux_projection.py`, `notion_travaux_sync.py`, `cleaner.py`, `settings.py`, `run_travaux_pipeline.py` are **byte-identical** (sha256) between local and `/root/Customs/Dashboard_Commercial`.
- **Excluded-owner filter** — Not the cause here. The cleaner drops `eloi.pujet` (446 rows), but `clemence` is not excluded.
- **"Pris en charge" leftover hiding** — Not the cause. That only hides pages that *were* in the pipe and later fell out; these proposals never qualified to be created in the first place.

---

## 6. Recommended fixes (NOT yet applied — awaiting decision)

**A. Primary — lower/remove the probability gate** (`config/settings.py:238`)
```python
TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD = 25   # → 10 (include Brief-stage briefs) or 0 (all waiting TRAVAUX)
```
- `= 10` brings in all 7 examples' siblings (Furious's Brief default) while still excluding 0% "just-exploring" devis.
- `= 0` = every waiting TRAVAUX appears regardless of probability (max coverage, more noise).
- *Alternative with no code change:* the team sets probability ≥ 25% in Furious when they want a deal in the pipe.

**B. Secondary — broaden the date window** (`src/processing/travaux_projection.py`, rule 4)
Include recently-sent / already-started proposals (e.g. allow `projet_start` within a recent grace period, or a recent devis `date`) so a live deal doesn't vanish the moment its start date passes. Fixes the Ocellis case.

**C. Optional — cadence**
If proposals should appear faster than once a week, the projection sync could also run inside the daily 06:00 pipeline (or on a shorter schedule).

---

## 7. How to verify after any future fix

1. Edit the threshold (and/or window), run the test suite.
2. On the VPS: `git pull`, then `python3 scripts/run_travaux_pipeline.py` (or `--dry-run` first).
3. Confirm the log shows "Found N proposal(s)" rising and the 7 IDs above present, then check the Notion "Previsions travaux" view.

*Access used for this investigation: `ssh root@72.61.166.144` (password worked), logs at `/root/Customs/Dashboard_Commercial/logs/`, plus live Furious + Notion via MCP.*
