# Myrium - Commercial Tracking & BI System
## Comprehensive Project Documentation

---

## Executive Summary

**Myrium** is an automated commercial tracking and business intelligence system designed for **Merci Raymond** (Urban Landscapers). The system replaces a previous n8n-based workflow with a more flexible, maintainable Python application that extracts data from the Furious CRM, processes complex revenue forecasting rules, stores monthly snapshots in Google Sheets, sends automated email alerts, syncs project timelines to Notion, and provides a Streamlit-based BI dashboard.

**Key Achievement**: Migrated from a slow, inflexible n8n workflow to a robust, scalable Python application that processes 1,700+ proposals in ~20 seconds with comprehensive error handling and logging.

---

## 1. Project Scope & Context

### 1.1 Business Context

Merci Raymond is an urban landscaping company that manages hundreds of commercial proposals across three main business units:
- **MAINTENANCE** (Maintenance/Entretien)
- **TRAVAUX** (Construction/Chantier)
- **CONCEPTION** (Design/Etude)

The company needed an automated system to:
- Track commercial pipeline in real-time
- Forecast revenue across multiple years (current + 2 years ahead)
- Generate monthly snapshots for management reporting
- Alert sales teams about data quality issues and follow-up opportunities
- Visualize project timelines for resource planning

### 1.2 Previous System Limitations

The original system was built in **n8n** (workflow automation tool) with Python scripts embedded. Key limitations:
- **Performance**: Too slow for processing large datasets
- **Flexibility**: Difficult to modify business logic
- **Maintainability**: Complex workflow dependencies
- **Error Handling**: Limited debugging capabilities
- **Scalability**: Hard to extend with new features

### 1.3 Solution Approach

**Complete rewrite in Python** with:
- Modular architecture for easy maintenance
- Comprehensive error handling and logging
- Dynamic year calculation (no hardcoded dates)
- Multi-spreadsheet organization by type and year
- Test mode for safe development
- Production-ready deployment with cron scheduling

---

## 2. Business Goals & Objectives

### 2.1 Primary Objectives

1. **Automated Data Pipeline**
   - Fetch all proposals from Furious CRM API (with pagination)
   - Process 1,700+ proposals in under 30 seconds
   - **Daily**: Full pipeline (compute + Sheets + Notion) without emails for real-time updates
   - **Bi-monthly**: Emails only (objectives + alerts) on 15th and last day of month
   - **Weekly**: TRAVAUX projection for proactive planning (every Sunday)

2. **Financial Forecasting**
   - Calculate revenue spreading across 3 years (Y, Y+1, Y+2)
   - Apply business-specific rules per BU type
   - Generate quarterly breakdowns for planning
   - Production-year based forecasting with carryover tracking

3. **Data Quality & Alerts**
   - Identify proposals with data quality issues (missing dates, zero probability)
   - Alert sales reps about follow-up opportunities (with OR logic for dates)
   - Group alerts by owner with full assignee visibility for efficient action

4. **Reporting & Visualization**
   - Generate 3 main views: Snapshot ("État actuel"), Sent Month, Won Month
   - Store in Google Sheets with summaries and currency formatting
   - Provide interactive BI dashboard with production year tabs and time-based filtering

5. **Resource Planning**
   - Sync TRAVAUX projects to Notion for Gantt visualization
   - Identify high-probability opportunities to fill calendar gaps (Projection)
   - **TRAVAUX Projection**: Rolling 365-day window with OR logic (`date` OR `projet_start`), probability threshold 25%

### 2.2 Success Metrics

- **Performance**: Pipeline completes in < 30 seconds
- **Reliability**: 99%+ success rate with comprehensive error handling
- **Accuracy**: Revenue calculations match business rules exactly
- **Usability**: Dashboard loads in < 1 second with optimized caching
- **Maintainability**: Clear code structure, easy to modify business logic

---

## 3. Technical Architecture

### 3.1 System Architecture

```
┌─────────────────┐
│  Furious API    │ (Data Source)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  API Clients    │ (auth.py, proposals.py)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Data Processing │ (cleaner.py, revenue_engine.py, views.py, alerts.py)
└────────┬────────┘
         │
         ├──► Daily Full Pipeline (run_pipeline.py --skip-emails)
         │    ├──► Google Sheets (État actuel + monthly views)
         │    └──► Notion Sync (Alerts + TRAVAUX + MAINTENANCE won + Devis gagnés all BUs)
         │
         ├──► Bi-Monthly Emails (run_pipeline_scheduled.py --emails-only)
         │    ├──► Email Alerts (Weird + Follow-ups)
         │    └──► Objectives Email (Production + Carryover)
         │
         ├──► Weekly Pipeline (run_travaux_pipeline.py)
         │    ├──► Email (TRAVAUX projection)
         │    ├──► Notion (TRAVAUX projection DB)
         │    └──► Notion (Recent TRAVAUX projects - last 7 days)
         │
         └──► Streamlit Dashboard (BI Visualization - reads from Google Sheets)
```

### 3.2 Data Flow

**Common Processing Steps** (shared by all pipelines):
1. **Authentication**: JWT token acquisition with auto-refresh
2. **Data Extraction**: Paginated fetch of all proposals (250 per page)
3. **Data Cleaning**: Normalization, date parsing, BU assignment
4. **Revenue Calculation**: Complex spreading logic per BU type (production year based)
5. **View Generation**: Filter and aggregate into 3 main views

**Pipeline-Specific Outputs**:
- **Daily Pipeline**: Writes to "État actuel" (stable snapshot) and monthly sheets. Syncs Notion databases (alerts, TRAVAUX, MAINTENANCE won, and all won devis in step 11, §18.16). MAINTENANCE won: all proposals won in the current year with BU MAINTENANCE; POST if ID Devis not in Notion, PATCH if already present; no archiving (all years/months kept for grouping in Notion). **Maintenance Entretien début 2026**: same état spreadsheet gets a **Paramètres** worksheet (Notion → JSON + Sheets); see `run_sheets_update.py` Step 7 and `run_pipeline.py` after Step 7 (§18.11).
- **Bi-Monthly Pipeline**: Sends emails only (objectives + alerts). No external writes to avoid overwriting daily data.
- **Weekly Pipeline**: Dedicated TRAVAUX projection email + Notion sync.

**Visualization**: Dashboard reads from Google Sheets (prefers "État actuel" for snapshot view)

### 3.3 Component Architecture

**Layered Architecture**:
- **API Layer**: External service clients (Furious, Google, Notion)
- **Processing Layer**: Business logic (cleaning, revenue, views, alerts)
- **Integration Layer**: Output handlers (Sheets, Email, Notion)
- **Presentation Layer**: Streamlit dashboard

**Separation of Concerns**:
- Each module has a single responsibility
- Business logic separated from I/O operations
- Configuration centralized in `settings.py`
- Error handling at each layer

---

## 4. Business Logic & Rules

### 4.1 Business Unit Assignment

**Priority Order**:
1. **TS Rule (Highest Priority)**: If proposal title contains "TS" (case-insensitive, word boundary), force assignment to **TRAVAUX** regardless of CRM value
2. **Keyword Mapping**:
   - `MAINTENANCE` or `ENTRETIEN` → **MAINTENANCE**
   - `TRAVAUX` or `CHANTIER` → **TRAVAUX**
   - `CONCEPTION` or `ETUDE` → **CONCEPTION**
3. **Fallback**: Use raw value if meaningful, otherwise "AUTRE"

**Implementation**: `src/processing/cleaner.py::assign_bu()`

### 4.2 Revenue Spreading Rules

The revenue engine calculates monthly allocations based on BU type and project characteristics:

#### MAINTENANCE
- **Rule**: Spread total amount evenly over project duration
- **Formula**: `monthly_amount = total_amount / months_duration`

#### TRAVAUX
- **Short Projects (< 1 month)**: 100% revenue on `projet_start` date
- **Long Projects (≥ 1 month)**: Spread evenly over duration

#### CONCEPTION (Complex Phasing)
- **Small (< 15k€)**: 1/3 per month for 3 months
- **Medium (15k-30k€)**: 60% over 6mo → 6mo pause → 40% over 6mo
- **Large (> 30k€)**: 40% over 12mo → 6mo pause → 60% over 12mo

**Date Replacement Rules** (applied when dates are missing):
- **Rule 1 (start missing)**: MAINTENANCE uses `projet_stop - 11mo`, TRAVAUX uses `date` to `projet_stop`, CONCEPTION uses `date`
- **Rule 2 (end missing)**: MAINTENANCE extends +11mo, TRAVAUX extends +5mo, CONCEPTION unchanged
- **Rule 3 (both missing)**: All BUs use `date` column with BU-specific spans
- **Rule 4 (Window Clamping)**: Allocations outside Y..Y+3 window are clamped to first/last tracked month to prevent revenue loss

**Implementation**: `src/processing/revenue_engine.py`

### 4.3 Financial Columns Generated

For each proposal, the system generates:
- **Annual Totals**: `Montant Total {Year}`, `Montant Pondéré {Year}`
- **Quarterly Breakdowns**: `Montant Total Q{1-4}_{Year}`, `Montant Pondéré Q{1-4}_{Year}`

**Years Tracked**: Current year, Y+1, Y+2, Y+3 (dynamic)

**Weighted Amounts**: `Montant Pondéré = Montant Total × (Probability / 100)`

### 4.4 View Generation Rules

#### View 1: "État au {DD-MM-YYYY}" (Snapshot)
- **Scope**: All proposals with status in `STATUS_WAITING`
- **Purpose**: Real-time snapshot of the commercial pipeline
- **Updates**: Written to "État actuel" daily; dated snapshots created bi-monthly (historical)

#### View 2: "Envoyé {Month} {Year}" (Sent)
- **Scope**: Proposals created in current month AND status is `STATUS_WAITING`
- **Purpose**: Track new proposals sent this month

#### View 3: "Signé {Month} {Year}" (Won)
- **Scope**: Proposals with status in `STATUS_WON`
- **Date Rule**: Included if `signature_date` is current month OR `date` (proposal date) is current month
- **Purpose**: Track won deals for the month

### 4.5 Alert Rules

#### Weird Proposals Alert
- **Triggers**: Missing `projet_start`/`projet_stop`, invalid range, Probability = 0%
- **Note**: < 1,000€ threshold removed (January 2026)
- **Grouping**: By `alert_owner` (VIP resolution logic)
- **Delivery**: One email per owner with all their weird proposals

#### Commercial Follow-up Alert
- **Scope**: Proposals with status `STATUS_WAITING`
- **Time Window**: Previous Month 1st to Today + 60 Days (default)
- **Date Reference**:
  - **CONCEPTION**: Uses `date`
  - **TRAVAUX/MAINTENANCE**: Uses OR logic (`date` <= window OR `projet_start` <= window)
- **VIP Routing**: If `assigned_to` contains a VIP, assign alert ONLY to that VIP
- **Notion "Devis à suivre"**: no window at all since 2026-09-24 (`AlertsGenerator(followup_window=False)`): every WAITING devis, for everyone; only the emails keep the window (§18.17)

**Implementation**: `src/processing/alerts.py`

### 4.6 Summary Calculations

Each view includes summaries at the bottom:
- **By BU**: Aggregated by Business Unit
- **By Typologie**: Aggregated by primary typologie only (see below)
- **TS Total**: Deprecated; TS merged into typologie summary

**Primary Typologie Allocation** (9 subcategories: Conception Concours/DV/Paysage, Travaux Direct/DV/Conception, Maintenance TS/Entretien/Animation):
- **Logic**: Deterministic selection via `typologie_allocation.allocate_typologie_for_row`; each row contributes to exactly one typologie bucket (no comma-split, no amount division)
- **Priority**: Maintenance TS (highest) → First non-Maintenance Animation tag → Maintenance Animation (if only tag)
- **Views** (`src/processing/views.py`): Typologie summary uses primary-only; BU summary unchanged
- **Dashboard** (`src/dashboard/app.py`): Year and quarter typologie both use primary-only (no space-split)
- **Variants**: Furious labels (e.g. "Maintenance TS (+DT)", "Travaux conception") normalized to canonical names in `typologie_allocation.normalize_typologie_tag`

---

## 5. Code Frameworks & Technologies

### 5.1 Core Technologies

| Technology | Version | Purpose |
|------------|---------|---------|
| **Python** | 3.8+ | Core language |
| **Pandas** | ≥2.0.0 | Data manipulation and analysis |
| **NumPy** | ≥1.24.0 | Numerical operations |
| **Requests** | ≥2.31.0 | HTTP API calls |

