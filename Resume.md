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
   - Run bi-monthly (1st and last day of each month) automatically

2. **Financial Forecasting**
   - Calculate revenue spreading across 3 years (Y, Y+1, Y+2)
   - Apply business-specific rules per BU type
   - Generate quarterly breakdowns for planning

3. **Data Quality & Alerts**
   - Identify proposals with data quality issues
   - Alert sales reps about follow-up opportunities
   - Group alerts by owner for efficient action

4. **Reporting & Visualization**
   - Generate 3 main views: Snapshot, Sent Month, Won Month
   - Store in Google Sheets with summaries
   - Provide interactive BI dashboard

5. **Resource Planning**
   - Sync TRAVAUX projects to Notion for Gantt visualization
   - Filter active projects within 90-day horizon

### 2.2 Success Metrics

- **Performance**: Pipeline completes in < 30 seconds
- **Reliability**: 99%+ success rate with comprehensive error handling
- **Accuracy**: Revenue calculations match business rules exactly
- **Usability**: Dashboard loads in < 5 seconds with cached data
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
│  API Clients    │ (auth.py, proposals.py, projects.py)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Data Processing │ (cleaner.py, revenue_engine.py, views.py, alerts.py)
└────────┬────────┘
         │
         ├──► Google Sheets (Monthly Snapshots)
         ├──► Email Alerts (SMTP)
         ├──► Notion (Gantt Sync)
         └──► Streamlit Dashboard (BI Visualization)
```

### 3.2 Data Flow

1. **Authentication**: JWT token acquisition with auto-refresh
2. **Data Extraction**: Paginated fetch of all proposals (250 per page)
3. **Data Cleaning**: Normalization, date parsing, BU assignment
4. **Revenue Calculation**: Complex spreading logic per BU type
5. **View Generation**: Filter and aggregate into 3 main views
6. **Alert Generation**: Identify data quality issues and follow-ups
7. **Output**: Write to Google Sheets, send emails, sync Notion
8. **Visualization**: Dashboard reads from Google Sheets

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
- **Example**: 12,000€ over 12 months = 1,000€/month

#### TRAVAUX
- **Short Projects (< 1 month)**:
  - **Rule**: 100% revenue on `projet_start` date
  - **Example**: 5,000€ project starting Jan 15 → 5,000€ in January

- **Long Projects (≥ 1 month)**:
  - **Rule**: Spread evenly over duration
  - **Formula**: `monthly_amount = total_amount / months_duration`
  - **Example**: 60,000€ over 6 months = 10,000€/month

#### CONCEPTION (Complex Phasing)

**Small Projects (< 15,000€)**:
- **Rule**: 1/3 per month for 3 months
- **Example**: 12,000€ → 4,000€/month for 3 months

**Medium Projects (15,000€ - 30,000€)**:
- **Rule**:
  1. 60% over 6 months
  2. 6-month pause
  3. 40% over 6 months
- **Example**: 24,000€ → 2,400€/month (6mo) → pause (6mo) → 1,600€/month (6mo)

**Large Projects (> 30,000€)**:
- **Rule**:
  1. 40% over 12 months
  2. 6-month pause
  3. 60% over 12 months
- **Example**: 60,000€ → 2,000€/month (12mo) → pause (6mo) → 3,000€/month (12mo)

**Date Replacement Rules** (applied when dates are missing):
- **Rule 1 (start missing)**: MAINTENANCE uses `projet_stop - 11mo`, TRAVAUX uses `date` to `projet_stop`, CONCEPTION uses `date`
- **Rule 2 (end missing)**: MAINTENANCE extends +11mo, TRAVAUX extends +5mo, CONCEPTION unchanged
- **Rule 3 (both missing)**: All BUs use `date` column with BU-specific spans
- **Window Clamping (Rule 4)**: Allocations outside Y..Y+3 window are clamped to first/last tracked month

**Implementation**: `src/processing/revenue_engine.py`

### 4.3 Financial Columns Generated

For each proposal, the system generates:
- **Annual Totals**: `Montant Total {Year}`, `Montant Pondéré {Year}`
- **Quarterly Breakdowns**: `Montant Total Q{1-4}_{Year}`, `Montant Pondéré Q{1-4}_{Year}`

**Years Tracked**: Current year, Y+1, Y+2, Y+3 (calculated dynamically, extended from Y+2 in January 2026)

**Weighted Amounts**: `Montant Pondéré = Montant Total × (Probability / 100)`

### 4.4 View Generation Rules

#### View 1: "État au {DD-MM-YYYY}" (Snapshot)
- **Scope**: All proposals with status in `STATUS_WAITING`
- **Purpose**: Real-time snapshot of the commercial pipeline
- **Updates**: Every pipeline run (creates new worksheet with current date)

#### View 2: "Envoyé {Month} {Year}" (Sent)
- **Scope**: Proposals created in current month AND status is `STATUS_WAITING`
- **Purpose**: Track new proposals sent this month
- **Naming**: e.g., "Envoyé Décembre 2025"

#### View 3: "Signé {Month} {Year}" (Won)
- **Scope**: Proposals with status in `STATUS_WON`
- **Date Rule**: Included if `signature_date` is current month OR `date` (proposal date) is current month
- **Purpose**: Track won deals for the month
- **Naming**: e.g., "Signé Décembre 2025"
- **Note**: Uses non-weighted amounts (deals are closed)

### 4.5 Alert Rules

#### Weird Proposals Alert
- **Scope**: Only proposals appearing in one of the 3 active views
- **Triggers**:
  - Amount < 1,000€
  - Missing `projet_start` date
  - Missing `projet_stop` date
  - `projet_start` > `projet_stop` (invalid range)
  - Probability = 0%
- **Grouping**: By `alert_owner` (VIP resolution logic)
- **Delivery**: One email per owner with all their weird proposals

#### Commercial Follow-up Alert
- **Scope**: Proposals with status `STATUS_WAITING`
- **Time Window**:
  - **Backward**: From 1st of Previous Month
  - **Forward**: To Today + 60 Days
- **Date Reference**:
  - **CONCEPTION**: Uses `date` (proposal date)
  - **TRAVAUX/MAINTENANCE**: Uses `projet_start` (fallback to `date`)
- **VIP Routing**: If `assigned_to` contains a VIP, assign alert ONLY to that VIP
- **VIP List**: Clemence, Vincent.Delavarende, Anne-Valerie, Guillaume, Julien.Jonis, Zoelie, Adelaide.Patureau

**Implementation**: `src/processing/alerts.py`

### 4.6 Summary Calculations

Each view includes summaries at the bottom:
- **By BU**: Aggregated by Business Unit (with split handling)
- **By Typologie**: Aggregated by `cf_typologie_de_devis` (with split handling)
- **TS Total**: Sum of all proposals with "TS" in title

**Split Handling**: If typology is "DV, PAYSAGE", amount is added to both "DV" and "PAYSAGE" totals.

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
| **notion-client** | Notion API client |
| **python-dotenv** | Environment variable management |

### 5.3 Visualization & Dashboard

| Library | Purpose |
|---------|---------|
| **Streamlit** | Web dashboard framework |
| **Plotly** | Interactive charts and graphs |

### 5.4 Design Patterns

**Singleton Pattern**: `settings` object in `config/settings.py`

**Factory Pattern**: `get_or_create_spreadsheet()`, `get_or_create_worksheet()`

**Strategy Pattern**: Different revenue spreading strategies per BU type

**Template Method**: Email HTML generation with different templates

**Repository Pattern**: API clients abstract data source access

### 5.5 Code Organization

```
myrium/
├── config/              # Configuration & constants
│   ├── settings.py      # Environment variables, business constants
│   └── credentials/     # Service account JSON (gitignored)
│
├── src/
│   ├── api/            # External API clients
│   │   ├── auth.py     # JWT authentication with caching
│   │   ├── proposals.py # Proposals API with pagination
│   │   └── projects.py  # Projects API (for Gantt)
│   │
│   ├── processing/     # Business logic
│   │   ├── cleaner.py  # Data normalization & BU assignment
│   │   ├── revenue_engine.py # Revenue spreading calculations
│   │   ├── views.py    # View generation & summaries
│   │   └── alerts.py   # Alert generation logic
│   │
│   ├── integrations/   # Output handlers
│   │   ├── google_sheets.py # Google Sheets read/write
│   │   ├── email_sender.py  # SMTP email with HTML templates
│   │   └── notion_sync.py   # Notion database sync
│   │
│   └── dashboard/      # Streamlit application
│       └── app.py      # Main dashboard with Plotly charts
│
├── scripts/
│   ├── run_pipeline.py # Main orchestrator
│   └── run_dashboard.py # Dashboard launcher
│
└── logs/               # Pipeline execution logs
```

---

## 6. Implementation Strategies

### 6.1 Error Handling Strategy

**Multi-Layer Error Handling**:
1. **API Layer**: Specific exceptions (`AuthenticationError`, `ProposalsAPIError`)
2. **Processing Layer**: Validation and data quality checks
3. **Integration Layer**: Retry logic and graceful degradation
4. **Orchestration Layer**: Comprehensive logging and error reporting

**Error Logging**:
- Structured logging with timestamps
- Traceback capture for debugging
- Error details saved to JSON output
- Console output for real-time monitoring

### 6.2 Caching Strategy

**Google Sheets Client**:
- Spreadsheet objects cached in memory
- Reduces API calls for multiple worksheet operations

**Dashboard**:
- Streamlit `@st.cache_data` decorator
- 5-minute TTL for worksheet data
- Reduces Google Sheets API calls

**Authentication**:
- JWT token cached until near expiry (60s buffer)
- Auto-refresh on expiry

### 6.3 Data Validation Strategy

**Input Validation**:
- Date parsing with fallback to NaT for invalid dates
- Numeric conversion with error handling
- String field cleaning (handle lists, NaN values)

**Business Rule Validation**:
- TS rule applied before standard BU mapping
- Date range validation (start ≤ end)
- Probability normalization (0% → 50% default)

### 6.4 Configuration Management

**Environment Variables**:
- All sensitive data in `.env` file (gitignored)
- Default values in code for non-sensitive settings
- Type-safe configuration with dataclasses

**Dynamic Configuration**:
- Year-based spreadsheet selection
- Dynamic year calculation (no hardcoded dates)
- Flexible email routing with test mode

### 6.5 Testing Strategy

**Test Mode**:
- `--test` flag redirects all emails to test address
- `--dry-run` flag skips external writes
- Allows safe testing without affecting production data

**Error Simulation**:
- Comprehensive error handling allows graceful failures
- Partial success (some views succeed, others fail)
- Detailed error reporting for debugging

---

## 7. Key Features & Capabilities

### 7.1 Data Extraction

- **Automatic Pagination**: Handles 1,700+ proposals seamlessly
- **Field Selection**: Fetches 29 specific fields from API
- **Error Recovery**: Continues on individual page failures
- **Progress Logging**: Real-time progress updates

### 7.2 Data Processing

- **Robust Date Parsing**: Handles various date formats, invalid dates
- **TS Rule Override**: Automatic TRAVAUX assignment for TS projects
- **VIP Routing**: Intelligent owner resolution for alerts
- **Split Summaries**: Handles comma-separated typologies

### 7.3 Revenue Forecasting

- **Multi-Year Projections**: Current year + 2 years ahead
- **Quarterly Breakdowns**: Q1-Q4 for each year
- **Weighted Calculations**: Probability-adjusted amounts
- **Complex Phasing**: CONCEPTION projects with pause periods

### 7.4 Google Sheets Integration

- **Multi-Spreadsheet Architecture**: Separate spreadsheets by type and year
- **Auto-Creation**: Creates worksheets and spreadsheets as needed
- **Summary Tables**: BU and Typologie summaries appended
- **TS Tracking**: Special line item for TS totals

### 7.5 Email Alerts

- **Combined Emails**: One email per person with all alerts
- **HTML Templates**: Professional, responsive email design
- **Test Mode**: Redirect all emails for testing
- **VIP Routing**: Smart owner-to-email mapping

### 7.6 Notion Integration

- **Gantt Sync**: TRAVAUX projects for timeline visualization
- **Auto-Update**: Creates, updates, and archives pages
- **Time Filtering**: Only active projects (90-day horizon)
- **Flexible Fields**: Handles missing API fields gracefully
- **API 2025-09-03 Compatibility**: Schema retrieval from data sources (not database object)
- **Complete Field Mapping**: All project fields (type, type_label, project_manager, total_amount, dates) properly synced
- **Property Type Detection**: Automatically handles Rich Text, Select, People, and Date property types

### 7.7 BI Dashboard

- **Year Selector**: Aggregate data across multiple years
- **Multiple Views**: Global, monthly, and detailed views
- **Interactive Charts**: Plotly bar, pie, donut, and line charts with BU color theming
- **Data Export**: CSV download functionality
- **Real-Time Updates**: 5-minute cache refresh
- **Multi-Sheet Reading**: Reads all worksheets from Google Sheets spreadsheets (not just the first one)
- **BU Color Theme**: Consistent color coding (CONCEPTION=green, TRAVAUX=yellow, MAINTENANCE=purple, AUTRE=gray)
- **Project Counts**: Displayed in all charts and KPIs (format: "BU_NAME (X projets)")
- **Typologie Split**: Multi-typologie projects have amounts divided equally among categories
- **Error Handling**: Graceful handling of individual sheet errors, continues processing other sheets
- **Column Deduplication**: Automatically handles duplicate column names when combining multiple sheets
- **Data Ingestion Fix**: Stops reading before summary rows ("Résumé par BU", etc.) to ensure accurate counts

**Recent Improvements (December 2025)**:
- Complete dashboard redesign with BU color theming and enhanced visualizations
- Fixed critical data ingestion bug (summary rows excluded from data)
- Added project counts to all charts and KPIs
- Enhanced Vue Globale with separate BU and Typologie sections
- Added monthly stacked bar charts with cumulative and average trend lines
- Added sent/pondéré comparison charts for Envoyé/État views
- Implemented typologie split logic for multi-category projects
- Professional PDF export with high-resolution charts and formatted tables
- Fixed Streamlit API deprecation warnings (`use_container_width` → `width='stretch'`)
- Added unique keys to all plotly_chart calls to prevent duplicate element ID errors

---

## 8. Configuration & Setup

### 8.1 Environment Variables

**Furious API**:
```env
FURIOUS_API_URL=https://merciraymond.furious-squad.com/api/v2
FURIOUS_USERNAME=your_username
FURIOUS_PASSWORD=your_password
```

**Google Sheets** (Multi-Spreadsheet):
```env
GOOGLE_SERVICE_ACCOUNT_PATH=config/credentials/service_account.json
SPREADSHEET_ETAT_2025=spreadsheet_id_1
SPREADSHEET_ENVOYE_2025=spreadsheet_id_2
SPREADSHEET_SIGNE_2025=spreadsheet_id_3
SPREADSHEET_ETAT_2026=spreadsheet_id_4
# ... etc for each year
```

**SMTP Email**:
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password  # Gmail App Password
```

