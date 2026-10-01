# ─────────────────────────────────────────────────────────────────────────────
# Naukri Auto-Apply Bot — Cloud Run Container
# ─────────────────────────────────────────────────────────────────────────────
# Base image: slim Python + Chromium pre-installed (for undetected-chromedriver)
FROM python:3.11-slim

# Install system dependencies for Chrome / undetected-chromedriver
RUN apt-get update && apt-get install -y \
    wget curl gnupg unzip \
    chromium chromium-driver \
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
    xdg-utils libxss1 libgbm1 libu2f-udev \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot files
COPY naukri_bot.py .
COPY sheets_client.py .
COPY sheets_auth.py .
COPY application_answers.csv .

# Cloud Run: secrets are mounted at /secrets/ (configured in deploy command)
# token.json and credentials.json are symlinked at runtime via entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Cloud Run listens on PORT env var (default 8080)
# We also expose an HTTP endpoint so Cloud Scheduler can trigger us
COPY cloud_run_server.py .

ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true
ENV PORT=8080

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
