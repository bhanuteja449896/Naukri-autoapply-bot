# 🚀 Naukri Auto-Apply Bot — Operations, Architecture & Cost Guide

This reference document contains complete operational commands, architecture mappings, cost analytics, and emergency troubleshooting procedures for maintainers and AI assistants.

---

## 📊 1. Monthly Cost Analytics & Breakdown

### Google Cloud Platform (GCP) Configuration
- **Region**: `asia-south1` (Mumbai, India — Tier 2 pricing)
- **Cloud Run Sizing**: `1 vCPU`, `1.5 GiB RAM` per instance
- **Scaling**: `min-instances = 0` (scales to zero when idle = **$0.00 cost when idle**)
- **Schedule**: `0 8-22 * * *` (8:00 AM to 10:00 PM IST hourly = **15 runs/day** per bot)
- **Active Bots**: 3 (`naukri-bot-bhanu`, `naukri-bot-kiran`, `naukri-bot` / `naukri-bot-rahul`)
- **Total Invocations**: 15 runs/day × 3 bots × 30 days = **1,350 runs/month**

---

### Monthly Free Tier Allowances (Every Month)
| Service | Free Tier Allowance | Our Usage | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Cloud Scheduler** | 3 jobs free per billing account | Exactly 3 jobs | **$0.00** |
| **Cloud Run Requests** | 2,000,000 requests/month | 1,350 requests (0.07%) | **$0.00** |
| **Cloud Run vCPU** | 360,000 vCPU-seconds (100 CPU-hrs) | Partially / Fully offsets runs | Free tier applied |
| **Cloud Run RAM** | 180,000 GiB-seconds (50 GiB-hrs) | Partially / Fully offsets runs | Free tier applied |
| **Cloud Build** | 120 build-minutes/day | ~3 min only on code updates | **$0.00** |
| **Secret Manager** | 6 active secret versions | 2 secrets used | **$0.00** |
| **Google Sheets API** | Unlimited (Standard quota) | Append rows & read cache | **$0.00** |
| **Telegram Bot API** | Unlimited | Instant job notifications | **$0.00** |
| **Cloud Storage** | 5 GB (Regional storage free) | < 100 MB session cookies | **< $0.01** |
| **Artifact Registry** | $0.10 / GB / month | ~1.5 GB Docker images | **~$0.15** |

---

### Tier 2 (`asia-south1` Mumbai) Unit Rates (After Free Tier)
- **vCPU**: `$0.0000336` per second
- **RAM (GiB)**: `$0.0000042` per GiB-second
- **Combined (1 vCPU + 1.5 GiB)**: `$0.0000399` per second (≈ **$0.144 per active hour**)

---

### Estimated Monthly Cost Scenarios (All 3 Bots Combined)

| Scenario | Avg Run Duration | Monthly Execution Time | Free Tier Deduction | Net Monthly Cost (USD) | Net Monthly Cost (INR) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario A: Steady State (Optimized)**<br>Freshness 1d + 1-page stop when caught up | **~1.5 - 2 mins** (90s) | 33.75 hours | Covered ~98% by Free Tier | **$0.15 - $0.80** | **₹15 - ₹70** |
| **Scenario B: Moderate Activity**<br>New job batches, applying to 5-15 jobs/hour | **~5 mins** (300s) | 112.5 hours | Free Tier covers 100 vCPU hrs | **$3.00 - $4.00** | **₹250 - ₹350** |
| **Scenario C: Heavy Peak Running**<br>Fresh keywords, high search volume every hour | **~15 mins** (900s) | 337.5 hours | Free Tier covers 100 vCPU hrs | **$30.00 - $36.00** | **₹2,500 - ₹3,000** |

> **Key Takeaway**: Under current settings (`JOB_AGE_DAYS=1`, `MIN_PAGES_PER_KEYWORD=1`, `MAX_EMPTY_PAGES=1`), steady-state hourly runs take between 45 seconds and 3 minutes. Total monthly infrastructure cost across all 3 profiles typically totals **less than $5 (₹400) per month**.

---

## 🏗️ 2. Infrastructure & Service Mapping

