"""
IDX Financial Report Scraper

Fetches quarterly financial reports (rdf) and annual reports (rda) from the
Indonesia Stock Exchange API and downloads PDF, XLSX, and ZIP attachments.

Usage:
    python main.py <year> <periode> [emiten_type]

    year          : report year (e.g. 2026)
    periode       : tw1 | tw2 | tw3 | tahunan | all
                    'all' fetches tw1+tw2+tw3 (rdf) + annual (rda)
    emiten_type   : s (saham/stock, default) | o (obligasi/bonds)

API endpoint:
    GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport
    Params: indexFrom (page number), pageSize, year, reportType (rdf|rda),
            EmitenType, periode, kodeEmiten, SortColumn, SortOrder

Resume: tracks progress in 'idx_companies.csv' — interrupted runs skip
already-downloaded (code, report_type) pairs.
"""
import os
import random
import sys
import time
from urllib.parse import urljoin

import pandas as pd
import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration (defaults; override via .env)
# ---------------------------------------------------------------------------

IDX_BASE_URL = "https://www.idx.co.id"
IDX_API_URL = f"{IDX_BASE_URL}/primary/ListedCompany/GetFinancialReport"

PAGE_SIZE = 100

# Human-like jitter ranges (min, max) in seconds
DELAY_PAGE = (1.0, 3.0)        # between API page requests
DELAY_COMPANY = (0.5, 2.0)     # between processing companies
DELAY_ATTACHMENT = (0.2, 0.8)  # between downloading attachments

DOWNLOAD_TIMEOUT = 120

STATUS_CSV = "idx_companies.csv"
DOWNLOAD_DIR = "download"

REPORT_TYPES = ["rdf", "rda"]


def _load_env():
    """Load overrides from .env file (no external deps)."""
    env_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), ".env")
    if not os.path.exists(env_path):
        return {}
    vals = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip("\"'")
    return vals


def _apply_env():
    """Apply .env overrides to module-level config."""
    global PAGE_SIZE, DELAY_PAGE, DELAY_COMPANY, DELAY_ATTACHMENT
    global DOWNLOAD_TIMEOUT, DOWNLOAD_DIR

    env = _load_env()

    if "PAGE_SIZE" in env:
        PAGE_SIZE = int(env["PAGE_SIZE"])
    if "DOWNLOAD_TIMEOUT" in env:
        DOWNLOAD_TIMEOUT = int(env["DOWNLOAD_TIMEOUT"])
    if "DOWNLOAD_DIR" in env:
        DOWNLOAD_DIR = env["DOWNLOAD_DIR"]

    def _parse_range(key, default):
        lo = float(env.get(f"{key}_MIN", default[0]))
        hi = float(env.get(f"{key}_MAX", default[1]))
        return (lo, hi)

    DELAY_PAGE = _parse_range("DELAY_PAGE", DELAY_PAGE)
    DELAY_COMPANY = _parse_range("DELAY_COMPANY", DELAY_COMPANY)
    DELAY_ATTACHMENT = _parse_range("DELAY_ATTACHMENT", DELAY_ATTACHMENT)


_apply_env()
# Browser-like headers to pass CloudFront/WAF
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Referer": "https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan",
    "Origin": "https://www.idx.co.id",
}

# Keywords in lowercase filenames that indicate a financial report (for rdf)
LK_KEYWORDS = [
    "lk ", " lk ", "_lk", "lk_", "_lk_",
    "laporan keuangan", "laporan_keuangan", "lap keu",
    "lk-", "-lk", "lap_keu",
    "3103", "31 maret", "31maret",
    "3006", "30 juni", "30juni",
    "3009", "30 september", "30september",
    "audit", "pt", "tbk",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def jitter(delay_range):
    """Sleep for a random duration within (min, max) range."""
    time.sleep(random.uniform(*delay_range))


def _refresh_cookie(session):
    """Re-visit the referer page to obtain a fresh WAF cookie."""
    try:
        session.get(
            "https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan",
            timeout=15,
            stream=True,
        )
    except requests.RequestException:
        pass


def _get_session():
    """Create a requests.Session with browser headers and WAF cookie."""
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
    )
    session.mount("https://", adapter)
    _refresh_cookie(session)
    return session


def build_params(year, periode, emiten_type, page, report_type):
    """Build query parameters for the IDX API.

    indexFrom is a 1-based page number, NOT a result offset.
    """
    return {
        "indexFrom": page,
        "pageSize": PAGE_SIZE,
        "year": year,
        "reportType": report_type,
        "EmitenType": emiten_type,
        "periode": periode if report_type == "rdf" else "",
        "kodeEmiten": "",
        "SortColumn": "KodeEmiten",
        "SortOrder": "asc",
    }

# ---------------------------------------------------------------------------
# API fetching
# ---------------------------------------------------------------------------


