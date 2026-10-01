#!/bin/bash
# entrypoint.sh — Cloud Run container startup script

echo "Setting up secrets..."

if [ -f /secrets/token/token.json ]; then
    cat /secrets/token/token.json > /app/token.json
    echo "  token.json configured from /secrets/token/"
elif [ -f /secrets/token ]; then
    cat /secrets/token > /app/token.json
    echo "  token.json configured from /secrets/token"
fi

if [ -f /secrets/credentials/credentials.json ]; then
    cat /secrets/credentials/credentials.json > /app/credentials.json
    echo "  credentials.json configured from /secrets/credentials/"
elif [ -f /secrets/credentials ]; then
    cat /secrets/credentials > /app/credentials.json
    echo "  credentials.json configured from /secrets/credentials"
fi

echo "Starting HTTP server on port ${PORT:-8080}..."
exec python cloud_run_server.py
