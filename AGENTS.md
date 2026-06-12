# Repository Guidelines

## Project Overview

IDX Financial Report Scraper — fetches quarterly financial reports (`rdf`) and annual reports (`rda`) from the Indonesia Stock Exchange public API and downloads PDF, XLSX, and ZIP (XBRL) attachments.

Single-file Python CLI with resume capability. No framework, no database.

## Architecture & Data Flow

```
CLI args (year, periode, emiten_type)
  → _get_session()        # requests.Session + WAF cookie + retry adapter
  → fetch_all_results()   # paginate through IDX API, one report_type at a time
  → main() loop           # iterate companies, filter attachments, download
  → save_status()         # incremental CSV writes for resume
```

**API endpoint**: `GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport`

**Pagination**: `indexFrom` (1-based), `pageSize` (default 100). Total pages computed from `ResultCount`.

**Resume**: `idx_companies.csv` tracks `(code, report_type, status)`. On re-run, `load_done()` builds a set of completed pairs; those companies are skipped. CSV is written incrementally after each successful company.

**Anti-bot measures**:
- Browser-like headers (`User-Agent`, `Referer`, `Origin`)
- WAF cookie obtained by visiting the referer page first (`stream=True`, short timeout)
- Jittered delays between pages, companies, and attachments (configurable via `.env`)

## Key Directories

```
.
├── main.py              # Everything — config, API client, downloader, CLI
├── requirements.txt     # pandas, requests, tqdm
├── .env.example         # Documented config overrides
├── idx_companies.csv    # Resume file (gitignored)
├── download/            # Output: {code}/{year}/{report_type}/{filename} (gitignored)
└── README.md
```

## Development Commands

```bash
pip install -r requirements.txt
python main.py 2026 tw1 s          # Q1 2026, stock issuers (both rdf + rda)
python main.py 2026 tahunan s      # Annual reports only
```

No build, lint, or test tooling — single-file script.

`.env` (optional): copy `.env.example` to `.env` and uncomment overrides for `PAGE_SIZE`, delay ranges, `DOWNLOAD_DIR`.

## Code Conventions & Common Patterns

### Single-file structure

Everything lives in `main.py` with clear section separators:

```
# Configuration (defaults; override via .env)
# Helpers (_jitter, _get_session, _load_env)
# API fetching (build_params, fetch_page, fetch_all_results)
# Filtering & downloading (is_report_file, download_file)
# Resume / progress tracking (save_status, load_done)
# Main
```

### Session reuse

One `requests.Session` for the entire run — cookie persistence, connection pooling, retry adapter with exponential backoff on `[429, 500, 502, 503, 504]`.

### Jittered delays

`jitter(delay_range)` → `time.sleep(random.uniform(min, max))`. Three delay bands:
- `DELAY_PAGE` — between API page requests
- `DELAY_COMPANY` — between processing each company
- `DELAY_ATTACHMENT` — between each file download within a company

### .env loading

Pure stdlib parser — no `python-dotenv` dependency. Reads `KEY=VALUE` lines, strips quotes, skips comments and blank lines. Applied at module level via `_apply_env()` so imports see final values.

### CSV format

`idx_companies.csv`: `code,company,report_type,status`. `code` = emiten code (key), `company` = real name from API (`NamaEmiten`). `_load_done()` handles both old (`company`) and new (`code`) column names for backward compat.

### Error handling

Individual download failures log with `tqdm.write()` but don't crash the run. API errors propagate (`raise_for_status()`). WAF cookie pre-fetch failure is silently swallowed (API might still work).

### Filtering logic

- `rda` (annual report): download **all** attachments
- `rdf` (quarterly financial): download if filename matches `LK_KEYWORDS` (Indonesian financial report terms) **or** ends with `.zip` (XBRL instance data)

## Important Files

| File | Role |
|---|---|
| `main.py` | Entire application |
| `requirements.txt` | `pandas`, `requests`, `tqdm` |
| `.env.example` | Documented config template |
| `idx_companies.csv` | Runtime resume state (gitignored) |

## Runtime/Tooling Preferences

- **Runtime**: Python 3.10+
- **Package manager**: pip
- **No virtual env enforced** — use at your discretion
- **No type checking**, **no linting**, **no tests** — single-use scraper script

## Testing & QA

No test framework. Manual QA:
1. Run with a limited page size (`PAGE_SIZE=2` in `.env` or source)
2. Verify `idx_companies.csv` has correct `code` + `company` columns
3. Verify `download/{code}/{year}/{report_type}/` structure
4. Verify ZIP files (`instance.zip`, `inlineXBRL.zip`) are downloaded alongside PDFs
5. Kill mid-run, re-run — confirm resume skips already-done companies