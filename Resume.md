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
         │    └──► Notion Sync (Alerts + TRAVAUX)
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
- **Daily Pipeline**: Writes to "État actuel" (stable snapshot) and monthly sheets. Syncs Notion databases.
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
- **By Typologie**: Aggregated by `cf_typologie_de_devis`
- **TS Total**: Sum of all proposals with "TS" in title

**Primary Typologie Allocation**:
- **Logic**: Deterministic selection (no equal splitting)
- **Priority**: TS (highest) → First non-Animation tag → Animation (if only tag)
- **Result**: Each project amount lands in exactly one typology category

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
- **4 Databases**: Weird Proposals, Follow-up, TRAVAUX Projection, Recent TRAVAUX Projects
- **Commercial/Chef de projet Split**: People properties for clear responsibility
- **Schema-Aware Sync**: Only sets properties that exist in database schema (prevents 400 errors)
- **Property Preservation**: Preserves user-edited notes/checkboxes during sync
- **Notion API 2025-09-03**: All clients pinned to latest API version with data_sources support
- **Fail-Closed Behavior**: Refuses to create pages when schema cannot be loaded (prevents blank page spam)
- **Owner-Specific Follow-up Windows**: Vincent and Adélaïde get 365-day forward windows in Notion (emails use 60 days)

### 7.7 BI Dashboard
- **Production Tabs**: "À produire {Year}" with cross-year aggregation
- **Time Filtering**: Filter by Month/Quarter based on source sheet
- **Date Columns**: Full visibility of proposal dates
- **Clickable Project Lists**: KPI cards display project counts with clickable "🔎 Voir projets" buttons that open large modal dialogs showing detailed project lists with Furious CRM links
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

**Document Version**: 1.30
**Last Updated**: January 2026
**Maintained By**: Development Team
**Project**: Myrium - Commercial Tracking & BI System
