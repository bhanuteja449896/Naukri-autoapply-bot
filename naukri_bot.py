"""
Naukri Auto-Apply Bot — Upgraded Edition
=========================================
Features:
  - undetected-chromedriver (bypasses Cloudflare / bot detection)
  - Human-like delays + randomized behaviour
  - Keyword + filter based job scraping
  - Auto-applies to Naukri native applications
  - Logs "Apply on company site" jobs to Google Sheets
  - Skips jobs already applied (deduplicates from Sheets history)
  - Cloud Run / Lambda compatible (single run, no persistent scheduler)
  - Logs: Job Title, Company, Naukri URL, External URL, Location,
          Experience, Salary, Date Scraped, Status

Usage:
    1. python sheets_auth.py          # First-time OAuth2 setup (creates token.json)
    2. python naukri_bot.py           # Run the bot once
    3. Schedule via Cloud Scheduler / cron to run every hour
"""

import os
import re
import csv
import json
import time
import random
import logging
import difflib
import traceback
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Selenium + stealth (Python 3.14 compatible — replaces undetected-chromedriver)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, WebDriverException,
    StaleElementReferenceException,
)
try:
    from selenium_stealth import stealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
try:
    from webdriver_manager.chrome import ChromeDriverManager
    WDM_AVAILABLE = True
except ImportError:
    WDM_AVAILABLE = False

from bs4 import BeautifulSoup

try:
    from sheets_client import (
        get_sheets_client,
        ensure_sheets_exist,
        append_external_job,
        append_direct_job,
        get_all_seen_urls,
    )
    SHEETS_AVAILABLE = True
except ImportError:
    SHEETS_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

NAUKRI_EMAIL    = os.getenv("NAUKRI_EMAIL", "")
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "")
FIRSTNAME       = os.getenv("FIRSTNAME", "")
LASTNAME        = os.getenv("LASTNAME", "")

KEYWORDS         = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]
LOCATION         = os.getenv("LOCATION", "").strip()
EXPERIENCE_MIN   = os.getenv("EXPERIENCE_MIN", "").strip()
EXPERIENCE_MAX   = os.getenv("EXPERIENCE_MAX", "").strip()
SALARY_MIN       = os.getenv("SALARY_MIN", "").strip()
JOB_AGE_DAYS     = os.getenv("JOB_AGE_DAYS", "1").strip()

# Location filtering logic:
# If ALLOWED_LOCATIONS is set, or if LOCATION contains multiple cities separated by commas,
# filter jobs in-memory by city and perform a nationwide search on Naukri.
_env_allowed = os.getenv("ALLOWED_LOCATIONS", "").strip()
if _env_allowed:
    ALLOWED_LOCATIONS = [l.strip().lower() for l in _env_allowed.split(",") if l.strip()]
elif "," in LOCATION:
    ALLOWED_LOCATIONS = [l.strip().lower() for l in LOCATION.split(",") if l.strip()]
    LOCATION = ""  # Clear so it doesn't try to use comma-separated string in URL slug
elif LOCATION.lower() in ("all", "india", ""):
    ALLOWED_LOCATIONS = []
    LOCATION = ""
else:
    ALLOWED_LOCATIONS = []

WFH_TYPE         = os.getenv("WFH_TYPE", "").strip()
_env_work_mode   = os.getenv("WORK_MODE_ONLY", "").strip()
WORK_MODE_ONLY   = [m.strip().lower() for m in _env_work_mode.split(",") if m.strip()]

MAX_PAGES_PER_KEYWORD = int(os.getenv("MAX_PAGES_PER_KEYWORD", os.getenv("PAGES_PER_KEYWORD", "15")))
if MAX_PAGES_PER_KEYWORD <= 0:
    MAX_PAGES_PER_KEYWORD = 15
PAGES_PER_KEYWORD = MAX_PAGES_PER_KEYWORD
MAX_APPLICATIONS  = int(os.getenv("MAX_APPLICATIONS", "50"))

GOOGLE_SHEET_ID  = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_NAME       = os.getenv("SHEET_NAME", "Applications")

