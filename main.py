"""
IDX Financial Report Scraper

Fetches financial reports (rdf) and annual reports (rda) from the Indonesia
Stock Exchange (IDX) API and downloads matching PDF, XLSX, and ZIP attachments.

Usage:
    python main.py <year> <periode> [emiten_type]

    year          : report year (e.g. 2026)
    periode       : tw1 | tw2 | tw3 | tahunan
    emiten_type   : s (saham/stock, default) | o (obligasi/bonds)

API endpoint:
    GET https://www.idx.co.id/primary/ListedCompany/GetFinancialReport
    Params: indexFrom, pageSize, year, reportType (rdf|rda), EmitenType,
            periode, kodeEmiten, SortColumn, SortOrder

Resume: tracks progress in 'status perusahaan.csv' — interrupted runs skip
already-downloaded (company, report_type) pairs.
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


def _get_session():
    """Create a requests.Session with browser headers and WAF cookie.

    The IDX site uses a WAF that sets a cookie on first visit. We hit the
    referer page first to obtain it before calling the API.
    """
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

    try:
        session.get(
            "https://www.idx.co.id/id/perusahaan-tercatat/laporan-keuangan-dan-tahunan",
            timeout=15,
            stream=True,
        )
    except requests.RequestException:
        pass

    return session


def build_params(year, periode, emiten_type, index_from, report_type):
    """Build query parameters for the IDX API.

    For rda (annual report), periode is sent empty since the API ignores it.
    """
    return {
        "indexFrom": index_from,
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


def fetch_page(session, year, periode, emiten_type, index_from, report_type):
    """Fetch a single page of results from the IDX API."""
    params = build_params(year, periode, emiten_type, index_from, report_type)
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
    for page in range(2, total_pages + 1):
        jitter(DELAY_PAGE)
        index_from = (page - 1) * PAGE_SIZE + 1
        data = fetch_page(session, year, periode, emiten_type, index_from, report_type)
        all_results.extend(data["Results"])

    return all_results

# ---------------------------------------------------------------------------
# Filtering & downloading
# ---------------------------------------------------------------------------


def is_report_file(filename, report_type):
    """Decide whether a file should be downloaded.

    rda (annual report) → download everything.
    rdf (quarterly financial) → match keywords (PDF/XLSX) or .zip (XBRL).
    """
    if report_type == "rda":
        return True
    lower = filename.lower()
    if lower.endswith(".zip"):
        return True
    return any(kw in lower for kw in LK_KEYWORDS)


def download_file(session, company_code, year, report_type, file_path):
    """Download a file into download/{kode}/{year}/{report_type}/{filename}."""
    cwd = os.path.dirname(os.path.realpath(__file__))
    filename = file_path.rsplit("/", 1)[-1]
    local_path = os.path.join(cwd, DOWNLOAD_DIR, company_code, year, report_type, filename)

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

    Columns: code (emiten code), company (real name), report_type, status.
    """
    df = pd.DataFrame.from_dict(rows)
    df.to_csv(STATUS_CSV, index=False)


def load_done():
    """Return set of (code, report_type) pairs already downloaded."""
    if not os.path.exists(STATUS_CSV):
        return set()
    df = pd.read_csv(STATUS_CSV)
    # Support both old (company=code) and new (code+company) formats
    code_col = "code" if "code" in df.columns else "company"
    df = df[df["status"] == True]
    return set(zip(df[code_col], df["report_type"]))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 3:
        print("Usage: python main.py <year> <periode> [emiten_type]")
        print("  periode    : tw1 | tw2 | tw3 | tahunan")
        print("  emiten_type: s (default) | o")
        sys.exit(1)

    year = sys.argv[1]
    periode = sys.argv[2]
    emiten_type = sys.argv[3] if len(sys.argv) > 3 else "s"

    print(f"Fetching: year={year}  periode={periode}  emiten_type={emiten_type}")

    session = _get_session()
    done = load_done()
    # Rebuild status lists from done set for incremental CSV writes
    codes, names, types, statuses = [], [], [], []
    for c, rt in done:
        codes.append(c)
        names.append("")  # real name not known for resumed entries
        types.append(rt)
        statuses.append(True)

    for report_type in REPORT_TYPES:
        results = fetch_all_results(session, year, periode, emiten_type, report_type)
        print(f"\n[{report_type}] Total companies: {len(results)}")

        pending = [
            (r, report_type)
            for r in results
            if (r["KodeEmiten"], report_type) not in done
        ]
        already = len(results) - len(pending)
        print(f"[{report_type}] Already done: {already}, remaining: {len(pending)}")

        pbar = tqdm(pending, desc=f"Downloading {report_type}")
        for entry, rt in pbar:
            jitter(DELAY_COMPANY)
            code = entry["KodeEmiten"]
            name = entry["NamaEmiten"]
            pbar.set_postfix_str(code)
            ok = False
            codes.append(code)
            names.append(name)
            types.append(rt)

            for attachment in entry.get("Attachments", []):
                fname = attachment["File_Name"]
                if is_report_file(fname, rt):
                    try:
                        jitter(DELAY_ATTACHMENT)
                        download_file(session, code, year, rt, attachment["File_Path"])
                        ok = True
                    except requests.RequestException as e:
                        tqdm.write(f"  [{code}] Failed: {fname} — {e}")

            statuses.append(ok)

            if ok:
                save_status({
                    "code": codes,
                    "company": names,
                    "report_type": types,
                    "status": statuses,
                })

    print("Done.")


if __name__ == "__main__":
    main()