**Notion**:
```env
NOTION_API_KEY=your_notion_api_key
NOTION_DATABASE_ID=your_notion_database_id
```

### 8.2 Business Constants

Defined in `config/settings.py`:
- **Status Lists**: `STATUS_WON`, `STATUS_WAITING`
- **VIP Commercials**: List of VIP sales reps
- **Email Mapping**: Owner identifier → email address
- **BU Keywords**: Mapping keywords to business units
- **Revenue Thresholds**: CONCEPTION project thresholds (15k, 30k)
- **Alert Configuration**: Amount threshold (1k), follow-up window (60 days)

### 8.3 Google Service Account Setup

1. Create project in Google Cloud Console
2. Enable Google Sheets API and Google Drive API
3. Create Service Account
4. Download JSON key file
5. Save to `config/credentials/service_account.json`
6. Share spreadsheets with Service Account email

---

## 9. Deployment & Operations

### 9.1 Pipeline Execution

**Manual Run**:
```bash
python scripts/run_pipeline.py
```

**Test Mode** (redirects emails):
```bash
python scripts/run_pipeline.py --test
```

**Dry Run** (no external writes):
```bash
python scripts/run_pipeline.py --dry-run
```

**With Output**:
```bash
python scripts/run_pipeline.py --output results.json
```

### 9.2 Cron Scheduling

**Bi-Monthly Execution** (1st and last day of month at 8 AM):
```bash
0 8 1,15 * * cd /path/to/myrium && /path/to/venv/bin/python scripts/run_pipeline.py >> logs/cron.log 2>&1
```

### 9.3 Dashboard Deployment

**Local Development**:
```bash
python scripts/run_dashboard.py
```

**Production** (with Streamlit Cloud or custom server):
```bash
streamlit run src/dashboard/app.py --server.port 8501
```

### 9.4 Logging

**Log Files**:
- Location: `logs/pipeline_{timestamp}.log`
- Format: Timestamp, level, message
- Includes: All pipeline steps, errors, tracebacks

**Console Output**:
- Real-time progress updates
- Error messages with context
- Success confirmations

---

## 10. Data Models & Structures

### 10.1 Proposal Data Model

**Core Fields** (from Furious API):
- `id`, `date`, `title`, `amount`, `discount`, `vat`, `currency`
- `assigned_to`, `client_id`, `opportunity_id`
- `statut`, `pipe`, `pipe_name`
- `created_at`, `last_updated_at`
- `legal_entity`, `company_name`, `id_furious`
- `total_sold_days`, `total_cost`, `probability`
- `entity`, `projet_start`, `projet_stop`
- `sign_url`, `cf_typologie_de_devis`, `cf_typologie_myrium`, `cf_bu`
- `signature_date`

**Computed Fields** (added during processing):
- `statut_clean`: Normalized status (lowercase, trimmed)
- `final_bu`: Assigned business unit (with TS rule)
- `alert_owner`: Resolved owner for alerts (VIP priority)
- `probability_calc`: Probability with 0% → 50% default
- `probability_factor`: Probability as decimal (0.0-1.0)
- `date_effective_won`: Signature date or proposal date for won filtering

**Financial Fields** (added by revenue engine):
- `Montant Total {Year}`: Annual total revenue
- `Montant Pondéré {Year}`: Annual weighted revenue
- `Montant Total Q{1-4}_{Year}`: Quarterly total revenue
- `Montant Pondéré Q{1-4}_{Year}`: Quarterly weighted revenue

### 10.2 View Data Structure

**ViewResult** (dataclass):
```python
@dataclass
class ViewResult:
    name: str                    # Worksheet name
    data: pd.DataFrame          # Filtered proposals
    summary_by_bu: List[Dict]   # BU summary
    summary_by_type: List[Dict] # Typologie summary
    ts_total: float             # TS projects total
```

**ViewsOutput** (dataclass):
```python
@dataclass
class ViewsOutput:
    snapshot: ViewResult        # Snapshot view
    sent_month: ViewResult      # Sent month view
    won_month: ViewResult       # Won month view
    sheet_names: Dict[str, str] # Sheet name mappings
    counts: Dict[str, int]      # Row counts per view
```

### 10.3 Alert Data Structure

**AlertsOutput** (dataclass):
```python
@dataclass
class AlertsOutput:
    weird_proposals: Dict[str, List[Dict]]  # Owner → alert items
    commercial_followup: Dict[str, List[Dict]] # Owner → alert items
    count_weird: int
    count_followup: int
```

**Alert Item Structure**:
```python
{
    'title': str,
    'company_name': str,
    'amount': float,
    'statut': str,
    'date': str (YYYY-MM-DD),
    'projet_start': str,
    'projet_stop': str,
    'signature_date': str,
    'created_at': str,
    'sign_url': str,
    'reason': str,  # For weird proposals
    'probability': float  # For follow-ups
}
```

---

## 11. Performance Characteristics

### 11.1 Pipeline Performance

**Typical Execution Time**: ~20-25 seconds for 1,700+ proposals

**Breakdown**:
- Authentication: < 1 second
- Data Fetching: ~3-5 seconds (depends on API response time)
- Data Cleaning: ~2-3 seconds
- Revenue Engine: ~1-2 seconds
- View Generation: < 1 second
- Google Sheets Write: ~5-10 seconds (depends on network)
- Email Sending: ~5-10 seconds (depends on number of recipients)
- Notion Sync: ~2-3 seconds

**Bottlenecks**:
- Google Sheets API rate limits
- SMTP email sending (sequential)
- Network latency

### 11.2 Optimization Strategies

- **Pagination**: Efficient API calls (250 items per page)
- **Caching**: Spreadsheet objects cached in memory
- **Batch Operations**: Write all data in single API call per worksheet
- **Parallel Processing**: Could be added for email sending (future enhancement)

### 11.3 Scalability

**Current Capacity**:
- Handles 2,000+ proposals without issues
- Google Sheets: Up to 5 million cells per spreadsheet
- Email: Limited by SMTP server rate limits

**Future Scaling**:
- Database backend (PostgreSQL) for historical data
- Message queue (RabbitMQ) for async email sending
- Caching layer (Redis) for dashboard data

---

## 12. Security Considerations

### 12.1 Authentication

**Furious API**:
- JWT tokens with 1-hour expiry
- Auto-refresh before expiry (60s buffer)
- Token stored in memory only

**Google Service Account**:
- JSON key file stored securely (gitignored)
- Least privilege: Only Sheets API + Drive API access
- Service Account isolated from user accounts

**Notion API**:
- API key stored in environment variable
- Database-level permissions

### 12.2 Data Protection

**Credentials**:
- All secrets in `.env` file (gitignored)
- Service account JSON in `config/credentials/` (gitignored)
- No hardcoded credentials in code

**Email**:
- SMTP with TLS encryption
- App Password (not regular password)
- Test mode for safe development

### 12.3 Access Control

**Google Sheets**:
- Service Account has Editor access to specific spreadsheets
- Can be restricted to specific folders
- Audit trail in Google Cloud Console

---

## 13. Error Handling & Resilience

### 13.1 Error Categories

**API Errors**:
- Authentication failures → Retry with new token
- Network timeouts → Retry with exponential backoff
- Rate limiting → Wait and retry

**Data Errors**:
- Invalid dates → Set to NaT, continue processing
- Missing fields → Use defaults, log warning
- Invalid amounts → Set to 0, flag in alerts

**Integration Errors**:
- Google Sheets API errors → Detailed error message, continue with other views
- Email failures → Log error, continue with other emails
- Notion sync errors → Log error, don't block pipeline

### 13.2 Error Recovery

**Partial Success**:
- Pipeline continues even if one step fails
- Successful steps are logged
- Failed steps include detailed error information

