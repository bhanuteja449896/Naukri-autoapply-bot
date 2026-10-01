#!/bin/bash
# entrypoint.sh — Cloud Run container startup script
# Links secrets mounted by Cloud Run into /app/ working directory

set -e

# Cloud Run mounts secrets at these paths (configured in deploy command):
#   /secrets/token        → token.json
#   /secrets/credentials  → credentials.json

echo "Setting up secrets..."

if [ -f /secrets/token ]; then
    cp /secrets/token /app/token.json
    echo "  token.json linked from secret"
fi

if [ -f /secrets/credentials ]; then
    cp /secrets/credentials /app/credentials.json
    echo "  credentials.json linked from secret"
fi

echo "Starting HTTP server..."
exec python cloud_run_server.py
