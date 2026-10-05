#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# AWS SES Setup Script — No passwords stored anywhere
#
# Uses IAM Instance Profile credentials (already attached to EC2)
# Creates SES SMTP credentials via IAM and stores in AWS Secrets Manager
#
# Run this ONCE on your observability server after IAM role is attached
# Usage: sudo bash scripts/setup-ses.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SECRET_NAME="observability/ses-smtp"
FROM_EMAIL="${SMTP_FROM:-observability-alerts@timesgroup.com}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
error(){ echo "[ERROR] $*" >&2; exit 1; }

log "=== AWS SES Setup (no passwords stored) ==="
log "Account : $ACCOUNT_ID"
log "Region  : $REGION"
log "From    : $FROM_EMAIL"

# ── Step 1: Verify SES domain/email ──────────────────────────────────
log "Checking SES identity verification..."
VERIFIED=$(aws ses list-verified-email-addresses --region "$REGION" \
    --query "VerifiedEmailAddresses" --output text 2>/dev/null || echo "")

if echo "$VERIFIED" | grep -q "$FROM_EMAIL"; then
    log "✅ $FROM_EMAIL is already verified in SES"
else
    log "Requesting SES verification for: $FROM_EMAIL"
    aws ses verify-email-identity --email-address "$FROM_EMAIL" --region "$REGION"
    log "📧 Verification email sent to $FROM_EMAIL — click the link to verify"
    log "   (Re-run this script after verifying)"
fi

# ── Step 2: Create SES SMTP IAM user ─────────────────────────────────
IAM_USER="observability-ses-smtp"
log "Creating IAM user for SES SMTP: $IAM_USER"

# Create user if not exists
aws iam get-user --user-name "$IAM_USER" &>/dev/null || \
    aws iam create-user --user-name "$IAM_USER" \
        --tags Key=Purpose,Value=ObservabilitySES Key=ManagedBy,Value=setup-ses.sh

# Attach SES send policy
aws iam put-user-policy \
    --user-name "$IAM_USER" \
    --policy-name "SESSendEmail" \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ses:SendEmail","ses:SendRawEmail"],
            "Resource": "*"
        }]
    }'

# Create access key
log "Generating SES SMTP credentials..."
KEY_JSON=$(aws iam create-access-key --user-name "$IAM_USER" --output json)
ACCESS_KEY=$(echo "$KEY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['AccessKey']['AccessKeyId'])")
SECRET_KEY=$(echo "$KEY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['AccessKey']['SecretAccessKey'])")

# ── Step 3: Convert IAM key to SES SMTP password ─────────────────────
# AWS SES SMTP password is derived from the secret key using HMAC-SHA256
SMTP_PASSWORD=$(python3 << 'PYEOF'
import hmac, hashlib, base64, sys
date   = "11111111"
region = "$REGION"
service= "ses"
message= "SendRawEmail"
version= "\x04"
secret_key = "$SECRET_KEY"

def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

sig = sign(sign(sign(sign(
    ("AWS4" + secret_key).encode("utf-8"), date),
    region), service), "aws4_request")
smtp_pw = base64.b64encode(version.encode("latin1") + sign(sig, message)).decode()
print(smtp_pw)
PYEOF
)

# Fix variable substitution in heredoc
SMTP_PASSWORD=$(python3 - "$SECRET_KEY" "$REGION" << 'PYEOF'
import hmac, hashlib, base64, sys
secret_key = sys.argv[1]
region     = sys.argv[2]
date    = "11111111"
service = "ses"
message = "SendRawEmail"
version = b"\x04"
def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
sig = sign(sign(sign(sign(("AWS4"+secret_key).encode("utf-8"),date),region),service),"aws4_request")
print(base64.b64encode(version + sign(sig, message)).decode())
PYEOF
)

SES_SMTP_HOST="email-smtp.${REGION}.amazonaws.com"
SES_SMTP_USER="$ACCESS_KEY"
SES_SMTP_PASS="$SMTP_PASSWORD"

# ── Step 4: Store in AWS Secrets Manager ─────────────────────────────
log "Storing SMTP credentials in AWS Secrets Manager: $SECRET_NAME"

SECRET_VALUE=$(python3 -c "
import json
print(json.dumps({
    'smtp_host':     '${SES_SMTP_HOST}:587',
    'smtp_from':     '${FROM_EMAIL}',
    'smtp_user':     '${SES_SMTP_USER}',
    'smtp_password': '${SES_SMTP_PASS}'
}))
")

# Create or update secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" &>/dev/null; then
    aws secretsmanager put-secret-value \
        --secret-id "$SECRET_NAME" \
        --secret-string "$SECRET_VALUE" \
        --region "$REGION"
    log "✅ Secret updated: $SECRET_NAME"
else
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "SES SMTP credentials for Observability Platform alerts" \
        --secret-string "$SECRET_VALUE" \
        --tags Key=Purpose,Value=ObservabilityAlerts \
        --region "$REGION"
    log "✅ Secret created: $SECRET_NAME"
fi

# ── Step 5: Write to .env (SMTP creds only, not passwords) ───────────
log "Updating .env with SES SMTP settings..."
ENV_FILE="/observability-platform/.env"
[[ -f "$ENV_FILE" ]] || ENV_FILE="$(dirname "$0")/../.env"

# Remove old SMTP lines
sed -i '/^SMTP_/d' "$ENV_FILE" 2>/dev/null || true

# Add SES settings (no password — loaded from Secrets Manager at runtime)
cat >> "$ENV_FILE" << ENVEOF

# ── AWS SES SMTP (credentials from Secrets Manager — no password here) ──
SMTP_HOST=${SES_SMTP_HOST}:587
SMTP_FROM=${FROM_EMAIL}
SMTP_USER=${SES_SMTP_USER}
# SMTP_PASSWORD is loaded from AWS Secrets Manager at startup
# Secret name: ${SECRET_NAME}
ENVEOF

log ""
log "════════════════════════════════════════════════════"
log "  ✅ SES Setup Complete!"
log ""
log "  SMTP Host : ${SES_SMTP_HOST}:587"
log "  From      : ${FROM_EMAIL}"
log "  User      : ${SES_SMTP_USER}"
log "  Password  : stored in Secrets Manager (${SECRET_NAME})"
log ""
log "  Next steps:"
log "  1. Verify $FROM_EMAIL in SES console if not done"
log "  2. Run: sudo bash scripts/load-smtp-secret.sh"
log "  3. Run: docker compose restart alertmanager"
log "════════════════════════════════════════════════════"
