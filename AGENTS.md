# Repository Guidelines

## Project Overview

IDX Financial Report Scraper — fetches quarterly financial reports (`rdf`) and annual reports (`rda`) from the Indonesia Stock Exchange public API and downloads PDF, XLSX, and ZIP (XBRL) attachments.

Single-file Python CLI with resume capability. No framework, no database.

## Architecture & Data Flow

```
CLI args (year, periode, emiten_type)
  → _get_session()        # requests.Session + WAF cookie + retry adapter
  → fetch_all_results()   # paginate through IDX API, one (periode, report_type) at a time
  → main() loop           # iterate companies, filter attachments, download
  → save_status()         # incremental CSV writes for resume
```

**`all` mode**: expands to `[("tw1","rdf"), ("tw2","rdf"), ("tw3","rdf"), ("","rda")]` — four sequential fetch+download jobs in one run.

**API endpoint**: `GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport`

**Pagination**: `indexFrom` is a **1-based page number** (not a result offset). `pageSize` (default 100). Total pages computed from `ResultCount`. Cookie refreshed every 10 pages and on 403 retry.

**Resume key**: `(code, report_type, periode, year)` — a 4-tuple. `idx_companies.csv` columns: `code`, `company`, `report_type`, `periode`, `year`, `status`. CSV written incrementally after each successful company. `load_done()` tolerates old CSVs missing `periode`/`year` columns (backward compat).

**Anti-bot measures**:
- Browser-like headers (`User-Agent`, `Referer`, `Origin`)
- WAF cookie obtained by visiting the referer page first (`stream=True`, short timeout)
- Cookie refreshed mid-run (every 10 pages, and on 403 retry)
- Jittered delays between pages, companies, and attachments (configurable via `.env`)

## Key Directories

```
.
├── main.py              # Everything — config, API client, downloader, CLI
├── requirements.txt     # pandas, requests, tqdm
├── .env.example         # Documented config overrides
├── idx_companies.csv    # Resume file (gitignored)
├── download/            # Output: {code}/{year}/{periode}/{report_type}/{filename} (gitignored)
└── README.md
```

## Development Commands

```bash
pip install -r requirements.txt
python main.py 2026 all s       # All quarters + annual for 2026
python main.py 2026 tw1 s       # Just Q1 2026 (rdf + rda)
python main.py 2025 all s       # 2025 — tracked independently from 2026
python main.py 2026 tahunan s   # Annual reports only
```

No build, lint, or test tooling — single-file script.

`.env` (optional): copy `.env.example` to `.env` and uncomment overrides for `PAGE_SIZE`, delay ranges, `DOWNLOAD_DIR`.

## Code Conventions & Common Patterns

### Single-file structure

Everything lives in `main.py` with clear section separators:

```
# Configuration (defaults; override via .env)
# Helpers (_jitter, _refresh_cookie, _get_session, _load_env)
# API fetching (build_params, fetch_page, fetch_all_results)
# Filtering & downloading (is_report_file, download_file)
# Resume / progress tracking (save_status, load_done)
# Main
```

### Session reuse

One `requests.Session` for the entire run — cookie persistence, connection pooling, retry adapter with exponential backoff on `[429, 500, 502, 503, 504]`. Cookie refreshed every 10 API pages and on any 403 response.

### Jittered delays

`jitter(delay_range)` → `time.sleep(random.uniform(min, max))`. Three delay bands:
- `DELAY_PAGE` — between API page requests
- `DELAY_COMPANY` — between processing each company
- `DELAY_ATTACHMENT` — between each file download within a company

### .env loading

Pure stdlib parser — no `python-dotenv` dependency. Reads `KEY=VALUE` lines, strips quotes, skips comments and blank lines. Applied at module level via `_apply_env()` so imports see final values.

### CSV format

`idx_companies.csv`: `code, company, report_type, periode, year, status`.

`code` = emiten code (key), `company` = real name from API (`NamaEmiten`). `load_done()` handles backward compat: tolerates missing `periode`/`year` columns and old `company`-as-code format.

### Error handling

Individual download failures log with `tqdm.write()` but don't crash the run. API errors propagate (`raise_for_status()`). WAF cookie pre-fetch failure is silently swallowed. 403 on API calls triggers a cookie refresh and retry.

### Filtering logic

- `rda` (annual report): download **all** attachments
- `rdf` (quarterly financial): download if filename matches `LK_KEYWORDS` (Indonesian financial report terms) **or** ends with `.zip`, `.xls`, or `.xlsx`

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

## Git Conventions

**Conventional Commits** — every commit follows `<type>: <description>`:

| Type | Use for |
|---|---|
| `feat` | New features (e.g., `all` mode, year column, ZIP support) |
| `fix` | Bug fixes (e.g., `indexFrom` pagination fix) |
| `docs` | README, AGENTS.md, docstrings, comments |
| `chore` | Config, dependencies, gitignore, .env, cleanup |
| `refactor` | Code restructuring without behavior change |

**Rules**:
- Commit files **one by one** — each commit touches one logical group
- Never mix `feat` and `chore` in the same commit
- Push to `main` after all commits are ready
- Description is lowercase, imperative mood, no period at the end

Example history:
```
fix: use page number for indexFrom instead of result offset
feat: add 'all' mode to fetch tw1+tw2+tw3 (rdf) + annual (rda) in one run
feat: include periode in resume key, download path, and CSV; add XLS/XLSX support
feat: add year column to resume CSV so multiple years can be tracked independently
```

## Testing & QA

No test framework. Manual QA:
1. Run with a limited page size (`PAGE_SIZE=2` in `.env` or source)
2. Verify `idx_companies.csv` has columns: `code`, `company`, `report_type`, `periode`, `year`, `status`
3. Verify `download/{code}/{year}/{periode}/{report_type}/` structure
4. Verify ZIP and XLS/XLSX files are downloaded alongside PDFs
5. Kill mid-run, re-run — confirm resume skips already-done `(code, report_type, periode, year)` tuples
6. Run a different `year` — confirm it starts fresh without blocking the previous year