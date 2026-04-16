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
         │    └──► Notion Sync (Alerts + TRAVAUX + MAINTENANCE won)
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
- **Daily Pipeline**: Writes to "État actuel" (stable snapshot) and monthly sheets. Syncs Notion databases (alerts, TRAVAUX, MAINTENANCE won). MAINTENANCE won: all proposals won in the current year with BU MAINTENANCE; POST if ID Devis not in Notion, PATCH if already present; no archiving (all years/months kept for grouping in Notion). **Maintenance Entretien début 2026**: same état spreadsheet gets a **Paramètres** worksheet (Notion → JSON + Sheets); see `run_sheets_update.py` Step 7 and `run_pipeline.py` after Step 7 (§18.11).
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
- **Owner-Specific Windows (Notion Only)**: Vincent and Adélaïde get 365-day forward windows in Notion sync (emails still use 60 days)

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
- **Automatic Pagination**: Handles 1,700+ proposals
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
- **5 Databases**: Weird Proposals, Follow-up, TRAVAUX Projection, Recent TRAVAUX Projects, MAINTENANCE Won (current year)
- **Commercial/Chef de projet Split**: People properties for clear responsibility
- **Schema-Aware Sync**: Only sets properties that exist in database schema (prevents 400 errors)
- **TRAVAUX Projection dates**: Sync maps to both "Date"/"Début projet" and "Date Signature"/"Début Chantier" when present in schema; projection passes `signature_date` for "Date Signature"
- **Property Preservation**: Preserves user-edited notes/checkboxes during sync
- **Notion API 2025-09-03**: All clients pinned to latest API version with data_sources support
- **Fail-Closed Behavior**: Refuses to create pages when schema cannot be loaded (prevents blank page spam)
- **Owner-Specific Follow-up Windows**: Vincent and Adélaïde get 365-day forward windows in Notion (emails use 60 days)
- **Leftover Marking (Follow-up & TRAVAUX Projection)**: Pages in Notion but not in current run get "Pris en charge" ticked so they are filtered out; current-run pages get "Pris en charge" unchecked (see §18.8).

### 7.7 BI Dashboard
- **Production Tabs**: "À produire {Year}" with cross-year aggregation
- **Time Filtering**: Filter by Month/Quarter based on source sheet
- **Date Columns**: Full visibility of proposal dates
- **Clickable Project Lists**: KPI cards display project counts with clickable "🔎 Voir projets" buttons that open large modal dialogs showing detailed project lists with Furious CRM links
- **Objectifs Signé (Production vs Signature)**: For the Signé view, the Objectifs tab shows two blocks: **Objectif Production** vs **Réalisé** (signed-to-produce in the period) and **Objectif Signature** vs **Signature** (ex-Pur: amount signed in the period). Objectives data: `signe` = production (Réalisé), `signature` = signature (Signé). **Maintenance Entretien – Début 2026**: resolution order and Sheets persistence in **§18.11** (dashboard also applies it for **Envoyé 2026**). **Objectifs tab end section**: projection + Pur-by-month + expanders + colors (§18.10, §18.11).
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
MAINTENANCE_ENTRETIEN_START_2026=...   # Optional fallback: value for "Maintenance Entretien – Début 2026" (e.g. 1084000)
NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID=...  # Optional: data source ID (preferred)
NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID=...   # Optional: legacy database ID if no datasource
```

### 8.2 Business Constants
Defined in `config/settings.py`:
- **VIP Commercials**: List of VIP sales reps
- **BU Keywords**: Mapping keywords to business units
- **Alert Config**: Follow-up window (60 days default, 365 days for Vincent/Adélaïde in Notion), Excluded owners
- **TRAVAUX Projection**: Start window (365 days), Probability threshold (25%)
- **Notion Follow-up Overrides**: `NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER` dict for owner-specific windows

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

**Weekly TRAVAUX Projection** (every Sunday at 11 PM):
```bash
0 23 * * 0 cd /path/to/myrium && /path/to/venv/bin/python3 scripts/run_travaux_pipeline.py >> logs/travaux_cron.log 2>&1
```
- Filters TRAVAUX proposals with probability ≥ 25% and `date` OR `projet_start` within rolling 365 days
- Sends projection email to Mathilde with Guillaume and Vincent in CC
- Syncs to Notion TRAVAUX projection database
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

**Document Version**: 1.38
**Last Updated**: April 2026
**Maintained By**: Development Team
**Project**: Myrium - Commercial Tracking & BI System
