#!/bin/bash
# deploy.sh — One-command Cloud Run deployment
# =============================================
# Prerequisites:
#   1. Google Cloud SDK installed: https://cloud.google.com/sdk/docs/install
#   2. Run: gcloud auth login
#   3. Run: gcloud config set project YOUR_PROJECT_ID
#   4. token.json and credentials.json must exist locally (run sheets_auth.py first)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -e

# ── CONFIG — edit these ──────────────────────────────────────────────────────
PROJECT_ID="metabase-mvp"
SERVICE_NAME="naukri-bot"
REGION="asia-south1"                     # Mumbai — closest to India
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/naukri-repo/$SERVICE_NAME"

# ─────────────────────────────────────────────────────────────────────────────
echo "========================================"
echo "  Naukri Bot — Cloud Run Deployment"
echo "========================================"

# 1. Enable required APIs
echo "[1/7] Enabling Google Cloud APIs..."
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    --project=$PROJECT_ID

# 2. Create secrets from local files
echo "[2/7] Uploading secrets to Secret Manager..."

# token.json
if [ -f token.json ]; then
    gcloud secrets create naukri-bot-token \
        --data-file=token.json \
        --project=$PROJECT_ID 2>/dev/null || \
    gcloud secrets versions add naukri-bot-token \
        --data-file=token.json \
        --project=$PROJECT_ID
    echo "  token.json uploaded"
else
    echo "  ERROR: token.json not found. Run: python sheets_auth.py"
    exit 1
fi

# credentials.json
if [ -f credentials.json ]; then
    gcloud secrets create naukri-bot-credentials \
        --data-file=credentials.json \
        --project=$PROJECT_ID 2>/dev/null || \
    gcloud secrets versions add naukri-bot-credentials \
        --data-file=credentials.json \
        --project=$PROJECT_ID
    echo "  credentials.json uploaded"
else
    echo "  ERROR: credentials.json not found. Download from Google Cloud Console."
    exit 1
fi

# 3. Build and push Docker image to Artifact Registry
echo "[3/7] Building Docker image..."
gcloud artifacts repositories create naukri-repo --repository-format=docker --location=$REGION --project=$PROJECT_ID 2>/dev/null || true
BUILD_STAGING_BUCKET="${PROJECT_ID}-naukri-build-staging"
gcloud storage buckets create gs://$BUILD_STAGING_BUCKET --location=$REGION --project=$PROJECT_ID 2>/dev/null || true
gcloud builds submit --tag $IMAGE --gcs-source-staging-dir="gs://$BUILD_STAGING_BUCKET/source" --project=$PROJECT_ID

# 3b. Create GCS Session Bucket for persistent login sessions
SESSION_BUCKET="${PROJECT_ID}-naukri-session"
echo "Setting up session bucket gs://$SESSION_BUCKET ..."
gcloud storage buckets create gs://$SESSION_BUCKET --location=$REGION --project=$PROJECT_ID 2>/dev/null || true

# 3c. Grant Secret Manager & Storage permissions to Cloud Run service account
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "Granting secret and storage access to $COMPUTE_SA ..."
for SECRET in naukri-bot-token naukri-bot-credentials; do
    gcloud secrets add-iam-policy-binding $SECRET \
        --member="serviceAccount:$COMPUTE_SA" \
        --role="roles/secretmanager.secretAccessor" \
        --project=$PROJECT_ID 2>/dev/null || true
done
gcloud storage buckets add-iam-policy-binding gs://$SESSION_BUCKET \
    --member="serviceAccount:$COMPUTE_SA" \
    --role="roles/storage.objectAdmin" \
    --project=$PROJECT_ID 2>/dev/null || true

# 4. Prepare env.yaml and deploy to Cloud Run
echo "[4/7] Deploying to Cloud Run ($REGION)..."

python3 -c "
import os
session_bucket = '${SESSION_BUCKET}'
env_file = '.env.rahul' if os.path.exists('.env.rahul') else '.env'
answers_file = 'application_answers_rahul.csv' if os.path.exists('application_answers_rahul.csv') else 'application_answers.csv'

