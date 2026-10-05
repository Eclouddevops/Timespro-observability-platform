#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Load SMTP credentials from AWS Secrets Manager into .env
#
# Run this:
#   - On server startup (add to crontab @reboot)
#   - Before starting docker compose
#   - Called automatically by docker-compose via entrypoint
#
# NO passwords are stored on disk — fetched from Secrets Manager
# each time using the EC2 IAM Instance Profile
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SECRET_NAME="observability/ses-smtp"
ENV_FILE="${ENV_FILE:-/observability-platform/.env}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
error(){ echo "[ERROR] $*" >&2; exit 1; }

log "Loading SMTP credentials from AWS Secrets Manager..."

# Fetch secret
SECRET_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --region "$REGION" \
    --query SecretString \
    --output text 2>/dev/null) || error "Cannot fetch secret '$SECRET_NAME'. Check IAM role permissions."

# Parse values
SMTP_HOST=$(echo "$SECRET_JSON"     | python3 -c "import json,sys; print(json.load(sys.stdin)['smtp_host'])")
SMTP_FROM=$(echo "$SECRET_JSON"     | python3 -c "import json,sys; print(json.load(sys.stdin)['smtp_from'])")
SMTP_USER=$(echo "$SECRET_JSON"     | python3 -c "import json,sys; print(json.load(sys.stdin)['smtp_user'])")
SMTP_PASSWORD=$(echo "$SECRET_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['smtp_password'])")

# Remove old SMTP_PASSWORD line from .env
sed -i '/^SMTP_PASSWORD=/d' "$ENV_FILE" 2>/dev/null || true
sed -i '/^SMTP_HOST=/d'     "$ENV_FILE" 2>/dev/null || true
sed -i '/^SMTP_FROM=/d'     "$ENV_FILE" 2>/dev/null || true
sed -i '/^SMTP_USER=/d'     "$ENV_FILE" 2>/dev/null || true

# Write fresh values
cat >> "$ENV_FILE" << ENVEOF
SMTP_HOST=${SMTP_HOST}
SMTP_FROM=${SMTP_FROM}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}
ENVEOF

log "✅ SMTP credentials loaded from Secrets Manager"
log "   Host: $SMTP_HOST"
log "   From: $SMTP_FROM"
log "   User: $SMTP_USER"
log "   Pass: *** (hidden)"

# Reload alertmanager if running
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "alertmanager"; then
    docker compose restart alertmanager 2>/dev/null && log "✅ Alertmanager restarted" || true
fi