COOKIES_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "naukri_cookies.json")
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
GCS_BUCKET         = os.getenv("GCS_BUCKET", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram_notification(text: str):
    """Send a notification message via Telegram bot."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token:
        return

    # If chat_id is not set, try to auto-detect from getUpdates
    if not chat_id:
        try:
            import urllib.request
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/getUpdates")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                updates = data.get("result", [])
                if updates:
                    last_msg = updates[-1].get("message") or updates[-1].get("channel_post")
                    if last_msg and "chat" in last_msg:
                        chat_id = str(last_msg["chat"]["id"])
                        logger.info(f"Auto-detected Telegram chat_id: {chat_id}")
        except Exception as e:
            logger.debug(f"Could not auto-detect chat_id: {e}")

    if not chat_id:
        logger.warning("Telegram notification skipped: TELEGRAM_CHAT_ID not configured.")
        return

    try:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info(f"📲 Telegram notification sent: {text.splitlines()[0]}")
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def format_telegram_summary(first_name: str, start_time: str, applied_count: int) -> str:
    """Format Telegram completion message according to profile specification."""
    fn = (first_name or "").lower()
    if "bhanu" in fn:
        return f"Bhanu Teja\nTime : {start_time}\njobs applied : {applied_count}"
    elif "kiran" in fn:
        return f"Kiran Kumar\nTime : {start_time}\njobs applied : {applied_count}"
    else:
        return f"Rahul\nstarting time : {start_time}\nJobs applied : {applied_count}"


def human_sleep(mn=1.5, mx=4.0):
    time.sleep(random.uniform(mn, mx))


def human_type(element, text):
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.14))


# ─────────────────────────────────────────────────────────────────────────────
# Application Questionnaire & Knowledge Base
# ─────────────────────────────────────────────────────────────────────────────

ANSWERS_CSV = os.getenv("ANSWERS_CSV", "application_answers.csv")


def load_known_answers() -> dict:
    """Load question -> answer mappings from application_answers.csv."""
    answers = {}
    if not os.path.exists(ANSWERS_CSV):
        return answers
    try:
        with open(ANSWERS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                q = (row.get("question_text") or "").strip().lower()
                a = (row.get("answer") or "").strip()
                if q and a:
                    answers[q] = a
        logger.info(f"Loaded {len(answers)} question answers from {ANSWERS_CSV}")
    except Exception as e:
        logger.warning(f"Could not load {ANSWERS_CSV}: {e}")
    return answers


def fuzzy_lookup(question_text: str, known_answers: dict, threshold: float = 0.65) -> str:
    """Look up answer using exact, keyword, or fuzzy string similarity."""
    q_norm = question_text.strip().lower()
    if not q_norm:
        return ""

    # 1. Exact match
    for k, v in known_answers.items():
        if k.lower() == q_norm:
            return v

    # 2. Smart domain heuristics (handles varying phrasing for common questions)
    if "notice" in q_norm or "join" in q_norm:
        if "serving" in q_norm:
            return known_answers.get("serving notice period", "No")
        return known_answers.get("notice period", "Immediate")
    if "relocate" in q_norm:
        return known_answers.get("willing to relocate", "Yes")
    if "hybrid" in q_norm or "remote" in q_norm or "wfh" in q_norm:
        return "Yes"

    # 3. Direct phrase match (longest / most specific phrase first)
    for k in sorted(known_answers.keys(), key=len, reverse=True):
        k_lower = k.lower()
        if k_lower in q_norm:
            return known_answers[k]

    if "current ctc" in q_norm or "fixed ctc" in q_norm:
        return known_answers.get("current ctc", "0")
    if "expected ctc" in q_norm or "expected salary" in q_norm:
        return known_answers.get("expected ctc", "8")
    if "pyspark" in q_norm or "spark" in q_norm:
        return known_answers.get("pyspark", "1")
    if "databricks" in q_norm:
        return known_answers.get("azure databricks", "1")
    if "adf" in q_norm or "data factory" in q_norm:
        return known_answers.get("azure data factory", "1")
    if "azure" in q_norm:
        return known_answers.get("azure", "1")
    if "c#" in q_norm or "c sharp" in q_norm:
        return known_answers.get("c#", known_answers.get("experience in c#", "4"))
    if ".net" in q_norm or "dotnet" in q_norm or "asp.net" in q_norm:
        return known_answers.get(".net", known_answers.get(".net core", "4"))
    if "entity framework" in q_norm or "ef core" in q_norm:
        return known_answers.get("entity framework", "4")
    if "fastapi" in q_norm:
        return known_answers.get("fastapi", "1")
    if "python" in q_norm:
        return known_answers.get("experience in python", "2")
    if "spring" in q_norm:
        return known_answers.get("spring boot", "1")
    if "java" in q_norm:
        return known_answers.get("experience in java", "2")
    if "react" in q_norm:
        return known_answers.get("react", "1")
    if "node" in q_norm:
        return known_answers.get("node.js", "1")
    if "langchain" in q_norm or "llm" in q_norm or "genai" in q_norm or "agent" in q_norm:
        return known_answers.get("langchain", "1")
    if "total" in q_norm and "experience" in q_norm:
        return known_answers.get("total experience", "1")
    if "microservice" in q_norm:
        return known_answers.get("microservices", "1")
    if "docker" in q_norm:
        return known_answers.get("docker", "1")
    if "kubernetes" in q_norm:
        return known_answers.get("kubernetes", "1")
    if "aws" in q_norm or "cloud" in q_norm:
        return known_answers.get("azure", "1")
    if "sql" in q_norm or "postgres" in q_norm or "mysql" in q_norm:
        return known_answers.get("sql", "2")

    # 4. Difflib close matches
    matches = difflib.get_close_matches(q_norm, [k.lower() for k in known_answers.keys()], n=1, cutoff=threshold)
    if matches:
        for k, v in known_answers.items():
            if k.lower() == matches[0]:
                logger.info(f"   Fuzzy match: '{matches[0]}' for '{question_text[:35]}'")
                return v

    return ""


def handle_chatbot_drawer(driver, known_answers: dict) -> bool:
    """
    Handle Naukri's application questionnaire drawer (radio buttons, text areas, dropdowns).
    Auto-fills answers based on application_answers.csv and smart fuzzy matching.
    """
    drawer_selectors = [
        "[id*='ChatbotContainer']",
        ".chatbot_Drawer",
        ".drawer-wrapper",
        ".apply-message-container",
    ]
    drawer_found = False
    for sel in drawer_selectors:
        if driver.find_elements(By.CSS_SELECTOR, sel):
            drawer_found = True
            break

    if not drawer_found:
        return True

    logger.info("  📋 Questionnaire drawer detected — auto-filling questions...")
    human_sleep(2, 3)

    for iteration in range(15):  # up to 15 questions
        try:
            # Check if drawer has closed
            is_open = False
            for sel in drawer_selectors:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        is_open = True
                        break
                if is_open:
                    break
            if not is_open:
                logger.info("  ✓ Questionnaire drawer completed.")
                return True

            # Extract current question text
            bot_msgs = driver.find_elements(By.CSS_SELECTOR, ".chatbot_ListItem.botItem .msg, .chatbot_ListItem .botMsg .msg, .botMsg .msg, .botItem")
            q_text = bot_msgs[-1].text.strip() if bot_msgs else "Questionnaire"

            # 1. Radio buttons question
            radio_containers = driver.find_elements(By.CSS_SELECTOR, ".singleselect-radiobutton-container, .radio-container")
            answered_radio = False
            for container in radio_containers:
                if not container.is_displayed():
                    continue
                radios = container.find_elements(By.CSS_SELECTOR, ".ssrc__radio, input[type='radio']")
                labels = container.find_elements(By.CSS_SELECTOR, ".ssrc__label, label")
                if not radios and not labels:
                    continue

                target_val = fuzzy_lookup(q_text, known_answers)
                clicked = False
                for r, l in zip(radios, labels if len(labels) == len(radios) else radios):
                    lbl_text = l.text.strip().lower() if hasattr(l, "text") else ""
                    r_val = (r.get_attribute("value") or "").lower()
                    if (target_val and (target_val.lower() in lbl_text or target_val.lower() == r_val)) or (not target_val and "yes" in lbl_text):
                        driver.execute_script("arguments[0].click();", r)
                        logger.info(f"   Radio selected: '{lbl_text or target_val}' for '{q_text[:35]}'")
                        clicked = True
                        break

                if not clicked and radios:
                    driver.execute_script("arguments[0].click();", radios[0])
                    clicked = True

                if clicked:
                    answered_radio = True
                    human_sleep(1.0, 2.0)
                    _click_chatbot_send(driver)
                    break

            if answered_radio:
                continue

            # 2. Text input / Contenteditable question
            text_areas = driver.find_elements(By.CSS_SELECTOR, ".chatbot_SendMessageContainer .textArea[contenteditable='true'], div.textArea[contenteditable='true'], .chatbot_InputContainer textarea, .drawer-wrapper input[type='text']")
            answered_text = False
            for ta in text_areas:
                if not ta.is_displayed():
                    continue
                ans = fuzzy_lookup(q_text, known_answers) or "Yes"
                try:
                    driver.execute_script("""
                        arguments[0].focus();
                        arguments[0].innerText = arguments[1];
                        arguments[0].dispatchEvent(new Event('input', {bubbles: true}));
                        arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
                    """, ta, ans)
                    try:
                        ta.send_keys(ans)
                    except Exception:
                        pass
                    logger.info(f"   Text answered: '{ans}' for '{q_text[:35]}'")
                    answered_text = True
                    human_sleep(1.0, 2.0)
                    _click_chatbot_send(driver)
                    break
                except Exception as e:
                    logger.debug(f"Error filling text area: {e}")

            if answered_text:
                continue

            # 3. Check for standalone Save / Submit / Next button
            if _click_chatbot_send(driver):
                human_sleep(2, 3)
                continue

            human_sleep(1.5, 2.5)

        except StaleElementReferenceException:
            human_sleep(0.5, 1.0)
            continue

    return True


def _click_chatbot_send(driver) -> bool:
    """Click Save, Send, Submit or Next in questionnaire drawer."""
    send_selectors = [
        ".sendMsg",
        ".sendMsgbtn_container .sendMsg",
        "[class*='sendMsg']",
        "//button[contains(text(),'Save')]",
        "//button[contains(text(),'Submit')]",
        "//button[contains(text(),'Next')]",
        "//button[contains(text(),'Submit and Apply')]",
    ]
    for sel in send_selectors:
        try:
            if sel.startswith("//"):
                btns = driver.find_elements(By.XPATH, sel)
            else:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for b in btns:
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].click();", b)
                    return True
        except Exception:
            continue
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Browser
# ─────────────────────────────────────────────────────────────────────────────

def _clean_stale_profile_locks(profile_dir: str):
    """Remove stale Chrome singleton locks left behind by interrupted runs."""
    if not os.path.exists(profile_dir):
        return
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_dir, name)
        if os.path.exists(p) or os.path.islink(p):
            try:
                os.unlink(p)
            except Exception:
                pass


def create_driver():
    """
    Create a stealth-patched Chrome/Chromium driver with persistent profile.
    Uses selenium-stealth to hide automation fingerprints.
    Uses --user-data-dir to retain logged-in sessions across hourly runs.
    """
    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--no-zygote")                      # required in containers
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Disable images and remote fonts to drastically cut load times and bandwidth
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-remote-fonts")

    # Persistent user profile across hourly runs
    _clean_stale_profile_locks(CHROME_PROFILE_DIR)
    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")

    # Force headless if no display is available (e.g. Codespaces, Cloud Run)
    no_display = not os.getenv("DISPLAY", "").strip()
    headless_env = os.getenv("HEADLESS", "false").lower() == "true"
    if headless_env or no_display:
        options.add_argument("--headless=new")

    # Auto-detect browser binary (Chromium or Chrome)
    chrome_binary = _find_chrome_binary()
    if chrome_binary:
        options.binary_location = chrome_binary
        logger.info(f"Using browser: {chrome_binary}")

    # Auto-download matching ChromeDriver
    if WDM_AVAILABLE:
        service = ChromeService(ChromeDriverManager().install())
    else:
        service = ChromeService()  # assume chromedriver is in PATH

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)

    # Apply stealth patches to hide automation fingerprints
    if STEALTH_AVAILABLE:
        stealth(
            driver,
            languages=["en-IN", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )
        logger.info("Stealth mode applied.")
    else:
        logger.warning("selenium-stealth not installed — bot detection risk higher.")

    return driver


def _find_chrome_binary() -> str:
    """Find Chrome or Chromium binary on common paths."""
    import shutil
    import glob

    candidates = [
        # Standard installs
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        # Absolute paths (apt / snap)
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/snap/bin/google-chrome",
        # Chrome for Testing (downloaded by webdriver-manager)
        *glob.glob("/home/**/.wdm/drivers/chrome/**/chrome", recursive=True),
        *glob.glob("/root/.wdm/drivers/chrome/**/chrome", recursive=True),
        # Codespace-specific paths
        "/usr/local/bin/chromium",
        "/opt/google/chrome/chrome",
    ]
    for c in candidates:
        path = shutil.which(c) or (c if os.path.isfile(c) else None)
        if path:
            logger.info(f"Found browser binary: {path}")
            return path

    logger.warning("No Chrome/Chromium binary found. Install with: sudo apt-get install -y chromium")
    return ""


def download_session_from_gcs(path=COOKIES_FILE):
    """Download session backup from Google Cloud Storage bucket if configured."""
    if not GCS_BUCKET:
        return
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(os.path.basename(path))
        if blob.exists():
            blob.download_to_filename(path)
            logger.info(f"☁️ Downloaded saved session from gs://{GCS_BUCKET}/{os.path.basename(path)}")
    except Exception as e:
        logger.warning(f"Could not download session from GCS: {e}")


def upload_session_to_gcs(path=COOKIES_FILE):
    """Upload session backup to Google Cloud Storage bucket if configured."""
    if not GCS_BUCKET or not os.path.exists(path):
        return
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(os.path.basename(path))
        blob.upload_from_filename(path)
        logger.info(f"☁️ Synced session to gs://{GCS_BUCKET}/{os.path.basename(path)}")
    except Exception as e:
        logger.warning(f"Could not sync session to GCS: {e}")


SEEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs_cache.json")


def load_seen_cache() -> set:
    """Load previously seen/evaluated job URLs from GCS and local cache."""
    seen = set()
    if GCS_BUCKET:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(GCS_BUCKET)
            blob = bucket.blob("seen_jobs_cache.json")
            if blob.exists():
                blob.download_to_filename(SEEN_CACHE_FILE)
                logger.info(f"☁️ Downloaded seen cache from gs://{GCS_BUCKET}/seen_jobs_cache.json")
        except Exception as e:
            logger.debug(f"Could not download seen cache from GCS: {e}")

    if os.path.exists(SEEN_CACHE_FILE):
        try:
            with open(SEEN_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    seen.update(data)
            logger.info(f"Loaded {len(seen)} previously evaluated job URLs from cache.")
        except Exception as e:
            logger.warning(f"Could not load seen cache file: {e}")
    return seen


def save_seen_cache(seen: set):
    """Save seen job URLs to local cache file and sync to GCS."""
    if not seen:
        return
    try:
        # Keep last 3000 URLs to keep file lightweight (< 150 KB)
        urls_to_save = list(seen)[-3000:]
        with open(SEEN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(urls_to_save, f)

        if GCS_BUCKET:
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket = client.bucket(GCS_BUCKET)
                blob = bucket.blob("seen_jobs_cache.json")
                blob.upload_from_filename(SEEN_CACHE_FILE)
                logger.info(f"☁️ Synced {len(urls_to_save)} evaluated job URLs to gs://{GCS_BUCKET}/seen_jobs_cache.json")
            except Exception as e:
                logger.debug(f"Could not sync seen cache to GCS: {e}")
    except Exception as e:
        logger.warning(f"Could not save seen cache: {e}")


def save_cookies(driver, path=COOKIES_FILE):
    """Save current browser cookies to JSON file and sync to Cloud Storage if configured."""
    try:
        cookies = driver.get_cookies()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        logger.info(f"💾 Saved {len(cookies)} session cookies to {os.path.basename(path)}")
        upload_session_to_gcs(path)
    except Exception as e:
        logger.warning(f"Could not save cookies: {e}")


def load_cookies(driver, path=COOKIES_FILE) -> bool:
    """Load session cookies from JSON file into driver."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies:
            return False
        driver.get("https://www.naukri.com/robots.txt")
        human_sleep(1.0, 2.0)
        loaded = 0
        for c in cookies:
            try:
                cookie_dict = {
                    "name": c["name"],
                    "value": c["value"],
                }
                if "domain" in c and "naukri" in c["domain"]:
                    cookie_dict["domain"] = c["domain"]
                if "path" in c:
                    cookie_dict["path"] = c["path"]
                if "secure" in c:
                    cookie_dict["secure"] = c["secure"]
                if "httpOnly" in c:
                    cookie_dict["httpOnly"] = c["httpOnly"]
                if "sameSite" in c and c["sameSite"] in ("Strict", "Lax", "None"):
                    cookie_dict["sameSite"] = c["sameSite"]
                driver.add_cookie(cookie_dict)
                loaded += 1
            except Exception:
                continue
        logger.info(f"🍪 Injected {loaded} cookies from {os.path.basename(path)}")
        return loaded > 0
    except Exception as e:
        logger.warning(f"Could not load cookies: {e}")
        return False


def is_session_active(driver) -> bool:
    """Check if the current browser session is already authenticated on Naukri."""
    try:
        driver.get("https://www.naukri.com/mnjuser/homepage")
        human_sleep(2.5, 4.0)
        curr = driver.current_url.lower()
        if "login" in curr or "naukri.com/login" in curr:
            return False

        if "mnjuser" in curr:
            logged_in_selectors = [
                ".nI-gNb-drawer",
                "a[href*='mnjuser/profile']",
                "div.user-name",
                "div.nI-gNb-header__wrapper",
                "a[title='View profile']",
                ".user-info",
            ]
            for sel in logged_in_selectors:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if any(e.is_displayed() for e in elems):
                    return True
            return True
        return False
    except Exception as e:
        logger.debug(f"Session check error: {e}")
        return False


def ensure_naukri_session(driver) -> bool:
    """
    Ensure active logged-in session without re-entering credentials every hour:
    1. If in cloud, sync session from Cloud Storage if configured.
    2. Check if persistent Chrome profile already has an active session.
    3. If not, try restoring session cookies from JSON.
    4. If still not authenticated (first run or expired session), perform login with credentials.
    """
    download_session_from_gcs()
    logger.info("🔍 Checking existing Naukri session...")

    # 1. Existing Chrome profile session check
    if is_session_active(driver):
        logger.info("✅ Active Naukri session found! Reusing existing session (no credentials needed).")
        save_cookies(driver)
        return True

    # 2. Try restoring from cookie file
    if load_cookies(driver):
        if is_session_active(driver):
            logger.info("✅ Session restored from saved cookies! Reusing session (no credentials needed).")
            return True

    # 3. Session not active or expired -> authenticate using credentials
    logger.info("🔑 No active session found (or expired). Logging in with credentials...")
    logged_in = login_naukri(driver)
    if logged_in:
        save_cookies(driver)
    return logged_in


def login_naukri(driver) -> bool:
    if not NAUKRI_EMAIL or not NAUKRI_PASSWORD or "example.com" in NAUKRI_EMAIL or "your_real" in NAUKRI_PASSWORD:
        logger.error("❌ NAUKRI_EMAIL or NAUKRI_PASSWORD is not configured in .env (still has placeholder values).")
        logger.error("Please add your real Naukri login credentials in .env to apply to jobs.")
        return False

    logger.info(f"Logging in to Naukri.com as {NAUKRI_EMAIL[:3]}***@... ...")
    try:
        driver.get("https://login.naukri.com/")
        human_sleep(3, 5)

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "usernameField"))
        )

        uname = driver.find_element(By.ID, "usernameField")
        uname.clear()
        human_type(uname, NAUKRI_EMAIL)
        human_sleep(0.5, 1.2)

        passwd = driver.find_element(By.ID, "passwordField")
        passwd.clear()
        human_type(passwd, NAUKRI_PASSWORD)
        human_sleep(0.3, 0.8)
        passwd.send_keys(Keys.ENTER)

        human_sleep(7, 11)
        if "login" in driver.current_url.lower():
            otp_inputs = driver.find_elements(By.CSS_SELECTOR, "input[placeholder*='OTP'], input[id*='otp'], .otp-container, input[type='number']")
            if otp_inputs:
                logger.error("❌ Naukri prompted for OTP verification sent to your mobile/email.")
            try:
                driver.save_screenshot("login_debug.png")
                logger.info("📸 Saved screenshot to 'login_debug.png' for visual inspection.")
            except Exception:
                pass
            logger.error("❌ Still on login page — possible CAPTCHA, OTP, or invalid credentials.")
            return False
        else:
            logger.info("✅ Login successful.")
            save_cookies(driver)
            return True
    except Exception as e:
        logger.error(f"❌ Login error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Location Filtering & URL Builder
# ─────────────────────────────────────────────────────────────────────────────

def is_allowed_location(job_loc: str) -> bool:
    """
    Check if a job location matches any of the configured allowed locations.
    If ALLOWED_LOCATIONS is empty, allows all locations (e.g. nationwide apply).
    Supports remote, wfh, and hybrid synonyms if configured.
    """
    if not ALLOWED_LOCATIONS:
        return True
    if not job_loc:
        return False
    job_loc_lower = job_loc.lower()

    if any(allowed in job_loc_lower for allowed in ALLOWED_LOCATIONS):
        return True

    # If remote, wfh, or hybrid is in ALLOWED_LOCATIONS, check common terms
    if any(r in ALLOWED_LOCATIONS for r in ("remote", "wfh", "hybrid")):
        flexible_terms = ("remote", "work from home", "wfh", "anywhere in india", "hybrid")
        if any(term in job_loc_lower for term in flexible_terms):
            return True

    return False


def is_allowed_job(job: dict) -> bool:
    """
    Check if a job matches both the configured workplace type (Hybrid, Remote, etc.)
    and location requirements.
    """
    loc = job.get("location", "")
    work_mode = job.get("work_mode", "").lower()
    title = job.get("title", "").lower()
    loc_lower = loc.lower()

    if WORK_MODE_ONLY:
        matched_mode = any(m in work_mode or m in loc_lower or m in title for m in WORK_MODE_ONLY)
        if not matched_mode:
            return False

    # If hybrid, remote, or wfh is in allowed locations, and job is tagged as Hybrid or Remote, allow it!
    if any(r in ALLOWED_LOCATIONS for r in ("remote", "wfh", "hybrid")):
        if work_mode in ("hybrid", "remote"):
            return True

    return is_allowed_location(loc)


def sanitize_keyword_slug(keyword: str) -> str:
    """Convert human keyword into valid Naukri URL slug."""
    s = keyword.lower()
    s = s.replace("c#", "c-sharp")
    s = s.replace(".net", "dotnet")
    s = s.replace("dot net", "dotnet")
    s = s.replace("asp.net", "asp-net")
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def build_search_url_for_page(keyword: str, page: int = 1) -> str:
    """Build Naukri search URL for a given keyword and page number."""
    slug = sanitize_keyword_slug(keyword)
    if LOCATION and "," not in LOCATION:
        loc_slug = sanitize_keyword_slug(LOCATION)
        base = f"https://www.naukri.com/{slug}-jobs-in-{loc_slug}"
    else:
        base = f"https://www.naukri.com/{slug}-jobs"

    if page > 1:
        base += f"-{page}"

    params = []
    if EXPERIENCE_MIN:
        try:
            # If EXPERIENCE_MIN is 0, do not pass experience=0 to Naukri
            # because Naukri interprets experience=0 as strictly Fresher (0 years only),
            # excluding 1-2 years experience jobs when EXPERIENCE_MAX is 2.
            if int(EXPERIENCE_MIN) > 0:
                params.append(f"experience={EXPERIENCE_MIN}")
        except ValueError:
            params.append(f"experience={EXPERIENCE_MIN}")
    if SALARY_MIN:
        raw_sal = "".join(c for c in SALARY_MIN if c.isdigit())
        if raw_sal:
            sal_num = int(raw_sal)
            sal_val = sal_num * 100000 if sal_num < 100 else sal_num
            params.append(f"salary={sal_val}")
    if JOB_AGE_DAYS:
        params.append(f"jobAge={JOB_AGE_DAYS}")
    if params:
        base += "?" + "&".join(params)
    return base


def build_search_urls():
    """Build initial search URLs (Page 1) for all keywords."""
    return [(kw, build_search_url_for_page(kw, 1)) for kw in KEYWORDS]


# ─────────────────────────────────────────────────────────────────────────────
# Scraping
# ─────────────────────────────────────────────────────────────────────────────

def scrape_jobs_from_page(driver, url, keyword):
    jobs = []
    try:
        driver.get(url)
        human_sleep(3, 6)
        # Lazy-load scroll
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 600);")
            human_sleep(0.5, 1.2)
        driver.execute_script("window.scrollTo(0, 0);")
        human_sleep(1, 2)

        soup = BeautifulSoup(driver.page_source, "html5lib")
        wrappers = soup.find_all("div", class_="srp-jobtuple-wrapper")
        if not wrappers:
            wrappers = soup.find_all("div", class_="cust-job-tuple")

        logger.info(f"  [{keyword}] {len(wrappers)} jobs on {url[:70]}")

        for w in wrappers:
            job = _parse_card(w, keyword)
            if job:
                jobs.append(job)
    except WebDriverException as e:
        logger.warning(f"  Failed to load {url}: {e}")
    return jobs