**Error Reporting**:
- Console output with error details
- Log files with full tracebacks
- JSON output with error summary

---

## 14. Future Enhancements & Considerations

### 14.1 Potential Improvements

**Performance**:
- Parallel email sending
- Batch Google Sheets writes
- Database caching layer

**Features**:
- Historical trend analysis
- Predictive analytics
- Custom alert rules
- Multi-language support

**Integration**:
- Slack notifications
- Microsoft Teams integration
- Additional CRM systems

### 14.2 Maintenance Considerations

**Code Updates**:
- Business rule changes → Update `revenue_engine.py`
- New BU types → Update `cleaner.py::assign_bu()`
- Alert rules → Update `alerts.py`
- Email templates → Update `email_sender.py`

**Configuration Updates**:
- New VIPs → Update `VIP_COMMERCIALS` in `settings.py`
- Email mappings → Update `OWNER_EMAIL_MAP` in `settings.py`
- Thresholds → Update constants in `settings.py`
- Dashboard URL → Update `DASHBOARD_URL` in `email_sender.py` (line 40) when dashboard is publicly accessible

**Spreadsheet Management**:
- New years → Add `SPREADSHEET_*_{YEAR}` to `.env`
- Archive old years → Move spreadsheets to archive folder

---

## 15. Key Design Decisions

### 15.1 Why Python?

- **Flexibility**: Easy to modify business logic
- **Libraries**: Rich ecosystem (Pandas, gspread, Streamlit)
- **Performance**: Fast enough for 1,700+ proposals
- **Maintainability**: Clear, readable code

### 15.2 Why Multi-Spreadsheet Architecture?

- **Organization**: Separate by type and year
- **Performance**: Smaller spreadsheets load faster
- **Permissions**: Control access per year
- **Archiving**: Easy to archive old years

### 15.3 Why Combined Emails?

- **Efficiency**: One email per person
- **Context**: All alerts in one place
- **Actionability**: Easier to prioritize

### 15.4 Why Dynamic Year Calculation?

- **Future-Proof**: No hardcoded dates
- **Automatic**: Adapts as years change
- **Flexible**: Easy to extend to more years

### 15.5 Why Test Mode?

- **Safety**: Test without affecting production
- **Development**: Iterate quickly
- **Debugging**: Isolate email issues

---

## 16. Troubleshooting Guide

### 16.1 Common Issues

**Google Sheets Error (Empty Error)**:
- Check spreadsheet ID in `.env`
- Verify Service Account has access
- Check Google Sheets API is enabled

**Email Authentication Failed**:
- Verify `SMTP_USER` and `SMTP_PASSWORD` in `.env`
- Use Gmail App Password (not regular password)
- Check SMTP settings (host, port)

**Project API Field Errors**:
- Project API has different fields than Proposal API
- Fields are now minimal and flexible
- Check API documentation for available fields

**No Data in Dashboard**:
- Verify spreadsheet IDs are correct
- Check worksheet names match expected format
- Ensure data was written successfully

### 16.2 Debugging Steps

1. **Check Logs**: `logs/pipeline_{timestamp}.log`
2. **Run with `--test`**: Isolate email issues
3. **Run with `--dry-run`**: Test without external writes
4. **Check `.env`**: Verify all required variables are set
5. **Test API Access**: Verify credentials work independently

---

## 17. Conclusion

Myrium is a comprehensive, production-ready commercial tracking system that successfully replaces the previous n8n workflow with a more flexible, maintainable Python application. The system processes 1,700+ proposals in ~20 seconds, applies complex business rules accurately, and provides multiple output channels (Google Sheets, Email, Notion, Dashboard) for different stakeholders.

**Key Strengths**:
- Modular, maintainable architecture
- Comprehensive error handling
- Dynamic year calculation
- Flexible configuration
- Production-ready deployment

**Success Metrics**:
- ✅ Performance: < 30 seconds for full pipeline
- ✅ Reliability: Comprehensive error handling
- ✅ Accuracy: Business rules implemented exactly
- ✅ Usability: Intuitive dashboard and alerts

The system is ready for production use and can be easily extended with new features as business needs evolve.

---

## 18. Recent Updates & Fixes

### 18.1 Notion API Migration (December 2025)

**Problem**: Notion database was empty after sync - properties not being set correctly.

**Root Cause**: Notion API version 2025-09-03 changed the database structure. Properties are no longer directly on the database object but are nested in `data_sources`.

**Solution**:
- Updated `NotionSync._get_database_schema()` to retrieve properties from data source endpoint
- Added fallback to query sample page if data source properties unavailable
- Fixed schema caching to work with new API structure

**Code Changes**:
- `src/integrations/notion_sync.py`: Modified `_get_database_schema()` to query `data_sources/{id}` endpoint
- Added proper error handling and fallback mechanisms

### 18.2 Complete Project Data Retrieval (December 2025)

**Enhancement**: Extended ProjectsClient to fetch all required fields for complete Notion Gantt visualization.

**Fields Added**:
- `type`: Project typology code
- `type_label`: Human-readable type label
- `project_manager`: Project manager identifier
- `total_amount`: Total project amount (replaces `amount` from Proposals API)

**Code Changes**:
- `src/api/projects.py`: Extended `self.fields` list to include all required fields
- `src/integrations/notion_sync.py`: Enhanced `_build_page_properties()` to map all new fields

### 18.3 Property Type Handling (December 2025)

**Enhancement**: Dynamic property type detection and appropriate value formatting.

**Features**:
- **Type Property**: Supports both `select` and `rich_text` types
- **Type Label**: Rich text mapping
- **Project Manager**: Handles People, Select, and Rich Text property types
- **Total Amount**: Fixed to use `total_amount` field (not `amount`)
- **Dates**: Critical for Gantt chart - properly formatted date ranges

**Code Changes**:
- `src/integrations/notion_sync.py`: Added property type detection in `_build_page_properties()`
- Handles People properties gracefully (skips if type is People, requires user ID lookup)

### 18.4 Date Handling for Gantt Chart (December 2025)

**Enhancement**: Robust date extraction and formatting for Notion Gantt visualization.

**Features**:
- Prioritizes `start_date` and `end_date` from Projects API
- Fallback to alternative field names (`from_date`, `to_date`, `projet_start`, `projet_stop`)
- Proper date range formatting (start + end dates)
- Handles single-day events (same start and end date)

**Result**: All 14 TRAVAUX projects successfully synced with complete data including dates for Gantt chart visualization.

### 18.5 Dashboard Multi-Sheet Reading Fix (December 2025)

**Problem**: Dashboard was only reading the first worksheet from Google Sheets spreadsheets, missing data from additional sheets (e.g., "Signé Novembre 2025" and "Signé Décembre 2025" but only one was being loaded).

**Root Cause**:
- `list_worksheets()` method lacked proper error handling and logging
- `load_year_data()` didn't handle duplicate column names when concatenating DataFrames from multiple sheets
- No visibility into which sheets were being loaded

**Solution**:
- Enhanced `list_worksheets()` in `google_sheets.py` with try/except error handling and detailed logging showing all worksheets found
- Improved `load_year_data()` in `app.py` with:
  - Per-sheet error handling (continues processing if one sheet fails)
  - Automatic column name deduplication (renames duplicates with `.1`, `.2`, etc.)
  - Column alignment before concatenation (ensures all DataFrames have same columns, adds missing columns with None)
  - Comprehensive logging for each sheet loaded (success, empty, or error)
- Added debug UI in sidebar expander showing loaded sheets with row counts
- Fixed cache clearing to properly clear both `cache_data` and `cache_resource`

**Code Changes**:
- `src/integrations/google_sheets.py::list_worksheets()`: Added error handling, logging, and explicit worksheet retrieval
- `src/dashboard/app.py::load_year_data()`: Added column deduplication, alignment, and per-sheet error handling
- `src/dashboard/app.py::parse_numeric_columns()`: Fixed SettingWithCopyWarning by using `.copy()` to avoid modifying views
- All `st.plotly_chart()` calls: Updated to use `use_container_width=True` and `config={}` (removed deprecation warnings)
- Fixed `st.columns()` error when empty typologies by adding check and using `max(num_cols, 1)`

**Additional Fixes**:
- Fixed pandas `InvalidIndexError` when concatenating DataFrames with duplicate column names

**Result**: Dashboard now correctly reads and combines all worksheets from spreadsheets (e.g., both "Signé Novembre 2025" and "Signé Décembre 2025"), providing complete data aggregation across multiple monthly sheets with proper error handling and user visibility.

### 18.6 Dashboard Complete Overhaul & PDF Export (December 2025)

**Major Redesign**: Complete rebuild of Streamlit dashboard with enhanced visualizations, BU theming, and professional PDF export.

**Key Changes**:

1. **BU Color Theme System**:
   - Consistent color mapping: CONCEPTION (green #2d5a3f), TRAVAUX (yellow #f4c430), MAINTENANCE (purple #7b4b94), AUTRE (gray #808080)
   - Applied to all donut charts, bar charts, and KPI cards throughout dashboard

2. **Fixed Critical Data Ingestion Bug**:
   - **Problem**: Dashboard was reading summary rows from Google Sheets, showing incorrect project counts (24 instead of 6)
   - **Solution**: Enhanced `read_worksheet()` in `google_sheets.py` to stop reading when encountering "Résumé par BU", "Résumé par Typologie", or "Total TS" markers
   - Validates numeric ID column to exclude summary rows and empty rows
   - Checks all cells in first 10 columns for summary markers

3. **Enhanced KPIs & Metrics**:
   - Removed "Montant Moyen" (not business-relevant)
   - Added project counts per BU in all visualizations (format: "BU_NAME (X projets)")
   - Added CA Pondéré (weighted revenue) for Envoyé/État views
   - BU totals displayed in colored KPI cards with proper centering
   - Monthly averages per BU and overall

4. **Redesigned Vue Globale**:
   - **Separate Sections**: Clear separation between "Analyse par Business Unit" and "Analyse par Typologie"
   - **Monthly Stacked Bar Chart**: Shows monthly CA by BU with three trend lines:
     - Monthly total (sum of all BUs per month)
     - Cumulative total (running sum across months)
     - Average (horizontal reference line)
   - **Sent/Pondéré Chart**: Stacked bars showing pondéré vs non-pondéré amounts with trend lines for Envoyé/État views
   - All charts display CA amounts (not percentages) with project counts in labels

5. **Enhanced Vue Mensuelle**:
   - BU and Typologie amount breakdowns in header with colored cards
   - Donut + bar charts for both BU and Typologie
   - Project counts included in all chart labels

6. **Typologie Split Logic**:
   - When projects have multiple typologies (e.g., "DV,Animation,Paysage"), amounts are divided equally among all categories
   - Prevents over-counting while ensuring all typologies are represented
   - Applied consistently across all views and charts

7. **Streamlit API Updates**:
   - Replaced deprecated `use_container_width=True` with `width='stretch'`
   - Added unique `key` parameters to all `plotly_chart` calls to prevent `StreamlitDuplicateElementId` errors
   - Fixed variable naming conflicts (renamed duplicate `fig_type` variables)

**Code Changes**:
- `src/dashboard/app.py`: Complete rewrite (~1200 lines) with new chart functions, BU color constants, typologie split logic, and enhanced UI
- `src/integrations/google_sheets.py`: Enhanced `read_worksheet()` with comprehensive summary row detection and validation

**Impact**: Dashboard now provides accurate data visualization matching business requirements for BU analysis and typologie tracking. All charts are color-coded by BU and include project counts.

### 18.7 Quarterly CA Breakdown View (December 2025)

**Feature Addition**: Added expandable quarterly CA breakdown view in Vue Globale tab, positioned after BU totals and before graphs.

**Key Features**:

1. **Quarterly Data Extraction**:
   - `get_quarterly_totals()`: Extracts quarterly totals from DataFrame columns (`Montant Total Q{1-4}_{year}`, `Montant Pondéré Q{1-4}_{year}`)
   - `get_quarterly_by_bu()`: Extracts quarterly amounts grouped by Business Unit
   - `get_quarterly_by_typologie()`: Extracts quarterly amounts grouped by Typologie (with split handling)

2. **Quarterly Breakdown Display**:
   - **Overall Section**: Total CA per quarter (Q1-Q4) with pondération support for Envoyé/État views
   - **Per BU Section**: CA per quarter per BU with colored rows (CONCEPTION=green, TRAVAUX=yellow, MAINTENANCE=purple, AUTRE=gray)
   - **Per Typologie Section**: CA per quarter per Typologie with colored rows, sorted by total amount (descending)
   - Expandable section (collapsed by default) accessible in Vue Globale tab only
   - For Envoyé views: Shows "Total / Pondéré" format matching existing BU KPI format

3. **Color Standardization**:
   - Defined `TYPOLOGIE_COLORS` constant at app level for consistent typologie colors across all charts
   - Updated `plot_typologie_donut()` and `plot_typologie_bar()` to use centralized color palette
   - Created `create_colored_table_html()` helper function for HTML tables with colored rows
   - Automatic text color adjustment for readability (dark text on light backgrounds, white text on dark)

4. **Data Handling**:
   - Handles missing quarterly columns gracefully
   - Shows appropriate messages when no data available
   - Only displays data for selected year
   - Uses split typologie logic for accurate multi-typologie project counting

**Code Changes**:
- `src/dashboard/app.py`: Added quarterly extraction functions, `display_quarterly_breakdown()` function, `create_colored_table_html()` helper, and integrated expandable section in Vue Globale tab
- Centralized `TYPOLOGIE_COLORS` constant definition (moved from local definitions in plot functions)

**Impact**: Dashboard now provides quarterly CA analysis with visual color coding for both BU and Typologie breakdowns, enabling quick quarterly performance assessment. Colors are consistent across the entire application, improving visual coherence and user experience.

### 18.8 Dashboard Performance Optimization - Critical Fix (December 2025)

**Problem**: Dashboard was extremely slow (50-70 seconds per page load) with ghost views (old content persisting) and tab jumping issues when changing months in Vue Mensuelle.

**Root Cause Identified** (Historical - PDF functionality since removed):
- **Eager PDF Generation**: `generate_pdf_report()` was being called on EVERY script rerun, even when user never clicked download
- PDF generation included expensive `fig.write_image()` calls using Kaleido (high-resolution chart export: 2000x1200px, scale 3)
- Each chart export took ~5 seconds, with 4 charts = 20+ seconds wasted on every interaction
- This happened even when viewing data, not just when exporting

**Solution Implemented** (Historical - PDF functionality since removed):
1. **Lazy PDF Generation**: PDFs were only generated when user explicitly clicked "Générer le PDF" button
2. **Expander UI**: PDF export moved to collapsed expanders, preventing automatic execution
3. **Session State Caching**: Generated PDFs cached in `st.session_state` to avoid regeneration
4. **PDF Cache Invalidation**: PDFs automatically cleared when view type or year changes

**Performance Results**:
- **Before**: 50-70 seconds per page load, 18-22 seconds for chart rendering
- **After**: 0.38-0.45 seconds per page load (cached), 0.10-0.15 seconds for chart rendering
- **Improvement**: ~99% reduction in load time (~150x faster)

**Code Changes** (Historical - PDF functionality since removed):
- `src/dashboard/app.py`:
  - Wrapped all `generate_pdf_report()` calls in `st.expander()` with "Générer le PDF" button
  - Added session state management for PDF caching (`globale_pdf`, `monthly_pdf`, `data_pdf`)
  - Added automatic PDF cache clearing on parameter changes
  - Moved quarterly breakdown from Vue Globale to production tabs (user modification)

**Impact**: Dashboard performance optimization eliminated eager PDF generation, resulting in instant page loads. Performance improvements remain, though PDF functionality has since been removed (see section 18.22). Ghost view issues eliminated due to faster rendering preventing overlapping script executions.

### 18.9 Production Year Views & UI Enhancements (December 2025)

**Major Feature Addition**: Added "À produire" (To Be Produced) tabs showing revenue breakdown by production year with cross-year aggregation and enhanced visual consistency.

**Key Features**:

1. **Production Year Tabs ("À produire")**:
   - Added tabs for each production year (selected_year, Y+1, Y+2) in both Vue Globale and Vue Mensuelle
   - Each tab displays KPIs, BU breakdowns, Typologie breakdowns, and charts specific to that production year
   - Uses `Montant Total {year}` and `Montant Pondéré {year}` columns from revenue engine
   - **Cross-Year Aggregation**: For "À produire 2026", aggregates data from both `SPREADSHEET_SIGNE_2025` (deals signed in 2025 but produced in 2026) and `SPREADSHEET_SIGNE_2026` (deals signed and produced in 2026)
   - Monthly view shows production breakdown for deals signed in that specific month only

2. **Quarterly Breakdown Integration**:
   - Moved quarterly CA breakdown from Vue Globale summary section to inside each "À produire" tab
   - Each production year tab now has its own expandable quarterly breakdown showing Q1-Q4 distribution
   - More logical organization: quarterly data is now contextual to each production year

3. **UI Consistency Improvements**:
   - **Vue Mensuelle**: Replaced plain `st.metric()` with styled `create_kpi_card()` boxes for "Résumé du Mois" and "Montants par Typologie" sections
   - **Vue Globale**: Added "Montants par Typologie" KPI boxes section matching BU section format
   - **À produire Views**: Added styled Typologie KPI cards with production year-specific amounts
   - All sections now use consistent card styling with proper color theming

4. **Typologie Color System**:
   - Created unique color palette for typologies distinct from BU colors (coral, teal, light green, navy, orange, pink, purple, blue)
   - Changed Paysage color from yellow (#e9c46a) to light green (#90be6d) to avoid confusion with TRAVAUX yellow
   - Undefined/unknown typologies consistently use gray (#808080) matching AUTRE BU color
   - Added CSS classes for typologie KPI cards (`.metric-card-dv`, `.metric-card-paysage`, etc.)

5. **Chart Type Standardization**:
   - Replaced all donut charts with vertical bar charts for consistency
   - Changed all "À produire" bar charts from horizontal to vertical orientation
   - Updated all bar charts to sort by value (descending) with text labels outside bars
   - Added stacked monthly bar chart for typologies (top 6) matching BU monthly chart format

6. **Table Styling Consistency**:
   - All quarterly tables now use consistent dark green headers (#1a472a)
   - Section titles use neutral gray/blue color (#2c3e50) distinct from BU/Typologie colors
   - Overall quarterly table uses same styling as BU/Typologie tables (dark green header, light gray row background)

**Code Changes**:
- `src/dashboard/app.py`:
  - Added production year data functions: `get_production_year_totals()`, `get_production_bu_amounts()`, `get_production_typologie_amounts()`, `get_production_ts_total()`
  - Added `load_aggregated_production_data()` for cross-year aggregation
  - Added production chart functions: `plot_production_bu_bar()`, `plot_production_typologie_bar()`
  - Added UI components: `create_production_bu_kpi_row()`, `create_production_typologie_kpi_row()`, `render_single_production_view()`, `render_production_tabs()`
  - Added `get_monthly_data_by_typologie()` and `plot_monthly_stacked_bar_typologie()` for typologie monthly evolution
  - Updated `plot_bu_donut()` and `plot_typologie_donut()` to create vertical bar charts instead
  - Updated all bar chart functions to use vertical orientation
  - Updated `display_quarterly_breakdown()` to use consistent table styling
  - Updated `create_colored_table_html()` to support custom header colors
  - Moved quarterly breakdown from Vue Globale summary to `render_single_production_view()`
  - Updated color constants: `TYPOLOGIE_COLORS` dictionary, `TYPOLOGIE_DEFAULT_COLOR`, `TYPOLOGIE_COLOR_LIST`
  - Added CSS classes for typologie metric cards

**Impact**: Dashboard now provides comprehensive production year forecasting with cross-year aggregation, enabling users to see how deals signed in different years contribute to each production year's revenue. UI consistency improvements make the dashboard more professional and easier to navigate. All charts and tables follow consistent styling patterns with proper color theming for both BU and Typologie categories.

### 18.10 Google Sheets Formatting & Visual Enhancements (December 2025)

**Major Enhancement**: Comprehensive formatting system for Google Sheets output with currency formatting, color coding, and visual organization improvements.

**Key Features**:

1. **Currency Formatting (Devises arrondis)**:
   - All amount columns formatted as currency with € symbol and thousands separator
   - Pattern: `#,##0 €` (rounded to whole numbers, no decimals)
   - Applied to main data rows, summary rows, and TS Total row
   - Amount columns preserved as numeric (not strings) to enable proper formatting

2. **Color System Alignment**:
   - **BU Colors**: Matched dashboard colors (CONCEPTION=green #2d5a3f, TRAVAUX=yellow #f4c430, MAINTENANCE=purple #7b4b94, AUTRE=gray #808080)
   - **Typologie Colors**: Matched dashboard palette (DV=coral #e76f51, Animation=teal #2a9d8f, Paysage=light green #90be6d, etc.)
   - Colors applied only to first column (category name) for better readability
   - Header rows use light green background (#93c47d) for visual consistency

3. **Visual Organization**:
   - **Main Data View**: No row coloring (only borders) for cleaner data reading
   - **Summary Sections**: Colored category names (first column only) with white data cells
   - **Year Separators**: Blank merged columns with gray background and thick borders between each year's quarterly breakdown
   - **TS Total Row**: Purple background with white text, properly positioned

4. **Technical Fixes**:
   - Fixed API method: Changed from `worksheet.batch_update()` to `spreadsheet.batch_update(body={...})` for formatting requests
   - Removed invalid border fields (`innerHorizontal`, `innerVertical`) - only valid for `updateBorders`, not `repeatCell`
   - Increased default worksheet size from 1000 to 2000 rows with auto-resize for existing worksheets
   - Fixed row indexing: Proper conversion between 1-indexed (gspread) and 0-indexed (Google Sheets API)

**Code Changes**:
- `src/integrations/google_sheets.py`:
  - Added `BU_COLORS` and `TYPOLOGIE_COLORS` dictionaries matching dashboard colors
  - Added `_get_typologie_color()` method for typologie color lookup
  - Updated `_prepare_dataframe()` to preserve numeric types for amount columns
  - Updated `write_summary()` to preserve numeric types for amount columns
  - Added `_insert_year_separators()` method to insert blank columns between years
  - Modified `write_summary()` to return separator column indices
  - Updated `format_view()` to:
    - Remove row coloring from main data view (borders only)
    - Apply colors only to first column in summary sections
    - Format amount columns as currency
    - Format year separator columns with merged cells and borders
    - Fix TS Total row positioning (0-indexed conversion)

**Impact**: Google Sheets output now provides professional, visually organized data with proper currency formatting, color-coded categories aligned with dashboard, and clear year separation for quarterly breakdowns. Main data view remains clean and readable while summaries are visually organized with color-coded category names.

### 18.11 Commercial Alerts Notion Sync & Email Enhancements (December 2025)

**Major Feature**: Complete overhaul of commercial alert system with Notion database integration, enhanced email tables, and automatic person property mapping.

**Key Features**:

1. **Email Table Enhancements**:
   - Added "ID" column to both weird proposals and follow-up tables
   - Added "Lien Furious" column with direct clickable links to proposals
   - Removed "Date signature" from follow-up table (irrelevant for unsigned proposals)
   - Furious URL format: `https://merciraymond.furious-squad.com/compta.php?view=5&cherche={id}`

2. **Notion Alerts Sync System**:
   - Created two dedicated Notion databases: "Alertes Weird" and "Suivi Commercial"
   - Automatic sync of all alerts to Notion after email sending
   - Person property mapping: Automatic matching of Furious owners to Notion users via email/name
   - User mapping script: `build_notion_user_mapping.py` generates JSON mapping file
   - Sync strategy: Archives existing pages and recreates (alerts are transient)

3. **Notion Database Schema**:
   - **Weird Proposals**: Name (title), ID Devis (text), Client (text), Montant (number), Statut (status), Date (date), Début projet (date), Fin projet (date), Responsable (people), Lien Furious (url), Probleme (multi-select)
   - **Follow-ups**: Same as Weird except Probabilite (number) instead of Probleme
   - Probleme multi-select options: "Montant faible (<1000€)", "Date début manquante", "Date fin manquante", "Date début > Date fin", "Probabilité 0%"

4. **Excluded Owners Filter**:
   - Added `EXCLUDED_OWNERS` configuration in settings
   - Filters out proposals from former employees (e.g., eloi.pujet) early in pipeline
   - Prevents excluded proposals from appearing in views, alerts, sheets, and Notion

**Code Changes**:
- `src/processing/alerts.py`: Already had `id` field in alert dictionaries
- `src/integrations/email_sender.py`: Added `_build_furious_url()`, ID column, removed signature_date from follow-ups
- `src/integrations/notion_users.py`: New module for fetching Notion workspace users and building owner-to-user-ID mapping
- `src/integrations/notion_alerts_sync.py`: New module for syncing alerts to Notion with person property support
- `scripts/build_notion_user_mapping.py`: New script to generate user mapping JSON (run once, updates as needed)
- `scripts/run_pipeline.py`: Added Step 10 for Notion alerts sync
- `config/settings.py`: Added `notion_weird_database_id`, `notion_followup_database_id`, `EXCLUDED_OWNERS`
- `src/processing/cleaner.py`: Added filter to exclude proposals from excluded owners
- Fixed Furious URL format (removed unnecessary query parameters)
- Fixed Statut property type: Changed from `select` to `status` for Notion API compatibility

**Technical Details**:
- Person property mapping uses email prefix matching and name normalization
- Mapping file: `config/notion_user_mapping.json` (auto-generated, cached for performance)
- Notion API version: Uses latest client with status property support
- Error handling: Graceful handling of missing properties, unmapped users, and API errors

**Impact**: Commercial alerts are now fully integrated with Notion, enabling team-wide visibility and filtering. Email alerts include direct links to Furious proposals. Person properties automatically populate, allowing filtering by "me" in Notion. Excluded owners filter ensures clean data by removing irrelevant proposals from former employees.

### 18.12 TRAVAUX Projection Pipeline Overhaul (December 2025)

**Major Feature**: Complete replacement of TRAVAUX Gantt sync with new "Projection Travaux prochains 4 mois" system for identifying high-probability waiting proposals to fill calendar gaps.

**Key Features**:

1. **TRAVAUX Projection Generator**:
   - Filters proposals with: BU = TRAVAUX (includes TS rule), Status = WAITING, Probability ≥ 50%
   - Date criteria: proposal `date` within 30 days OR `projet_start` within 120 days (~4 months)
   - Returns structured proposal data for email and Notion sync

2. **Specialized Email for Mathilde**:
   - Subject: "Projection Travaux prochains 4 mois"
   - Recipients: mathilde@merciraymond.fr (TO), guillaume@merciraymond.com and vincent.delavarende@merciraymond.com (CC)
   - HTML table with: Nom du projet, Client, Montant, Commercial(s), Date, Début projet, Probabilité, Lien Furious
   - TRAVAUX-themed styling (yellow/gold color scheme)

3. **Notion Database Sync with Deduplication**:
   - New dedicated database: `NOTION_TRAVAUX_PROJECTION_DATABASE_ID`
   - **Upsert strategy**: Uses "ID Devis" as unique key (fallback: parse from "Lien Furious" URL)
   - **Preserves comments**: Updates properties but keeps existing page and Name/title unchanged
   - Prevents duplicates when pipeline runs twice monthly (1st and last day)
   - Schema validation: Only includes properties that exist in database

4. **Notion Deduplication System (All Databases)**:
   - Replaced "archive all + recreate" strategy with true upsert for all 3 Notion DBs
   - **Weird Proposals DB**: Upsert by "ID Devis", preserves Name and comments
   - **Follow-up DB**: Upsert by "ID Devis", preserves Name and comments
   - **TRAVAUX Projection DB**: Upsert by "ID Devis", preserves Name and comments
   - Critical for bi-monthly runs (1st and last day): prevents duplicate pages while preserving meeting comments

5. **Test Mode Email Improvements**:
   - Alerts: Sends individual emails per owner (not batched) to test address, with owner name in subject
   - TRAVAUX projection: Sends to test address without CC
   - Enables granular testing of production email flow

6. **Property Fixes**:
   - Added "Probabilite" property to Weird Proposals DB (was missing)
   - Fixed "Probabilite" spelling in TRAVAUX Projection DB (was "Probabilité" with accent)
   - All databases now use consistent "Probabilite" (without accent) format

**Code Changes**:
- **Deleted**: `src/api/projects.py` (old Gantt sync), `src/integrations/notion_sync.py` (old Gantt sync)
- **Created**: `src/processing/travaux_projection.py` (filtering logic), `src/integrations/notion_travaux_sync.py` (Notion sync)
- **Modified**: `src/integrations/email_sender.py` (added `send_travaux_projection_email()`, improved test mode)
- **Modified**: `src/integrations/notion_alerts_sync.py` (upsert strategy, added Probabilite to weird proposals)
- **Modified**: `src/processing/alerts.py` (added probability to weird proposals alert data)
- **Modified**: `scripts/run_pipeline.py` (replaced Step 9 with new Step 8: TRAVAUX projection)
- **Modified**: `config/settings.py` (added `notion_travaux_projection_database_id`, `TRAVAUX_PROJECTION_*` constants)

**Technical Details**:
- Deduplication key: "ID Devis" (rich_text) - Furious proposal ID
- Fallback deduplication: Parses proposal ID from "Lien Furious" URL (`cherche={id}` parameter)
- Schema-aware property building: Validates database schema before setting properties (prevents 400 errors)
- Error logging: Enhanced with detailed Notion API error messages (code + body) for debugging
- Date windows: Configurable via `TRAVAUX_PROJECTION_DATE_WINDOW` (30 days) and `TRAVAUX_PROJECTION_START_WINDOW` (120 days)

**Impact**: TRAVAUX pipeline now focuses on proactive opportunity identification rather than tracking signed deals. Bi-monthly runs (1st and last day) no longer create duplicate Notion pages, preserving meeting comments and discussion history. Test mode provides granular email testing without affecting production recipients.

### 18.13 Google Sheets Dynamic Formatting Fix (December 2025)

**Critical Fix**: Resolved formatting misalignment issue where colors and merged cells persisted at hardcoded row positions, causing visual clutter when data row counts varied between pipeline runs.

**Problem**:
- Formatting (colors, borders, merged cells) was applied to fixed row ranges
- When data row counts changed, old formatting remained at previous positions, creating "ghost" formatting blocks
- Summary sections had hardcoded column width (`endColumnIndex: 20`) and currency formatting didn't account for year separator columns
- `worksheet.clear()` only clears cell values, not formatting or merged cells

**Root Cause**:
- Google Sheets API preserves formatting and merged cells even after `worksheet.clear()`
- Formatting ranges were computed correctly but old formatting persisted from previous runs
- Summary formatting assumed fixed column count without considering dynamically inserted year separator columns

**Solution**:
1. **Worksheet Reset Before Write**: Added `_reset_worksheet_layout_and_formatting()` method that:
   - Unmerges all cells in worksheet using `unmergeCells` batch request
   - Clears all `userEnteredFormat` (colors, borders, number formats) across entire sheet
   - Called immediately after `worksheet.clear()` in `write_view()` method
   - Ensures clean slate before applying new formatting

2. **Dynamic Summary Formatting**:
   - Removed hardcoded `endColumnIndex: 20` limit
   - Compute summary column width from actual summary DataFrame columns + inserted separator columns
   - Currency formatting now accounts for year separator column indices (separator-aware column mapping)

3. **Formatting Application**:
   - All formatting ranges now computed dynamically based on actual written data
   - Row positions tracked correctly: `data_end_row`, `bu_summary_start/end`, `type_summary_start/end`, `ts_total_row`
   - Formatting applied only to actual data ranges, not fixed positions

**Code Changes**:
- `src/integrations/google_sheets.py`:
  - Added `_reset_worksheet_layout_and_formatting()` method with `unmergeCells` and `repeatCell` (clear format) batch requests
  - Updated `write_view()` to call reset method after `worksheet.clear()`
  - Updated `format_view()` to compute summary column width dynamically from prepared DataFrame
  - Fixed currency formatting in summary sections to use separator-aware column indices
  - Removed hardcoded `endColumnIndex: 20` from summary formatting requests

**Impact**: All 3 Google Sheets (État, Envoyé, Signé) now maintain clean, properly aligned formatting regardless of data row count variations. Formatting adapts dynamically to actual data size, eliminating visual clutter from stale formatting blocks. Summary sections correctly format all columns including year separators.

### 18.14 Dashboard Data Reading Fix - Currency Formatting Issue (December 2025)

**Critical Fix**: Resolved dashboard showing all zeros (00) for "Envoyé vue globale" and missing December data in "Signed" view despite sheets containing valid data.

**Problem**:
- Dashboard displayed all amounts as 0€ in Vue Globale for Envoyé view
- Signed view showed November data but not December, even though both sheets existed
- Amount slider in dashboard crashed with `ValueError` (min=max=0)
- Terminal logs confirmed sheets were being read (28 rows for Envoyé, 24 rows for December Signed)

**Root Cause**:
- Google Sheets currency formatting (`#,##0 €`) was applied to amount columns (from section 18.10)
- Dashboard's `read_worksheet()` used gspread's default `get_all_values()` which returns **formatted** values
- Formatted values returned as strings like `"12 345 €"` instead of numeric `12345`
- String-to-numeric conversion in `parse_numeric_columns()` coerced these to `0`, causing all amounts to appear as zero
- ID column validation also failed because numeric IDs came back as `"12345.0"` strings, causing some rows to be filtered out

**Solution**:
1. **Unformatted Value Reading**: Modified `read_worksheet()` to use `ValueRenderOption.unformatted` when calling `get_all_values()`
   - Ensures numeric columns remain numeric (not formatted strings)
   - Amounts are read as actual numbers, preserving their values
2. **ID Validation Fix**: Enhanced ID column parsing to handle float-to-string conversion
   - Accepts numeric IDs even when returned as `"12345.0"` strings
   - Validates by converting to float and checking if it's a valid number

**Code Changes**:
- `src/integrations/google_sheets.py::read_worksheet()`:
  - Added import: `from gspread.utils import ValueRenderOption`
  - Changed `worksheet.get_all_values()` to `worksheet.get_all_values(value_render_option=ValueRenderOption.unformatted)`
  - Enhanced ID validation to handle float string format (`"12345.0"`)

**Verification**:
- CLI test confirmed Envoyé December: 28 rows, `amount_sum = 815,946.87€`, `amount_max = 300,000.0€`
- CLI test confirmed Signé (Nov+Dec): 31 rows, `amount_sum = 285,517.84€`, `amount_max = 47,600.0€`
- Dashboard now correctly displays non-zero amounts and includes all monthly sheets

**Impact**: Dashboard now correctly reads numeric values from Google Sheets regardless of currency formatting. All amounts display accurately, and all monthly sheets (November, December, etc.) are properly aggregated. This fix ensures data integrity between Google Sheets output (formatted for human readability) and dashboard consumption (unformatted for accurate calculations).

### 18.15 BU-Grouped Typologies Dashboard Reorganization (December 2025)

**Major Feature**: Complete reorganization of typology display in dashboard to show typologies grouped by Business Unit, with new typology structure derived from BU categories.

**Business Context**:
- Typologies are now derived from BU categories (3 main categories with subcategories)
- New typology structure: CONCEPTION → DV, Paysage, Concours; TRAVAUX → DV(Travaux), Travaux Vincent, Travaux conception; MAINTENANCE → TS, Entretien, Animation; AUTRE → Autre
- Some typologies already exist (DV, Entretien, Paysage, Animation) with existing tracking; new ones (Concours, DV(Travaux), Travaux Vincent, Travaux conception) start at 0€
- Dual TS tracking: TS(title) based on proposal title containing "TS", and TS(typologie) based on `cf_typologie_de_devis == "TS"`

**Key Features**:
1. **BU-to-Typologies Mapping**: Added `BU_TO_TYPOLOGIES` constant mapping each BU to its typologies
2. **BU-Grouped Display**: Typologies now displayed in blocks grouped under their parent BU with colored headers
3. **Zero-Filled Output**: All typologies in mapping appear even if 0€/0 projets (ensures visibility of new typologies)
4. **Special TS Handling**: TS(typologie) appears under MAINTENANCE even when BU=TRAVAUX (due to title rule), enabling dual tracking visibility
5. **Consistent UI**: BU-grouped typology blocks applied to Vue Globale, Vue Mensuelle, "À produire {year}" views, and quarterly breakdown expanders

**Code Changes**:
- `src/dashboard/app.py`:
  - Added `BU_TO_TYPOLOGIES` mapping constant
  - Added `normalize_typologie_for_css()` helper for safe CSS class generation
  - Added `get_typologie_amounts_for_bu()` function with zero-filled output and TS special-casing
  - Added `get_ts_typologie_total()` for separate TS(typologie) tracking
  - Created `create_bu_grouped_typologie_blocks()` for regular views
  - Created `create_bu_grouped_typologie_blocks_production()` for production year views
  - Added `get_production_typologie_amounts_for_bu()` for production-year aggregation
  - Extended `TYPOLOGIE_COLORS` with new typologies (Concours, DV(Travaux), Travaux Vincent, Travaux conception, TS, Entretien)
  - Added CSS classes for all new typologies
  - Updated Vue Globale and Vue Mensuelle to use BU-grouped blocks
  - Updated "À produire" views to use BU-grouped typology blocks
  - Enhanced quarterly breakdown to include BU-grouped typology section
- `src/integrations/google_sheets.py`:
  - Extended `TYPOLOGIE_COLORS` (RGB format) to match dashboard colors for consistent sheet formatting

**Bug Fixes**:
- **TRAVAUX Blank Cards Bug**: Fixed CSS class mismatch causing white-on-white text. Normalization helper now generates kebab-case classes (e.g., `DV(Travaux)` → `dv-travaux`) matching CSS definitions. Cards now use BU-themed colors instead of typology-specific colors to avoid CSS lookup failures.

**Design Decisions**:
- Typology cards use BU-themed colors (not individual typology colors) for visual consistency and to avoid CSS class mismatches
- BU headers use simple colored text (no background boxes) for cleaner, more readable design
- All typologies always visible (zero-filled) to show complete structure even when no data exists

**Impact**: Dashboard now clearly shows the BU→Typologie dependency structure, making it easy to see which typologies belong to which Business Unit. New typologies are immediately visible even with 0€, and the dual TS tracking provides comprehensive visibility. The reorganization improves data understanding and business unit analysis across all dashboard views.

### 18.16 Email Template Overhaul & Production CC Configuration (December 2025)

**Major Enhancement**: Complete redesign of email templates with improved date formatting, Notion integration, branding removal, and production CC configuration.

**Key Features**:

1. **Date Formatting Standardization**:
   - All dates in emails now formatted as DD/MM/YYYY (French format)
   - Updated `_format_date_display()` method to parse YYYY-MM-DD input and convert to DD/MM/YYYY
   - Applied to all date fields: proposal date, projet_start, projet_stop, signature_date
   - Consistent date display across all email templates

2. **Notion Link Integration**:
   - Added prominent Notion links at the beginning of all email templates
   - **Commercial Alerts Email**: Links to "Suivi Commercial" database (`https://www.notion.so/Suivi-Commercial-2ced927802d7809faef6fe444b90d526`)
   - **TRAVAUX Projection Email**: Links to TRAVAUX-specific Notion database (`https://www.notion.so/2d5d927802d78002b8cbcee60cc75c29`)
   - **Objectives Management Email**: Links to "Suivi Commercial" database
   - Button-style links with gradient backgrounds, shadows, and prominent styling for high visibility

3. **Branding Removal**:
   - Removed all "Myrium" mentions from email content and subjects
   - Changed headers from "📧 Myrium - Alertes Commerciales" to "📧 Alertes Commerciales"
   - Changed headers from "🎯 Myrium - Rapport Objectifs" to "🎯 Rapport Objectifs"
   - Removed "Myrium" from all email subject lines
   - Removed footer message "Cet email a été généré automatiquement par Myrium" from all templates

4. **Production CC Configuration**:
   - Added `taddeo.carpinelli@merciraymond.fr` to CC for all production emails
   - **Commercial Alerts**: CC added to all owner-specific alert emails (production mode only)
   - **TRAVAUX Projection**: CC added alongside existing CC recipients (Guillaume, Vincent)
   - **Objectives Management**: CC added to objectives report email
   - CC only applied in production mode (not in test mode)
   - Enhanced `_send_email()` method to support optional CC parameter

**Code Changes**:
- `src/integrations/email_sender.py`:
  - Updated `_format_date_display()` to convert YYYY-MM-DD to DD/MM/YYYY format
  - Added Notion link sections to all three email HTML templates with button-style styling
  - Removed all "Myrium" text from headers, subjects, and footer messages
  - Enhanced `_send_email()` method with optional `cc_emails` parameter
  - Updated `send_combined_alerts()` to add CC for production emails
  - Updated `send_travaux_projection_email()` to include CC in existing CC list
  - Updated `send_objectives_management_email()` to add CC for production emails

**Technical Details**:
- Date parsing handles YYYY-MM-DD format with fallback to original format if parsing fails
- Notion links use gradient backgrounds and button styling for maximum visibility
- CC configuration respects test mode (no CC added when `test_mode=True`)
- All email recipients now receive CC copy for production monitoring

**Impact**: Email templates now provide consistent French date formatting, prominent Notion database access, and clean branding-free presentation. Production emails include automatic CC to project maintainer for monitoring and oversight. Notion links enable direct access to detailed tracking databases from email notifications.

### 18.17 Dashboard TS Display Simplification (December 2025)

**UI Simplification**: Removed redundant TS distinction display from Vue Globale to streamline dashboard interface.

**Key Changes**:

1. **TS Counting Clarification**:
   - Confirmed that TS (Titres de Séjour) projects are included in global CA calculations
   - Global CA (`total_amount`) sums all rows from DataFrame, including TS projects
   - "Total TS" KPI remains as separate metric showing TS portion of total CA

2. **Removed TS Distinction Section**:
   - Removed dual TS display cards ("TS (title)" and "TS (typologie)") from end of Vue Globale
   - Eliminated redundant section that showed both TS(title) and TS(typologie) side-by-side
   - Dashboard now flows directly from typologie blocks to monthly average section

**Code Changes**:
- `src/dashboard/app.py`: Removed lines 3163-3173 containing TS distinction KPI cards section
- Removed `get_ts_typologie_total()` call and dual-column TS display from Vue Globale tab

**Impact**: Dashboard interface simplified by removing redundant TS distinction. Global CA correctly includes TS projects, and "Total TS" KPI in main indicators section provides sufficient TS visibility without duplication. Cleaner, more streamlined dashboard layout.

### 18.18 Follow-up Alerts OR Rule & 2026 Objectives Update (January 2026)

**Business Logic Enhancement**: Updated commercial follow-up alert filtering rules and refreshed 2026 objectives with new targets, 11-month accounting, and pondéré-based Envoyé calculations.

**Key Changes**:

1. **Follow-up Alerts OR Rule (TRAVAUX/MAINTENANCE)**:
   - Changed forward-window check from "projet_start else date" to **OR logic**: `date <= window_end OR projet_start <= window_end`
   - TRAVAUX/MAINTENANCE proposals now pass if **either** date field is within the 60-day forward window
   - CONCEPTION unchanged (still uses `date` only)
   - Makes alert selection more inclusive for TRAVAUX/MAINTENANCE as requested

2. **Email CC Configuration**:
   - Added `guillaume@merciraymond.fr` to production CC for commercial follow-up alerts
   - Updated all Guillaume email addresses from `.com` to `.fr` domain
   - CC list now includes: `taddeo.carpinelli@merciraymond.fr` and `guillaume@merciraymond.fr`

3. **2026 Objectives - New Targets**:
   - **CONCEPTION**: DV (50k), Concours (100k), Paysage (650k) - Total: 800k
   - **TRAVAUX**: DV(Travaux) (1M), Travaux conception (500k), Travaux Vincent (1.5M) - Total: 3M
   - **MAINTENANCE**: Entretien (495k), TS (137.5k/year), Animation (50k/year) - Total: 682.5k
   - BU totals stored directly (not computed on-the-fly) matching sum of typologies

4. **11-Month Accounting**:
   - August objective = 0 (merged into July)
   - July objective = 2× normal month
   - All other months = normal month (annual_total / 11)
   - Applied to all 2026 objectives via `generate_11_month_distribution()` helper

5. **Envoyé Objectives = Signé Objectives**:
   - Envoyé objectives set equal to Signé objectives for 2026
   - Envoyé realized amounts now calculated using **pondéré** (amount × probability)
   - Signé realized amounts remain as raw `amount` values
   - Applied in both dashboard and objectives management email

**Code Changes**:
- `src/processing/alerts.py`:
  - Updated `AlertsGenerator._needs_followup()` with OR logic for TRAVAUX/MAINTENANCE
  - Forward check now evaluates both `date` and `projet_start` independently

- `src/integrations/email_sender.py`:
  - Added `guillaume@merciraymond.fr` to CC in `send_combined_alerts()`
  - Updated Guillaume email addresses from `.com` to `.fr` throughout
  - Modified `_calculate_realized_by_month()` to use `amount_pondere` when available
  - Compute pondéré for Envoyé data in `_generate_objectives_management_html()`

- `src/processing/objectives.py`:
  - Added `generate_11_month_distribution()` helper for 11-month accounting
  - Updated `OBJECTIVES[2026]['signe']['typologie']` with new targets
  - BU totals stored directly: CONCEPTION (800k), TRAVAUX (3M), MAINTENANCE (682.5k)
  - Set `OBJECTIVES[2026]['envoye']` equal to `signe` for all dimensions

- `src/dashboard/app.py`:
  - Compute `amount_pondere` for `df_envoye` using `calculate_weighted_amount()`
  - Modified `calculate_realized_by_month/quarter/year()` to prefer `amount_pondere` when available
  - Charts automatically use pondéré for Envoyé via existing calculation functions

- `myrium/tests/`:
  - Added `test_alerts_followup.py`: Tests for TRAVAUX/MAINTENANCE OR rule
  - Added `test_objectives_2026.py`: Tests for 11-month distribution, Envoyé=Signé, BU totals

**Technical Details**:
- Follow-up OR rule uses pandas Timestamp comparisons with proper NaT handling
- 11-month distribution helper supports both annual totals and fixed monthly amounts
- Pondéré calculation: `amount × (probability / 100)` with default 50% if probability missing
- BU totals validation ensures consistency with typology sums
- All changes backward-compatible with existing data structures

**Impact**: Follow-up alerts now correctly include TRAVAUX/MAINTENANCE proposals when either date field is within window, improving alert coverage. 2026 objectives reflect new business targets with proper 11-month accounting. Envoyé tracking now uses probability-weighted amounts, providing more accurate forecasting aligned with signature objectives. Guillaume added to CC for better visibility on commercial alerts.

### 18.19 Dashboard Objectifs Tab Refactoring & UI Cleanup (January 2026)

**Major UX Improvement**: Removed redundant view selector in Objectifs tab and eliminated duplicate charts in Vue Mensuelle for cleaner, more intuitive dashboard navigation.

**Key Changes**:

1. **Objectifs Tab Follows Sidebar View**:
   - Removed internal Envoyé/Signé sub-tabs - Objectifs tab now automatically uses sidebar "Type de vue" selection
   - If sidebar = Signé → shows Signé objectives only
   - If sidebar = Envoyé → shows Envoyé objectives only (with pondéré calculations)
   - If sidebar = État actuel → shows info message (objectives not available for snapshot view)
   - Performance improvement: Only loads relevant dataset (Envoyé OR Signé), not both

2. **Quarter Start Dates Added**:
   - Added `quarter_start_dates()` function to `objectives.py`
   - Trimestre section now displays both start and end dates: "Début: 01/01/2026 | Fin: 31/03/2026"

3. **Duplicate Charts Removed from Vue Mensuelle**:
   - Removed duplicate "Détail par Typologie" chart (kept only "Répartition par Typologie")
   - Removed duplicate "Détail par Business Unit" chart (kept only "Répartition par Business Unit")
   - Cleaner monthly view with one chart per dimension

**Code Changes**:
- `src/dashboard/app.py`: Refactored TAB 3 (Objectifs) to conditionally render based on `is_signed`/`is_sent` flags, removed internal tabs loop, removed duplicate chart rendering in Vue Mensuelle
- `src/processing/objectives.py`: Added `quarter_start_dates()` helper function
- Widget keys remain stable and unique (include `metric_key` to prevent duplicate widget ID errors)

**Impact**: Dashboard navigation is now more intuitive - users no longer need to select view type twice. Objectifs tab automatically reflects sidebar selection, reducing confusion. Vue Mensuelle is cleaner with no duplicate visualizations. Performance improved by loading only relevant objectives data.

### 18.20 Production-Year Reconciliation Fix & Date Handling Rules (January 2026)

**Critical Fix**: Resolved production-year allocation reconciliation issue where CA Total didn't match sum of production years (e.g., 3.6M€ total vs 1.5M€+700k€+165k€ = 2.365M€ missing). Implemented comprehensive date replacement rules (Rules 1-3) and window clamping (Rule 4) to prevent revenue loss.

**Root Causes Identified**:
- Projects starting before tracked window (e.g., Jan 2025 project starting in 2024) → revenue in 2024 lost
- Projects extending beyond tracked window (e.g., large CONCEPTION projects ending in 2028+) → revenue beyond Y+2 lost
- Missing `projet_start` or `projet_stop` dates → entire project amount lost (no allocation)

**Key Features**:

1. **Date Replacement Rules (Rules 1-3)**:
   - **Rule 1 (start missing only)**:
     - MAINTENANCE: Use `projet_stop` as end, start = end - 11 months (12-month span)
     - TRAVAUX: Start = `date`, end = `projet_stop` (even monthly spread)
     - CONCEPTION: Start = `date` (existing phasing rules apply)
   - **Rule 2 (end missing only)**:
     - MAINTENANCE: End = start + 11 months (12-month span)
     - TRAVAUX: End = start + 5 months (6-month span)
     - CONCEPTION: Unchanged (start-only, existing rules work)
   - **Rule 3 (both missing)**:
     - MAINTENANCE: Start = `date`, end = start + 11 months
     - TRAVAUX: Start = `date`, end = start + 5 months
     - CONCEPTION: Start = `date` (existing phasing rules apply)

2. **Window Clamping (Rule 4)**:
   - Tracked window extended from Y..Y+2 to **Y..Y+3** (up to +3 years)
   - Allocations before tracked window → clamped to first month of first year (Jan Y)
   - Allocations after tracked window → clamped to last month of last year (Dec Y+3)
   - Guarantees no revenue loss due to early starts or late ends

3. **Flagging System**:
   - Added columns: `dates_rule_applied` (bool), `dates_rule` (string), `dates_effective_start`, `dates_effective_stop`
   - Dashboard diagnostic expander shows flagged rows with rule details
   - Enables data quality review and validation

4. **Dashboard Enhancements**:
   - Production-year tabs automatically detect and display all years up to +3
   - Diagnostic panel shows flagged rows (Rules 1-3 applied) with rule breakdown
   - Reconciliation warning shows unallocated CA with detailed diagnostics

5. **Backfill Script Improvements**:
   - Uses Y..Y+3 tracking window (instead of Y..Y+2)
   - Enhanced rate-limit recovery: 10 retries (was 5), 15s base delay (was 10s), exponential backoff with jitter
   - Detects 429/503/500 errors and retries safely
   - Increased inter-month delays (40s) for better rate-limit protection

**Code Changes**:
- `src/processing/revenue_engine.py`:
  - Added `_compute_effective_dates()` method implementing Rules 1-3
  - Added `_clamp_allocation_to_window()` method implementing Rule 4
  - Updated `calculate_revenue()` to use effective dates and apply clamping
  - Default tracking window changed from Y..Y+2 to Y..Y+3
  - Added flagging columns to revenue calculation output
- `src/dashboard/app.py`:
  - Updated `render_production_tabs()` to show all detected years up to +3
  - Enhanced diagnostic expander to display flagged rows with rule details
  - Added filtering to cap production years at +3 from selected_year
- `src/integrations/google_sheets.py`:
  - Added `dates_effective_start` and `dates_effective_stop` to date column conversion
- `scripts/backfill_google_sheets_2025.py`:
  - Updated to use Y..Y+3 tracking window
  - Enhanced `_write_view_with_retry()` with stronger rate-limit recovery
  - Increased inter-month delays for better API rate-limit protection
- `tests/test_revenue_engine_dates.py`:
  - Added comprehensive tests for Rules 1-4 (9 tests total)
  - Tests cover all BU types and missing date scenarios

**Technical Details**:
- Effective dates computed before revenue spreading calculations
- Original dates preserved in DataFrame (not overwritten)
- Flagging columns added to DataFrame for dashboard visibility
- Clamping applied after monthly allocation calculation but before aggregation
- All rules preserve total project amount (no revenue loss)

**Impact**: Production-year reconciliation now works correctly - CA Total equals sum of production years (2025..2028) except for genuinely unallocatable rows (missing all dates including `date` column, which should be rare). Dashboard provides full visibility into date replacement rules applied, enabling data quality monitoring. Backfill script can reliably regenerate historical sheets with new rules without rate-limit crashes.

### 18.21 Dashboard Quarterly Breakdown Cleanup (January 2026)

**UI Simplification**: Removed redundant quarterly typology table from production year views to eliminate duplicate information display.

**Key Changes**:

1. **TS Amount Inclusion Clarification**:
   - Confirmed that TS (Titres de Séjour) amounts are included in **TRAVAUX** BU totals for all views
   - TS rule in `cleaner.py` assigns projects with "TS" in title to TRAVAUX BU (highest priority)
   - BU total calculations (`get_production_bu_amounts()`, `get_bu_amounts()`) filter by `cf_bu` column, so TS projects appear in TRAVAUX totals
   - TS typology display under MAINTENANCE is for typology breakdown only (doesn't affect BU totals)

2. **Redundant Table Removal**:
   - Removed "CA par Trimestre et par Typologie" table from quarterly breakdown in "À produire {year}" tabs
   - This table showed typologies without BU grouping, duplicating information already available in "Typologies (groupées par BU)" section below
   - Cleaner dashboard flow: BU totals → Typologies grouped by BU (with quarterly breakdown)

**Code Changes**:
- `src/dashboard/app.py`: Removed lines 2441-2484 containing the standalone quarterly typology table section from `display_quarterly_breakdown()` function

**Impact**: Dashboard quarterly analysis is now cleaner and more focused, eliminating redundant information display. Users see the same typology data organized by BU, which is more business-relevant. TS amount inclusion in TRAVAUX totals is now clearly documented and understood.

### 18.22 PDF Export Removal (January 2026)

**Feature Removal**: Complete removal of PDF download functionality from dashboard to simplify codebase and reduce maintenance overhead.

**Key Changes**:

1. **Removed PDF Generation Function**:
   - Deleted entire `generate_pdf_report()` function (~220 lines)
   - Removed all fpdf2 and kaleido dependencies from dashboard code
   - Eliminated PDF-related imports and helper functions

2. **Removed PDF Export UI**:
   - Removed PDF export expanders from Vue Globale tab
   - Removed PDF export expanders from Vue Mensuelle tab
   - Removed PDF export expanders from Données Détaillées tab
   - Removed all PDF download buttons and session state management

3. **Code Cleanup**:
   - Removed PDF session state initialization (`globale_pdf`, `monthly_pdf`, `data_pdf`)
   - Removed PDF cache clearing logic on parameter changes
   - Updated module docstring to remove PDF export mention

**Code Changes**:
- `src/dashboard/app.py`: Removed `generate_pdf_report()` function, all PDF export UI components, and PDF session state management

**Impact**: Dashboard codebase simplified by removing unused PDF export functionality. CSV export remains available for data download. No functional impact on core dashboard features - all visualization and analysis capabilities remain intact.

### 18.23 Objectifs Tab Overhaul: Production-Year Based with Carryover Visibility & 11-Month Accounting (January 2026)

**Major Enhancement**: Complete refactoring of Objectifs tab to be production-year based with comprehensive carryover tracking, 11-month accounting period system, and fixed navigation stability.

**Key Features**:

1. **Navigation Stability Fix**:
   - Replaced `st.tabs` with persisted navigation using `st.radio` + `st.session_state` + `st.query_params`
   - Eliminates tab reset bug where month selector interactions kicked users back to Vue Globale
   - Navigation state persists across reruns and page refreshes via URL query parameters

2. **Production-Year Based Objectives**:
   - Changed data loading from `load_year_data()` to `load_aggregated_production_data()` for production-year aggregation
   - Objectives now measure "how much production in year Y is secured" rather than "how much was signed in year Y"
   - Includes deals signed in previous years (carryover) that produce in the target year
   - Example: Signatures in 2025 producing in Q1 2026 now count toward 2026 Q1 objectives

3. **11-Month Accounting Period System**:
   - Implemented accounting period model: July+August merged into single "Juil+Août" period (11 periods total)
   - Updated `generate_11_month_distribution()`: July = annual/11 (not doubled), August = 0
   - Objectifs uses 11-period selector instead of 12-month selector
   - Vue Globale averages count unique accounting periods (July+August = 1 period)
   - Vue Mensuelle keeps real month selector (unchanged as requested)

4. **Carryover Visibility Everywhere**:
   - All Objectifs tables (Période, Trimestre, Année) display realized amounts with format: `TOTAL€ (dont PREV€ années précéd.)`
   - Carryover amount computed from rows with `signed_year < selected_year`
   - Added expandable "Répartition par année de signature" section showing breakdown by signing year

5. **Carryover Distribution for Monthly Objectives**:
   - Previous-year production-quarter amounts distributed evenly across quarter months
   - Example: 2025 signature with `Montant Total Q1_2026 = 90k` contributes 30k to Jan, 30k to Feb, 30k to Mar
   - For Q3 (Jul/Aug/Sep): Juil+Août period receives 2/3 of Q3 carryover, Sep receives 1/3
   - Enables accurate monthly objective tracking including carryover attribution

**Code Changes**:
- `src/dashboard/app.py`:
  - Replaced `st.tabs` navigation with persisted `st.radio` control
  - Added production-year helper functions: `calculate_realized_by_production_year()`, `calculate_realized_by_production_quarter()`, `calculate_production_period_with_carryover_distribution()`
  - Added carryover formatting helper: `_format_realized_with_carryover()`, `calculate_production_amount_with_carryover()`
  - Refactored entire Objectifs tab section to use production-year aggregation
  - Updated Vue Globale monthly average calculation to use `count_unique_accounting_periods()`
- `src/processing/objectives.py`:
  - Fixed `generate_11_month_distribution()` to implement July+August concatenation (July not doubled, August = 0)
  - Added accounting period helpers: `get_accounting_period_for_month()`, `get_accounting_period_label()`, `get_months_for_accounting_period()`, `count_unique_accounting_periods()`
  - Added `ACCOUNTING_PERIODS` constant and `ACCOUNTING_PERIOD_MONTH_MAP` mapping
- `tests/test_accounting_periods.py` (new): 8 tests for 11-month distribution and period mapping
- `tests/test_objectifs_carryover_distribution.py` (new): Tests for carryover distribution logic

**Technical Details**:
- Production-year aggregation checks `production_year-2..production_year` and filters on `Montant Total {production_year} > 0`
- Adds `signed_year` column to track origin year of each deal
- Quarter objectives use production-quarter columns (`Montant Total Q{1-4}_{year}`)
- Year objectives use production-year columns (`Montant Total {year}`)
- Period objectives combine signature-period filtering with production-year amounts

**Impact**: Objectifs tab now provides accurate production-year forecasting with full carryover visibility. Navigation is stable (no more tab resets). 11-month accounting is consistently applied across objectives and averages. Users can see exactly how much of each production year's progress comes from previous-year signings, enabling better planning and resource allocation.

### 18.24 Notion Property Preservation & TRAVAUX People Properties Split (January 2026)

**Major Enhancements**: Enhanced Notion sync to preserve user-edited properties and implemented Commercial/Chef de projet People properties split for TRAVAUX projection.

**Key Features**:

1. **Notion Property Preservation**:
   - All three Notion databases now preserve user-edited properties during pipeline updates
   - **TRAVAUX Projection DB**: Preserves `Commentaire Mathilde` and `Next Steps Commercial` (meeting notes)
   - **Weird Proposals & Follow-up DBs**: Preserves `Pris en charge` tickbox (meeting tracking)
   - Properties are excluded from update dictionary before API calls, ensuring user comments and checkboxes remain untouched
   - Critical for bi-monthly runs: preserves meeting notes and action items between pipeline executions

2. **TRAVAUX Commercial/Chef de projet Split**:
   - Replaced single `Commercial` (rich_text) property with two People properties:
     - `Commercial` (People): Assignees classified as commercials
     - `Chef de projet` (People): All other assignees (project managers)
   - **Commercial Classification**: Uses `VIP_COMMERCIALS` + `alienor` + `luana` to identify commercial assignees
   - **Flexible Parsing**: Handles whitespace-separated format (e.g., `"anne-valerie manon.navarro"`) and defensively handles commas/semicolons
   - **Normalized Matching**: Uses same normalization logic as `NotionUserMapper` (removes dots/hyphens) for flexible identifier matching
   - **Notion User Mapping**: Reuses existing `NotionUserMapper` to map Furious identifiers to Notion user IDs
   - Deduplicates user IDs if same person appears multiple times

3. **Dashboard Warning Removal**:
   - Removed yellow warning message "No data loaded from any sheets" from Objectifs tab
   - Cleaner UI when no data is available (function still returns empty DataFrame)

**Code Changes**:
- `src/integrations/notion_travaux_sync.py`:
  - Added `_parse_assigned_to()` method for robust identifier parsing
  - Added `_normalize_identifier()` helper matching `NotionUserMapper` normalization
  - Added `_classify_assignees()` method to split commercials vs chefs de projet
  - Added `_build_people_property()` method for Notion People property building
  - Updated `_build_page_properties()` to build both Commercial and Chef de projet People properties
  - Updated `sync_proposals()` to preserve `Commentaire Mathilde` and `Next Steps Commercial` on updates
  - Added `commercials_set` initialization: `VIP_COMMERCIALS | {'alienor', 'luana'}`
  - Added debug logging for classification troubleshooting
- `src/integrations/notion_alerts_sync.py`:
  - Updated `sync_weird_proposals()` and `sync_followup_alerts()` to preserve `Pris en charge` tickbox on updates
- `src/dashboard/app.py`:
  - Removed `st.warning()` call for empty data in `load_year_data()` function

**Technical Details**:
- Commercial classification uses exact match first, then normalized match (handles variations like `vincent.delavarende` vs `vincentdelavarende`)
- People properties always set (even if empty) to allow clearing when assignees change
- Schema-aware: only sets properties if they exist in database schema
- User mapping gracefully skips unmapped identifiers (logs warning for debugging)
- All property preservation uses `properties.pop(key, None)` pattern before update calls

**Impact**: Notion databases now preserve all user-edited meeting notes and tracking checkboxes across bi-monthly pipeline runs. TRAVAUX projection provides clear separation between commercial and project management responsibilities with proper People property assignments. Users can safely add comments and checkboxes in Notion without fear of them being overwritten by automated syncs.

### 18.25 Objectives Email Alignment with Dashboard (January 2026)

**Major Enhancement**: Aligned objectives management email (sent to Guillaume on each pipeline run) with dashboard Objectifs tab logic for consistency and accuracy.

**Key Features**:

1. **Production-Year Based Calculations**:
   - Email now uses same production-year aggregation as dashboard
   - Loads data across `production_year-2..production_year` to include carryover
   - Tables labeled "Objectifs {Envoyé|Signé} - Production {year}" (not signature-year based)

2. **Carryover Display**:
   - All "Réalisé" amounts formatted as `TOTAL€ (dont PREV€ années précéd.)`
   - Carryover computed from rows with `signed_year < production_year`
   - Enables clear visibility of previous-year contributions to current production objectives

3. **11-Month Accounting Periods**:
   - "Month" section replaced with "Période" using accounting period labels (e.g., "Juil+Août")
   - Period selection uses `get_accounting_period_for_month()` and `get_accounting_period_label()`
   - Consistent with dashboard 11-month accounting logic

4. **Monthly Carryover Distribution**:
   - Previous-year production-quarter amounts distributed evenly across quarter months
   - Example: 2025 signature with `Montant Total Q1_2026 = 100k` contributes 33.3k to each month (Jan/Feb/Mar)
   - For Q3: Juil+Août period receives 2/3 of Q3 carryover, Sep receives 1/3
   - Matches dashboard distribution logic exactly

5. **Email Order & Link Updates**:
   - Changed order: **Signé** section first, then **Envoyé** (as requested)
   - Replaced Notion link with dashboard link
   - Added configurable `DASHBOARD_URL` constant in `EmailSender` class (line 40)
   - Link text changed to "📊 Tableau de Bord" / "🔗 Accéder au Dashboard"

6. **Typologie Tables**:
   - Added Typologie breakdown tables (matching dashboard structure)
   - Shows objectives and realized amounts by typologie with carryover

**Code Changes**:
- `src/integrations/email_sender.py`:
  - Added `DASHBOARD_URL` class constant (line 40) with TODO comment for public URL update
  - Added `_load_aggregated_production_data_for_objectives()` method to load cross-year production data
  - Added `_production_period_with_carryover_distribution()` for period/month calculations with carryover
  - Added `_production_amount_with_carryover()` for quarter/year calculations with carryover
  - Added `_format_realized_with_carryover()` helper for display formatting
  - Replaced `_calculate_realized_by_month()`, `_calculate_realized_for_quarter()`, `_calculate_realized_for_year()` with production-year based methods
  - Updated `_generate_objectives_management_html()` to use production-year aggregation
  - Changed `metric_specs` order: Signé first, then Envoyé
  - Updated HTML template: replaced Notion link with dashboard link using `{EmailSender.DASHBOARD_URL}`
- `tests/test_objectives_email_html.py` (new):
  - Added test to verify HTML contains production-year labels, accounting period labels, and carryover formatting

**Technical Details**:
- Email aggregation checks `production_year-2..production_year` and filters on `Montant Total {production_year} > 0`
- Adds `signed_year` column to track origin year of each deal
- Uses same helper functions from `src.processing.objectives` as dashboard (accounting periods, carryover distribution)
- Dashboard URL placeholder: `"https://dashboard.merciraymond.com"` - update when dashboard is publicly accessible

**Impact**: Objectives email now provides Guillaume with the exact same data and calculations as the dashboard Objectifs tab, ensuring consistency across all reporting channels. Production-year forecasting with full carryover visibility enables accurate planning. Email recipients can click through to dashboard for detailed analysis. Order change (Signé first) prioritizes signed revenue visibility.

---

**Document Version**: 1.18
**Last Updated**: January 2026
**Maintained By**: Development Team
**Project**: Myrium - Commercial Tracking & BI System