### 5.2 Integration Libraries

| Library | Purpose |
|---------|---------|
| **gspread** | Google Sheets API client |
| **google-auth** | Google Service Account authentication |
| **notion-client** | Notion API client (Dual API support) |
| **python-dotenv** | Environment variable management |

### 5.3 Visualization & Dashboard

| Library | Purpose |
|---------|---------|
| **Streamlit** | Web dashboard framework |
| **Plotly** | Interactive charts and graphs |

### 5.4 Design Patterns
- **Singleton Pattern**: `settings` object
- **Factory Pattern**: Spreadsheet creation
- **Strategy Pattern**: Revenue spreading rules
- **Template Method**: Email HTML generation
- **Repository Pattern**: API clients

### 5.5 Code Organization
```
myrium/
├── config/              # Configuration & constants
├── src/
│   ├── api/            # External API clients (auth.py, proposals.py, projects.py)
│   ├── processing/     # Business logic
│   ├── integrations/   # Output handlers (Sheets, Email, Notion)
│   └── dashboard/      # Streamlit application
├── scripts/            # Pipeline entrypoints
└── logs/               # Execution logs
```

---

## 6. Implementation Strategies

### 6.1 Error Handling Strategy
- **Multi-Layer**: API, Processing, Integration, Orchestration layers
- **Fail-Closed**: Notion sync fails loudly if API incompatible (prevents duplicates)
- **Logging**: Structured logs with tracebacks

### 6.2 Caching Strategy
- **Google Sheets**: Objects cached in memory
- **Dashboard**: Streamlit `@st.cache_data` with 5-min TTL
- **Auth**: JWT token cached with auto-refresh

### 6.3 Data Validation Strategy
- **Input Validation**: Date parsing with NaT fallback
- **Business Rules**: TS rule applied before standard BU mapping
- **Date Rules**: Rules 1-4 for missing/invalid dates

### 6.4 Configuration Management
- **Env Vars**: Sensitive data in `.env`
- **Settings**: Business constants in `config/settings.py`

### 6.5 Testing Strategy
- **Test Mode**: `--test` flag redirects emails
- **Dry Run**: `--dry-run` flag skips external writes
- **Unit Tests**: `pytest` suite covering revenue logic, alerts, and objectives

---

## 7. Key Features & Capabilities

### 7.1 Data Extraction
- **Automatic Pagination**: Handles 2,300+ proposals; offset pages ordered on a total order (`date desc, id desc`) so no devis is duplicated or skipped (§18.16)
- **Field Selection**: Fetches 29 specific fields
- **Error Recovery**: Continues on individual page failures

### 7.2 Data Processing
- **Robust Date Parsing**: Handles various formats
- **TS Rule Override**: Automatic TRAVAUX assignment
- **VIP Routing**: Intelligent owner resolution for alerts

### 7.3 Revenue Forecasting
- **Multi-Year Projections**: Current + 2 years ahead
- **Production-Year Logic**: Aggregates revenue by production year (not just signing year)
- **Carryover Tracking**: Tracks revenue from previous-year signings
- **Window Clamping**: Prevents revenue loss outside tracked window

### 7.4 Google Sheets Integration
- **Formatting**: Currency formatting (`#,##0 €`), color-coded summaries
- **Dynamic Sizing**: Adapts to data row count (fixes ghost formatting)
- **Multi-Spreadsheet**: Separate files by year/type

### 7.5 Email Alerts
- **Templates**: Professional HTML with Notion links and French dates
- **Production CC**: Automatic CC to project maintainers
- **Objectives Email**: Aligned with dashboard calculations (11-month accounting)
- **Assignee Visibility**: Shows all assignees in alert tables

### 7.6 Notion Integration
- **7 Databases**: Weird Proposals, Follow-up (every waiting devis), TRAVAUX Projection ("Pipe travaux"), Recent TRAVAUX Projects, MAINTENANCE Won (current year), Devis gagnés (all BUs, rolling last 365 days, avenants as sub-items, signature date and team columns typed in Notion, §18.16 to §18.18), Devis perdus (rolling last 365 days, loss reasons, §18.18)
- **Commercial/Chef de projet Split**: People properties for clear responsibility
- **Schema-Aware Sync**: Only sets properties that exist in database schema (prevents 400 errors)
- **TRAVAUX Projection dates**: Sync maps to both "Date"/"Début projet" and "Date Signature"/"Début Chantier" when present in schema; projection passes `signature_date` for "Date Signature"
- **Property Preservation**: Preserves user-edited notes/checkboxes during sync
- **Notion API 2025-09-03**: All clients pinned to latest API version with data_sources support
- **Fail-Closed Behavior**: Refuses to create pages when schema cannot be loaded (prevents blank page spam)
- **Follow-up table**: every waiting devis (no window). The team owns Commentaire, Pris en charge, Date archivage, Origine Transfo (never written by the sync); a devis that leaves the list gets its real Furious status in "Statut" and the views filter on the waiting statuses (§18.17)
- **Scope rule (5 sales tables)**: "Dans le périmètre" (sync) says whether a row is in its table's scope and every view filters on it; a row that leaves is archived by the sync ("Pris en charge" ticked + archive date); the sync never unticks a person's tick; archived rows out of scope go to the trash after 6 months (monthly job). See §18.19
- **Notion ↔ Furious check**: after the syncs (step 13) and at 07:30 (reconciliation e-mail on drift), the three sales tables are compared with Furious (§18.17, §18.18)

