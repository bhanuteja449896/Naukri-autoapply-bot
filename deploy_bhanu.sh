#!/bin/bash
# deploy_bhanu.sh — Deploy dedicated Cloud Run bot for Bhanu Teja Makkineni
# =========================================================================

set -e

PROJECT_ID="metabase-mvp"
SERVICE_NAME="naukri-bot-bhanu"
REGION="asia-south1"                     # Mumbai — closest to India
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/naukri-repo/$SERVICE_NAME"
ENV_FILE=".env.bhanu"
[ ! -f "$ENV_FILE" ] && [ -f ".env" ] && ENV_FILE=".env"

ANSWERS_FILE="application_answers_bhanu.csv"
[ ! -f "$ANSWERS_FILE" ] && [ -f "application_answers.csv" ] && ANSWERS_FILE="application_answers.csv"

echo "========================================"
echo "  Bhanu Teja Bot — Cloud Run Deployment"
echo "  Service: $SERVICE_NAME"
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

# 2. Upload secrets to Secret Manager
echo "[2/7] Uploading secrets to Secret Manager..."
if [ -f token.json ]; then
    gcloud secrets create naukri-bot-token \
        --data-file=token.json \
        --project=$PROJECT_ID 2>/dev/null || \
    gcloud secrets versions add naukri-bot-token \
        --data-file=token.json \
        --project=$PROJECT_ID
    echo "  token.json uploaded"
else
    echo "  ERROR: token.json not found."
    exit 1
fi

if [ -f credentials.json ]; then
    gcloud secrets create naukri-bot-credentials \
        --data-file=credentials.json \
        --project=$PROJECT_ID 2>/dev/null || \
    gcloud secrets versions add naukri-bot-credentials \
        --data-file=credentials.json \
        --project=$PROJECT_ID
    echo "  credentials.json uploaded"
else
    echo "  ERROR: credentials.json not found."
    exit 1
fi

# 3. Build and push Docker image
echo "[3/7] Building Docker image for $SERVICE_NAME..."
gcloud artifacts repositories create naukri-repo --repository-format=docker --location=$REGION --project=$PROJECT_ID 2>/dev/null || true
BUILD_STAGING_BUCKET="${PROJECT_ID}-naukri-build-staging"
gcloud storage buckets create gs://$BUILD_STAGING_BUCKET --location=$REGION --project=$PROJECT_ID 2>/dev/null || true
gcloud builds submit --tag $IMAGE --gcs-source-staging-dir="gs://$BUILD_STAGING_BUCKET/source" --project=$PROJECT_ID

# 3b. Create GCS Session Bucket for Bhanu's login session
SESSION_BUCKET="${PROJECT_ID}-naukri-session-bhanu"
echo "Setting up session bucket gs://$SESSION_BUCKET ..."
gcloud storage buckets create gs://$SESSION_BUCKET --location=$REGION --project=$PROJECT_ID 2>/dev/null || true

# 3c. Grant permissions
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
echo "[4/7] Deploying $SERVICE_NAME to Cloud Run ($REGION)..."

python3 -c "
session_bucket = '${SESSION_BUCKET}'
answers_file = '${ANSWERS_FILE}'
with open('${ENV_FILE}') as f, open('env_bhanu.yaml', 'w') as out:
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
    --timeout=1800 \
    --no-cpu-throttling \
    --max-instances=1 \
    --concurrency=1 \
    --env-vars-file=env_bhanu.yaml \
    --set-secrets="/secrets/token/token.json=naukri-bot-token:latest,/secrets/credentials/credentials.json=naukri-bot-credentials:latest"

gcloud run services update-traffic $SERVICE_NAME --to-latest --region=$REGION --project=$PROJECT_ID

rm -f env_bhanu.yaml

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION \
    --project=$PROJECT_ID \
    --format="value(status.url)")

echo "  Deployed at: $SERVICE_URL"

# 5. Service Account for Scheduler
echo "[5/7] Setting up Cloud Scheduler service account..."
SA_NAME="naukri-scheduler"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME \
    --display-name="Naukri Bot Scheduler" \
    --project=$PROJECT_ID 2>/dev/null || true

gcloud run services add-iam-policy-binding $SERVICE_NAME \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/run.invoker" \
    --region=$REGION \
    --project=$PROJECT_ID

# 6. Create dedicated hourly Cloud Scheduler job
echo "[6/7] Creating hourly Cloud Scheduler job for Bhanu (8 AM - 10 PM IST)..."
JOB_NAME="naukri-bot-bhanu-hourly"
gcloud scheduler jobs create http $JOB_NAME \
    --schedule="0 8-22 * * *" \
    --uri="$SERVICE_URL/run" \
    --http-method=POST \
    --oidc-service-account-email=$SA_EMAIL \
    --oidc-token-audience=$SERVICE_URL \
    --location=$REGION \
    --project=$PROJECT_ID \
    --time-zone="Asia/Kolkata" \
    --attempt-deadline=30m \
    --description="Triggers Bhanu Naukri bot every hour during hiring hours" \
    2>/dev/null || \
gcloud scheduler jobs update http $JOB_NAME \
    --schedule="0 8-22 * * *" \
    --uri="$SERVICE_URL/run" \
    --http-method=POST \
    --oidc-service-account-email=$SA_EMAIL \
    --oidc-token-audience=$SERVICE_URL \
    --location=$REGION \
    --project=$PROJECT_ID \
    --time-zone="Asia/Kolkata" \
    --attempt-deadline=30m

# 7. Grant access
CR_SA=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION --project=$PROJECT_ID \
    --format="value(spec.template.spec.serviceAccountName)")

for SECRET in naukri-bot-token naukri-bot-credentials; do
    gcloud secrets add-iam-policy-binding $SECRET \
        --member="serviceAccount:$CR_SA" \
        --role="roles/secretmanager.secretAccessor" \
        --project=$PROJECT_ID
done

gcloud storage buckets add-iam-policy-binding gs://$SESSION_BUCKET \
    --member="serviceAccount:$CR_SA" \
    --role="roles/storage.objectAdmin" \
    --project=$PROJECT_ID 2>/dev/null || true

echo ""
echo "========================================"
echo "  Bhanu Bot Deployment Complete!"
echo "========================================"
echo "  Service URL : $SERVICE_URL"
echo "  Schedule    : Every hour (Asia/Kolkata)"
echo "  Job Name    : $JOB_NAME"
echo "========================================"
