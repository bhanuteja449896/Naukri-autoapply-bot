"""
Google Sheets Client
====================
Handles OAuth2 authentication and all Google Sheets operations.

Two sheets per Google Sheet file:
  - SHEET_EXTERNAL  ("Apply on Website")  → jobs requiring manual apply on company site
  - SHEET_DIRECT    ("Direct Applied")    → jobs directly applied on Naukri

First-time setup:
    python sheets_auth.py   # opens browser for OAuth2 consent
"""

import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Sheet tab names (configurable via .env) ────────────────────────────────────
SHEET_EXTERNAL = os.getenv("SHEET_EXTERNAL", "Apply on Website")
SHEET_DIRECT   = os.getenv("SHEET_DIRECT",   "Direct Applied")

# ── Column headers ─────────────────────────────────────────────────────────────
HEADERS_EXTERNAL = [
    "Job Title", "Company", "Naukri URL", "Company Apply URL",
    "Location", "Experience", "Salary", "Date Scraped", "Keyword",
]

HEADERS_DIRECT = [
    "Job Title", "Company", "Naukri URL",
    "Location", "Experience", "Salary", "Date Applied", "Keyword",
]

# ── Field ordering for each sheet ──────────────────────────────────────────────
FIELDS_EXTERNAL = [
    "title", "company", "naukri_url", "external_url",
    "location", "experience", "salary", "date", "keyword",
]

FIELDS_DIRECT = [
    "title", "company", "naukri_url",
    "location", "experience", "salary", "date", "keyword",
]


def get_sheets_client():
    """
    Authenticate with Google Sheets API using OAuth2 or Service Account.
    Prioritizes service_account.json if present, then token.json.
    Returns a googleapiclient service object.
    """
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    if os.path.exists(sa_path):
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )
        return build("sheets", "v4", credentials=creds, static_discovery=False)

    creds = None
    token_path = "token.json"
    creds_path = "credentials.json"

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Missing credentials. Please supply '{sa_path}' or '{creds_path}'."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("sheets", "v4", credentials=creds, static_discovery=False)


def ensure_sheets_exist(service, sheet_id: str):
    """
    Make sure both sheet tabs exist in the spreadsheet.
    Creates them if missing, and writes headers if the sheet is empty.
    """
    # Get existing sheet names
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}

    requests = []
    for tab_name in (SHEET_EXTERNAL, SHEET_DIRECT):
        if tab_name not in existing:
            requests.append({
                "addSheet": {
                    "properties": {"title": tab_name}
                }
            })

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests},
        ).execute()

    # Write headers if each sheet is empty
    _ensure_headers(service, sheet_id, SHEET_EXTERNAL, HEADERS_EXTERNAL)
    _ensure_headers(service, sheet_id, SHEET_DIRECT,   HEADERS_DIRECT)


def _ensure_headers(service, sheet_id: str, sheet_name: str, headers: list):
    """Write header row if the sheet is empty."""
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"'{sheet_name}'!A1:Z1",
    ).execute()

    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()


def append_external_job(service, sheet_id: str, job: dict):
    """
    Log a job that requires applying on the company's own website.
    Goes into the 'Apply on Website' sheet (appended to bottom).
    """
    if not job.get("date"):
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            job["date"] = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            from datetime import datetime
            job["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [str(job.get(f, "")) for f in FIELDS_EXTERNAL]
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{SHEET_EXTERNAL}'!A:I",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()


def append_direct_job(service, sheet_id: str, job: dict):
    """
    Log a job that was directly applied on Naukri.
    Goes into the 'Direct Applied' sheet (appended to bottom).
    """
    if not job.get("date"):
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            job["date"] = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            from datetime import datetime
            job["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [str(job.get(f, "")) for f in FIELDS_DIRECT]
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{SHEET_DIRECT}'!A:H",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()


def get_all_seen_urls(service, sheet_id: str) -> set:
    """
    Return all Naukri URLs already logged in BOTH sheets (column C).
    Used for deduplication — bot skips any URL it has seen before.
    """
    seen = set()
    for sheet_name in (SHEET_EXTERNAL, SHEET_DIRECT):
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"'{sheet_name}'!C2:C",
            ).execute()
            for row in result.get("values", []):
                if row and row[0].strip():
                    seen.add(row[0].strip())
        except Exception:
            pass
    return seen


# ── Legacy alias kept for backward compatibility ───────────────────────────────
def append_job_row(service, sheet_id: str, sheet_name: str, job: dict):
    """Deprecated: use append_external_job / append_direct_job instead."""
    if job.get("status") == "External":
        append_external_job(service, sheet_id, job)
    else:
        append_direct_job(service, sheet_id, job)


def get_applied_urls(service, sheet_id: str, sheet_name: str = None) -> set:
    """Deprecated: use get_all_seen_urls instead."""
    return get_all_seen_urls(service, sheet_id)
