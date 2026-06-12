# IDX Financial Report Scraper

Downloads quarterly financial reports (`rdf`) and annual reports (`rda`) from the Indonesia Stock Exchange (IDX) website.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Download all reports for 2026 (Q1+Q2+Q3 financial + annual)
python main.py 2026 all s

# Download just Q1 2026 financial + annual reports
python main.py 2026 tw1 s

# Download 2025 too — resume CSV tracks years independently
python main.py 2025 all s
```

## Usage

```
python main.py <year> <periode> [emiten_type]
```

| Arg | Values | Description |
|---|---|---|
| `year` | e.g. `2026`, `2025` | Report year |
| `periode` | `tw1`, `tw2`, `tw3`, `tahunan`, `all` | `all` = tw1+tw2+tw3 (rdf) + annual (rda) |
| `emiten_type` | `s` (default), `o` | `s` = saham (stocks), `o` = obligasi (bonds) |

## Configuration

Copy `.env.example` to `.env` and uncomment overrides:

| Variable | Default | Description |
|---|---|---|
| `PAGE_SIZE` | `100` | Results per API page |
| `DOWNLOAD_TIMEOUT` | `120` | Download timeout (seconds) |
| `DOWNLOAD_DIR` | `download` | Output directory |
| `DELAY_PAGE_MIN` / `MAX` | `1.0` / `3.0` | Jitter between API pages |
| `DELAY_COMPANY_MIN` / `MAX` | `0.5` / `2.0` | Jitter between companies |
| `DELAY_ATTACHMENT_MIN` / `MAX` | `0.2` / `0.8` | Jitter between file downloads |

## What It Downloads

- **rdf** (quarterly financial reports): PDF/XLSX matching financial report keywords, plus ZIP (XBRL) and XLS/XLSX (spreadsheet)
- **rda** (annual reports): all attachments

Files are saved to:

```
download/
  {code}/
    {year}/
      {periode}/
        rdf/
          file.pdf
          instance.zip
        rda/
          annual-report.pdf
```

## Resume

Progress is tracked in `idx_companies.csv`:

| Column | Example |
|---|---|
| `code` | `AADI` |
| `company` | `PT Adaro Andalan Indonesia Tbk` |
| `report_type` | `rdf` or `rda` |
| `periode` | `tw1`, `tw2`, `tw3`, or empty (for rda) |
| `year` | `2026` |
| `status` | `True` / `False` |

Interrupted runs skip already-downloaded `(code, report_type, periode, year)` tuples — just re-run the same command. Different years are tracked independently.

## API

```
GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport
```

Parameters: `indexFrom` (page number, 1-based), `pageSize`, `year`, `reportType` (`rdf`|`rda`), `EmitenType`, `periode`, `kodeEmiten`, `SortColumn`, `SortOrder`.

The site uses a WAF — the scraper obtains the required cookie by visiting the referer page first, then uses jittered delays between requests to avoid triggering rate limits.

## See Also

`AGENTS.md` — full architecture, code conventions, and git rules for contributors.