def _parse_card(wrapper, keyword):
    try:
        title_tag = wrapper.find("a", class_="title")
        if not title_tag:
            return None

        title = title_tag.get_text(strip=True)
        href  = title_tag.get("href", "")
        if href.startswith("/"):
            href = "https://www.naukri.com" + href

        company_tag = (
            wrapper.find("a", class_="comp-name") or
            wrapper.find("span", class_="comp-name") or
            wrapper.find("a", attrs={"data-ga-track": re.compile("company", re.I)})
        )
        company = company_tag.get_text(strip=True) if company_tag else "N/A"

        loc_tag = wrapper.find("span", class_="locWdth") or wrapper.find("span", class_="location")
        location = loc_tag.get_text(strip=True) if loc_tag else (LOCATION or "N/A")

        # Capture card text to detect workplace type (Hybrid / Remote / On-site)
        card_text = wrapper.get_text(separator=" ", strip=True).lower()
        work_mode = "On-site"
        if "hybrid" in card_text or "hybrid" in location.lower():
            work_mode = "Hybrid"
        elif any(r in card_text or r in location.lower() for r in ("remote", "work from home", "wfh")):
            work_mode = "Remote"

        exp_tag = wrapper.find("span", class_="expwdth") or wrapper.find("span", class_="experience")
        experience = exp_tag.get_text(strip=True) if exp_tag else "N/A"

        sal_tag = wrapper.find("span", class_="sal") or wrapper.find("span", class_="salary")
        salary = sal_tag.get_text(strip=True) if sal_tag else "Not Disclosed"

        return {
            "title":        title,
            "company":      company,
            "naukri_url":   href,
            "external_url": "",
            "location":     location,
            "work_mode":    work_mode,
            "experience":   experience,
            "salary":       salary,
            "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status":       "Pending",
            "keyword":      keyword,
        }
    except Exception as e:
        logger.debug(f"Card parse error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Apply
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_job(driver, job, known_answers: dict = None):
    if known_answers is None:
        known_answers = {}

    try:
        driver.get(job["naukri_url"])
        human_sleep(3, 6)
        if "login" in driver.current_url.lower():
            logger.warning("  ⚠️ Session expired while navigating to job. Re-authenticating...")
            if login_naukri(driver):
                driver.get(job["naukri_url"])
                human_sleep(3, 5)
            else:
                job["status"] = "Session Expired"
                return job
    except WebDriverException as e:
        logger.warning(f"  Failed to load page: {e}")
        job["status"] = "Failed"
        return job

    # Daily quota check
    try:
        driver.find_element(By.XPATH, "//*[contains(text(),'daily quota')]")
        logger.warning("Daily quota reached.")
        job["status"] = "Quota Reached"
        return job
    except NoSuchElementException:
        pass

    selectors = [
        (By.ID,    "company-site-button"),
        (By.XPATH, "//button[contains(text(),'Apply on company site')]"),
        (By.XPATH, "//button[contains(text(),'Apply on Company Site')]"),
        (By.XPATH, "//button[contains(text(),'Apply')]"),
        (By.CSS_SELECTOR, "[class*='apply-button-container'] button"),
    ]

    apply_btn = None
    btn_text  = ""
    for by, sel in selectors:
        try:
            btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
            btn_text = btn.text.strip()
            apply_btn = btn
            break
        except (TimeoutException, NoSuchElementException):
            continue

    if apply_btn is None:
        logger.warning(f"  No apply button: {job['title'][:50]}")
        job["status"] = "No Apply Button"
        return job

    is_external = (
        "company site" in btn_text.lower() or
        "company-site" in (apply_btn.get_attribute("id") or "").lower() or
        "apply on company" in (apply_btn.get_attribute("title") or "").lower()
    )

    if is_external:
        orig_windows = set(driver.window_handles)
        ext_url = ""

        # Click the button on Naukri to register the application on user's Naukri profile
        # and capture the destination company website URL from the newly opened tab
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", apply_btn)
            human_sleep(0.5, 1.0)
            apply_btn.click()
            human_sleep(2.0, 3.5)

            new_windows = set(driver.window_handles) - orig_windows
            if new_windows:
                ext_win = list(new_windows)[0]
                driver.switch_to.window(ext_win)
                human_sleep(1.0, 2.0)
                ext_url = driver.current_url
                driver.close()
                driver.switch_to.window(list(orig_windows)[0])
        except Exception as e:
            logger.debug(f"External click handling note: {e}")

        # Fallback to data-href or parent a tag if popup tab didn't appear
        if not ext_url or ext_url.startswith("chrome://"):
            try:
                ext_url = apply_btn.get_attribute("data-href") or ""
                if not ext_url:
                    parent_a = driver.execute_script("return arguments[0].closest('a');", apply_btn)
                    if parent_a:
                        ext_url = parent_a.get_attribute("href") or ""
            except Exception:
                ext_url = ""

        job["external_url"] = ext_url or job["naukri_url"]
        job["status"] = "External"
        logger.info(f"  External apply recorded on Naukri: {job['company']} | {job['title'][:40]}")
    else:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", apply_btn)
            human_sleep(0.5, 1.0)
            apply_btn.click()
            human_sleep(2, 4)

            # Check if login prompt / modal was triggered (unauthenticated)
            login_modal = driver.find_elements(By.CSS_SELECTOR, ".login-layer, #login-layer, .drawer-wrapper .login-container, .login-modal")
            if login_modal or "login" in driver.current_url.lower():
                logger.warning(f"  Login prompt detected — session not logged in! Skipping direct application.")
                job["status"] = "Requires Login"
                return job

            _fill_name_fields(driver)

            # Auto-handle application questions / chatbot drawer
            handle_chatbot_drawer(driver, known_answers)

            _click_submit_apply(driver)
            human_sleep(1.5, 3.0)

            job["status"] = "Applied"
            logger.info(f"  Applied: {job['company']} | {job['title'][:40]}")
        except (ElementClickInterceptedException, WebDriverException) as e:
            logger.warning(f"  Click failed: {e}")
            job["status"] = "Failed"

    return job


def _fill_name_fields(driver):
    for fid, val in [("CUSTOM-FIRSTNAME", FIRSTNAME), ("CUSTOM-LASTNAME", LASTNAME)]:
        if not val:
            continue
        try:
            el = driver.find_element(By.ID, fid)
            el.clear()
            human_type(el, val)
        except NoSuchElementException:
            pass


def _click_submit_apply(driver):
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'Submit and Apply')]"))
        )
        btn.click()
        human_sleep(1, 2)
    except TimeoutException:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Local CSV
