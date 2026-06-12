# IDX Financial Report Scraper

Downloads financial reports (quarterly) and annual reports from the Indonesia Stock Exchange (IDX) website.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Download Q1 2026 financial reports (stock issuers)
python main.py 2026 tw1 s

# Download annual reports
python main.py 2026 tahunan s
```

## Usage

```
python main.py <year> <periode> [emiten_type]
```

| Arg | Values | Description |
|---|---|---|
| `year` | e.g. `2026` | Report year |
| `periode` | `tw1`, `tw2`, `tw3`, `tahunan` | Reporting period |
| `emiten_type` | `s` (default), `o` | `s` = saham (stocks), `o` = obligasi (bonds) |

## What It Downloads

- **rdf** (financial reports): PDF/XLSX matching financial report keywords, plus ZIP files (XBRL instance data)
- **rda** (annual reports): all attachments

Files are saved to `download/` as `{company_code} - {filename}`.

## Resume

Progress is tracked in `status perusahaan.csv`. Interrupted runs skip already-downloaded `(company, report_type)` pairs — just re-run the same command.

## API

The scraper calls IDX's internal API:
```
GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport
```

Parameters: `indexFrom`, `pageSize`, `year`, `reportType` (`rdf`|`rda`), `EmitenType`, `periode`, `kodeEmiten`, `SortColumn`, `SortOrder`.

The site uses a WAF — the scraper obtains the required cookie by visiting the referer page first, then uses jittered delays between requests to avoid triggering rate limits.