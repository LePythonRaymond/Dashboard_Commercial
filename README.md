# Myrium - Commercial Tracking System

Automated commercial tracking and BI dashboard for Merci Raymond.

## Features

- **Data Extraction**: Fetches proposals from Furious CRM API with automatic pagination
- **Revenue Spreading**: Complex business logic for MAINTENANCE, TRAVAUX, and CONCEPTION
- **Google Sheets Integration**: Writes monthly snapshots and summaries
- **Email Alerts**: Sends notifications for data quality issues and commercial follow-ups
- **Notion Gantt**: Syncs TRAVAUX projects for timeline visualization
- **BI Dashboard**: Streamlit-based dashboard with Plotly charts

## Quick Start

### 1. Install Dependencies

```bash
cd myrium
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `env.example` to `.env` and fill in your credentials:

```bash
cp env.example .env
```

Required variables:
- `FURIOUS_USERNAME` / `FURIOUS_PASSWORD`: Furious API credentials
- `GOOGLE_SERVICE_ACCOUNT_PATH`: Path to Google Service Account JSON
- `SPREADSHEET_{TYPE}_{YEAR}`: Google Sheets spreadsheet IDs by type and year
  - Types: `ETAT` (État au), `ENVOYE` (Envoyé), `SIGNE` (Signé)
  - Example: `SPREADSHEET_ETAT_2025`, `SPREADSHEET_ENVOYE_2025`, `SPREADSHEET_SIGNE_2025`
  - Add entries for each year you need (2025, 2026, 2027, etc.)
- `SMTP_USER` / `SMTP_PASSWORD`: Gmail credentials (use App Password)
- `NOTION_API_KEY` / `NOTION_DATABASE_ID`: Notion integration credentials

**Note**: Years are calculated dynamically from the current date. The system automatically:
- Uses current year for views and spreadsheet selection
- Calculates revenue projections for current year, Y+1, and Y+2
- Creates/updates spreadsheets based on the current year

### 3. Set Up Google Service Account

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable Google Sheets API and Google Drive API
3. Create a Service Account and download the JSON key
4. Save the JSON to `config/credentials/service_account.json`
5. Share your target spreadsheet with the Service Account email

### 4. Run the Pipeline

```bash
# Full pipeline
python scripts/run_pipeline.py

# Dry run (no external writes)
python scripts/run_pipeline.py --dry-run

# Test mode (redirects all emails to taddeo.carpinelli@merciraymond.fr)
python scripts/run_pipeline.py --test

# Save results to JSON
python scripts/run_pipeline.py --output results.json

# Combine options
python scripts/run_pipeline.py --test --output test_results.json
```

### 5. Launch Dashboard

```bash
python scripts/run_dashboard.py

# Custom port
python scripts/run_dashboard.py --port 8080
```

## Scheduling (Cron)

Add to crontab for bi-monthly execution (1st and 15th at 8 AM):

```bash
crontab -e
```

Add this line:
```
0 8 1,15 * * cd /path/to/myrium && /path/to/venv/bin/python scripts/run_pipeline.py >> logs/cron.log 2>&1
```

## Project Structure

```
myrium/
├── config/
│   ├── settings.py       # Configuration and constants
│   └── credentials/      # Service account JSON (gitignored)
├── src/
│   ├── api/              # Furious API clients
│   ├── processing/       # Data cleaning, revenue engine, views, alerts
│   ├── integrations/     # Google Sheets, Email, Notion
│   └── dashboard/        # Streamlit app
├── scripts/
│   ├── run_pipeline.py   # Main orchestrator
│   └── run_dashboard.py  # Dashboard launcher
├── logs/                 # Pipeline logs
└── tests/               # Test suite
```

## Business Logic

### Business Unit Assignment

1. **TS Rule (Priority)**: If title contains "TS", assign to TRAVAUX
2. **Keyword Mapping**:
   - MAINTENANCE/ENTRETIEN → MAINTENANCE
   - TRAVAUX/CHANTIER → TRAVAUX
   - CONCEPTION/ETUDE → CONCEPTION

### Revenue Spreading

| BU | Rule |
|---|---|
| MAINTENANCE | Evenly spread over project duration |
| TRAVAUX (< 1 month) | 100% on project start |
| TRAVAUX (> 1 month) | Evenly spread over duration |
| CONCEPTION (< 15k€) | 1/3 per month for 3 months |
| CONCEPTION (15k€ - 30k€) | 60% over 6mo, pause 6mo, 40% over 6mo |
| CONCEPTION (> 30k€) | 40% over 12mo, pause 6mo, 60% over 12mo |

### Views Generated

1. **État au {DD-MM-YYYY}**: All waiting proposals (snapshot)
2. **Envoyé {Month} {Year}**: Proposals created this month, still waiting
3. **Signé {Month} {Year}**: Won proposals for current month

### Alerts

- **Weird Proposals**: Amount < 1k, missing dates, invalid date ranges, 0% probability
- **Commercial Follow-up**: Projects needing attention within date window

## Development

### Running Tests

```bash
pytest tests/
```

### Code Style

```bash
# Format
black src/ scripts/

# Lint
flake8 src/ scripts/
```

## License

Proprietary - Merci Raymond