# ─────────────────────────────────────────────────────────────────────────────

FIELDNAMES = [
    "title", "company", "naukri_url", "external_url",
    "location", "experience", "salary", "date", "status", "keyword",
]
LOCAL_CSV = "naukriapplied.csv"


def load_local_csv_urls():
    seen = set()
    if os.path.exists(LOCAL_CSV) and os.path.getsize(LOCAL_CSV) > 0:
        with open(LOCAL_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                status = (row.get("status") or "").strip().lower()
                url = (row.get("naukri_url") or "").strip()
                # Only skip jobs that were successfully applied or queued as external
                if url and status in ("applied", "external", "already applied", "success"):
                    seen.add(url)
    return seen


def save_to_local_csv(results):
    file_exists = os.path.exists(LOCAL_CSV) and os.path.getsize(LOCAL_CSV) > 0
    with open(LOCAL_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for job in results:
            writer.writerow(job)
    logger.info(f"Saved {len(results)} rows to {LOCAL_CSV}")


def append_single_job_to_local_csv(job: dict):
    """Immediately append a single applied job to local CSV."""
    try:
        file_exists = os.path.exists(LOCAL_CSV) and os.path.getsize(LOCAL_CSV) > 0
        with open(LOCAL_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(job)
            f.flush()
    except Exception as e:
        logger.warning(f"Could not append to {LOCAL_CSV}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Concurrency Lock (Prevents overlapping hourly runs)
# ─────────────────────────────────────────────────────────────────────────────

LOCK_FILE = "/tmp/naukri_bot.lock"


class BotLock:
    def __init__(self, lock_path=LOCK_FILE):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self) -> bool:
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r") as f:
                    pid = int(f.read().strip())
                # Check if previous process is still actively running
                os.kill(pid, 0)
                return False
            except (OSError, ValueError):
                # Process died or lock is stale
                pass

        try:
            with open(self.lock_path, "w") as f:
                f.write(str(os.getpid()))
            self.acquired = True
            return True
        except Exception:
            return False

    def release(self):
        if self.acquired and os.path.exists(self.lock_path):
            try:
                os.remove(self.lock_path)
            except Exception:
                pass
            self.acquired = False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run_bot():
    lock = BotLock()
    if not lock.acquire():
        logger.warning("=" * 60)
        logger.warning("⚠️  Another bot run is currently in progress!")
        logger.warning("Skipping this scheduled tick to avoid concurrent session conflicts.")
        logger.warning("=" * 60)
        return

    logger.info("=" * 60)
    logger.info("Naukri Auto-Apply Bot — Starting")
    logger.info(f"  Keywords : {KEYWORDS}")
    logger.info(f"  Location : {LOCATION or 'Anywhere'}")
    logger.info(f"  Max Apps : {MAX_APPLICATIONS}")
    logger.info("=" * 60)

    if not NAUKRI_EMAIL or not NAUKRI_PASSWORD or not KEYWORDS:
        logger.error("Missing NAUKRI_EMAIL / NAUKRI_PASSWORD / KEYWORDS in .env")
        return

    # ── Load seen URLs & Evaluated Cache ─────────────────────────────────────
    applied_urls = load_local_csv_urls()
    sheets = None

    if SHEETS_AVAILABLE and GOOGLE_SHEET_ID:
        try:
            sheets = get_sheets_client()
            # Auto-create both sheet tabs + headers if needed
            ensure_sheets_exist(sheets, GOOGLE_SHEET_ID)
            sheet_urls = get_all_seen_urls(sheets, GOOGLE_SHEET_ID)
            applied_urls.update(sheet_urls)
            logger.info(f"Loaded {len(applied_urls)} already-applied URLs (Sheets + local CSV).")
        except Exception as e:
            logger.warning(f"Sheets load failed: {e}. Proceeding with local CSV only.")
    else:
        logger.info(f"Loaded {len(applied_urls)} already-applied URLs (local CSV).")

    # Load evaluated/seen cache so the bot never re-evaluates skipped or old jobs from earlier runs
    seen_cache = load_seen_cache()
    applied_urls.update(seen_cache)

    # ── Browser ─────────────────────────────────────────────────────────────
    driver = None
    results = []
    applied_count = 0
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    if now.minute < 10:
        start_time_str = now.replace(minute=0, second=0).strftime("%I:%M %p")
    else:
        start_time_str = now.strftime("%I:%M %p")
    start_time_epoch = time.time()
    MAX_RUN_SECONDS = int(os.getenv("MAX_RUN_SECONDS", "1680"))  # 28 mins max (leaving 2 mins for clean sleep)
    notification_sent = False
    try:
        driver = create_driver()
        is_logged_in = ensure_naukri_session(driver)
        if not is_logged_in:
            logger.error("=" * 60)
            logger.error("⚠️  BOT HALTED: Not logged in to Naukri.")
            logger.error("Please update your NAUKRI_EMAIL and NAUKRI_PASSWORD in .env with your real credentials.")
            logger.error("No applications were sent.")
            logger.error("=" * 60)
            return

        # Scrape with dynamic pagination per keyword
        all_jobs = []
        logger.info(f"🚀 Starting job search across {len(KEYWORDS)} keywords (Freshness: {JOB_AGE_DAYS}d, Max Pages/Keyword: {MAX_PAGES_PER_KEYWORD})")
        if ALLOWED_LOCATIONS:
            logger.info(f"📍 Allowed locations filter active: {', '.join(ALLOWED_LOCATIONS)}")
        else:
            logger.info("📍 Nationwide search active (all locations allowed)")

        for keyword in KEYWORDS:
            if time.time() - start_time_epoch > MAX_RUN_SECONDS:
                logger.info(f"⏱️ Maximum search duration reached ({MAX_RUN_SECONDS}s). Proceeding to process collected jobs.")
                break

            page = 1
            seen_urls_for_keyword = set()
            logger.info(f"🔎 Keyword: '{keyword}'")

            while True:
                if time.time() - start_time_epoch > MAX_RUN_SECONDS:
                    logger.info("⏱️ Time limit reached during pagination. Stopping search.")
                    break

                url = build_search_url_for_page(keyword, page)
                logger.info(f"  Fetching Page {page}: {url}")
                page_jobs = scrape_jobs_from_page(driver, url, keyword)

                if not page_jobs:
                    logger.info(f"  No jobs found on page {page} for '{keyword}'. End of results.")
                    break

                # Check if Naukri wrapped around or redirected to an already-seen page
                page_urls = [j["naukri_url"] for j in page_jobs if j.get("naukri_url")]
                if page_urls and all(u in seen_urls_for_keyword for u in page_urls):
                    logger.info(f"  Page {page} returned duplicate jobs from earlier pages for '{keyword}'. End of results.")
                    break
                seen_urls_for_keyword.update(page_urls)

                all_jobs.extend(page_jobs)
                seen_cache.update(page_urls)

                # Stop paging if all jobs on this page have already been evaluated/seen in previous runs,
                # saving compute and preventing re-checks of earlier jobs (check at least 2 pages)
                if page >= 2 and page_urls and all(u in applied_urls for u in page_urls):
                    logger.info(f"  ⚡ All {len(page_jobs)} jobs on page {page} were already evaluated in earlier runs. Stopping pagination for '{keyword}'.")
                    break

                if page >= MAX_PAGES_PER_KEYWORD:
                    logger.info(f"  Reached max page limit ({MAX_PAGES_PER_KEYWORD}) for '{keyword}'.")
                    break

                page += 1
                human_sleep(2.0, 3.5)

        logger.info(f"Total jobs scraped: {len(all_jobs)}")

        # Location filtering (keep only allowed cities if configured)
        matching_jobs = []
        skipped_loc_count = 0
        for j in all_jobs:
            if is_allowed_job(j):
                matching_jobs.append(j)
            else:
                skipped_loc_count += 1
                logger.debug(f"Skipping {j['title']} (Location '{j.get('location')}', Mode '{j.get('work_mode')}' outside allowed criteria)")

        logger.info(f"Criteria filtering: {len(matching_jobs)} matched allowed criteria ({skipped_loc_count} skipped)")

        # Deduplicate
        seen_this_run = set()
        new_jobs = []
        for j in matching_jobs:
            u = j["naukri_url"]
            if u not in applied_urls and u not in seen_this_run:
                seen_this_run.add(u)
                new_jobs.append(j)

        logger.info(f"New jobs to process: {len(new_jobs)} | Skipped (already applied/logged): {len(matching_jobs) - len(new_jobs)}")

        if not new_jobs:
            logger.info("⚡ No new jobs found this run (all already processed). Exiting early to enter sleep mode.")
            return

        # Load questionnaire answers from application_answers.csv
        known_answers = load_known_answers()

        # Apply
        for i, job in enumerate(new_jobs, 1):
            if time.time() - start_time_epoch > MAX_RUN_SECONDS:
                logger.info(f"⏱️ Maximum run duration reached ({MAX_RUN_SECONDS}s). Stopping applications to enter sleep mode.")
                break

            if MAX_APPLICATIONS > 0 and applied_count >= MAX_APPLICATIONS:
                logger.info(f"Target applications limit ({MAX_APPLICATIONS}) reached.")
                break

            logger.info(f"[{i}/{len(new_jobs)}] {job['company']} — {job['title'][:50]}")
            job = apply_to_job(driver, job, known_answers)
            results.append(job)

            if job["status"] == "Applied":
                applied_count += 1
                applied_urls.add(job["naukri_url"])
            elif job["status"] == "External":
                applied_urls.add(job["naukri_url"])

            # ── Real-time one-by-one logging (Local CSV + Google Sheets) ──
            append_single_job_to_local_csv(job)

            if sheets and GOOGLE_SHEET_ID:
                try:
                    if job["status"] == "External":
                        append_external_job(sheets, GOOGLE_SHEET_ID, job)
                        logger.info(f"  📋 Logged to 'Apply on Website' sheet")
                    elif job["status"] == "Applied":
                        append_direct_job(sheets, GOOGLE_SHEET_ID, job)
                        logger.info(f"  📋 Logged to 'Direct Applied' sheet")
                except Exception as e:
                    logger.warning(f"Sheets append failed: {e}")

            human_sleep(2.0, 5.0)

    except Exception as e:
        logger.error(f"Bot error: {e}")
        traceback.print_exc()
    finally:
        n_applied  = sum(1 for j in results if j.get("status") == "Applied")
        n_external = sum(1 for j in results if j.get("status") == "External")
        n_failed   = sum(1 for j in results if j.get("status") in ("Failed", "No Apply Button"))

        logger.info("=" * 60)
        logger.info("RUN SUMMARY")
        logger.info(f"  Applied (Naukri)   : {n_applied}")
        logger.info(f"  External (Sheets)  : {n_external}")
        logger.info(f"  Failed             : {n_failed}")
        logger.info("=" * 60)

        if not notification_sent:
            send_telegram_notification(format_telegram_summary(FIRSTNAME, start_time_str, n_applied))
            notification_sent = True
        save_seen_cache(seen_cache)
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed.")
            except Exception:
                pass
        lock.release()


if __name__ == "__main__":
    run_bot()