| Bot Profile | Cloud Run Service | Scheduler Job | Session GCS Bucket | Config File |
| :--- | :--- | :--- | :--- | :--- |
| **Bhanu Teja** | `naukri-bot-bhanu` | `naukri-bot-bhanu-hourly` | `gs://metabase-mvp-naukri-session-bhanu` | [.env.bhanu](file:///workspaces/Naukri-autoapply-bot/.env.bhanu) |
| **Kiran Kumar** | `naukri-bot-kiran` | `naukri-bot-kiran-hourly` | `gs://metabase-mvp-naukri-session-kiran` | [.env.kiran](file:///workspaces/Naukri-autoapply-bot/.env.kiran) |
| **Rahul RP** | `naukri-bot`<br>`naukri-bot-rahul` | `naukri-bot-hourly` | `gs://metabase-mvp-naukri-session` | [.env.rahul](file:///workspaces/Naukri-autoapply-bot/.env.rahul) |

- **Project ID**: `metabase-mvp`
- **Default Region**: `asia-south1`
- **Container Repository**: `asia-south1-docker.pkg.dev/metabase-mvp/naukri-repo/naukri-bot:latest`

---

## ⚡ 3. Essential Operational Commands

### 🔄 A. Updating Config / Keywords / Requirements (Zero Rebuild — 10 Seconds)
Whenever you change keywords, locations, experience, salary, or sheets in `.env.<name>`:
```bash
# Update Bhanu only
./sync_env.sh bhanu

# Update Kiran only
./sync_env.sh kiran

# Update Rahul only
./sync_env.sh rahul

# Update ALL 3 bots simultaneously
./sync_env.sh all
```

---

### 🚀 B. Manual Triggering (Run Bot On-Demand)
To run a bot right now without waiting for the schedule:
```bash
# Trigger Bhanu
gcloud scheduler jobs run naukri-bot-bhanu-hourly --location=asia-south1

# Trigger Kiran
gcloud scheduler jobs run naukri-bot-kiran-hourly --location=asia-south1

# Trigger Rahul
gcloud scheduler jobs run naukri-bot-hourly --location=asia-south1
```

---

### 📋 C. Viewing Live Logs & Execution Status
```bash
# View last 40 log lines for Bhanu
gcloud logging read 'resource.labels.service_name="naukri-bot-bhanu"' --limit=40 --format="value(textPayload)"

# Stream / tail live logs for Kiran
gcloud logging tail 'resource.labels.service_name="naukri-bot-kiran"'

# Check the duration and status of the latest runs
gcloud logging read 'resource.type="cloud_run_revision" AND httpRequest.status=200' \
  --limit=10 \
  --format="table(resource.labels.service_name, timestamp, httpRequest.latency, httpRequest.status)"
```

---

### ⏸️ D. Pausing and Resuming Schedules
```bash
# Pause a schedule (stop hourly runs)
gcloud scheduler jobs pause naukri-bot-bhanu-hourly --location=asia-south1

# Resume a schedule
gcloud scheduler jobs resume naukri-bot-bhanu-hourly --location=asia-south1

# Check current status of all schedules
gcloud scheduler jobs list --location=asia-south1
```

---

### 🐳 E. Rebuilding Docker Image (Code Changes to `naukri_bot.py`)
Only needed when modifying Python source code or dependencies:
```bash
# 1. Build and push image via Cloud Build (~3 mins)
gcloud builds submit \
  --tag asia-south1-docker.pkg.dev/metabase-mvp/naukri-repo/naukri-bot:latest \
  --gcs-source-staging-dir="gs://metabase-mvp-naukri-build-staging/source" \
  --project=metabase-mvp

# 2. Update all services to the new image
for svc in naukri-bot-bhanu naukri-bot-kiran naukri-bot naukri-bot-rahul; do
  gcloud run services update "$svc" \
    --image="asia-south1-docker.pkg.dev/metabase-mvp/naukri-repo/naukri-bot:latest" \
    --region="asia-south1" \
    --quiet
done

# 3. Re-apply env vars & route 100% traffic
./sync_env.sh all
```

---

## 🛠️ 4. Key Configuration Levers & Gotchas

1. **`JOB_AGE_DAYS=1`**:
   - Keeps search focused only on jobs posted in the last 24 hours.
   - Prevents scraping stale jobs and saves ~80% of execution time.

2. **`MIN_PAGES_PER_KEYWORD=1` & `MAX_EMPTY_PAGES=1`**:
   - Ensures the bot visits at least 1 page per keyword.
   - If page 1 has 0 new jobs (or all duplicates), the bot immediately skips to the next keyword instead of cycling through 3-5 empty pages.

3. **Naukri URL Rules**:
   - **Never** add `wfhType=0,1` to keyword search slugs (it breaks Naukri Next.js hydration into an infinite loading shimmer). Filtering for WFH/Hybrid is handled in Python code via `WORK_MODE_ONLY`.
   - **Never** merge all keywords into a single `k=Java, Spring Boot...` URL query. Naukri returns 0 results for complex boolean strings. Sequential single-keyword search (`/{slug}-jobs`) is mandatory.

4. **Google Sheets Real-time Logging**:
   - The bot logs each job (`Direct Applied` or `Apply on Website`) **immediately** after clicking the button, before moving to the next card. Data is preserved even if the container reaches its 30-minute timeout.