def fetch_page(session, year, periode, emiten_type, page, report_type):
    """Fetch a single page of results, refreshing cookie on 403."""
    params = build_params(year, periode, emiten_type, page, report_type)
    resp = session.get(IDX_API_URL, params=params, timeout=30)
    if resp.status_code == 403:
        _refresh_cookie(session)
        resp = session.get(IDX_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()
def fetch_all_results(session, year, periode, emiten_type, report_type):
    """Paginate through all results; return a flat list of company entries."""
    first_page = fetch_page(session, year, periode, emiten_type, 1, report_type)
    total = first_page["ResultCount"]
    if total == 0:
        return []
    all_results = list(first_page["Results"])

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pbar = tqdm(
        range(2, total_pages + 1),
        desc=f"  Pages {report_type}",
        initial=1,
        total=total_pages,
        unit="pg",
    )
    for page in pbar:
        jitter(DELAY_PAGE)
        data = fetch_page(session, year, periode, emiten_type, page, report_type)
        all_results.extend(data["Results"])
        pbar.set_postfix_str(f"{len(all_results)}/{total}")
        if page % 10 == 0:
            _refresh_cookie(session)

    return all_results
# ---------------------------------------------------------------------------
# Filtering & downloading
# ---------------------------------------------------------------------------


def is_report_file(filename, report_type):
    """Decide whether a file should be downloaded.

    rda (annual report) → download everything.
    rdf (quarterly financial) → match keywords (PDF) or extensions:
      .zip  → XBRL instance data
      .xls, .xlsx → spreadsheet
    """
    if report_type == "rda":
        return True
    lower = filename.lower()
    if lower.endswith((".zip", ".xls", ".xlsx")):
        return True
    return any(kw in lower for kw in LK_KEYWORDS)


def download_file(session, company_code, year, periode, report_type, file_path):
    """Download a file into download/{kode}/{year}/{periode}/{report_type}/{filename}."""
    cwd = os.path.dirname(os.path.realpath(__file__))
    filename = file_path.rsplit("/", 1)[-1]
    local_path = os.path.join(cwd, DOWNLOAD_DIR, company_code, year, periode, report_type, filename)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    full_url = urljoin(IDX_BASE_URL, file_path)
    resp = session.get(full_url, allow_redirects=True, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)

# ---------------------------------------------------------------------------
# Resume / progress tracking
# ---------------------------------------------------------------------------


def save_status(rows):
    """Persist download progress to CSV.

    Columns: code, company, report_type, periode, year, status.
    """
    df = pd.DataFrame.from_dict(rows)
    df.to_csv(STATUS_CSV, index=False)


def load_done():
    """Return set of (code, report_type, periode, year) already downloaded."""
    if not os.path.exists(STATUS_CSV):
        return set()
    df = pd.read_csv(STATUS_CSV)
    code_col = "code" if "code" in df.columns else "company"
    df = df[df["status"] == True]
    peri_col = df["periode"] if "periode" in df.columns else ""
    year_col = df["year"] if "year" in df.columns else ""
    return set(zip(df[code_col], df["report_type"], peri_col, year_col))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 3:
        print("Usage: python main.py <year> <periode> [emiten_type]")
        print("  periode    : tw1 | tw2 | tw3 | tahunan | all")
        print("  emiten_type: s (default) | o")
        sys.exit(1)

    year = sys.argv[1]
    periode = sys.argv[2]
    emiten_type = sys.argv[3] if len(sys.argv) > 3 else "s"

    # Build work list: (periode, report_type) pairs
    if periode == "all":
        work = [("tw1", "rdf"), ("tw2", "rdf"), ("tw3", "rdf"), ("", "rda")]
    else:
        work = []
        if periode in ("tw1", "tw2", "tw3"):
            work.append((periode, "rdf"))
        work.append(("" if periode == "tahunan" else periode, "rda"))

    print(f"Year: {year}  emiten_type: {emiten_type}")
    job_desc = ", ".join(f"{p or 'rda'}/{rt}" for p, rt in work)
    print(f"Jobs: {len(work)} ({job_desc})")
    session = _get_session()
    done = load_done()

    codes, names, types, peris, years, statuses = [], [], [], [], [], []
    for item in done:
        if len(item) == 4:
            c, rt, pr, yr = item
        elif len(item) == 3:
            c, rt, pr = item
            yr = ""
        else:
            c, rt = item
            pr, yr = "", ""
        codes.append(c)
        names.append("")
        types.append(rt)
        peris.append(pr)
        years.append(yr)
        statuses.append(True)

    for peri, rt in work:
        label = f"{peri}/{rt}" if peri else rt
        results = fetch_all_results(session, year, peri, emiten_type, rt)
        print(f"\n[{label}] Total companies: {len(results)}")

        pending = [
            (r, rt, peri, year)
            for r in results
            if (r["KodeEmiten"], rt, peri, year) not in done
        ]
        already = len(results) - len(pending)
        print(f"[{label}] Already done: {already}, remaining: {len(pending)}")

        pbar = tqdm(pending, desc=f"  Downloading {label}")
        for entry, rtype, pr, yr in pbar:
            jitter(DELAY_COMPANY)
            code = entry["KodeEmiten"]
            name = entry["NamaEmiten"]
            pbar.set_postfix_str(code)
            ok = False
            codes.append(code)
            names.append(name)
            types.append(rtype)
            peris.append(pr)
            years.append(yr)

            for attachment in entry.get("Attachments", []):
                fname = attachment["File_Name"]
                if is_report_file(fname, rtype):
                    try:
                        jitter(DELAY_ATTACHMENT)
                        download_file(session, code, year, pr, rtype, attachment["File_Path"])
                        ok = True
                    except requests.RequestException as e:
                        tqdm.write(f"  [{code}] Failed: {fname} — {e}")

            statuses.append(ok)

            if ok:
                save_status({
                    "code": codes,
                    "company": names,
                    "report_type": types,
                    "periode": peris,
                    "year": years,
                    "status": statuses,
                })


if __name__ == "__main__":
    main()