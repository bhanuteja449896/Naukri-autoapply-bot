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
PROJECT_ID="your-gcloud-project-id"      # gcloud projects list
SERVICE_NAME="naukri-bot"
REGION="asia-south1"                     # Mumbai — closest to India
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

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

# 3. Build and push Docker image
echo "[3/7] Building Docker image..."
gcloud builds submit --tag $IMAGE --project=$PROJECT_ID

# 3b. Create GCS Session Bucket for persistent login sessions
SESSION_BUCKET="${PROJECT_ID}-naukri-session"
echo "Setting up session bucket gs://$SESSION_BUCKET ..."
gcloud storage buckets create gs://$SESSION_BUCKET --location=$REGION --project=$PROJECT_ID 2>/dev/null || true

# 4. Deploy to Cloud Run
echo "[4/7] Deploying to Cloud Run ($REGION)..."
gcloud run deploy $SERVICE_NAME \
    --image=$IMAGE \
    --platform=managed \
    --region=$REGION \
    --project=$PROJECT_ID \
    --no-allow-unauthenticated \
    --memory=2Gi \
    --cpu=2 \
    --timeout=900 \
    --no-cpu-throttling \
    --max-instances=1 \
    --concurrency=1 \
    --set-env-vars="HEADLESS=true,SHEET_NAME=Applications,GCS_BUCKET=$SESSION_BUCKET" \
    --set-secrets="/secrets/token=naukri-bot-token:latest,/secrets/credentials=naukri-bot-credentials:latest" \
    --update-env-vars-from-file=.env

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

# 6. Create Cloud Scheduler job (every hour)
echo "[6/7] Creating hourly Cloud Scheduler job..."
gcloud scheduler jobs create http naukri-bot-hourly \
    --schedule="0 * * * *" \
    --uri="$SERVICE_URL/run" \
    --http-method=POST \
    --oidc-service-account-email=$SA_EMAIL \
    --oidc-token-audience=$SERVICE_URL \
    --location=$REGION \
    --project=$PROJECT_ID \
    --time-zone="Asia/Kolkata" \
    --attempt-deadline=15m \
    --description="Triggers Naukri bot every hour" \
    2>/dev/null || \
gcloud scheduler jobs update http naukri-bot-hourly \
    --schedule="0 * * * *" \
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