with open(env_file) as f, open('env.yaml', 'w') as out:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip()
        if k == 'HEADLESS':
            continue
        v_clean = v.replace('\"', '\\\"')
        out.write(f'{k}: \"{v_clean}\"\n')
    out.write(f'GCS_BUCKET: \"{session_bucket}\"\n')
    out.write(f'ANSWERS_CSV: \"{answers_file}\"\n')
    out.write('HEADLESS: \"true\"\n')
"

gcloud run deploy $SERVICE_NAME \
    --image=$IMAGE \
    --platform=managed \
    --region=$REGION \
    --project=$PROJECT_ID \
    --no-allow-unauthenticated \
    --memory=1.5Gi \
    --cpu=1 \
    --timeout=900 \
    --no-cpu-throttling \
    --max-instances=1 \
    --concurrency=1 \
    --env-vars-file=env.yaml \
    --set-secrets="/secrets/token/token.json=naukri-bot-token:latest,/secrets/credentials/credentials.json=naukri-bot-credentials:latest"

gcloud run services update-traffic $SERVICE_NAME --to-latest --region=$REGION --project=$PROJECT_ID

rm -f env.yaml

# Get the Cloud Run service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(status.url)")

echo "  Deployed at: $SERVICE_URL"

# 5. Create Service Account for Cloud Scheduler
echo "[5/7] Setting up Cloud Scheduler service account..."
SA_NAME="naukri-scheduler"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME \
    --display-name="Naukri Bot Scheduler" \
    --project=$PROJECT_ID 2>/dev/null || true

# Grant the SA permission to invoke Cloud Run
gcloud run services add-iam-policy-binding $SERVICE_NAME \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/run.invoker" \
    --region=$REGION \
    --project=$PROJECT_ID

# 6. Create Cloud Scheduler job (every hour during hiring hours 8 AM - 10 PM IST)
echo "[6/7] Creating hourly Cloud Scheduler job (8 AM - 10 PM IST)..."
gcloud scheduler jobs create http naukri-bot-hourly \
    --schedule="0 8-22 * * *" \
    --uri="$SERVICE_URL/run" \
    --http-method=POST \
    --oidc-service-account-email=$SA_EMAIL \
    --oidc-token-audience=$SERVICE_URL \
    --location=$REGION \
    --project=$PROJECT_ID \
    --time-zone="Asia/Kolkata" \
    --attempt-deadline=15m \
    --description="Triggers Naukri bot every hour during hiring hours" \
    2>/dev/null || \
gcloud scheduler jobs update http naukri-bot-hourly \
    --schedule="0 8-22 * * *" \
    --uri="$SERVICE_URL/run" \
    --http-method=POST \
    --oidc-service-account-email=$SA_EMAIL \
    --oidc-token-audience=$SERVICE_URL \
    --location=$REGION \
    --project=$PROJECT_ID \
    --time-zone="Asia/Kolkata" \
    --attempt-deadline=15m

# 7. Grant Secret Manager access to Cloud Run service account
echo "[7/7] Granting secret access..."
CR_SA=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION --project=$PROJECT_ID \
    --format="value(spec.template.spec.serviceAccountName)")

for SECRET in naukri-bot-token naukri-bot-credentials; do
    gcloud secrets add-iam-policy-binding $SECRET \
        --member="serviceAccount:$CR_SA" \
        --role="roles/secretmanager.secretAccessor" \
        --project=$PROJECT_ID
done

# Grant storage access for session sync
gcloud storage buckets add-iam-policy-binding gs://$SESSION_BUCKET \
    --member="serviceAccount:$CR_SA" \
    --role="roles/storage.objectAdmin" \
    --project=$PROJECT_ID 2>/dev/null || true

echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo "  Service URL : $SERVICE_URL"
echo "  Schedule    : Every hour (Asia/Kolkata)"
echo ""
echo "  To trigger manually:"
echo "  gcloud scheduler jobs run naukri-bot-hourly --location=$REGION"
echo ""
echo "  To view logs:"
echo "  gcloud run services logs read $SERVICE_NAME --region=$REGION --limit=50"
echo "========================================"