### 7.7 BI Dashboard
- **Production Tabs**: "À produire {Year}" with cross-year aggregation
- **Time Filtering**: Filter by Month/Quarter based on source sheet
- **Date Columns**: Full visibility of proposal dates
- **Clickable Project Lists**: KPI cards display project counts with clickable "🔎 Voir projets" buttons that open large modal dialogs showing detailed project lists with Furious CRM links
- **Inline Editing & Manual Projects** (§18.13): the "Voir projets" dialog is an editable `st.data_editor` (all years' Total + T1–T4 at once); edit cells, add a project via the trailing row, delete manual rows. Edits persist as JSON overrides/manuals (`data/overrides.json`, `data/manual_projects.json`) re-applied at read-time and by the daily pipeline, so they survive the Furious re-pull. Read-only/computed columns are greyed; sidebar panel manages/links manual projects to Furious ids
- **Objectifs Signé (Production vs Signature)**: For the Signé view, the Objectifs tab shows two blocks: **Objectif Production** vs **Réalisé** (signed-to-produce in the period; **exception** in 2026: BU MAINTENANCE and typologie Maintenance Entretien use **only** prorated début d’année — §18.12) and **Objectif Signature** vs **Signature** (ex-Pur). Objectives data: `signe` = production (Réalisé), `signature` = signature (Signé). **Début 2026** resolution (Sheets Paramètres, JSON, Notion, secret): **§18.11**; same resolver drives **Envoyé 2026** when applicable. **Objectifs tab end section**: projection + Pur-by-month + expanders + colors (§18.10–18.12).
- **Budget Export** (§18.14): sidebar "Générer budget {année}" produces the one-sheet "Budget {Y} avec légende" `.xlsx` (download or Drive-as-Google-Sheet upload); Signés/Potentiels/Envoyés use production-year aggregation, sent pipe prunes stale overdue carryover, portefeuille from live Notion
- **Optimization**: Lazy loading, caching, efficient multi-sheet reading
- **PDF Removal**: Export feature removed for performance/simplicity

---

## 8. Configuration & Setup

### 8.1 Environment Variables
```env
FURIOUS_API_URL=...
FURIOUS_USERNAME=...
FURIOUS_PASSWORD=...
GOOGLE_SERVICE_ACCOUNT_PATH=...
SPREADSHEET_ETAT_2026=...
SMTP_HOST=...
SMTP_USER=...
SMTP_PASSWORD=...
NOTION_API_KEY=...
NOTION_DATABASE_ID=...
NOTION_TRAVAUX_PROJECTION_DATABASE_ID=...
NOTION_TRAVAUX_RECENT_PROJECTS_DATABASE_ID=...
NOTION_MAINTENANCE_WON_DATABASE_ID=...
NOTION_WON_DEVIS_DATABASE_ID=...     # "Devis gagnés" DB, step 11 (§18.16)
WON_DEVIS_LOOKBACK_DAYS=365          # Optional: rolling window of steps 11 and 12, in days (§18.17)
NOTION_LOST_DEVIS_DATABASE_ID=...    # "Devis perdus" DB, step 12 (§18.18)
LOST_DEVIS_WRITE_PEOPLE=1            # set on 2026-09-24 once "notify" was turned off on its People properties (§18.18)
MAINTENANCE_ENTRETIEN_START_2026=...   # Optional fallback: value for "Maintenance Entretien – Début 2026" (e.g. 1084000)
NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID=...  # Optional: data source ID (preferred)
NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID=...   # Optional: legacy database ID if no datasource
BUDGET_EXPORT_DRIVE_FOLDER_ID=...   # Optional: Drive folder for "Générer budget" upload-as-Google-Sheet (§18.14)
```

### 8.2 Business Constants
Defined in `config/settings.py`:
- **VIP Commercials**: List of VIP sales reps
- **BU Keywords**: Mapping keywords to business units
- **Alert Config**: Follow-up window of the emails (60 days); the Notion follow-up table has no window (§18.17); Excluded owners
- **TRAVAUX Projection**: Start window (365 days), Probability threshold (25%)

---

## 9. Deployment & Operations

### 9.1 Pipeline Execution Control

The pipeline supports granular flags to control execution components:
- `--skip-emails`: Skip all emails (objectives + alerts)
- `--emails-only`: Send emails only (skip Sheets writes + Notion sync)
- `--skip-sheets`: Skip Google Sheets writes
- `--skip-notion`: Skip Notion alerts sync
- `--live-snapshot`: Use stable "État actuel" sheet name (avoid dated snapshots)

### 9.2 Cron Scheduling

**Multi-Pipeline Architecture** (3 independent schedules):

**Daily Full Pipeline** (compute + Sheets + Notion, no emails):
```bash
0 6 * * * cd /path/to/myrium && /path/to/venv/bin/python3 scripts/run_pipeline.py --skip-emails --live-snapshot >> logs/pipeline_daily.log 2>&1
```
- Runs full pipeline daily: Auth → Fetch → Clean → Revenue → Views → Sheets + Notion sync
- **Step 10**: Syncs current year’s MAINTENANCE won proposals to Notion (when `NOTION_MAINTENANCE_WON_DATABASE_ID` is set); POST new by ID Devis, PATCH existing; no archiving
- **Step 9**: "Devis à suivre" gets every waiting devis; pages whose devis was won or lost get their Furious status; "Pris en charge" is never written (§18.17)
- **Step 11**: Syncs every won devis of the last `WON_DEVIS_LOOKBACK_DAYS` (365) days (all BUs, test devis excluded) to the "Devis gagnés" DB when `NOTION_WON_DEVIS_DATABASE_ID` is set: one row per devis with its own amount, one sub-item row per avenant; Furious fields refreshed only when changed, `Date signature` and team columns never overwritten; skipped when the avenants cannot be fetched (§18.16 to §18.18)
- **Step 12**: Syncs the lost devis of the same window, with their loss reasons, to "Devis perdus" when `NOTION_LOST_DEVIS_DATABASE_ID` is set (§18.18)
- **Step 13**: Compares the three Notion sales tables with Furious and logs the result (§18.17)
- Skips all emails (objectives + alerts) to avoid daily email noise
- Uses stable "État actuel" snapshot (no daily dated sheets)
- Provides complete data refresh including Notion sync for dashboard

**Bi-Monthly Emails Only** (15th and last day of month at 9 AM):
```bash
0 9 * * * cd /path/to/myrium && /path/to/venv/bin/python3 scripts/run_pipeline_scheduled.py --emails-only >> logs/cron.log 2>&1
```
- Wrapper script checks if today is 15th or last day before executing
- Sends objectives + alert emails without overwriting Sheets/Notion
- Still fetches data and computes alerts (needed for email content)
- Preserves daily data updates from full pipeline runs

**Daily Reconciliation** (07:30, after the pipeline):
```bash
30 7 * * * cd /path/to/myrium && /path/to/venv/bin/python3 scripts/run_reconciliation.py >> logs/reconciliation_cron_$(date +\%Y\%m\%d).log 2>&1
```
- Re-fetches Furious and checks the Envoyé / Signé Google Sheets and, since 2026-09-24, the Notion sales tables ("Devis à suivre", "Devis gagnés", "Devis perdus"); devis modified in Furious that day are checked the next day
- E-mails taddeo.carpinelli@merciraymond.fr on drift or when a check cannot run; always writes `logs/reconciliation_YYYYMMDD.json` (`--skip-notion` for sheets only)

**Monthly archive clean-up** (1st of the month, 05:00):
```bash
0 5 1 * * cd /path/to/myrium && /path/to/venv/bin/python3 scripts/archive_old_pages.py --apply >> logs/archive_old_pages_cron.log 2>&1
```
- Moves to the Notion trash the pages of the 5 scoped tables that are archived ("Pris en charge"), out of scope ("Dans le périmètre" unticked) and whose archive date is 6 months old or more; refuses more than 25 % of a table at once; report in `logs/archive_old_pages_YYYYMMDD.json` (§18.19)

**Weekly TRAVAUX Projection** (every Sunday at 11 PM):
```bash
0 23 * * 0 cd /path/to/myrium && /path/to/venv/bin/python3 scripts/run_travaux_pipeline.py >> logs/travaux_cron.log 2>&1
```
- Filters TRAVAUX proposals with probability ≥ 25% and `date` OR `projet_start` within rolling 365 days
- Sends projection email to Mathilde with Guillaume and Vincent in CC
- Syncs to Notion TRAVAUX projection database (also daily at 06:15 with `--skip-emails`); only changed properties are written, "Pris en charge" never (§18.18)
- **Step 6**: Fetches TRAVAUX projects created in last 7 days and syncs to "Récent projets travaux" Notion database

### 9.3 Dashboard Deployment
```bash
streamlit run src/dashboard/app.py --server.port 8501
```

---

## 10. Data Models & Structures

### 10.1 Proposal Data Model
Core fields from Furious + Computed fields (final_bu, alert_owner) + Financial fields (Annual/Quarterly totals).

### 10.2 View Data Structure
`ViewResult` dataclass containing DataFrame, BU summary, Typologie summary, and TS total.

### 10.3 Alert Data Structure
```python
{
    'title': str,
    'amount': float,
    'statut': str,
    'date': str,
    'assigned_to': str,  # All assignees
    'reason': str,       # Weird reason
    'probability': float # Follow-up prob
}
```

---

## 11-16. Maintenance & Troubleshooting

See original documentation for details on performance, security, error handling, future enhancements, design decisions, and troubleshooting.

**Key Troubleshooting Updates**:
- **Notion Duplicates**: Dual API support fix (2025-09-03 compatibility) prevents duplicates.
- **Dashboard Reading**: Unformatted value reading fixes currency issue.
- **Notion Schema Retrieval**: Enhanced schema fetching to handle Notion API changes where properties may be in data_sources (January 2026).

---

## 18. Recent Updates & Fixes

### 18.1 Notion Schema Retrieval Fix (January 2026)

**Critical Fix**: Resolved Notion API schema retrieval failures causing 400 errors when updating alert pages with properties that don't exist in database schema.

**Problem**:
- Notion API changes: Properties may be returned via `data_sources` endpoint instead of directly in `database.properties`
- Schema retrieval sometimes returned empty dictionary, causing code to attempt setting all properties (including optional ones like `Responsable`)
- When `Responsable` property was removed from Notion database, all page updates failed with "Responsable is not a property that exists" errors
- Schema fetch appeared to succeed (HTTP 200) but returned no properties, leading to unsafe property setting

**Solution**:
1. **Enhanced Schema Retrieval**:
   - Primary method: Fetch properties from `databases.retrieve().properties` (standard path)
   - Fallback method: If properties empty, resolve data source ID and fetch via `data_sources.retrieve()` (newer API)
   - Handles both old and new Notion API structures gracefully

2. **Fail-Safe Property Building**:
   - When schema is unknown (empty after all attempts), only send core required properties
   - Do NOT attempt to set optional properties (`Responsable`, `Commercial`, `Chef de projet`) when schema unavailable
   - Prevents 400 errors from missing properties while maintaining core functionality

3. **Schema-Aware Updates**:
   - All property building now checks schema before setting optional properties
   - Only sets `Commercial` and `Chef de projet` if they exist in schema
   - Gracefully handles databases where these properties haven't been created yet

**Code Changes**:
- `src/integrations/notion_alerts_sync.py`:
  - Enhanced `_get_database_schema()` to try data_sources endpoint as fallback
  - Added `_get_data_source_id_for_database()` helper (reuses existing method pattern)
  - Updated property building to use fail-safe approach when schema unavailable
  - Removed hardcoded `Responsable` property setting (now schema-aware)

**Technical Details**:
- Schema retrieval tries: `databases.retrieve().properties` → `data_sources.retrieve().properties` → empty dict (fail-safe)
- When schema is empty, only core properties sent: Name, ID Devis, Client, Montant, Statut, Probabilite, dates, URLs
- Optional People properties (`Responsable`, `Commercial`, `Chef de projet`) only set if they exist in schema
- All tests pass (55 passed, 1 skipped)

**Impact**: Notion sync now handles API changes gracefully and prevents 400 errors when properties are removed from databases. Schema-aware property building ensures compatibility with different database configurations. Users can safely remove `Responsable` property and add `Commercial`/`Chef de projet` without breaking sync functionality.

### 18.2 Date Window Updates: TRAVAUX Projection & VIP Notion Follow-ups (January 2026)

**Enhancement**: Extended date windows for TRAVAUX projection and owner-specific Notion follow-up alerts to improve long-term planning visibility.

**TRAVAUX Projection Changes**:
- **Rolling 365-day window**: Changed from 30/120-day windows to a unified rolling 365-day window
- **OR logic**: Proposals included if `date` OR `projet_start` falls within the 365-day window (today → today + 365 days)
- **Probability threshold**: Lowered from 50% to 25% (configurable via `TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD`)
- **Email copy updated**: Changed from "prochains 4 mois" to "prochains 12 mois" with OR logic description

**VIP Notion Follow-up Windows**:
- **Owner-specific forward windows**: Vincent (`vincent.delavarende`) and Adélaïde (`adelaide.patureau`) now get 365-day forward windows in Notion follow-up alerts
- **Email alerts unchanged**: Email alerts continue using the default 60-day forward window for all owners
- **Dual alert generation**: Main pipeline now generates alerts twice:
  - `alerts_for_email`: Default 60-day window (for email sending)
  - `alerts_for_notion`: Owner-specific overrides (365 days for VIPs, 60 days for others)
- **Backward window unchanged**: All alerts still require `date >= 1st of previous month` (backward check)

**Configuration**:
- `TRAVAUX_PROJECTION_START_WINDOW = 365` (replaces previous `TRAVAUX_PROJECTION_DATE_WINDOW` and `TRAVAUX_PROJECTION_START_WINDOW`)
- `NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER`: Dict mapping owner identifiers to custom forward window days
- `TRAVAUX_PROJECTION_PROBABILITY_THRESHOLD = 25` (lowered from 50)

**Code Changes**:
- `src/processing/travaux_projection.py`:
  - Updated `_matches_criteria()` to use OR logic with `date` and `projet_start`
  - Both fields use the same 365-day rolling window
- `src/processing/alerts.py`:
  - Added `followup_days_forward_by_owner` parameter to `AlertsGenerator.__init__()`
  - Added `_get_window_end_for_owner()` method for owner-specific window calculation
  - Updated `_needs_followup()` to use owner-specific forward windows
- `scripts/run_pipeline.py`:
  - Generates alerts twice: one for emails (default 60d), one for Notion (owner-specific)
  - Email alerts use `alerts_for_email`, Notion sync uses `alerts_for_notion`
- `src/integrations/email_sender.py`:
  - Updated TRAVAUX projection email summary to reflect OR logic and dynamic threshold

**Testing**:
- Added `tests/test_travaux_projection_window.py` (11 tests) covering:
  - OR logic with `date` and `projet_start`
  - Boundary conditions (today, 365-day limit)
  - Missing date handling
  - Other filters (BU, probability, status)
- Added `tests/test_alerts_followup_owner_windows.py` (7 tests) covering:
  - Default 60-day window behavior
  - VIP 365-day window behavior
  - Regular users still using default
  - CONCEPTION date field handling with owner overrides
  - Backward window still applying

**Impact**:
- TRAVAUX projection now captures proposals up to 12 months ahead, improving long-term resource planning
- VIP commercial teams (Vincent/Adélaïde) see extended follow-up opportunities in Notion (365 days) while email alerts remain focused on near-term (60 days)
- Lower probability threshold (25%) increases proposal coverage in TRAVAUX projection
- All existing tests pass, backward compatible with email alert behavior

### 18.3 Clickable Project Lists in Dashboard KPI Cards (January 2026)

**Enhancement**: Added interactive project list viewing capability to all KPI cards in the Streamlit dashboard, allowing users to drill down from summary counts to detailed project lists.

**Problem**:
- KPI cards displayed project counts but users couldn't see which specific projects contributed to each metric
- No way to verify accuracy of counts or access project details directly from the dashboard
- Limited visibility into project composition for each business unit, typologie, or production year

**Solution**:
1. **Modal Dialog Implementation**:
   - Small trigger button "🔎 Voir projets" added to each KPI card
   - Clicking opens a large modal dialog (`st.dialog` with `width="large"`) showing detailed project list
   - Dialog displays project title, dates, amounts, probability, and clickable Furious CRM links
   - Modal provides significantly more viewing space than popover (at least 4x larger)

2. **Accurate Project Filtering**:
   - Created dedicated filtering functions that replicate exact counting logic used in KPI calculations
   - `filter_projects_for_typologie_bu()`: Filters projects for BU/typologie combinations, including special "TS" case handling
   - `filter_projects_for_typologie_bu_production()`: Production-year specific filtering with year-based amount masks
   - Ensures project lists always match displayed counts (no discrepancies)

3. **Furious CRM Integration**:
   - `build_furious_url()`: Constructs direct links to Furious CRM proposal pages
   - Links displayed as clickable `LinkColumn` in dataframe for easy navigation
   - Users can jump directly from dashboard to CRM for detailed project information

4. **Comprehensive Coverage**:
   - **BU Summary Cards**: Clickable lists for each business unit (MAINTENANCE, TRAVAUX, CONCEPTION)
   - **Typologie Blocks**: Lists for each typologie within each BU
   - **Production Year Views**: Lists filtered by production year and BU/typologie
   - **Global/Monthly Summary**: Clickable lists for total project counts in snapshot and monthly views

**Technical Implementation**:
- `render_projects_popover()`: Main function that renders trigger button and dialog (despite name, uses `st.dialog` not `st.popover`)
- `prepare_projects_table()`: Prepares minimal project table with formatted dates, amounts, and Furious URLs
- `_show_projects_dialog()`: Dialog body function that displays header, project count, and interactive dataframe
- Filtering functions use same logic as `get_bu_amounts()` and `get_typologie_amounts_for_bu()` to ensure consistency

**Why `st.dialog` Instead of `st.popover`**:
- `st.popover` has no official API to control opened panel size (only supports label/help/on_click/disabled/use_container_width)
- `st.dialog` provides `width="large"` parameter for significantly larger viewing area
- Modal dialog approach is more reliable and maintainable than CSS hacks on popover containers

**Testing**:
- Added `tests/test_dashboard_kpi_project_filters.py` (7 tests) ensuring:
  - Project list counts match KPI card counts for BU summaries
  - Production-year filtered lists match production-year KPI counts
  - Typologie filtered lists match typologie KPI counts (including TS special case)
  - Filtering logic correctly handles edge cases and production year masks

**Code Changes**:
- `src/dashboard/app.py`:
  - Added `build_furious_url()`, `prepare_projects_table()`, `render_projects_popover()`
  - Added `filter_projects_for_typologie_bu()`, `filter_projects_for_typologie_bu_production()`
  - Modified `create_bu_kpi_row()`, `create_production_bu_kpi_row()`, `create_bu_grouped_typologie_blocks()`, `create_bu_grouped_typologie_blocks_production()`
  - Updated summary KPI cards in "Vue Globale", "Vue Mensuelle", and production views

**Impact**:
- Users can now drill down from any KPI card to see exact project composition
- Improved transparency and verification of dashboard calculations
- Direct access to Furious CRM from dashboard improves workflow efficiency
- Large modal dialogs provide comfortable viewing experience for project lists
- All tests pass, filtering logic validated against existing KPI counting functions

### 18.4 Recent TRAVAUX Projects Sync & Notion API 2025-09-03 Migration (January 2026)

**New Feature**: Added "Récent projets travaux" Notion database sync to weekly TRAVAUX pipeline.

**Business Need**: Track newly created TRAVAUX projects (last 7 days) in a dedicated Notion database for immediate visibility and resource planning.

**Implementation**:
1. **New Furious API Client** (`src/api/projects.py`):
   - `ProjectsClient` mirrors `ProposalsClient` pattern
   - Fetches from `/api/v2/project/` endpoint with GraphQL-like query syntax
   - Server-side filtering: `created_at >= now-7d` and `cf_bu == TRAVAUX`
   - Client-side validation as belt-and-suspenders
   - Fields: id, title, type, type_label, tags, start_date, end_date, created_at, project_manager, business_account, total_amount, cf_bu

2. **New Notion Sync Module** (`src/integrations/notion_recent_travaux_projects_sync.py`):
   - Upserts by `ID Projet` (dedupe key) to preserve manual Notion fields
   - Maps 11 properties: Name, ID Projet, Voir Furious (rich_text link), Type/Label/Tags (multi_select), Date début/fin/Creation (date), Chef de projet/Commercial (people), CA (number)
   - Uses existing `NotionUserMapper` for person property mapping
   - Preserves `Name` on updates (manual renames not overwritten)
   - Schema-aware property building (only sets properties that exist)

3. **Pipeline Integration** (`scripts/run_travaux_pipeline.py`):
   - Added Step 6 after TRAVAUX projection sync
   - Fetches recent projects and transforms to dict format
   - Non-blocking: errors don't fail entire pipeline
   - Respects `--dry-run` flag

**Notion API 2025-09-03 Migration**:
- **Problem**: Notion API 2025-09-03 introduced `data_sources` model. Databases with multiple data sources require using `data_source_id` for page creation, not `database_id`. Without pinned API version, clients defaulted to older behavior causing "multiple data sources not supported" errors. Schema retrieval also failed when properties were only available via `data_sources.retrieve()`.
- **Solution**:
  - All Notion clients now pin `notion_version="2025-09-03"` when instantiating `Client`
  - Page creation uses `parent={"data_source_id": ...}` when database has data_sources, falls back to `database_id` otherwise
  - Enhanced schema retrieval: tries `databases.retrieve().properties` → `data_sources.retrieve().properties` → fail-closed (refuses to create pages if schema unknown)
  - Prevents blank page creation when schema cannot be loaded

**Code Changes**:
- `src/api/projects.py`: New `ProjectsClient` with `fetch_recent_travaux(days=7)` method
- `src/integrations/notion_recent_travaux_projects_sync.py`: New sync module with data_sources support
- `src/integrations/notion_travaux_sync.py`: Enhanced schema retrieval, fail-closed behavior, data_source_id parent
- `src/integrations/notion_alerts_sync.py`: Pinned API version, data_source_id parent support
- `src/integrations/notion_users.py`: Pinned API version
- `config/settings.py`: Added `notion_travaux_recent_projects_database_id` setting
- `scripts/run_travaux_pipeline.py`: Added Step 6 for recent projects sync

**Configuration**:
- New env var: `NOTION_TRAVAUX_RECENT_PROJECTS_DATABASE_ID` (database or data_source ID)
- Database must be shared with Notion integration
- Required properties: Name (title), ID Projet (rich_text/number), Voir Furious (rich_text), Type/Label/Tags (multi_select), Date début/fin/Creation (date), Chef de projet/Commercial (people), CA (number)

**Testing**:
- Added `tests/test_projects_client_query.py`: Query building, date filtering, client-side validation
- Added `tests/test_recent_travaux_projects_sync.py`: Upsert mapping, multi-select parsing, people mapping, URL building
- All tests pass (97 passed, 1 skipped)

**Impact**:
- Weekly pipeline now tracks both long-term TRAVAUX projections (365-day window) and recent project creation (7-day window)
- Recent projects database provides immediate visibility into new TRAVAUX work for resource allocation
- Notion API 2025-09-03 compatibility ensures reliable sync with multi-data-source databases
- Fail-closed behavior prevents blank page spam when schema cannot be retrieved

### 18.5 Typologie Summary & Dashboard Fix (February 2026)

**Problem**: After moving to 9 typologie subcategories in Furious, "Résumé par Typologie" in Sheets and dashboard showed fragmented/duplicate buckets (DV, Paysage, TS, Animation, Entretien as standalone; Conception vs conception; same amount under Conception and Paysage).

**Root causes**:
1. **Dashboard** `calculate_realized_by_production_quarter` (typologie branch) used `typo_str.replace(',', ' ').split()` and divided amount by number of tokens → multi-word typologies (e.g. "Conception Paysage") were split into words and amounts misallocated
2. **Views** typologie summary used raw `cf_typologie_de_devis` + comma-split and added full row amount to each tag → double-counting and case-sensitive rows (Conception vs conception)
3. **No variant normalization**: Furious values like "Maintenance TS (+DT)" or "Travaux conception" were not mapped to canonical 9 subcategories

**Solution**:
- **Dashboard** (`src/dashboard/app.py`): Typologie branch of `calculate_realized_by_production_quarter` now uses `allocate_typologie_for_row` and primary-only (same as `calculate_realized_by_production_year`); no space-split, no amount division
- **Views** (`src/processing/views.py`): For `group_col == 'cf_typologie_de_devis'`, `_create_split_summary` uses `allocate_typologie_for_row` and assigns each row to one primary key only; BU branch unchanged
- **Typologie allocation** (`src/processing/typologie_allocation.py`): Added `CANONICAL_TYPOLOGIES`, `_TYPOLOGIE_VARIANT_TO_CANONICAL`, `normalize_typologie_tag()`; in `allocate_typologie_for_row`, tags are normalized after parse (e.g. "Maintenance TS (+DT)" → "Maintenance TS", "Travaux conception" → "Travaux Conception")

**Tests**: `test_typologie_allocation.py` (normalize_typologie_tag, variant allocation, inject_ts_tag expectation); `test_view_generator_summary_years.py` (`test_typologie_summary_uses_primary_only`); `test_dashboard_kpi_project_filters.py` (`test_calculate_realized_by_production_quarter_typologie_primary_only`)

**Reloading historical sheets**: To refresh "Signé Janvier 2026" (or any month) with the new typologie logic, run `scripts/backfill_google_sheets_2025.py --year 2026 --months 1 --overwrite-existing` (requires `SPREADSHEET_SIGNE_2026` in `.env`).

### 18.6 TRAVAUX Notion dates fix (February 2026)

**Problem**: TRAVAUX prevision DB had missing dates (Début Chantier, Date Signature) even when data existed in Furious; rerunning the pipeline did not fill them. Logs showed 79 existing Notion pages but only 61 proposals upserted per run.

**Root causes**:
1. **Property name mismatch**: Sync wrote only to "Date" and "Début projet"; the actual TRAVAUX projection DB uses "Date Signature" and "Début Chantier". Schema-aware sync never sent those properties, so no date fields were written.
2. **Partial update by design**: Only proposals in the current projection (BU=TRAVAUX, WAITING, probability ≥ 25%, date/projet_start in 365-day window) are synced; 18 Notion pages correspond to proposals outside that set and are never updated in a run.

**Solution**:
- **Sync** (`src/integrations/notion_travaux_sync.py`): In `_build_page_properties`, added mapping to "Début Chantier" (from `projet_start`) and "Date Signature" (from `proposal.signature_date`) when those properties exist in schema; kept "Date" and "Début projet" for backward compatibility. Added diagnostic log: count of existing pages not in current projection (no update this run).
- **Projection** (`src/processing/travaux_projection.py`): In `generate()`, proposal dict now includes `signature_date` (formatted from row) so sync can populate "Date Signature".
- **Debug script** (`scripts/debug_travaux_notion_sync.py`): Read-only script that fetches proposals, generates projection, queries Notion, prints overlap (in both / only in Notion / only in projection) and checks known project titles (e.g. Etude Axa Kennedy, Rue saint-florentin) for in-projection and in-Notion status.

**Tests**: `test_notion_typologie_devis.py`: `test_notion_travaux_sync_build_page_properties_debut_chantier_date_signature`, `test_notion_travaux_sync_build_page_properties_both_old_and_new_date_names`. `test_travaux_projection_window.py`: `test_travaux_projection_generate_includes_signature_date`.

**Impact**: Rerunning the TRAVAUX pipeline now fills "Début Chantier" and "Date Signature" in Notion for the ~61 proposals in the current projection (where Furious has values). The 18 pages whose proposals are outside the projection still do not get updated unless they re-enter the filter or a future "patch existing" flow is added. Deploy: push, pull on VPS, rerun `scripts/run_travaux_pipeline.py`; optionally run `scripts/debug_travaux_notion_sync.py` first to confirm overlap.

### 18.7 MAINTENANCE Won Notion Sync (February 2026)

**Feature**: Daily pipeline job that syncs all proposals **won in the current year** with BU = MAINTENANCE to a dedicated Notion database/datasource (`NOTION_MAINTENANCE_WON_DATABASE_ID`). Goal: load every such proposal from Furious, then POST if ID Devis does not exist in Notion, PATCH if it already exists (upsert by ID Devis); no archiving so the DB accumulates all years/months for grouping in Notion.

**Data source**: Built from full processed DataFrame (`df_processed`) in `run_pipeline.py` Step 10: filter by `statut_clean` in `STATUS_WON`, (`date_effective_won` or `date`) in current year, and `final_bu == "MAINTENANCE"`. Convert to list of dicts via `to_dict("records")` and pass to sync. Not from `views.won_month` (which is current-month only and was yielding empty lists when no MAINTENANCE won in that month).

**Implementation**:
- **New module** `src/integrations/notion_maintenance_won_sync.py`: `NotionMaintenanceWonSync` with upsert by ID Devis, schema-aware property building (Name, ID Devis, Client, Montant, Statut, Probabilite, Date, Début projet, Fin projet, Lien Furious, Commercial/Chef de projet, optional Mois signé). Supports both `database_id` and `data_source_id` (API 2025-09-03); on update, Name is not sent to preserve manual renames.
- **Config** `config/settings.py`: `notion_maintenance_won_database_id` from env `NOTION_MAINTENANCE_WON_DATABASE_ID`.
- **Pipeline** `scripts/run_pipeline.py`: After Step 9 (Notion alerts), Step 10 builds current-year MAINTENANCE won list from `df_processed`, calls `NotionMaintenanceWonSync().sync_maintenance_won(maintenance_won_items)` when sync_notion and DB id are set; logs count and created/updated/errors.
- **NaT fix**: In `notion_maintenance_won_sync.py`, `_format_date()` now returns `None` when value is pandas NaT (`pd.isna(value)` before calling `strftime`) to avoid `NaTType does not support strftime` when `signature_date` or `date_effective_won` are missing.

**Tests**: `tests/test_maintenance_won_sync.py` (format_database_id, format_date including NaT, extract_id_devis, schema_allows, build_page_properties, empty DB id skips, upsert strategy). Resume and env/docs: NOTION_MAINTENANCE_WON_DATABASE_ID, Step 10, and 7.6/3.2/9.2 bullets updated to "current year" and POST/PATCH.

**Impact**: Daily run syncs all MAINTENANCE won for the year to Notion without duplicates; deploy by setting `NOTION_MAINTENANCE_WON_DATABASE_ID` on VPS and ensuring the Notion DB/datasource is shared with the integration and has at least Name (title) and ID Devis for dedupe.

### 18.8 Follow-up & TRAVAUX Projection: "Pris en charge" for Leftovers (February 2026)

**Enhancement**: Follow-up and TRAVAUX projection Notion syncs now mark **leftover** pages (in Notion but no longer in the current run—e.g. won/lost or out of window) by ticking the **"Pris en charge"** checkbox so they are filtered out in Notion. Current-run pages get "Pris en charge" = false so they stay visible.

**Logic**: Leftover = existing page IDs in DB minus current run proposal IDs. For each leftover, sync calls `_update_page(page_id, {"Pris en charge": {"checkbox": True}})` (schema-aware). Current-run create/update sets "Pris en charge" = false so items reappear if they come back.

**Code**: `src/integrations/notion_alerts_sync.py` — `sync_followup_alerts`: stats `marked_taken_charge`; after upsert loop, leftover set from `existing_by_id.keys() - current_run_ids`; current-run properties set Pris en charge false via `_schema_allows`. `src/integrations/notion_travaux_sync.py` — `sync_proposals`: same pattern; `existing_not_in_projection` pages get Pris en charge true; current-run properties add checkbox false when `"Pris en charge" in schema`. `scripts/run_pipeline.py`: Step 9 success log includes `followup_marked_taken_charge`.

**Requirement**: Both Follow-up and TRAVAUX projection Notion DBs must have a "Pris en charge" checkbox property; Notion views use a filter on it to hide ticked rows.

**Tests**: `tests/test_notion_pris_en_charge_leftover.py` (5 tests) — current-run gets false, leftovers get true, skip when property not in schema (follow-up and TRAVAUX).

**Superseded on 2026-09-24**: the follow-up sync (§18.17) and the TRAVAUX projection sync (§18.18) no longer write "Pris en charge" at all.

### 18.9 Objectifs Signé: Production vs Signature (February 2026)

**Enhancement**: The Signé view Objectifs tab now clearly separates **Objectif Production** (vs Réalisé) and **Objectif Signature** (vs Signature, ex-Pur).

**Objectives data** (`src/processing/objectives.py`):
- **Production (Réalisé)**: 2026 `signe` metric updated to new BU/typologie numbers (e.g. CONCEPTION 850k, TRAVAUX 4.1M, MAINTENANCE 1.3M; typologies Conception DV 50k, Paysage 700k, Concours 100k; Travaux DV 1.3M, Conception 800k, Direct 1.7M; Maintenance Entretien 1.25M, TS 300k, Animation 50k). 11-month accounting (August 0).
- **Signature (Signé)**: New 2026 `signature` metric with BU totals (CONCEPTION 822 745€, TRAVAUX 4 920 663€, MAINTENANCE 459 800€) and typologie breakdown; CONCEPTION typologie prorated from 822 745. Helpers `objective_for_month/quarter/year` accept metric `"signature"`; validation allows optional `signature` per year.

**Dashboard** (`src/dashboard/app.py`), Signé view only when year has `signature` (e.g. 2026):
- Tables (Par BU, Par Typologie; Période, Trimestre, Année) show one wide table: **Objectif Production | Réalisé | Reste | % | Objectif Signature | Signature | Reste Sig | % Sig**. Column "Pur" renamed to "Signature". Styling applied to both Reste and Reste Sig, % and % Sig.
- **Maintenance Entretien – Début 2026**: See **§18.11** for resolution order (Sheets → file → Notion → secret), daily jobs, and **Réalisé** semantics. For BU **MAINTENANCE** and typologie **Maintenance Entretien** in 2026, **Objectif Production / Réalisé** uses **only** the prorated début d’année (no pipeline production mixed in). Core Notion sum: `src/integrations/notion_entretien_start.py`; persistence: `src/integrations/entretien_start_store.py`, `GoogleSheetsClient.read_entretien_start_parameter` / `write_entretien_start_parameter` (see §18.11).
- **Line charts**: "Objectif Signature" series added when `show_signature_objective`; legend "Pur" → "Signature" when in Signé two-block mode.

**Config**: `MAINTENANCE_ENTRETIEN_START_2026`, `NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID` or `NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID`, `NOTION_API_KEY`; Streamlit / VPS `.env` must match for dashboard vs jobs.

**Tests**: `tests/test_objectives_2026.py` updated for new production (signe) values and signature metric (BU totals, CONCEPTION typologie prorate sum); `test_2026_has_signature_metric`; `test_2026_envoye_equals_signe` replaced by 2026 signe/signature structure check.

### 18.10 Objectifs tab: projection charts, Pur-by-month, typo expanders, colors (March 2026)

**Enhancement**: Overhaul of the Objectifs tab (Signé/Envoyé) end section: projection charts replace the former monthly line charts; third chart and tables use consistent BU/Typologie coloring.

**Projection charts (Par BU and Par Typologie)**  
- Tabs **Réalisé (production)** vs **Signature (Pur)**. **Réalisé**: for **MAINTENANCE** / **Maintenance Entretien** in **2026** when début d’année is resolved, monthly slice **1/11** (August 0); chart **extends through December** so cumulative matches full début d’année; **Global** = sum of per-series Y. **Signature (Pur)**: Pur-based projection for all BUs including MAINTENANCE (entretien production schedule does not apply there).  
- **Par BU**: AUTRE excluded from chart and table.  
- Helpers: `get_remaining_months_excl_aug`, `get_months_range`, `compute_projection_and_objective`, `plot_objectives_projection_chart` in `src/dashboard/app.py`.  
- Tables: "À produire par mois" with `create_colored_table_html` / `BU_COLORS` / `TYPOLOGIE_COLORS`.

**Third chart: Signature (Pur) par mois**  
- Two charts (Par BU, Par Typologie): Pur amount per month Jan–current month only; **objectives by month** (dotted line from `objective_for_month`), not flat annual.

**Typologie tables**  
- The three typologie tables (Période, Trimestre, Année) are inside `st.expander(..., expanded=False)`; BU tables unchanged.

**Tests**: `tests/test_dashboard_objectives_projection.py` (remaining months excl. Aug, months range, projection math, to_produce_per_month, MAINTENANCE entretien 2026).

### 18.11 Maintenance Entretien via Sheets Paramètres, Sheets 429 retry, dashboard layout & projection fix (March 2026)

**Problem**: Streamlit / GitHub has no shared disk with the VPS job that wrote `data/entretien_start_2026.json`, so the dashboard could not rely on that file alone. Google Sheets **write requests per minute** often returned **429** when running full `write_all_views` twice in a row (many per-row `worksheet.update` calls).

**Sheets — Paramètres (état file, same ID as État actuel)**  
- `src/integrations/google_sheets.py`: `write_entretien_start_parameter` / `read_entretien_start_parameter` on worksheet **Paramètres** only (clear + fixed A1:C2 layout); read requires configured état spreadsheet ID (no create-on-read).  
- Parser + constants: `src/integrations/entretien_parametres_sheet.py` (`parse_entretien_parametres_rows`, tests without importing gspread-heavy path).  
- `src/integrations/__init__.py`: **lazy** `GoogleSheetsClient` via `__getattr__` so light imports do not load gspread.

**Pipelines**  
- `scripts/run_sheets_update.py`: after Notion fetch success for 2026, writes Paramètres (reuses same `GoogleSheetsClient` as Step 6 when not dry-run). Clearer skip logs when API key vs datasource/database ID is missing.  
- `scripts/run_pipeline.py`: after Step 7 Sheets (even if `write_all_views` errors on Signé), **Entretien début 2026** block runs Notion → JSON → Paramètres for `current_year == 2026` (same env vars as daily job).

**429 mitigation**  
- `google_sheets.py`: `_with_sheets_write_retry` (~65s sleep, up to 4 attempts) on `worksheet.clear` / `worksheet.update` / `batch_update` paths used by views and Paramètres.

**Dashboard** (`src/dashboard/app.py`)  
- **Vue Globale / Vue Mensuelle — Toute année**: **Analyse par typologie** expander moved **below** BU charts; mensuelle Plotly keys prefixed by month (`_m_prefix`).  
- **`render_single_production_view`**: BU bar first, typologie blocks + typo bar inside expander.  
- **Objectifs 2026**: `_resolve_entretien_start_2026` for **`selected_year == 2026`** (Signé **and** Envoyé), not Signé-only; projection captions distinguish Sheets / file / Notion / secret.  
- **Projection chart bugfix**: for entretien-only MAINTENANCE (Réalisé), months **after** `m_now` now add **1/11 per month** (excl. August) so **December cumulative = début d’année**; **Global** = sum of per-item series. `compute_projection_and_objective`: `projected_total` for that case = full entretien amount.

**Env (VPS / cron)**  
- `NOTION_API_KEY` plus **`NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID`** *or* **`NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID`**; pipeline must load `.env` from repo root (generic skip message previously masked “ID missing” when key was set).

**Tests**  
- `tests/test_google_sheets_entretien_parametres.py` (parser).  
- `tests/test_dashboard_objectives_projection.py`: `test_compute_projection_maintenance_entretien_debut_2026` (requires dashboard deps e.g. plotly for collection if importing `app`).  
- `tests/test_entretien_start_store.py` (JSON path under `data/`, staleness window, mocked Notion write).

**Next steps (ops)**  
- Deploy updated `google_sheets.py` / `run_pipeline.py` on VPS so 429 retry + Paramètres run apply; ensure Notion datasource/database env is set for entretien step. Long-term: batch fewer, larger `values.update` calls to reduce write volume (optional).  
- After deploy: spot-check Signé **Objectifs 2026** — MAINTENANCE and Maintenance Entretien **Réalisé** match prorated début d’année only (no pipeline double-count); confirm `run_sheets_update.py` (or full pipeline) ran so Paramètres / JSON are fresh for Cloud dashboards without VPS disk.

### 18.12 Objectifs Maintenance — Réalisé table rule & JSON store tests (April 2026)

**Plan closure**: Table logic in `src/dashboard/app.py` skips `calculate_production_period_with_carryover` / `calculate_production_amount_with_carryover` for BU **MAINTENANCE** and typologie **Maintenance Entretien** when 2026 début d’année is resolved; **Réalisé** = prorated slice only (`realized_prev` = 0). Same rule feeds projection (`entretien_start_2026` on `compute_projection_and_objective` / `plot_objectives_projection_chart`) and optional `plot_objectives_line_chart` via `_entretien_start_monthly_series`. **Notion source** unchanged: sum of property **Total HT Cette année** in `notion_entretien_start.py`. **Resolution order** and Sheets **Paramètres**: §18.11; **file store API**: `entretien_start_store.py` (`get_store_path`, `fetch_and_write_entretien_start_2026`, `read_entretien_start_2026_from_file`).

### 18.13 Manual Projects, Per-Project Overrides, Inline Editing & VPS Docker Deploy (June 2026)

**Feature**: Modify and create projects directly from the dashboard, with edits persisting across the daily Furious→Sheets recompute. Two persistent JSON stores (under `data/`, bind-mounted on VPS) hold user data; a shared merge layer re-injects it both at dashboard read-time and in every pipeline run, so user edits survive the daily re-pull from Furious.

**Stores** (atomic tempfile+`os.replace` writes, `RLock`):
- `src/processing/overrides_store.py` — `OverridesStore` / `ProjectOverride` keyed by proposal id. Two override kinds: **`input_overrides`** (amount, dates, BU, typologie, probability… applied BEFORE the engine → clean quarterly recompute) and **`quarter_overrides`** (direct `Montant Total Q{q}_{year}` cells applied AFTER the engine). `quarter_overrides` win; overrides are **sticky** (never expire until reset). `migrate(old,new)` moves overrides when a manual is linked to a real Furious id. Store: `data/overrides.json`.
- `src/processing/manual_projects_store.py` — `ManualProjectsStore` / `ManualProject`; sequential ids `MAN-{year}-{NNNN}`. Store: `data/manual_projects.json`.

**Merge layer** (`src/processing/manual_and_overrides.py`, pure functions + factories `get_overrides_store` / `get_manual_projects_store`):
- `apply_input_overrides(df, store)` — before engine; mutates whitelisted input cols, recomputes `probability_*` aux.
- `inject_manual_projects(df, store, engine)` — after engine; one row per manual via `RevenueEngine.process_single_row` (new method in `revenue_engine.py`); id = `MAN-…`.
- `apply_quarter_overrides(df, store, years_to_track)` — after engine; sets overridden quarter cells, re-sums `Montant Total {year}`, recomputes every `Montant Pondéré`. **June change**: for overridden rows it now also re-derives **`amount`** (and `amount_pondere` when present) as the **sum of all year totals**, so the displayed **Montant follows the breakdown** (editing a quarter/year moves the deal total; raw Furious value no longer diverges). No conservation check — totals are the literal sum of typed quarters.

**Pipeline wiring** (3 hook points, `scripts/run_pipeline.py` ~lines 237-253 and `scripts/run_sheets_update.py`): `apply_input_overrides` after `DataCleaner` → `RevenueEngine.process` → `inject_manual_projects` → `apply_quarter_overrides`, then ViewGenerator/Sheets write. So the daily 06:00 cron re-applies all user edits on top of fresh Furious data before writing the Sheet. Validated via a read-only `--dry-run` (override loaded & applied on 2073 real proposals; cross-year T4-2026→T1-2027 split correctly re-summed both years + pondéré). `src/integrations/google_sheets.py` id filter accepts `MAN-` ids.

**Dashboard** (`src/dashboard/app.py`):
- `_apply_user_layer` re-applies the merge layer on data read from Sheets so edits show immediately (before the next cron). Cache invalidated via `stores_signature` = combined mtime of the two JSON files (`_stores_mtime_signature`). **Superseded by §18.15**: `_apply_user_layer` now applies **quarter overrides only**; manual-project injection moved to `load_year_data` (`_inject_year_manuals`), status- and home-month-aware, to avoid per-sheet multiplication and wrong-pipe placement.
- **Inline editor** replaced the old nested-dialog approach (which crashed with "Dialogs may not be nested"). `_show_projects_dialog` now renders one `st.data_editor(num_rows="dynamic")` showing **all tracked years at once** (Total + T1–T4 per year). Add a project via the trailing "+" row; delete only manual (`MAN-`) rows (Furious deletions refused and restored next refresh). Helpers: `_build_editable_projects_frame` (resets to RangeIndex so dynamic add works + index hidden), `_build_projects_editor_config` (SelectboxColumn BU/typologie, NumberColumn €/%, DateColumn; read-only = id/year totals/pondéré), `_persist_projects_table_changes` (diffs edited vs snapshot → routes to input/quarter overrides, manual add/update/delete), `_create_manual_from_row`, value coercers `_to_iso_date`/`_values_differ`/`_normalize_for_store`. **Read-only columns greyed** (`#f0f2f6`) via a pandas `Styler` (Streamlit applies Styler styles only to non-editable columns; verified compatible with `num_rows="dynamic"` in Streamlit 1.56). Editable cells can't be colored (canvas grid; no `column_config` color API).
- Sidebar **`render_pending_links_sidebar`** (`src/dashboard/components/pending_links_panel.py`): list/create/edit/delete manuals and "Lier à Furious" (migrates overrides to the real id). Dialog components `create_manual_project_dialog.py` / `edit_project_dialog.py` remain, now used only by this sidebar (not by the in-table flow).

**Tests**: `tests/test_overrides_store.py`, `tests/test_manual_projects_store.py`, `tests/test_manual_and_overrides.py` (input/quarter overrides, manual injection, `process_single_row`, and `test_apply_quarter_overrides_syncs_amount_with_breakdown` for the June amount-sync rule).

**VPS deployment (Docker + Traefik)**: dashboard runs as a side-car container alongside existing n8n (untouched). `Dockerfile`, `.dockerignore`, `deploy/docker-compose.yml` (binds `../data` and `../config/credentials`, joins `root_merci_net`, Traefik labels), `deploy/dashboard.env.example`. Cron repo's `data/` symlinks to the shared host dir so cron + container read/write the same JSON. Deploy: `git pull` then `cd deploy && docker compose up -d --build`; cron picks up code changes with no rebuild. **Known HTTPS caveat**: Let's Encrypt rate-limits the shared `*.hstgr.cloud` parent domain (HTTP 429) → temporary URL serves a self-signed cert. Fix runbook (pivot to `dashboard-commercial.merciraymond.fr` via one A record + one compose label) in **`deploy/HTTPS_FIX_PIVOT_TO_MERCIRAYMOND.md`**.

**Ops note**: `.env` values containing spaces must be quoted (e.g. `SMTP_PASSWORD="…"`) or a shell `source .env` truncates them; production crons use python-dotenv and are unaffected.

### 18.14 Budget Export — "Générer budget {année}" xlsx (June 2026)

**Feature**: Sidebar button (below the Année selector in `src/dashboard/app.py`) that generates a one-sheet `.xlsx` replicating the manual **"Budget {Y} avec légende"** Google Sheet (reference `Budget 2026.xlsx`), then either offers a local download or, when `BUDGET_EXPORT_DRIVE_FOLDER_ID` is set, uploads it to Drive as a **native Google Sheet** and shows the link. Two-step `st.session_state` flow avoids recompute on rerun.

**Modules**:
- `src/integrations/budget_export.py` — `build_budget_workbook(year, *, bu_totals, portefeuille_debut_annee, portefeuille_running, today)` builds the sheet; helpers `_compute_bu_amounts` (legacy single-df fallback), `_sum_production_by_bu(df, year, bu, weighted)`, `drop_stale_sent_carryover(df, budget_year, today)`. Builder also accepts legacy `df_processed` path (computes bu_totals itself).
- `src/integrations/drive_uploader.py` — `upload_xlsx_as_google_sheet(...)` reuses `GoogleSheetsClient` credential resolution + Drive API (`google-api-python-client`) to upload+convert to a Sheet in the target folder.
- `_build_budget_xlsx_for_year(year)` (app.py) — orchestrates data + portefeuille + calls builder.

**Data methodology** (mirrors the Objectifs tab's production-year aggregation, `load_aggregated_production_data`):
- **Devis Signés (CONCEPTION/TRAVAUX)** = `load_aggregated_production_data(year,"Signé")` summed on raw `Montant Total {Y}` → carries prior-year signatures producing in Y into the pipe.
- **MAINTENANCE "Nouveaux contrats {Y}"** = won signed **in Y only** (`signed_year == year` filter); prior maintenance is carried by the portefeuille, so no production carryover here (avoids double-count).
- **Devis Potentiels** = `load_aggregated_production_data(year,"Envoyé")` on weighted `Montant Pondéré {Y}` — **same value as the Objectifs "Envoyé" view** (`use_pondere=True`).
- **Devis Envoyés** = same sent dataset, raw `Montant Total {Y}`. **Won (Signé) and sent (Envoyé) never mix** — disjoint datasets; legacy `_compute_bu_amounts` also updated so Envoyés = waiting-only.
- **Stale carryover pruning (budget export ONLY, not the dashboard tab)**: `drop_stale_sent_carryover` drops prior-year-sent (`signed_year < year`) waiting proposals whose `projet_start` is overdue (< today); current-year-sent and future/unknown-start kept. Decided via user Q&A (scope=budget-only, rule=past-years-only). Caveat: a genuine multi-year deal sent last year with an overdue start is also pruned (could switch to `projet_stop` if needed).
- **Portefeuille sites au {today}** (L21) = live Notion sum via `fetch_maintenance_entretien_start_2026(api_key, ds_id)` (the same "Total HT Cette année" source as Entretien, grows as sites are added); fallback = start-of-year `_get_entretien_start_2026_value()`. The earlier wrong-property module `notion_maintenance_portefeuille.py` was **deleted**.

**Layout/visuals** (single sheet; the old second "Maintenance" detail tab and all its helpers `_build_sheet2`/`_compute_maintenance_entries`/date helpers were **removed**):
- BU band colors = dashboard `BU_COLORS`: CONCEPTION `2D5A3F` green, TRAVAUX `F4C430` gold, MAINTENANCE `7B4B94` purple, TOTAL `3D85C6` blue; row-18 light sub-tints; thin borders on the whole grid (D17:P25).
- **Légende**: rich-text (`CellRichText`/`InlineFont`) so the terms "Devis Signés/Potentiels/Envoyés" are **bold**; bordered box; `E11:L11` merged.
- **Blank-block merges** copied from the reference exactly: `D21:J22`, `N21:P22`, `D24:P24` (plus existing per-BU value merges). Verified output merge set == reference.
- Maintenance **Portefeuille** (row 21) and **Total sécurisé** (row 22) rows: light-purple fill `D9D2E9`; column **K widened 15.4→18** and labels `K21`/`K22` wrapped (rows 21-22 height 30) so they aren't clipped by the merged blank block.

**Data fact (no expected signature date)**: Furious proposals (`src/api/proposals.py` `ProposalFields`) expose only `date` (devis date), `signature_date` (Furious e-signature date: **empty on every devis** as of 2026-09, only avenants carry one; won devis are locked for API updates, see §18.16), `projet_start`/`projet_stop` (production window), `created_at`/`last_updated_at`, and `probability`. There is **no forecast/expected signing date**; `projet_start` is the only forward-looking date.

**Config / deps**: env `BUDGET_EXPORT_DRIVE_FOLDER_ID` (in `deploy/dashboard.env.example`); `requirements.txt` adds `openpyxl>=3.1.0`, `google-api-python-client>=2.100.0`.

**Tests**: `tests/test_budget_export.py` (13) — `_compute_bu_amounts` (won/waiting, Envoyés waiting-only), `_sum_production_by_bu` carryover, `drop_stale_sent_carryover`, single-sheet builder structure/legend/merges, precomputed `bu_totals` path.

**Ops**: set `BUDGET_EXPORT_DRIVE_FOLDER_ID` on the VPS and ensure the Drive folder is shared with the service account; rebuild the dashboard Docker image so `openpyxl` + Drive client are installed. Local download over the self-signed temporary HTTPS URL is blocked by Chrome → use the Drive upload path (or the proper domain per `deploy/HTTPS_FIX_PIVOT_TO_MERCIRAYMOND.md`).

### 18.15 Manual WON Projects (oral agreements) + duplicate-prevention (June 2026)

**Goal**: enter a deal in the dashboard **before Furious has it**, including ones already **won** (oral agreement). It feeds the Signé/production views + budget like a real won deal, and is linked to its real `ID Devis` later — with **zero duplicates** as a hard constraint (extends §18.13, which previously allowed waiting-only manuals).

**Model** (`src/processing/manual_projects_store.py`): `ManualProject` gains **`signature_date`** (field + `from_dict`/`add`/`update` plumbing). When the status is won, this date anchors the deal's month/year.

**Injection** (`src/processing/manual_and_overrides.py`): `_manual_to_row` renamed to public **`make_manual_row`**; for a won manual it sets `signature_date` **and** `date_effective_won` from the manual (else `NaT`), so `ViewGenerator._filter_won_month` (+ orphan sweep) routes it into a Signé sheet. Pipeline path unchanged (inject ALL manuals → `ViewGenerator` status-filters): a won manual lands in Signé, a waiting one in snapshot/Envoyé (verified end-to-end).

**Dialogs**:
- `create_manual_project_dialog.py` — `STATUT_OPTIONS` now = waiting + **won** (`gagné`/`signé`, `_is_won_statut` vs `STATUS_WON`); a **Date de signature** field shows only for won; passed to `store.add(signature_date=…)`.
- `edit_project_dialog.py` + sidebar trigger — manuals (`is_manual`) can edit **statut + signature_date** (Furious-edit path untouched); `trigger_edit_project_dialog` gains `statut`/`signature_date`.

**Duplicate blind spots fixed**:
1. **Notion MAINTENANCE-Won** (`scripts/run_pipeline.py` Step 10): added `mask_not_manual = ~id.startswith("MAN-")` so manuals are **excluded** from that DB (decided w/ user — Notion mirrors real CRM only; a `MAN-` page would duplicate the deal once linked).
2. **Dashboard multiplication / wrong pipe** (`src/dashboard/app.py`): the old per-sheet `_apply_user_layer` injection multiplied a manual across every monthly sheet (`load_year_data` concats with no id-dedup) and ignored status. Now `load_worksheet_data` does overrides-only; new **`_inject_year_manuals(df, year, view_type)`** runs once post-concat in `load_year_data` (+ empty-result path): drops any `MAN-` rows, then injects **WON→"signe"** / **WAITING→"envoye"** only, for the manual's **home year**, with `source_sheet` = its home-month sheet name → **counted exactly once**, correct pipe & month. `etat`/snapshot untouched. Also fixes the same latent bug for existing waiting manuals.
3. **Link window** (deal in Furious but not yet linked): sidebar `render_pending_links_sidebar` (`src/dashboard/components/pending_links_panel.py`) now (a) flags manuals open **>7 days** ("à lier ?"), and (b) shows **soft auto-match** suggestions (same normalized client + amount ±5%) as one-click link buttons. Candidates via cached `_load_link_candidates(year, stores_signature)` (current-year Signé+Envoyé, `MAN-` excluded), passed as a lazy loader (computed only when a link panel opens). `_perform_link` shared by suggestions + manual confirm.

**Tests**: `tests/test_manual_projects_store.py` (signature_date persistence; update statut+signature_date); `tests/test_manual_and_overrides.py` (`make_manual_row` won vs waiting; **won manual routes into Signé view only** via real `ViewGenerator`). 66 passed across touched areas (manual stores, overrides, budget, maintenance-won, views, objectives, both dashboard test files). Note: full `pytest` hangs on a **pre-existing** network-dependent test (collection OK; unrelated to this change).

### 18.16 Devis gagnés in Notion (signature date), Furious pagination fix, sites monthly history (September 2026)

**Need (sales team)**: every devis won since 1 Jan 2026, split "gagné pas encore signé" / "gagné et signé", followed **by month** (her explicit choice); a 2025 vs 2026 analysis (sent devis, conversion, losses and reasons, DV); and, for Entretien, a monthly count of managed sites next to the signatures chart of the "Analytique" page.

**Furious facts checked on 2026-09-23**: `signature_date` is empty on all 2,305 devis (Furious e-signature unused). Won = `Gagnés en cours` / `Gagnés et finis`, and each win creates a project; the devis `date` is re-stamped at the win (≈ project creation) and at the loss (85 % within 3 days of the last update). **The API refuses any change on a won devis**: `POST /api/v2/proposal/` with `{"action":"update","data":{...}}` answers "La modification d'un devis gagné est interdite" (no-op test on devis 263464 left it untouched). Projects do accept updates. Consequence: the signature date lives in Notion; a Furious write-back would need a project custom field created by an admin.

**Notion DB "🖋️ Devis gagnés (synchro Furious)"** (db `c5d23eb32a3c45278ef066bf14066269`, data source `828e0a50-3487-4f8a-a0a9-56275fc27776`, under "🏮 Suivi Commercial (Devis)"): Nom, ID Devis (upsert key), Client, BU, Typologie, Montant HT, Statut Furious, Date gagné, **Date signature**, Statut signature (formula ⏳/✅), Commercial, Chef de projet, Début/Fin projet, Lien Furious (+ Mois gagné / Mois signature text formulas, unused by the views).
- `src/integrations/notion_won_devis_sync.py`: `select_won_devis(df, start_date)` keeps won statuses, date ≥ start, no `MAN-` rows, no test titles (`TEST_TITLE_RE`: 54 test devis in Furious, 4 of them won in 2026). `NotionWonDevisSync` reuses the plumbing of `NotionMaintenanceWonSync`. Rules: Furious-owned properties are written only when their value changed (compared page vs payload, else counted "unchanged"); `Nom` is set at creation only; **`Date signature` is human-owned**, never overwritten or cleared, filled from Furious `signature_date` only when Notion is empty; a page whose devis left the won statuses gets `Statut Furious` relabelled; orphans are left alone; 409/429/5xx retried with backoff. Avenant rows `<devis>_AV<id>` (cross-year addons from `merge_addons_into_proposals`) are synced too, linked to the parent devis, and arrive with real Furious signature dates.
- Step 11 in `scripts/run_pipeline.py` (after step 10, gated by `sync_notion` and `NOTION_WON_DEVIS_DATABASE_ID`); settings `notion_won_devis_database_id`, `won_devis_sync_start_date` in `config/settings.py`.
- `scripts/run_won_devis_sync.py [--dry-run]`: steps 1 to 4.5 of the pipeline, then only this sync (first load: 243 rows = 234 devis + 9 avenants, 4.64 M€).
- `scripts/setup_won_devis_views.py [--page <id>]`: views written through the **Notion REST views API** (`/v1/views`): ⏳ not signed grouped by month of Date gagné, ✅ signed grouped by month of Date signature (newest month first), 📊 amount won per month stacked by BU, 🖋️ amount signed per month, 📋 all. The MCP view DSL cannot express this (date grouping defaults to "relative", chart x-axis to "day", formula grouping and STACK BY are silently dropped). `--page` requires the page to be shared with the integration.

**Pagination bug fixed** (`src/api/proposals.py`, `proposal_addons.py`, `projects.py`): ordering on `date` alone made offset pagination return 4 devis twice and miss 4 others on every run (e.g. won devis 263037 absent from the dashboard and the MAINTENANCE Notion DB). Queries now order on `date desc, id desc` (`id_system` for addons, `id` for projects); `ProposalsClient.fetch_all` also drops duplicate ids with a warning.

**Tests**: `tests/test_won_devis_sync.py` (12: selection, test titles, mapping, signature protection, unchanged skip, relabel/orphans, retry, avenant link, pagination order); 86 passed across the related suites.

**Sales page** (private draft "🖋️ Suivi des devis gagnés et signés", `3e4d927802d781fbb195fa90667b96a2`, to be moved by Tadd): links to the DB views, then the 2025 vs 2026 analysis at 23/09/2026 as native tables plus a self-contained HTML block with inline SVG charts. Notion HTML blocks run sandboxed: no network, no Notion data, so they are snapshots only (live figures stay in native databases and charts). Definitions: cohorts by `created_at`; "envoyé" excludes Brief/En cours drafts and "Devis en doublon"; win date = project creation; loss date = devis date; DV = title prefix "(DV…)" or typologie containing DV. Headline results (1 Jan to 23 Sep): 384 devis sent vs 338, 16.96 M€ vs 10.33 M€; won by win date 4.54 M€ vs 2.62 M€ (top 5 deals = 39 %); conversion at equal age (90 days) unchanged overall (45 %) but 22 % vs 31 % for devis ≥ 15 k€; 133 devis created in 2026 still pending (10.9 M€); 170 real losses since 1 Oct 2025 (9.08 M€); 95 DV devis vs 115.

**Sites monthly history (Entretien)**: DB "📈 Sites gérés par mois" (db `5f285a54a8354408a3a59feed7d6fb09`, data source `3fd02456-d7fe-4a38-b44a-f5b6d8e05f62`, under "🎆 Entretiens X IA" next to Sites): Mois (key `AAAA-MM`), Date (1st of month), Sites gérés, Sites extérieur (EXT + INT/EXT), Sites intérieur (INT + INT/EXT), Portefeuille annuel HT (sum of Total HT Annuel), Source (Relevé / Reconstitué), Relevé le. Rule for day J: the site has a name, its contract has started (Date debut contrat ≤ J, else page creation date), and it is not Perdu/Archivé with an end date already past. Jan–Aug 2026 rebuilt from contract dates (121 → 155 sites), Sep = 160 sites, 1.39 M€. Linked charts (sites: line, portfolio: columns) appended at the end of the Analytique page.
- n8n workflow `deploy/n8n/sites_geres_par_mois.json` (for n8n.srv1082911.hstgr.cloud, credential "Notion Rapport" `ciGEF85fAeNKNsVu`): daily 07:00 Paris → paginated query of Sites → Code node count (throws instead of writing when the read is incomplete or empty) → upsert of the current month row (past months stay frozen). The HTTP nodes set `lowercaseHeaders: false`: otherwise n8n's Notion credential, which only adds `Notion-Version: 2022-02-22` when it cannot see that header, overrides 2025-09-03 and data source endpoints answer "Invalid request URL". Both branches (update, create) tested end to end on a throwaway n8n 1.76. `.gitignore` gains `!deploy/n8n/*.json`.

**Next steps (ops)**:
- ~~Deploy step 11 and the pagination fix~~: done on 2026-09-23 (PR #4, `NOTION_WON_DEVIS_DATABASE_ID` set on the VPS).
- Import and activate the n8n workflow.
- Move the draft page to the sales space and share it with the integration; optionally run `setup_won_devis_views.py --page <id>` for in-page monthly views.
- Optional Furious write-back of the signature date through a project custom field (admin action first).
- Security: the live n8n workflow "Projet Add Furious PIPELINE" hardcodes a Notion token and the Furious API password in HTTP nodes (also present in local backups): move them to n8n credentials and rotate them.

### 18.17 Waiting devis without window, won devis over 12 months, team-owned columns, Notion ↔ Furious check (September 2026)

**Decisions (2026-09-24)**: "Devis à suivre" must hold **every** waiting devis (they are few, only the current ones); "Devis gagnés" covers a **rolling 12 months** (a "this year" filter is built in Notion); **"Pris en charge" belongs to the team**, like "Commentaire"; the won table gets **the same team columns** as the follow-up table; both syncs are **checked against Furious**.

**Follow-up table ("Devis à suivre")**
- `AlertsGenerator(followup_window=False)` (step 6, Notion only): every WAITING devis, whatever its dates. Emails keep the 60-day window. The per-owner 365-day windows (`NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER`) are gone. First run: 312 waiting devis, 8 new pages (6 dated Nov 2026 to May 2027, 2 dated July 2024).
- `NotionAlertsSync.sync_followup_alerts(alerts, status_by_id)`:
  - never writes "Pris en charge", "Commentaire", "Date archivage" or "Origine Transfo". Before, it unticked "Pris en charge" on every current page each morning (a tick typed by someone did not survive the night) and ticked it on devis that had left the list;
  - sends only the properties whose value changed (`src/integrations/notion_values.py` reduces payloads and pages to comparable values). On the first check, 0 of the 304 existing pages needed a write; before, all of them were rewritten daily;
  - a page whose devis is no longer waiting gets its **real Furious status** in "Statut" ("Perdu", "gagnés en cours", ...), so it leaves the views but keeps its comment and history. A page whose devis is gone from Furious is left alone (counted as an orphan);
  - "Statut" is a Notion *status* property, whose options the API cannot create. A Furious label is matched to an existing option without case ("Envoyée(s) attente réponse" → "envoyée(s) attente réponse"). With no match, "Statut" is left out of the payload, because an unknown option would reject the whole page update, and the label is logged.
- Views: the old sync's ticks were what hid finished devis, through the quick filter "Pris en charge = non" in Vue personnelle, Vue Globale and Pipe Adélaïde. `scripts/setup_followup_views.py --apply` adds "Statut is brief / en cours / envoyée(s) attente réponse / envoyée(s) en attente de réponse", combined with AND, to the filter of the 5 views of the data source, linked views included (Vue personnelle, Vue Globale, Pipe Adélaïde, Pipe Valentin, Pipe Vincent). Quick filters, sorts and grouping are untouched, and the script is idempotent. A blank page created by hand on 2026-01-26 (no name, no ID, no status) is now hidden as well.

**Won table ("Devis gagnés")**
- Window: devis dated on or after `won_devis_window_start()` = today minus `WON_DEVIS_LOOKBACK_DAYS` (default 365). The 243 existing rows gain 87 (from 2025-09-24 on), 330 rows and 5.65 M€ on 2026-09-24. Pages that leave the window stay in the table as history; only their "Statut Furious" keeps being corrected.
- **Avenants year by year** (`add_previous_year_avenants`, replaced the same day by sub-items, §18.18). The pipeline merges avenants for the current year only: a devis dated this year absorbs its avenants, and an avenant dated this year on an older devis becomes a row `<devis>_AV<id>`. For each earlier year Y of the window, the same two rules run on the avenants dated Y only, skipping avenants of a devis dated this year (already merged) and of an unknown devis (excluded owner, deleted). Every avenant is counted once and nothing depends on the day of the run. Without this, the Sep–Dec 2025 rows would miss 56 k€, and on 1 January 2027 every 2026 amount would have lost its avenants.
- Step 11 is **skipped** when the avenants cannot be fetched, so the table never shows wrong amounts for a day.
- **Team columns** added to the database: Commentaire (text), Pris en charge (checkbox), Date archivage (date), Origine Transfo (select DV / Paysage / ENT / Travaux, same colours). The sync never writes them. Exception: when it *creates* a page, it copies Commentaire and Origine Transfo from the devis' "Devis à suivre" row (`load_followup_team_values`), so the note and the origin tag follow the devis. `run_won_devis_sync.py --copy-team-columns` does the same once for existing pages, filling only empty values. On 2026-09-24 no won devis of the window had such values.
- `scripts/setup_won_devis_views.py --team-columns`: creates the missing columns and shows them in every table view, leaving filters, grouping, order, widths and hand-made views alone. Two API traps: the views API returns property ids decoded ("<DRB") while data sources return them URL-encoded ("%3CDRB"); `frozen_column_index: -1` is read back but refused on write, so it is omitted.

**Check (`src/integrations/notion_sync_check.py`)**
- "Shown" means: follow-up page with a waiting "Statut"; won page with a won "Statut Furious" and "Date gagné" inside the window. The check reports **missing** (Furious expects the devis, Notion does not show it), **extra** (Notion shows it, Furious no longer does), **mismatches** (amount, status or devis date differ) and **duplicates** (same ID Devis twice).
- It runs as step 13 of the pipeline (step 12 until §18.18; log only) and in `scripts/run_reconciliation.py` at 07:30. The reconciliation e-mails on drift or when a check cannot run, and its JSON report gains a `notion` key. At 07:30, devis modified in Furious that day (`last_updated_at`) are skipped: the 06:00 sync could not know them, and they are checked the next day.
- `src/integrations/furious_snapshot.py::build_processed_dataframe` builds steps 1 to 4.5 of the pipeline once for the standalone won sync and the reconciliation, so the check compares Notion with exactly the numbers the sync wrote.
- Local note: `data/overrides.json` on a developer machine changes the amounts (e.g. 252889: 25 782.02 → 25 782.00 via a quarter override), so compare Notion with a VPS run, not a local one.

**Status changes from Notion (studied, not built)**: in Furious the status *is* the pipe (0 En cours, 1 Perdu, 2 Gagnés et finis, 3 Gagnés en cours, 4 Envoyée(s) attente réponse, 5 Brief). A waiting devis accepts API updates (only won ones are locked). Marking a devis lost from Notion looks feasible: `pipe: 1` plus `lost_reason_id` or `new_lost_reason`. Marking it won through the API may skip what the Furious interface does at a win (project creation, re-dated devis), so it must be tried on a test devis first.

**Tests**: `tests/test_notion_pris_en_charge_leftover.py` (follow-up rewritten: never writes Pris en charge, unchanged pages skipped, status matching, unknown status left out, relabel/orphans, duplicates; TRAVAUX tests unchanged), `tests/test_won_devis_sync.py` (+7: window, avenants counted once, team values copied at creation only, follow-up read, backfill, value helpers), `tests/test_notion_sync_check.py` (6), `tests/test_alerts_followup.py` (+1: no window vs e-mail window). Full suite: 284 passed.

### 18.18 Avenants as sub-items, "Devis perdus", Prévisions Travaux without "Pris en charge", "this year" view (September 2026)

**Avenants as sub-items ("Devis gagnés")**: every row now shows the numbers of one Furious document. A devis row carries the devis amount only (the pipeline's merged avenants are taken out: `amount - addon_amount`); each avenant is its own row `<devis>_AV<id>` (Type "Avenant") with its own amount and date, linked to its devis by the self relation "Devis parent" / "Avenants", which the table views display as sub-items (`subtasks` in the views API: `property_id` = "Devis parent", `display_mode: show`, `filter_scope: parents_and_subitems`). Summing a month gives devis + avenants signed that month, each once, whatever the day of the run. An avenant of the window whose devis is older keeps that devis as parent: the devis row is created with its own old date (`context=True`), so the yearly and 12-month views leave it out. `build_won_rows(df_processed, df_addons, start)` replaces `add_previous_year_avenants` + `select_won_devis`; parents come first so a run creates a parent before its avenants (`_create` now returns the page id). Avenants of a lost or unknown devis are ignored.
- **Fake signatures removed**: Furious records no signature date on avenants (empty on all 99 on 2026-09-24), but the pipeline's injected avenant rows used the avenant date as a fallback (`signature_date or date`), so 11 avenant pages showed "✅ Signé" without any signature. They were cleared on 2026-09-24 (each had Date signature = its own Date gagné, set by the sync at creation and never edited): 242081_AV82, 231782_AV80, 252414_AV83, 242117_AV85, 252628_AV86, 252552_AV87, 241943_AV88, 231594_AV91, 242117_AV92, 242348_AV93, 252418_AV121. Avenant rows now only get a real signature date.
- New columns: "Type" (Devis / Avenant, shown), "Devis parent" / "Avenants" (hidden), "Gagné cette année" (formula, hidden).

**"This year" view**: the views API has no "this year" date condition (only fixed dates, `past_year` = 365 days, `this_week`...). The formula `if(empty(prop("Date gagné")), false, year(prop("Date gagné")) == year(now()))` rolls over by itself on 1 January. New view "⏳ Gagnés, pas encore signés (cette année)" = the original filters + this formula; the original view is unchanged.

**"❌ Devis perdus (synchro Furious)"** (db `031c74940ef84f24abc3beb590348fe6`, next to "Devis gagnés" under "Suivi Commercial (Devis)"), step 12, `src/integrations/notion_lost_devis_sync.py`:
- Rows: status Perdu, devis date (re-stamped by Furious at the loss) in the same rolling window, no test devis, no "MAN-", no devis tagged "Devis en doublon" (a copy, not a lost sale: 27 over the 12 months).
- Columns: Nom, ID Devis, Client, BU, Typologie, Montant HT, Motif de perte (multi-select, a devis can carry two reasons), Statut Furious, Date perdu, Créé le, Commercial, Chef de projet, Lien Furious, Commentaire and Origine Transfo (team-owned, copied from "Devis à suivre" at creation), Perdu cette année (formula).
- Loss reasons come from Furious `lost_tags`, fetched apart for the lost devis only (`ProposalsClient.fetch_lost_tags`, filter `pipe: {eq: "1"}`, 891 devis), so the main fetch and the Google Sheets keep their columns.
- Views (`scripts/setup_lost_devis_table.py`): 📋 Devis perdus (by month), 📅 Perdus cette année, 📊 Montant perdu par mois (stacked by BU), 📊 Motifs de perte (bars, count per reason). The default view Notion adds on creation is removed.
- **People notifications**: Notion notifies every person added to a People property unless "notify" is off in that property's settings; the API cannot change that setting (`people: {}` accepts no option). Commercial / Chef de projet stay empty until `LOST_DEVIS_WRITE_PEOPLE=1`, to be set once the setting is off in Notion.

**Prévisions Travaux ("Pipe travaux", `notion_travaux_sync.py`)**: every run (06:15 daily, Sunday 22:00) used to untick "Pris en charge" on each devis still in the projection and tick it on the others, so the box never kept what a person set. Now the sync never writes it; the sync-owned checkbox "Dans la projection" is ticked while the devis meets the projection criteria (waiting, probability ≥ 10 %, date or start within 365 days) and unticked when it leaves them, and only changed properties are written. `scripts/setup_travaux_views.py`: `--add-property`, then `--apply` adds "Dans la projection is checked" to the 7 views (refused while no page is ticked, so the views never go empty).

**Tooling**: `src/integrations/notion_views.py` gathers the views API helpers and its traps (encoded vs decoded ids, `property_name` and `frozen_column_index: -1` refused on write, linked views listed by `data_source_id`, two nesting levels at most, no "this year"). The won and lost syncs never fall back to the MAINTENANCE database when their id is empty (the parent class did).

**Status changes from Notion**: no waiting test devis exists in Furious (53 test devis: 42 lost, 11 won), so a test would reuse a lost test devis. Not run without an explicit OK.

**Tests**: 295 passed (new `tests/test_lost_devis_sync.py`; sub-item, parent and signature tests in `tests/test_won_devis_sync.py`; TRAVAUX tests rewritten; avenant-parent and lost-table checks).

### 18.19 One scope rule for the sales tables, monthly archive clean-up, sub-items fixed, Furious status test (September 2026)

**Scope rule** (`src/integrations/notion_scope.py`, tables in `SCOPED_TABLES`): "Devis à normaliser", "Devis à suivre", "Pipe travaux", "Devis gagnés", "Devis perdus". Each sync knows its scope (the rows it is responsible for today) and writes:
- "Dans le périmètre" (sync-owned checkbox): ticked while the row is in scope. Every view of the 5 tables filters on it (`scripts/setup_scope_views.py --apply-views`, linked views included; in "Devis à suivre" it replaces the "Statut is a waiting status" condition; in "Pipe travaux" it is the former "Dans la projection", renamed).
- When a row leaves the scope, the sync archives it: "Pris en charge" ticked, "Archivé par la synchro" ticked (hidden), archive date ("Date archivage", or "Date archive" in Pipe travaux) set to that day. A Notion automation stamps the same date when a person ticks.
- The sync never unticks a person's tick. If a row it archived comes back into scope, it removes only its own tick (and the date).
- An empty Furious answer never archives anything (guard in every sync).
- Scopes: normaliser = waiting devis and devis won this month with a data problem; suivre = every waiting devis; Pipe travaux = TRAVAUX projection criteria; gagnés = devis and avenants won in the last 365 days plus the devis of those avenants; perdus = lost in the last 365 days, duplicates excluded. "Suivi signatures" (MAINTENANCE) and "Récent projets travaux" are yearly logs and keep their previous behaviour.
- Table views also get the quick filter "Pris en charge = non" where they had none on that box; charts only get the scope condition, so a devis handled by someone still counts in the amounts.
- Devis à normaliser now writes only changed properties and maps its Statut to existing options, like Devis à suivre.

**Monthly clean-up** (`scripts/archive_old_pages.py`, cron 1st of the month 05:00 on the VPS): pages archived, out of scope and whose archive date is 6 months old or more go to the Notion trash (`PATCH pages/{id} {"in_trash": true}`, restorable for 30 days). Out-of-scope is required: a devis still in scope would only be recreated by the next sync. More than 25 % of a table at once is refused.

**Sub-items fixed**: the avenant relation created through the API ("Devis parent" / "Avenants") is not what Notion uses for sub-items. Turning "Sub-items" on in the database menu created "Parent item" / "Sub-item": the 41 avenant links were copied to it, the API relation deleted, and Notion's pair renamed "Devis parent" / "Avenants" (the sync writes it by name). `setup_won_devis_views.py --subitems` now renames Notion's pair instead of creating a relation. A views API PATCH of `configuration` merges keys (grouping and columns kept).

**Furious status test** (devis 263219 "TEST(TAD)", the user's own, on 2026-09-24):
- `POST /proposal/ {"action": "update", "data": {"id", "pipe", ...}}` accepts only the pipes Brief (5), En cours (0), Envoyée(s) attente réponse (4) and Perdu (1): "Pipe invalide" for the won pipes. **A devis cannot be marked won through the API**; that stays in the Furious interface (which creates the project).
- Every update must re-send the required custom fields (`custom_fields: [{"name": "bu", "value": "MAINTENANCE"}, {"name": "typologie_de_devis", "value": [...]}, {"name": "typologie_myrium", "value": "PA <= 15 000€"}]`), even when the devis has them; otherwise "BU est requis" etc.
- Read back, `cf_typologie_myrium` loses its "<= 15 000€" part ("PA "): an automation must map it back to the full label before re-sending.
- Marking lost with `lost_reason_id` (e.g. 20 = Autre) works, but the devis date is not re-stamped as in the interface: a loss made through the API would keep its old date in "Devis perdus".
- The test devis ended lost, with reason "Autre" and its BU / typologies filled; no project was created.
- **Decision (2026-09-24): not built.** Devis statuses are changed in Furious only, so the team has one place for them; Notion only shows them. Do not propose a Notion to Furious status button again.

**Recap for the team**: `docs/Regles_tables_Notion.pdf` (2 pages, French: the rule, the life of a row, the scope of each table, who fills which column, what to check when a devis does not show), built by `scripts/build_notion_rules_pdf.py` (needs reportlab, not a pipeline dependency). Rebuild it whenever one of these rules changes.

**Rollout on 2026-09-24**: properties created in the 5 tables, 23 views checked (16 updated, 7 already right, quick filters kept); first scoped run: 41 fixed pages archived in "Devis à normaliser", flags set everywhere, checks 314/314, 376/376, 170/170; archive job dry run: nothing due before March 2027; cron added (1st of the month, 05:00).

**People notifications**: turned off by the user on the People properties of "Devis perdus"; `LOST_DEVIS_WRITE_PEOPLE=1` set on the VPS and the salespeople filled.

**Tests**: 308 passed (new `tests/test_notion_scope.py`; leftover, TRAVAUX, won and lost tests rewritten for the rule).

---

## 17. Conclusion

Myrium is a comprehensive, production-ready commercial tracking system. The system processes 1,700+ proposals in ~20 seconds, applies complex business rules accurately, and provides multiple output channels (Google Sheets, Email, Notion, Dashboard) for different stakeholders.

**Key Strengths**:
- Modular, maintainable architecture
- Comprehensive error handling
- Dynamic year calculation
- Flexible configuration
- Production-ready deployment with granular execution control

---

**Document Version**: 1.45
**Last Updated**: September 2026
**Maintained By**: Development Team
**Project**: Myrium - Commercial Tracking & BI System
