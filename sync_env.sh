#!/usr/bin/env bash
# sync_env.sh — Instantly update bot requirements without rebuilding Docker
# Usage:
#   ./sync_env.sh bhanu   -> updates naukri-bot-bhanu from .env.bhanu (~5-10 seconds)
#   ./sync_env.sh kiran   -> updates naukri-bot-kiran from .env.kiran (~5-10 seconds)
#   ./sync_env.sh rahul   -> updates naukri-bot & naukri-bot-rahul from .env.rahul (~5-10 seconds)
#   ./sync_env.sh all     -> updates all bots

set -e

TARGET="${1:-}"
REGION="asia-south1"

if [ -z "$TARGET" ]; then
    echo "Usage: $0 [bhanu|kiran|rahul|all]"
    exit 1
fi

update_service_env() {
    local bot_name="$1"
    local env_file=".env.$bot_name"
    local services=()

    if [ ! -f "$env_file" ]; then
        echo "❌ Error: $env_file not found!"
        return 1
    fi

    case "$bot_name" in
        bhanu)
            services=("naukri-bot-bhanu")
            session_bucket="metabase-mvp-naukri-session-bhanu"
            answers_file="application_answers_bhanu.csv"
            ;;
        kiran)
            services=("naukri-bot-kiran")
            session_bucket="metabase-mvp-naukri-session-kiran"
            answers_file="application_answers_kiran.csv"
            ;;
        rahul)
            services=("naukri-bot" "naukri-bot-rahul")
            session_bucket="metabase-mvp-naukri-session"
            answers_file="application_answers_rahul.csv"
            ;;
        *)
            echo "❌ Unknown bot: $bot_name (expected: bhanu, kiran, rahul)"
            return 1
            ;;
    esac

    local yaml_file="/tmp/env_${bot_name}.yaml"
    echo "⚙️  Generating environment configuration from $env_file..."

    python3 -c "
import yaml

env_data = {}
with open('$env_file') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip().strip('\"').strip(\"'\")
        if k in ('HEADLESS', 'GCS_BUCKET', 'ANSWERS_CSV'):
            continue
        env_data[k] = str(v)

# System managed defaults
env_data['GCS_BUCKET'] = '$session_bucket'
env_data['ANSWERS_CSV'] = '$answers_file'
env_data['HEADLESS'] = 'true'

with open('$yaml_file', 'w') as out:
    yaml.dump(env_data, out, default_flow_style=False, sort_keys=False)
"

    for svc in "${services[@]}"; do
        echo "🚀 Updating $svc in $REGION (zero-rebuild)..."
        gcloud run services update "$svc" \
            --env-vars-file="$yaml_file" \
            --region="$REGION" \
            --quiet > /dev/null

        gcloud run services update-traffic "$svc" \
            --to-latest \
            --region="$REGION" \
            --quiet > /dev/null

        echo "✅ $svc updated successfully!"
    done

    rm -f "$yaml_file"
}

case "$TARGET" in
    bhanu|kiran|rahul)
        update_service_env "$TARGET"
        ;;
    all)
        update_service_env "bhanu"
        update_service_env "kiran"
        update_service_env "rahul"
        ;;
    *)
        echo "❌ Unknown target: $TARGET"
        echo "Usage: $0 [bhanu|kiran|rahul|all]"
        exit 1
        ;;
esac

echo ""
echo "🎉 All requested environment variables are synced and active!"
